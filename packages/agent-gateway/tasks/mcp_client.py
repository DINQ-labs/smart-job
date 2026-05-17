"""tasks/mcp_client.py — task system 调 boss MCP 工具的统一入口

设计:
  - 一个 task 一个 ClientSession(整个 task 复用,不每 step 重连)
  - 通过 ctx['mcp'] 传给 step fn:`await ctx['mcp'].call('boss_search_jobs', {...})`
  - call() 自动:
    1. 抛 RiskControlSignal(detect_risk_signal 识别)
    2. 转 isError=true → RiskControlSignal('generic')
    3. 解 structured_data 出 dict
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import config
import db

# audit P2 fix:从跨服务 sys.path.insert 解耦,改用 repo-root 共享包 job_common
from job_common.risk_signals import RiskControlSignal
from job_common.risk_detector import detect_risk_signal

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = logging.getLogger(__name__)


class TaskMcpSession:
    """Task 范围的 MCP 客户端。通过 async with 进入,在 task 启动时建立,完成时拆除。"""

    def __init__(self, *, user_id: str, role: str, platform: str, request_id: str = ""):
        self.user_id = user_id
        self.role = role
        self.platform = platform
        self.request_id = request_id
        self._session: ClientSession | None = None
        self._cm = None
        self._session_cm = None

    async def __aenter__(self) -> "TaskMcpSession":
        headers: dict[str, str] = {}
        if self.user_id:
            headers["x-user-id"] = self.user_id
        if self.role:
            headers["x-user-role"] = self.role
        if self.request_id:
            headers["x-request-id"] = self.request_id
        # MCP HTTP url
        self._cm = streamablehttp_client(
            config.BOSS_GATEWAY_MCP,
            headers=headers if headers else None,
            timeout=300.0,
            sse_read_timeout=300.0,
        )
        # 分阶段 enter,任一阶段失败都回滚已 enter 的资源
        cm_entered = False
        try:
            read, write, _ = await self._cm.__aenter__()
            cm_entered = True
            self._session_cm = ClientSession(read, write)
            self._session = await self._session_cm.__aenter__()
            await self._session.initialize()
            return self
        except BaseException:
            # 失败时按相反顺序释放
            if self._session is not None:
                try: await self._session_cm.__aexit__(None, None, None)
                except Exception: pass
            if cm_entered:
                try: await self._cm.__aexit__(None, None, None)
                except Exception: pass
            self._session = None
            self._session_cm = None
            self._cm = None
            raise

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._session_cm:
                await self._session_cm.__aexit__(exc_type, exc, tb)
        except Exception:
            pass
        try:
            if self._cm:
                await self._cm.__aexit__(exc_type, exc, tb)
        except Exception:
            pass
        self._session = None
        self._cm = None
        self._session_cm = None

    async def call(self, tool_name: str, args: dict | None = None) -> dict:
        """调 MCP 工具。识别风控信号 → 抛 RiskControlSignal。
        正常 → 返回 structured_data dict(如果工具返了 JSON)否则 {'_text': result_text}。

        每次调用都埋点写 mcp_call_log(异步 fire-and-forget,不阻塞业务)给观测面用。
        """
        if self._session is None:
            raise RuntimeError("TaskMcpSession not entered")
        op_type = db.infer_mcp_op_type(tool_name)
        started = time.monotonic()
        success = False
        risk_signal_id: str | None = None
        try:
            try:
                result = await self._session.call_tool(tool_name, args or {})
            except Exception as e:
                sig_id = detect_risk_signal(tool_name, {}, str(e)) or "generic:unknown"
                risk_signal_id = sig_id
                raise RiskControlSignal(sig_id, original_text=str(e)) from e

            result_text = ""
            structured: dict = {}
            for c in (result.content or []):
                t = getattr(c, "text", None)
                if t:
                    result_text = t
                    # 尝试 JSON 解析
                    try:
                        parsed = json.loads(t)
                        if isinstance(parsed, dict):
                            structured = parsed
                    except Exception:
                        pass
                    break
            is_error = getattr(result, "isError", False)
            if is_error:
                sig_id = detect_risk_signal(tool_name, structured, result_text) or "generic:unknown"
                risk_signal_id = sig_id
                raise RiskControlSignal(sig_id, original_text=result_text or "")

            # ext 层检测的风控信号经常以 200 + 字段返回,主动检测
            sig_id = detect_risk_signal(tool_name, structured, "")
            if sig_id:
                risk_signal_id = sig_id
                raise RiskControlSignal(sig_id, original_text=str(structured)[:500])

            success = True
            if structured:
                return structured
            return {"_text": result_text}
        finally:
            # fire-and-forget:不 await,失败也不冒泡(record_mcp_call 内部 try 兜底)
            duration_ms = int((time.monotonic() - started) * 1000)
            try:
                asyncio.create_task(db.record_mcp_call(
                    user_id=self.user_id, role=self.role, platform=self.platform,
                    tool_name=tool_name, op_type=op_type,
                    duration_ms=duration_ms, success=success,
                    risk_signal=risk_signal_id,
                ))
            except RuntimeError:
                # 没 event loop(测试场景)→ 直接跳过
                pass


# _infer_op_type 已挪到 db.infer_mcp_op_type(给 task engine + agent_loop 共用)
