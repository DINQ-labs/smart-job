"""
MCP Server 连接管理器。

- job-api-gateway (HTTP)：每个 agent turn 临时连接（HTTP 无状态，保持原逻辑）
- mcp_servers.json 里的 stdio 服务（如 playwright）：启动时连接，全局复用，自动重连

用法：
    await mcp_manager.start()   # server 启动时调用
    await mcp_manager.stop()    # server 关闭时调用
    session, lock = mcp_manager.get(tool_name)  # agent_loop 调用
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

log = logging.getLogger(__name__)

_MCP_SERVERS_FILE = os.path.join(os.path.dirname(__file__), "mcp_servers.json")

# 重连参数
_RECONNECT_BASE = 2.0    # 首次等待 2 秒
_RECONNECT_MAX  = 60.0   # 最长等待 60 秒


def _load_extra_servers() -> dict[str, dict]:
    if not os.path.exists(_MCP_SERVERS_FILE):
        return {}
    try:
        with open(_MCP_SERVERS_FILE, encoding="utf-8") as f:
            return json.load(f).get("mcpServers", {})
    except Exception as e:
        log.warning("读取 mcp_servers.json 失败: %s", e)
        return {}


@dataclass
class _ServerEntry:
    name: str
    session: Optional[ClientSession] = None
    tools: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    reconnect_attempt: int = 0   # 当前连续失败次数，成功后归零


class McpManager:
    def __init__(self):
        self._entries: dict[str, _ServerEntry] = {}   # server_name → entry
        self._tool_router: dict[str, str] = {}         # tool_name → server_name
        self._tasks: list[asyncio.Task] = []
        self._stop: asyncio.Event = asyncio.Event()

    # ── 启动 / 停止 ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._stop.clear()
        servers = _load_extra_servers()
        if not servers:
            log.info("mcp_servers.json 未配置额外 MCP Server")
            return

        for name, cfg in servers.items():
            entry = _ServerEntry(name=name)
            self._entries[name] = entry
            task = asyncio.create_task(
                self._run_stdio(name, cfg, entry),
                name=f"mcp-{name}",
            )
            self._tasks.append(task)

        # 等待所有 server 完成首次连接尝试（成功或失败都会 set ready，最多 15 秒）
        if self._entries:
            await asyncio.gather(*(
                asyncio.wait_for(e.ready.wait(), timeout=15)
                for e in self._entries.values()
            ), return_exceptions=True)

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._entries.clear()
        self._tool_router.clear()

    # ── 查询 ─────────────────────────────────────────────────────────────────

    def get(self, tool_name: str) -> tuple[Optional[ClientSession], Optional[asyncio.Lock]]:
        """返回 (session, lock)，tool 不在额外 server 中或正在重连则返回 (None, None)。"""
        server_name = self._tool_router.get(tool_name)
        if not server_name:
            return None, None
        entry = self._entries.get(server_name)
        if not entry or not entry.session:
            return None, None
        return entry.session, entry.lock

    def extra_tools(self) -> list[str]:
        """返回所有已连接的额外 server 工具名列表。"""
        return list(self._tool_router.keys())

    def status(self) -> list[dict]:
        return [
            {
                "name": e.name,
                "connected": e.session is not None,
                "tools": e.tools,
                "reconnect_attempt": e.reconnect_attempt,
            }
            for e in self._entries.values()
        ]

    # ── 内部：保持 stdio server 进程存活，自动重连 ───────────────────────────

    async def _run_stdio(self, name: str, cfg: dict, entry: _ServerEntry) -> None:
        """
        连接 stdio MCP Server 并保持存活。
        断线后以指数退避（2s → 4s → 8s … 最大 60s）自动重连，
        直到 stop() 被调用。
        """
        while not self._stop.is_set():
            try:
                env = {**os.environ, **(cfg.get("env") or {})}
                params = StdioServerParameters(
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env=env,
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools_resp = await session.list_tools()
                        tool_names = [t.name for t in tools_resp.tools]

                        entry.session = session
                        entry.tools = tool_names
                        entry.reconnect_attempt = 0   # 成功后归零
                        entry.ready.set()             # 解除 start() 等待（幂等）
                        for t in tool_names:
                            self._tool_router[t] = name

                        log.info(
                            "MCP Server [%s] 已连接，工具数: %d\n  %s",
                            name, len(tool_names),
                            "\n  ".join(tool_names),
                        )

                        # 保持连接，直到进程异常退出或 stop() 被调用
                        await self._stop.wait()

            except asyncio.CancelledError:
                entry.ready.set()  # 确保 start() 不卡死
                return

            except Exception as e:
                log.error("MCP Server [%s] 连接断开: %s", name, e)
                entry.ready.set()  # 首次失败时解除 start() 等待

            finally:
                # 无论正常退出还是异常，都清理路由表和 session
                entry.session = None
                for t in list(entry.tools):
                    self._tool_router.pop(t, None)
                entry.tools = []

            if self._stop.is_set():
                break

            # 指数退避：等待 delay 秒后重连，期间若 stop() 被调用则立即退出
            delay = min(_RECONNECT_BASE * (2 ** entry.reconnect_attempt), _RECONNECT_MAX)
            entry.reconnect_attempt += 1
            log.info(
                "MCP Server [%s] 将在 %.0f 秒后重连（第 %d 次）...",
                name, delay, entry.reconnect_attempt,
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass  # 延迟结束，继续重连循环
            except asyncio.CancelledError:
                return

        log.info("MCP Server [%s] 已停止", name)


# 全局单例
mcp_manager = McpManager()
