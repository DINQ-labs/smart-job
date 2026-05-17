"""
agent_tracker.py: 追踪 MCP Agent（HTTP 客户端）连接，按 mcp-session-id 识别。

绑定规则：
  - 第一次工具调用时强制绑定 agent → 扩展会话（session_id）
  - 绑定后锁死，不允许切换到其他会话
  - bound_account: 绑定时的 Boss 账号名（用于管理后台展示）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentTracker:
    def __init__(self) -> None:
        self._agents: dict[str, dict] = {}

    def on_connect(self, agent_id: str | None = None) -> str:
        """注册 agent，返回 agent_id。"""
        aid = agent_id or uuid.uuid4().hex[:16]
        if aid not in self._agents:
            self._agents[aid] = {
                "agent_id": aid,
                "connected_at": _utc_now(),
                "disconnected_at": None,
                "bound_session": None,
                "bound_account": "",
                "user_id": "",
                "user_tier": "",
                "status": "connected",
                "request_count": 0,
            }
        return aid

    def on_request(self, agent_id: str) -> None:
        """每次请求计数。"""
        if agent_id in self._agents:
            self._agents[agent_id]["request_count"] += 1

    def on_disconnect(self, agent_id: str) -> None:
        if agent_id in self._agents:
            self._agents[agent_id]["status"] = "disconnected"
            self._agents[agent_id]["disconnected_at"] = _utc_now()

    def get_bound_session(self, agent_id: str) -> str | None:
        """返回已绑定的 session_id，未绑定返回 None。"""
        return self._agents.get(agent_id, {}).get("bound_session")

    def bind_session(self, agent_id: str, session_id: str) -> None:
        """
        绑定 agent 到 session。已绑定到不同 session 时抛出 RuntimeError。
        """
        if agent_id not in self._agents:
            return
        existing = self._agents[agent_id].get("bound_session")
        if existing and existing != session_id:
            raise RuntimeError(
                f"Agent 已绑定到会话 {existing[:16]}，不允许切换。"
                f"若需使用其他会话，请重新启动 Agent。"
            )
        self._agents[agent_id]["bound_session"] = session_id

    def set_bound_account(self, agent_id: str, account_name: str) -> None:
        """记录绑定时的 Boss 账号名（用于后台展示）。"""
        if agent_id in self._agents:
            self._agents[agent_id]["bound_account"] = account_name

    def set_user_id(self, agent_id: str, user_id: str) -> None:
        """记录 dinQ 系统用户 ID（由上游 agent-gateway 通过 x-user-id 注入）。"""
        if agent_id in self._agents:
            self._agents[agent_id]["user_id"] = user_id

    def get_user_id(self, agent_id: str) -> str:
        return self._agents.get(agent_id, {}).get("user_id", "")

    def set_user_tier(self, agent_id: str, tier: str) -> None:
        """记录用户订阅等级（由上游 Go gateway 注入）。"""
        if agent_id in self._agents:
            self._agents[agent_id]["user_tier"] = tier

    def get_user_tier(self, agent_id: str) -> str:
        return self._agents.get(agent_id, {}).get("user_tier", "")

    def get_current_agent_id(self, request: Any) -> str:
        """从 Starlette Request 获取 agent_id（mcp-session-id 头）。"""
        return request.headers.get("mcp-session-id", "")

    def list_all(self) -> list[dict]:
        return list(self._agents.values())


# 单例
agent_tracker = AgentTracker()
