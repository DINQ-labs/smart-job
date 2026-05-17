#!/usr/bin/env python3
"""
boss_cli_server.py: 多用户 httpx 虚拟扩展，连接 job-api-gateway WebSocket。

功能：作为长运行进程连接到 job-api-gateway，以与 Chrome 扩展完全相同的协议
接收命令，并用 httpx（BossClient）执行 Boss直聘 API 调用。

设计特点：
- 启动时不绑定任何账号，cookie_id 在每次命令调用时通过 body 动态传入
- 一个进程可同时服务多个账号，内部维护 _clients: dict[str, BossClient] 懒加载池

启动方式:
  # PostgreSQL（默认）
  DB_POSTGRES_URL=postgresql://... python boss_cli_server.py

  # 或通过 CLI 参数覆盖
  python boss_cli_server.py --pg-url postgresql://...
"""
from __future__ import annotations

# ── CLI 参数解析（必须在 import db 之前设置 env vars）──────────────────────────
import argparse
import os
import sys

_parser = argparse.ArgumentParser(
    description="Boss CLI Server (multi-user, cookie_id per request)"
)
_parser.add_argument("--gateway",       default="ws://127.0.0.1:8767/ext/ws",
                     help="job-api-gateway WebSocket 地址")
_parser.add_argument("--ext-name",      default="bosszp-cli",
                     help="扩展名称（用于注册到网关，默认 bosszp-cli）")
_parser.add_argument("--browser-id",    default="",
                     help="稳定的 browser_id（不指定则自动生成）")
_parser.add_argument("--pg-url",        default="",
                     help="覆盖 DB_POSTGRES_URL")
_parser.add_argument("--boss-cli-path", default="",
                     help="boss-cli 包路径（替代 pip install -e）")
_args = _parser.parse_args()

# 在 import db 之前覆盖 env vars，使 db.py 读到正确配置
if _args.pg_url:        os.environ["DB_POSTGRES_URL"]  = _args.pg_url

# boss_cli 包路径：优先用 --boss-cli-path 参数，否则自动推断（相对本文件位置）
_boss_cli_pkg_path = _args.boss_cli_path or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "cli", "boss-cli"
)
# 插到 sys.path 最前面，确保优先于同目录下的 boss_cli.py 文件
sys.path.insert(0, os.path.normpath(_boss_cli_pkg_path))

# ── 依赖（db 必须在 env vars 设置后导入）────────────────────────────────────────
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import websockets

import db  # 复用 gateway 的 db.py（PostgreSQL）

from boss_cli.client import BossClient
from boss_cli.auth import Credential


# ── Cookie 加载（通过 db.get_account_cookies，与 DB 后端无关）──────────────────

async def load_cookies(cookie_id: str) -> dict[str, str]:
    """从 account_cookies 表加载，Chrome cookie array → {name: value}。"""
    rec = await db.get_account_cookies(cookie_id)
    if not rec:
        raise ValueError(f"cookie_id={cookie_id!r} 不存在于 account_cookies 表")
    arr = json.loads(rec["cookies_json"])
    return {c["name"]: c["value"] for c in arr if c.get("value")}


# ── 多用户 Client 池 ─────────────────────────────────────────────────────────

_clients: dict[str, BossClient] = {}
_executor = ThreadPoolExecutor(max_workers=8)


async def get_or_create_client(cookie_id: str) -> BossClient:
    """懒加载：首次调用 cookie_id 时从 DB 加载 cookie 并创建 BossClient。"""
    if cookie_id not in _clients:
        cookies = await load_cookies(cookie_id)
        client = BossClient(credential=Credential(cookies=cookies))
        client.__enter__()  # BossClient 是 sync context manager，__enter__ 创建 httpx.Client
        _clients[cookie_id] = client
        print(f"[boss-cli-server] 已加载 cookie_id={cookie_id!r}（现有 {len(_clients)} 个账号）",
              flush=True)
    return _clients[cookie_id]


# ── async 包装（BossClient 方法全部是同步阻塞）──────────────────────────────────

async def run_sync(fn, *args, **kwargs):
    """在线程池中运行同步函数，避免阻塞事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


# ── 响应封装（与 Chrome 扩展格式完全一致）────────────────────────────────────────

def ext_ok(data, message: str = "") -> dict:
    """构造扩展标准成功响应，供 commands.py _unwrap() 提取 data 字段。"""
    return {"ok": True, "code": 200, "message": message, "data": data}


# ── 命令处理器 ────────────────────────────────────────────────────────────────

async def _require_client(body: dict) -> BossClient:
    """从 body 提取 cookie_id 并返回对应 BossClient（懒加载）。"""
    cookie_id = body.get("cookie_id", "")
    if not cookie_id:
        raise ValueError("cookie_id 必填")
    return await get_or_create_client(cookie_id)


async def handle_check_login(body: dict) -> dict:
    """检查登录状态：调用 get_user_info()，返回 {logged_in, userId, name}。"""
    client = await _require_client(body)
    try:
        info = await run_sync(client.get_user_info)
        return ext_ok({
            "logged_in": True,
            "userId":    info.get("userId", ""),
            "name":      info.get("name", ""),
        })
    except Exception as e:
        return ext_ok({"logged_in": False, "error": str(e)})


async def handle_init_session(body: dict) -> dict:
    """强制重新从 DB 加载 Cookie（删除旧 client + 重建）。"""
    cookie_id = body.get("cookie_id", "")
    if not cookie_id:
        raise ValueError("cookie_id 必填")
    # 移除旧 client（若存在）
    old = _clients.pop(cookie_id, None)
    if old:
        try:
            old.__exit__(None, None, None)
        except Exception:
            pass
    # 重新从 DB 加载
    await get_or_create_client(cookie_id)
    return ext_ok({"reloaded": True, "cookie_id": cookie_id})


async def handle_logout(body: dict) -> dict:
    """从池中移除指定 cookie_id 的 client 并关闭。"""
    cookie_id = body.get("cookie_id", "")
    if not cookie_id:
        raise ValueError("cookie_id 必填")
    client = _clients.pop(cookie_id, None)
    if client:
        try:
            client.__exit__(None, None, None)
        except Exception:
            pass
        print(f"[boss-cli-server] 已移除 cookie_id={cookie_id!r}", flush=True)
    return ext_ok({"logged_out": True, "cookie_id": cookie_id})


async def handle_session_status(body: dict) -> dict:
    """返回当前池中所有已加载的 cookie_id 列表。"""
    return ext_ok({"loaded_cookie_ids": list(_clients.keys())})


async def handle_search_jobs(body: dict) -> dict:
    """搜索职位，返回 {raw: {zpData: ...}} 兼容 cmd_search_jobs 的解析格式。"""
    client = await _require_client(body)
    keyword = body.get("keyword", "")
    city    = str(body.get("city", "101010100"))
    page    = int(body.get("page", 1))
    extra   = body.get("extra") or {}
    # 从 extra 提取可选过滤参数
    kwargs = {
        k: extra[k]
        for k in ("experience", "degree", "salary", "industry", "scale", "stage")
        if extra.get(k)
    }
    zpdata = await run_sync(client.search_jobs, keyword, city, page, **kwargs)
    # 包装成 {raw: {zpData: ...}} 供 _extract_job_list(raw) 正确解析
    return ext_ok({"raw": {"zpData": zpdata}})


async def handle_get_job_detail(body: dict) -> dict:
    """获取职位详情，返回 {raw: {zpData: ...}} 兼容 cmd_get_job_detail 的解析格式。"""
    client      = await _require_client(body)
    security_id = body.get("security_id", "")
    lid         = body.get("lid", "")
    zpdata = await run_sync(client.get_job_detail, security_id, lid)
    return ext_ok({"raw": {"zpData": zpdata}})


async def handle_start_chat(body: dict) -> dict:
    """打招呼（add_friend），返回 {raw: {friend_add: {zpData: ...}}} 兼容格式。"""
    client      = await _require_client(body)
    security_id = body.get("security_id", "")
    lid         = body.get("lid", "")
    zpdata = await run_sync(client.add_friend, security_id, lid)
    return ext_ok({"raw": {"friend_add": {"zpData": zpdata}}})


async def handle_get_recommend_jobs(body: dict) -> dict:
    client = await _require_client(body)
    page   = int(body.get("page", 1))
    zpdata = await run_sync(client.get_recommend_jobs, page)
    return ext_ok(zpdata)


async def handle_get_job_card(body: dict) -> dict:
    client      = await _require_client(body)
    security_id = body.get("security_id", "")
    lid         = body.get("lid", "")
    zpdata = await run_sync(client.get_job_card, security_id, lid)
    return ext_ok(zpdata)


async def handle_get_job_history(body: dict) -> dict:
    client = await _require_client(body)
    page   = int(body.get("page", 1))
    zpdata = await run_sync(client.get_job_history, page)
    return ext_ok(zpdata)


async def handle_get_resume_baseinfo(body: dict) -> dict:
    client = await _require_client(body)
    zpdata = await run_sync(client.get_resume_baseinfo)
    return ext_ok(zpdata)


async def handle_get_resume_expect(body: dict) -> dict:
    client = await _require_client(body)
    zpdata = await run_sync(client.get_resume_expect)
    return ext_ok(zpdata)


async def handle_get_resume_status(body: dict) -> dict:
    client = await _require_client(body)
    zpdata = await run_sync(client.get_resume_status)
    return ext_ok(zpdata)


async def handle_get_deliver_list(body: dict) -> dict:
    client = await _require_client(body)
    page   = int(body.get("page", 1))
    zpdata = await run_sync(client.get_deliver_list, page)
    return ext_ok(zpdata)


async def handle_get_interview_data(body: dict) -> dict:
    client = await _require_client(body)
    zpdata = await run_sync(client.get_interview_data)
    return ext_ok(zpdata)


async def handle_get_friend_list(body: dict) -> dict:
    client = await _require_client(body)
    zpdata = await run_sync(client.get_friend_list)
    return ext_ok(zpdata)


async def handle_get_geek_job(body: dict) -> dict:
    client      = await _require_client(body)
    security_id = body.get("security_id", "")
    zpdata = await run_sync(client.get_geek_job, security_id)
    return ext_ok(zpdata)


# ── 命令路由表 ───────────────────────────────────────────────────────────────

HANDLERS: dict[str, Any] = {
    "boss/check_login":         handle_check_login,
    "boss/init_session":        handle_init_session,
    "boss/logout":              handle_logout,
    "boss/get_session_status":  handle_session_status,
    "boss/search_jobs":         handle_search_jobs,
    "boss/get_job_detail":      handle_get_job_detail,
    "boss/start_chat":          handle_start_chat,
    "boss/get_recommend_jobs":  handle_get_recommend_jobs,
    "boss/get_job_card":        handle_get_job_card,
    "boss/get_job_history":     handle_get_job_history,
    "boss/get_resume_baseinfo": handle_get_resume_baseinfo,
    "boss/get_resume_expect":   handle_get_resume_expect,
    "boss/get_resume_status":   handle_get_resume_status,
    "boss/get_deliver_list":    handle_get_deliver_list,
    "boss/get_interview_data":  handle_get_interview_data,
    "boss/get_friend_list":     handle_get_friend_list,
    "boss/get_geek_job":        handle_get_geek_job,
}


# ── 消息分发 ─────────────────────────────────────────────────────────────────

async def dispatch(ws, msg_str: str) -> None:
    """解析命令消息，调用对应 handler，将结果发回网关。"""
    try:
        msg = json.loads(msg_str)
    except json.JSONDecodeError:
        return

    req_id = msg.get("id", "")
    path   = msg.get("path", "")
    body   = msg.get("body") or {}

    handler = HANDLERS.get(path)
    if not handler:
        await ws.send(json.dumps({
            "id":    req_id,
            "ok":    False,
            "error": f"unknown path: {path}",
        }))
        return

    try:
        result = await handler(body)
        await ws.send(json.dumps({"id": req_id, "ok": True, "result": result}))
    except Exception as e:
        await ws.send(json.dumps({"id": req_id, "ok": False, "error": str(e)}))


# ── 心跳 ────────────────────────────────────────────────────────────────────

async def heartbeat_loop(ws) -> None:
    """每 15 秒发送 WebSocket 协议级 ping，保持连接活跃。"""
    while True:
        await asyncio.sleep(15)
        try:
            await ws.ping()
        except Exception:
            break


# ── 消息接收循环 ─────────────────────────────────────────────────────────────

async def _message_loop(ws) -> None:
    """持续接收网关消息，将命令消息分发给 dispatch()。"""
    async for msg in ws:
        if isinstance(msg, str) and '"path"' in msg:
            asyncio.create_task(dispatch(ws, msg))


# ── 主运行逻辑 ───────────────────────────────────────────────────────────────

async def run() -> None:
    await db.init_db()
    print("[boss-cli-server] DB 初始化完成", flush=True)

    browser_id = _args.browser_id or f"bosszp-cli-{uuid.uuid4().hex[:8]}"
    url = f"{_args.gateway}?name={_args.ext_name}&bid={browser_id}"
    print(f"[boss-cli-server] 连接到网关: {url}", flush=True)

    async with websockets.connect(url) as ws:
        # 接收注册确认消息
        raw_reg = await ws.recv()
        reg = json.loads(raw_reg)
        session_id = reg.get("sessionId", reg.get("session_id", ""))
        display_id = reg.get("displayId", "")
        ext_name   = reg.get("extName", _args.ext_name)
        print(
            f"[boss-cli-server] 已注册: session_id={session_id[:16] if session_id else '?'}"
            f" display_id={display_id} ext_name={ext_name}",
            flush=True,
        )
        print(f"[boss-cli-server] 就绪，等待命令（支持 {len(HANDLERS)} 条路径）", flush=True)

        await asyncio.gather(
            _message_loop(ws),
            heartbeat_loop(ws),
        )


# ── 入口 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[boss-cli-server] 已停止", flush=True)
    finally:
        # 清理所有 httpx client
        for cid, client in list(_clients.items()):
            try:
                client.__exit__(None, None, None)
            except Exception:
                pass
        print(f"[boss-cli-server] 已关闭 {len(_clients)} 个 httpx 客户端", flush=True)
