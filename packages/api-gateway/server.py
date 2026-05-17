#!/usr/bin/env python3
"""
job-api-gateway: FastMCP + Starlette HTTP/WS 入口（多会话版）

启动方式:
  python server.py --http        # HTTP + WebSocket 模式（推荐）
  python server.py               # stdio 模式（Cursor 默认）

端口: 8767
  - MCP 端点:    http://127.0.0.1:8767/mcp
  - 扩展 WS:     ws://127.0.0.1:8767/ext/ws
  - Admin REST:  http://127.0.0.1:8767/admin/...
  - Admin WS:    ws://127.0.0.1:8767/admin/ws

模块结构:
  server.py              ← 本文件：应用组装 + 启动
  server_helpers.py      ← 共享工具函数 (_ok, _err, _resolve_and_bind 等)
  mcp_tools_boss.py      ← Boss 直聘 MCP tools (57)
  mcp_tools_linkedin.py  ← LinkedIn MCP tools (11)
  mcp_tools_indeed.py    ← Indeed MCP tools (31: 求职 7 + 雇主 24)
  http_routes.py         ← HTTP/WebSocket 路由处理器 (~72 条)
"""
from __future__ import annotations

from dotenv import load_dotenv  # type: ignore
load_dotenv()

import asyncio
import logging
import logging.handlers
import os
import sys

# audit P2 fix:把 repo-root 加到 sys.path,让 `from job_common.* import ...`
# 在 server 启动时一次到位 —— commands.py 等模块直接 import,不再各自 muck path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastmcp import FastMCP

import db
from session_store import session_store
from browser_pool import browser_pool
from proxy_pool import proxy_pool
from server_helpers import ADMIN_PASSWORD

# ── 日志 ────────────────────────────────────────────────────────────────────

_LOG_DIR   = os.environ.get("API_LOG_DIR", "logs")
_LOG_LEVEL = os.environ.get("API_LOG_LEVEL", "INFO")

def _setup_logging():
    os.makedirs(_LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    level = getattr(logging, _LOG_LEVEL.upper(), logging.INFO)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(_LOG_DIR, "api-gateway.log"),
        when="midnight", backupCount=30, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

_setup_logging()
log = logging.getLogger(__name__)

GATEWAY_PORT = int(os.environ.get("BOSS_GATEWAY_PORT", "8767"))

# ── FastMCP 实例 + MCP Tool 注册 ────────────────────────────────────────────

mcp = FastMCP(name="job-api-gateway")

import mcp_tools_boss      # noqa: E402
import mcp_tools_linkedin   # noqa: E402
import mcp_tools_indeed     # noqa: E402

mcp_tools_boss.register(mcp)
mcp_tools_linkedin.register(mcp)
mcp_tools_indeed.register(mcp)

# ── HTTP 路由 + 应用组装 ────────────────────────────────────────────────────

import http_routes  # noqa: E402


def _create_app():
    app = mcp.http_app(transport="streamable-http", path="/mcp")
    http_routes.register_routes(app)
    # 包裹 Agent 追踪中间件（必须在 add_route 之后，否则路由丢失）
    return http_routes._AgentTrackingMiddleware(app)


# ── 启动 / 停止 ─────────────────────────────────────────────────────────────

async def _startup():
    await db.init_db()
    print("[job-api-gateway] DB 初始化完成", flush=True)
    # 清理上次进程残留的 connected 状态
    from datetime import datetime, timezone
    stale = await db.list_sessions(active_only=True)
    if stale:
        now = datetime.now(timezone.utc).isoformat()
        for s in stale:
            await db.upsert_session(s["session_id"], status="disconnected", disconnected_at=now)
        print(f"[job-api-gateway] 清理 {len(stale)} 个残留 connected 会话", flush=True)
    await db.seed_command_registry(http_routes._SEED_COMMANDS)
    print(f"[job-api-gateway] 命令注册表已 seed（{len(http_routes._SEED_COMMANDS)} 条）", flush=True)
    # 加载当前生产的动态命令 path 集合，给 command_log.is_dynamic 打标用
    try:
        import dynamic_command_state
        n = await dynamic_command_state.hydrate_from_db()
        print(f"[job-api-gateway] 动态命令 path 集合加载（{n} 个）", flush=True)
    except Exception as e:
        print(f"[job-api-gateway] 动态命令 path 集合加载失败（不阻断启动）: {e}", flush=True)
    # Phase 2: 把 DB 里最新一版 dynamic_commands 注册成 MCP tool（agent 可见）
    try:
        import dynamic_mcp_registry
        dynamic_mcp_registry.set_mcp(mcp)
        result = await dynamic_mcp_registry.apply_latest_from_db()
        print(f"[job-api-gateway] 动态 MCP tool 注册：{len(result['added'])} 个"
              f"（失败 {len(result['failed'])}，跳过 {len(result['skipped'])}）", flush=True)
    except Exception as e:
        print(f"[job-api-gateway] 动态 MCP tool 注册失败（不阻断启动）: {e}", flush=True)
    # 从 DB 恢复最大 display_id
    max_id = await db.get_max_display_id()
    session_store.set_counter(max_id)
    if max_id:
        print(f"[job-api-gateway] display_id 续号起点: {max_id}", flush=True)
    if ADMIN_PASSWORD:
        print("[job-api-gateway] Admin 认证已启用（ADMIN_PASSWORD 已设置）", flush=True)
    else:
        print("[job-api-gateway] ⚠️  Admin 认证已禁用（未设置 ADMIN_PASSWORD）", flush=True)
    # 代理池持久化
    for entry in proxy_pool.list_all():
        await db.add_proxy_pool_entry(entry["url"])
    saved_proxies = await db.list_proxy_pool()
    loaded = 0
    for entry in saved_proxies:
        if proxy_pool.add(entry["url"]):
            loaded += 1
    if loaded:
        print(f"[job-api-gateway] 从 DB 恢复代理池：{loaded} 个代理", flush=True)
    # 初始化 BrowserPool
    await browser_pool.load_config()
    offline_slots = await db.list_browser_slots(state="offline")
    for slot in offline_slots:
        await db.delete_browser_slot(slot["browser_id"])
    if offline_slots:
        print(f"[job-api-gateway] 清理 {len(offline_slots)} 个残留 offline 槽位", flush=True)
    await browser_pool.restore_assignments()
    asyncio.create_task(browser_pool._start_timeout_watcher())
    print("[job-api-gateway] BrowserPool 已初始化", flush=True)
    print("[job-api-gateway] PlaywrightPool 就绪（懒加载）", flush=True)


async def _shutdown():
    print("[job-api-gateway] shutting down", flush=True)


if __name__ == "__main__":
    use_http = "--stdio" not in sys.argv
    if use_http:
        print(f"[job-api-gateway] 启动 HTTP + WebSocket 模式（多会话版）", flush=True)
        print(f"  MCP 端点:    http://0.0.0.0:{GATEWAY_PORT}/mcp", flush=True)
        print(f"  扩展 WS:     ws://0.0.0.0:{GATEWAY_PORT}/ext/ws", flush=True)
        print(f"  Admin REST:  http://0.0.0.0:{GATEWAY_PORT}/admin/sessions", flush=True)
        print(f"  Admin WS:    ws://0.0.0.0:{GATEWAY_PORT}/admin/ws", flush=True)
        app = _create_app()
        import uvicorn

        async def _run():
            await _startup()
            config = uvicorn.Config(app, host="0.0.0.0", port=GATEWAY_PORT, timeout_graceful_shutdown=3)
            server = uvicorn.Server(config)
            try:
                await server.serve()
            finally:
                await _shutdown()

        asyncio.run(_run())
    else:
        print(f"[job-api-gateway] stdio 模式（扩展无法连接）", flush=True)
        print(f"  建议改用: python server.py --http", flush=True)
        mcp.run(transport="stdio")
