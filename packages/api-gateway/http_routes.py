"""
http_routes.py — HTTP/WebSocket 路由处理器。

包含 Admin REST、OAuth、QR 登录、CLI、Boss/LinkedIn/Indeed HTTP API、
BrowserPool、Cookie 管理、Agent Gateway 代理等所有 HTTP/WS 端点。
"""
from __future__ import annotations

import asyncio
import httpx
import json
import logging
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone

import psutil
_PROCESS = psutil.Process()
_START_TIME = time.time()

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.websockets import WebSocket

import db
import oauth_linkedin
from ext_client import (
    list_extensions,
    register_extension,
    unregister_extension,
    send_command_to,
)
from commands import (
    cmd_capture_qr,
    cmd_check_login,
    cmd_geek_mark_job_interest,
    cmd_get_job_detail,
    cmd_get_quota_status,
    cmd_list_agents,
    cmd_list_sessions,
    cmd_login,
    cmd_logout,
    cmd_search_jobs,
    cmd_set_proxy,
    cmd_start_chat,
)
import linkedin_commands as li_cmd
import indeed_commands as in_cmd
from mcp_tools_linkedin import _default_li_session
from mcp_tools_indeed import _default_indeed_session
from session_store import session_store
from agent_tracker import agent_tracker
from quota_tracker import quota_tracker
import admin_broadcaster as ab
from browser_pool import browser_pool
from proxy_pool import proxy_pool
from server_helpers import (
    _ok, _err, _ext_connected, _auth_required, _require_internal, _http_resolve_session,
    _current_user_id, _current_role, ADMIN_PASSWORD, COOKIE_NAME, _admin_tokens, EXT_TOKEN,
)

log = logging.getLogger(__name__)

GATEWAY_PORT = int(os.environ.get("BOSS_GATEWAY_PORT", "8767"))
GATEWAY_PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", f"http://127.0.0.1:{GATEWAY_PORT}")
AGENT_GATEWAY_URL = os.environ.get("AGENT_GATEWAY_URL", "http://127.0.0.1:8769")


def _qr_file_path(session_id: str = "") -> str:
    name = f"boss_login_qr_{session_id}.png" if session_id else "boss_login_qr.png"
    return os.path.join(tempfile.gettempdir(), name)



# ── HTTP 路由 ────────────────────────────────────────────────────────────────


import oauth_linkedin  # noqa: E402


async def oauth_linkedin_authorize(request: Request) -> Response:
    """Step 1: 重定向用户到 LinkedIn OAuth2 授权页面。
    查询参数: app_user_id (可选), state (可选)"""
    state = request.query_params.get("state", "") or secrets.token_urlsafe(16)
    url = oauth_linkedin.build_authorization_url(state=state)
    from starlette.responses import RedirectResponse
    return RedirectResponse(url)


async def oauth_linkedin_callback(request: Request) -> JSONResponse:
    """Step 2: LinkedIn OAuth2 回调 — 换取 token，创建 Playwright 会话。"""
    code          = request.query_params.get("code", "")
    app_user_id   = request.query_params.get("state", "")  # state 复用传 app_user_id
    error         = request.query_params.get("error", "")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    if not code:
        return JSONResponse({"ok": False, "error": "缺少 code 参数"}, status_code=400)
    try:
        token_data   = await oauth_linkedin.exchange_code_for_token(code)
        access_token = token_data.get("access_token", "")
        profile      = await oauth_linkedin.fetch_profile(access_token)
        cookie_id    = await oauth_linkedin.store_oauth_token(app_user_id, token_data, profile)

        return JSONResponse({
            "ok": True,
            "cookie_id": cookie_id,
            "account_name": f"{profile.get('localizedFirstName','')} {profile.get('localizedLastName','')}".strip(),
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def http_status(request: Request) -> JSONResponse:
    """网关状态查询接口（非 MCP，供调试）。"""
    extensions = list_extensions()
    try:
        stats = await db.get_stats()
    except Exception:
        stats = {}
    return JSONResponse({
        "ok": True,
        "gateway": "job-api-gateway",
        "port": GATEWAY_PORT,
        "extensions_connected": len([e for e in extensions if e.get("status") == "connected"]),
        "extensions": extensions,
        "stats": stats,
        "login_page": f"{GATEWAY_PUBLIC_URL}/login",
    })


def _login_page_html(session_id: str = "") -> str:
    """生成登录页 HTML，session_id 不为空时使用会话隔离的 /qr 和 /refresh-qr 路径。"""
    sfx = f"/{session_id}" if session_id else ""
    qr_url = f"/qr{sfx}"
    refresh_url = f"/refresh-qr{sfx}"
    qr_exists = os.path.exists(_qr_file_path(session_id))
    ts = int(time.time())
    sid_hint = f"（会话: {session_id[:8]}…）" if session_id else ""
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>Boss直聘 扫码登录</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #f0f9ff; display: flex; justify-content: center;
           align-items: center; min-height: 100vh; }}
    .card {{ background: white; border-radius: 16px; padding: 32px 40px;
             box-shadow: 0 4px 24px rgba(0,0,0,.10); text-align: center; width: 340px; }}
    h2 {{ color: #0077b6; font-size: 20px; margin-bottom: 6px; }}
    .subtitle {{ color: #666; font-size: 13px; margin-bottom: 24px; }}
    .qr-wrap {{ background: #fafafa; border: 1px solid #e5e7eb; border-radius: 12px;
                padding: 12px; margin: 0 auto 20px; width: 240px; height: 240px;
                display: flex; align-items: center; justify-content: center; }}
    .qr-wrap img {{ width: 216px; height: 216px; display: block; }}
    .qr-placeholder {{ color: #aaa; font-size: 13px; }}
    .status {{ font-size: 13px; color: #555; margin-bottom: 16px; min-height: 20px; }}
    .status.ok {{ color: #16a34a; }}
    .status.warn {{ color: #d97706; }}
    .status.err {{ color: #dc2626; }}
    .btn {{ background: #0077b6; color: white; border: none; border-radius: 8px;
            padding: 10px 24px; font-size: 14px; cursor: pointer; width: 100%; }}
    .btn:hover {{ background: #005f92; }}
    .hint {{ color: #999; font-size: 12px; margin-top: 14px; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Boss直聘 APP 扫码登录</h2>
    <p class="subtitle">使用 Boss直聘 App → 右上角扫一扫 {sid_hint}</p>
    <div class="qr-wrap">
      {'<img id="qr" src="' + qr_url + '?t=' + str(ts) + '" alt="二维码">' if qr_exists else '<div class="qr-placeholder">二维码未生成<br>请先执行 boss_login<br>再执行 boss_capture_qr</div>'}
    </div>
    <div class="status" id="status">检测中...</div>
    <button class="btn" onclick="refreshQR()">刷新二维码</button>
    <p class="hint">二维码每 3 秒自动检测是否需要刷新</p>
  </div>
  <script>
    const QR_URL = '{qr_url}';
    const REFRESH_URL = '{refresh_url}';

    async function pollStatus() {{
      try {{
        const r = await fetch('/status');
        const d = await r.json();
        const st = document.getElementById('status');
        const active = d.extensions_connected || 0;
        if (active === 0) {{
          st.className = 'status err';
          st.textContent = '⚠️ 扩展未连接，请检查 job-api-ext 扩展';
          return;
        }}
        st.className = 'status ok';
        st.textContent = '✅ 扩展已连接（' + active + '个会话），等待扫码...';
      }} catch(e) {{
        document.getElementById('status').textContent = '连接网关失败';
      }}
    }}

    async function refreshQR() {{
      const btn = document.querySelector('.btn');
      const st = document.getElementById('status');
      btn.disabled = true; btn.textContent = '截图中...';
      st.className = 'status'; st.textContent = '正在调用扩展截取二维码...';
      try {{
        const r = await fetch(REFRESH_URL, {{ method: 'POST' }});
        const d = await r.json();
        if (d.ok) {{
          document.getElementById('qr').src = QR_URL + '?t=' + Date.now();
          st.className = 'status ok'; st.textContent = '✅ 二维码已刷新，请扫码';
        }} else {{
          st.className = 'status warn'; st.textContent = '⚠️ ' + (d.error || '刷新失败');
        }}
      }} catch(e) {{
        st.className = 'status err'; st.textContent = '请求失败: ' + e.message;
      }} finally {{
        btn.disabled = false; btn.textContent = '刷新二维码';
      }}
    }}

    setInterval(pollStatus, 3000);
    pollStatus();
  </script>
</body>
</html>"""


async def http_login_qr(request: Request) -> JSONResponse:
    """通过 pool 为指定用户分配扩展并调用 boss/login_with_qr 获取二维码。
    Body: { app_user_id: str }
    Response: { ok, qr_base64, session_id } 或 { ok: false, busy: true } 若扩展全忙。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    app_user_id = (body.get("app_user_id") or "").strip()
    if not app_user_id:
        return JSONResponse({"ok": False, "error": "app_user_id 必填"}, status_code=400)
    if not _ext_connected():
        return JSONResponse({"ok": False, "error": "扩展未连接，请检查 job-api-ext 扩展"}, status_code=503)

    # 从 pool 获取（或复用已分配）session，最多等 5 秒
    acquire_result = await browser_pool.acquire(app_user_id, "bosszp", timeout_sec=5)
    if not acquire_result.get("ok"):
        print(f"[login-qr] 扩展全忙 user={app_user_id}: {acquire_result.get('error')}", flush=True)
        return JSONResponse({"ok": False, "busy": True, "error": acquire_result.get("error", "所有服务器都被占用，请稍后重试")}, status_code=503)

    session_id = acquire_result.get("session_id", "")
    if not session_id:
        return JSONResponse({"ok": False, "error": "获取 session 失败"}, status_code=500)

    print(f"[login-qr] user={app_user_id} session={session_id[:16]} reused={acquire_result.get('reused', False)}", flush=True)
    try:
        result = await cmd_login(session_id=session_id)
        print(f"[login-qr] cmd_login 返回: ok={result.get('ok') if isinstance(result, dict) else result}", flush=True)
        qr_path = result.get("qr_image_path") if isinstance(result, dict) else None
        if not qr_path or not os.path.exists(qr_path):
            return JSONResponse({"ok": False, "error": "二维码获取失败，请确认登录页已打开"}, status_code=500)
        import base64 as _b64
        with open(qr_path, "rb") as f:
            qr_bytes = f.read()
        qr_base64 = "data:image/png;base64," + _b64.b64encode(qr_bytes).decode()
        print(f"[login-qr] 成功 file_size={len(qr_bytes)}bytes session={session_id[:16]}", flush=True)
        return JSONResponse({"ok": True, "qr_base64": qr_base64, "session_id": session_id})
    except Exception as e:
        print(f"[login-qr] 异常: {e}", flush=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def http_refresh_qr(request: Request) -> JSONResponse:
    """触发扩展重新截取 QR 码并保存（供 /login 页面按钮调用）。"""
    if not _ext_connected():
        return JSONResponse({"ok": False, "error": "扩展未连接，请检查 job-api-ext 扩展"}, status_code=503)
    try:
        path = await cmd_capture_qr()
        if path:
            return JSONResponse({"ok": True, "path": path})
        return JSONResponse({"ok": False, "error": "截图失败，请在浏览器中直接扫码"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def http_refresh_qr_session(request: Request) -> JSONResponse:
    """触发指定会话重新截取 QR 码（会话隔离版）。"""
    session_id = request.path_params.get("session_id", "")
    if not _ext_connected():
        return JSONResponse({"ok": False, "error": "扩展未连接，请检查 job-api-ext 扩展"}, status_code=503)
    try:
        path = await cmd_capture_qr(session_id or None)
        if path:
            return JSONResponse({"ok": True, "path": path})
        return JSONResponse({"ok": False, "error": "截图失败，请在浏览器中直接扫码"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def http_qr_image(request: Request) -> Response:
    """返回登录二维码 PNG 图片（用于 /login 页面嵌入）。"""
    path = _qr_file_path()
    if not os.path.exists(path):
        return Response(
            content=b"QR code not generated yet. Call boss_login then boss_capture_qr first.",
            status_code=404,
            media_type="text/plain",
        )
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


async def http_qr_image_session(request: Request) -> Response:
    """返回指定会话的登录二维码 PNG（会话隔离版）。"""
    session_id = request.path_params.get("session_id", "")
    path = _qr_file_path(session_id)
    if not os.path.exists(path):
        # 尝试回退到无 session 的通用文件
        fallback = _qr_file_path()
        if os.path.exists(fallback):
            path = fallback
        else:
            return Response(
                content=b"QR code not generated yet for this session.",
                status_code=404,
                media_type="text/plain",
            )
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


async def http_login_page(request: Request) -> HTMLResponse:
    """扫码登录页面（无会话隔离，兼容旧链接）。"""
    return HTMLResponse(content=_login_page_html())


async def http_login_page_session(request: Request) -> HTMLResponse:
    """会话隔离扫码登录页面。"""
    session_id = request.path_params.get("session_id", "")
    return HTMLResponse(content=_login_page_html(session_id))


# ── Admin REST API ────────────────────────────────────────────────────────────


async def admin_get_sessions(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    try:
        sessions = await db.list_sessions()
        # 合并内存中的实时状态
        mem_sessions = {s["session_id"]: s for s in session_store.list_all()}
        for s in sessions:
            if s["session_id"] in mem_sessions:
                m = mem_sessions[s["session_id"]]
                # 运行时状态覆盖 DB 快照（job_store_count 仅内存可知）
                s["status"] = m["status"]
                s["job_store_count"] = m.get("job_store_count", 0)
                # 内存中的实时值优先（DB 可能有延迟）
                if m.get("ip_address"):
                    s["ip_address"] = m["ip_address"]
                if m.get("display_id"):
                    s["display_id"] = m["display_id"]
                if m.get("account_name"):
                    s["account_name"] = m["account_name"]
                if m.get("user_id"):
                    s["user_id"] = m["user_id"]
                if m.get("app_user_id"):
                    s["app_user_id"] = m["app_user_id"]
            else:
                # 不在内存中的会话一定已断开（本次进程未建立该连接）
                s["status"] = "disconnected"
        return JSONResponse({"sessions": sessions})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_get_session(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    sid = request.path_params.get("id", "")
    try:
        sessions = await db.list_sessions()
        for s in sessions:
            if s["session_id"] == sid:
                entry = session_store.get(sid)
                if entry:
                    s["status"] = "connected"
                    s["job_store_count"] = len(entry.job_store.list_all())
                return JSONResponse(s)
        return JSONResponse({"error": "session not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_logout_session(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    sid = request.path_params.get("id", "")
    try:
        result = await cmd_logout(sid or None)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_disconnect_session(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    sid = request.path_params.get("id", "")
    try:
        _entry = session_store.get(sid)
        _uid = _entry.user_id if _entry else ""
        await session_store.force_disconnect(sid)
        await db.upsert_session(sid, status="disconnected")
        await db.log_session_event(sid, None, "disconnected", "force_disconnect via admin")
        await ab.admin_broadcaster.broadcast({
            "event": "session_disconnected",
            "session_id": sid,
            "disconnected_at": datetime.now(timezone.utc).isoformat(),
            "user_id": _uid,
        })
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_custom_command(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    sid = request.path_params.get("id", "")
    try:
        body = await request.json()
        method = body.get("method", "GET")
        path = body.get("path", "")
        cmd_body = body.get("body")
        result = await send_command_to(sid or None, method, path, cmd_body, timeout_ms=30000)
        # 根据命令路径自动同步会话账号信息
        _entry = session_store.get(sid) if sid else None
        if _entry:
            data = result.get("data") if (isinstance(result, dict) and result.get("ok") is True and "data" in result) else result
            if path == "boss/check_login" and isinstance(data, dict) and data.get("logged_in"):
                _entry.account_name = data.get("name", "")
                _entry.app_user_id = data.get("userId", "")
                try:
                    await db.upsert_session(_entry.session_id,
                                            account_name=_entry.account_name,
                                            app_user_id=_entry.app_user_id)
                    await ab.admin_broadcaster.broadcast({
                        "event": "session_login",
                        "session_id": _entry.session_id,
                        "account_name": _entry.account_name,
                        "user_id": _entry.user_id,
                        "app_user_id": _entry.app_user_id,
                    })
                except Exception as _e:
                    log.debug("silently swallowed: %s", _e)
                    pass
            elif path == "boss/logout":
                _entry.account_name = ""
                _entry.app_user_id = ""
                try:
                    await db.upsert_session(_entry.session_id, account_name="", app_user_id="")
                except Exception as _e:
                    log.debug("silently swallowed: %s", _e)
                    pass
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_get_agents(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    try:
        agents = await db.list_agent_sessions()
        mem_agents = {a["agent_id"]: a for a in agent_tracker.list_all()}
        for a in agents:
            if a["agent_id"] in mem_agents:
                m = mem_agents[a["agent_id"]]
                a["status"] = m["status"]
                a["request_count"] = m.get("request_count", 0)
                a["bound_session"] = m.get("bound_session")
                if m.get("user_id"):
                    a["user_id"] = m["user_id"]
            else:
                # 不在内存中的 agent 一定已断开（本次进程未建立该连接）
                a["status"] = "disconnected"
        return JSONResponse({"agents": agents})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _parse_response_ok(value: str | None) -> int | None:
    """0/1 字面量；其它（空、null、all）返回 None 表示不过滤。"""
    if value in (None, "", "null", "all", "any"):
        return None
    if value in ("0", "false", "False"):
        return 0
    if value in ("1", "true", "True"):
        return 1
    return None


async def admin_get_commands(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    sid = request.query_params.get("session_id") or None
    aid = request.query_params.get("agent_id") or None
    tool_name = request.query_params.get("tool_name") or ""
    since_iso = request.query_params.get("since_iso") or ""
    keyword = request.query_params.get("keyword") or ""
    response_ok = _parse_response_ok(request.query_params.get("response_ok"))
    try:
        limit = min(max(int(request.query_params.get("limit", "100")), 1), 500)
        offset = max(int(request.query_params.get("offset", "0")), 0)
    except ValueError:
        return JSONResponse({"error": "limit 和 offset 必须为整数"}, status_code=400)
    try:
        commands = await db.list_commands(
            sid, aid, limit, offset,
            response_ok=response_ok, tool_name=tool_name,
            since_iso=since_iso, keyword=keyword,
        )
        return JSONResponse({"commands": commands, "limit": limit, "offset": offset})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_get_errors(request: Request) -> JSONResponse:
    """GET /admin/errors —— 等价于 /admin/commands?response_ok=0，多带 tool_names 用于前端下拉。

    query: since_iso, tool_name, agent_id, session_id, keyword, limit, offset
    """
    if (err := _auth_required(request)): return err
    sid = request.query_params.get("session_id") or None
    aid = request.query_params.get("agent_id") or None
    tool_name = request.query_params.get("tool_name") or ""
    since_iso = request.query_params.get("since_iso") or ""
    keyword = request.query_params.get("keyword") or ""
    try:
        limit = min(max(int(request.query_params.get("limit", "100")), 1), 500)
        offset = max(int(request.query_params.get("offset", "0")), 0)
    except ValueError:
        return JSONResponse({"error": "limit 和 offset 必须为整数"}, status_code=400)
    try:
        errors = await db.list_commands(
            sid, aid, limit, offset,
            response_ok=0, tool_name=tool_name,
            since_iso=since_iso, keyword=keyword,
        )
        # tool_names 取「失败过的工具」列表，前端下拉用 —— 不带 tool_name 过滤
        tool_names = await db.list_command_tool_names(
            since_iso=since_iso, response_ok=0,
        )
        return JSONResponse({
            "errors": errors,
            "tool_names": tool_names,
            "limit": limit,
            "offset": offset,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_get_stats(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    try:
        stats = await db.get_stats()
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_set_proxy(request: Request) -> JSONResponse:
    """POST /admin/sessions/{id}/set-proxy — 为会话设置或清除代理。"""
    if (err := _auth_required(request)): return err
    sid = request.path_params.get("id", "")
    try:
        body = await request.json()
        proxy_url = body.get("proxy_url", "")
        result = await cmd_set_proxy(proxy_url, sid or None)
        return JSONResponse({"ok": True, "proxy_url": proxy_url, "result": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_get_proxy_pool(request: Request) -> JSONResponse:
    """GET /admin/proxy-pool — 列出所有代理及使用情况。"""
    if (err := _auth_required(request)): return err
    return JSONResponse({
        "strategy": proxy_pool.strategy,
        "size": proxy_pool.size,
        "proxies": proxy_pool.list_all(),
    })


async def admin_update_proxy_pool(request: Request) -> JSONResponse:
    """POST /admin/proxy-pool — 动态增删代理。body: {action: add|remove, proxy_url: str}"""
    if (err := _auth_required(request)): return err
    try:
        body = await request.json()
        action = body.get("action", "")
        proxy_url = body.get("proxy_url", "").strip()
        if not proxy_url:
            return JSONResponse({"ok": False, "error": "proxy_url 不能为空"}, status_code=400)
        if action == "add":
            added = proxy_pool.add(proxy_url)
            if added:
                await db.add_proxy_pool_entry(proxy_url)
            return JSONResponse({"ok": True, "added": added, "proxy_url": proxy_url})
        elif action == "remove":
            removed = proxy_pool.remove(proxy_url)
            if removed:
                await db.remove_proxy_pool_entry(proxy_url)
            return JSONResponse({"ok": True, "removed": removed, "proxy_url": proxy_url})
        else:
            return JSONResponse({"ok": False, "error": "action 须为 add 或 remove"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_get_quota(request: Request) -> JSONResponse:
    """GET /admin/quota — 返回所有会话的配额使用情况及当前上限。"""
    if (err := _auth_required(request)): return err
    try:
        return JSONResponse({
            "limits": quota_tracker.get_limits(),
            "sessions": quota_tracker.list_all(),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── 命令注册表 ────────────────────────────────────────────────────────────────

_SEED_COMMANDS = [
    # 状态检查
    {"ext_name": "bosszp", "cmd_group": "状态检查", "path": "boss/check_login",         "label": "检查登录状态",    "method": "GET",  "description": "返回 logged_in / userId / name"},
    {"ext_name": "bosszp", "cmd_group": "状态检查", "path": "boss/get_session_status",  "label": "会话状态",        "method": "GET",  "description": "扩展连接状态 + 令牌缓存数量"},
    {"ext_name": "bosszp", "cmd_group": "状态检查", "path": "boss/tokens",              "label": "令牌快照",        "method": "GET",  "description": "完整 token 链（调试用）"},
    # 登录
    {"ext_name": "bosszp", "cmd_group": "登录",     "path": "boss/generate_qrcode",     "label": "生成二维码(API)", "method": "POST", "description": "直接调用 randkey+getqrcode 接口生成二维码，无需打开登录页"},
    {"ext_name": "bosszp", "cmd_group": "登录",     "path": "boss/navigate_login",      "label": "打开登录页",      "method": "POST", "description": "在 Worker Tab 打开 Boss直聘登录页"},
    {"ext_name": "bosszp", "cmd_group": "登录",     "path": "boss/capture_qrcode",      "label": "截取二维码(截图)", "method": "POST", "description": "截取当前登录页的二维码图片（旧方式，备用）"},
    {"ext_name": "bosszp", "cmd_group": "登录",     "path": "boss/login_with_qr",       "label": "二维码登录(截图)", "method": "POST", "description": "导航到登录页并截取二维码（旧方式，备用）"},
    {"ext_name": "bosszp", "cmd_group": "登录",     "path": "boss/init_session",        "label": "初始化会话",      "method": "GET",  "description": "获取 wt2 令牌和用户信息（登录后调用）"},
    {"ext_name": "bosszp", "cmd_group": "登录",     "path": "boss/logout",              "label": "退出登录",        "method": "POST", "description": "清除认证 cookies + 重置令牌"},
    {"ext_name": "bosszp", "cmd_group": "登录",     "path": "boss/set_proxy",           "label": "设置代理",        "method": "POST", "description": "为该会话设置或清除代理（proxy_url 为空时直连）"},
    # 求职
    {"ext_name": "bosszp", "cmd_group": "求职",     "path": "boss/search_jobs",         "label": "搜索职位",        "method": "POST", "description": "搜索职位列表，自动存储 listSecurityId"},
    {"ext_name": "bosszp", "cmd_group": "求职",     "path": "boss/get_job_detail",      "label": "职位详情",        "method": "POST", "description": "获取职位详情，需先调用搜索职位"},
    {"ext_name": "bosszp", "cmd_group": "求职",     "path": "boss/start_chat",          "label": "发起聊天",        "method": "POST", "description": "打招呼（消耗 job_application 配额）"},
    {"ext_name": "bosszp", "cmd_group": "求职",     "path": "boss/send_message",        "label": "发送消息",        "method": "POST", "description": "发送消息，需先发起聊天"},
    {"ext_name": "bosszp", "cmd_group": "求职",     "path": "boss/get_chat_history",    "label": "聊天历史",        "method": "POST", "description": "拉取聊天历史记录"},
    # 职位发现
    {"ext_name": "bosszp", "cmd_group": "职位发现",       "path": "boss/get_recommend_jobs",   "label": "推荐职位",       "method": "POST", "description": "个性化推荐职位列表（page 参数）"},
    {"ext_name": "bosszp", "cmd_group": "职位发现",       "path": "boss/get_job_card",         "label": "职位卡片",       "method": "POST", "description": "轻量职位详情，不触发完整 token 链（security_id 必填）"},
    {"ext_name": "bosszp", "cmd_group": "职位发现",       "path": "boss/get_job_history",      "label": "浏览历史",       "method": "POST", "description": "最近浏览过的职位列表（page 参数）"},
    # 个人中心
    {"ext_name": "bosszp", "cmd_group": "个人中心",       "path": "boss/get_resume_baseinfo",  "label": "简历基本信息",   "method": "GET",  "description": "姓名、年龄、学历等基本简历信息"},
    {"ext_name": "bosszp", "cmd_group": "个人中心",       "path": "boss/get_resume_expect",    "label": "求职期望",       "method": "GET",  "description": "目标职位、城市、薪资期望等"},
    {"ext_name": "bosszp", "cmd_group": "个人中心",       "path": "boss/get_resume_status",    "label": "投递状态汇总",   "method": "GET",  "description": "简历投递状态汇总（投递数、被看次数等）"},
    {"ext_name": "bosszp", "cmd_group": "个人中心",       "path": "boss/get_deliver_list",     "label": "已投递列表",     "method": "POST", "description": "已投递职位列表（jobName、brandName、status 等）"},
    {"ext_name": "bosszp", "cmd_group": "个人中心",       "path": "boss/get_interview_data",   "label": "面试邀请",       "method": "GET",  "description": "面试邀请数据"},
    # 社交
    {"ext_name": "bosszp", "cmd_group": "社交",           "path": "boss/get_friend_list",      "label": "好友列表(旧)",   "method": "GET",  "description": "已沟通 Boss 好友列表（旧接口，前端已切到 geek_filter_by_label）"},
    {"ext_name": "bosszp", "cmd_group": "社交",           "path": "boss/get_geek_job",         "label": "互动职位",       "method": "POST", "description": "查询与某 Boss 互动的职位上下文（security_id 必填）"},
    {"ext_name": "bosszp", "cmd_group": "消息中心",       "path": "boss/geek_filter_by_label", "label": "消息列表(求职者)", "method": "GET",  "description": "求职者消息中心主接口（labelId: 0=全部 1=新招呼 2=仅沟通 3=有交换 4=有面试 5=不感兴趣）"},
    {"ext_name": "bosszp", "cmd_group": "消息中心",       "path": "boss/geek_get_boss_data",   "label": "聊天前 Boss 元信息", "method": "GET",  "description": "求职者进入聊天前拉 boss + 关联职位"},
    {"ext_name": "bosszp", "cmd_group": "消息中心",       "path": "boss/get_ws_endpoints",     "label": "WS 服务器列表",  "method": "GET",  "description": "Boss 实时消息 WebSocket 域名列表"},
    {"ext_name": "bosszp", "cmd_group": "消息中心",       "path": "boss/msg_history_pull",     "label": "离线消息补拉",   "method": "GET",  "description": "增量补拉离线消息（last_id + secret_id）"},
    # 候选人（招聘方）
    {"ext_name": "bosszp", "cmd_group": "候选人(招聘方)", "path": "boss/my_job_list",          "label": "我的职位列表",   "method": "GET",  "description": "招聘方获取自己发布的职位列表（分页）"},
    {"ext_name": "bosszp", "cmd_group": "候选人(招聘方)", "path": "boss/rec_job_list",         "label": "推荐职位简表",   "method": "GET",  "description": "招聘方获取简化职位列表（recJobList）"},
    {"ext_name": "bosszp", "cmd_group": "候选人(招聘方)", "path": "boss/search_candidates",    "label": "搜索候选人",     "method": "GET",  "description": "招聘方搜索候选人，返回 encryptGeekId+securityId 列表"},
    {"ext_name": "bosszp", "cmd_group": "候选人(招聘方)", "path": "boss/auto_suggest",         "label": "关键词补全",     "method": "GET",  "description": "招聘方搜索关键词自动补全"},
    {"ext_name": "bosszp", "cmd_group": "候选人(招聘方)", "path": "boss/get_candidate_detail", "label": "候选人详情",     "method": "GET",  "description": "搜索页候选人详情 /wapi/zpitem/web/boss/search/geek/info，返回 encryptGeekId+令牌链"},
    {"ext_name": "bosszp", "cmd_group": "候选人(招聘方)", "path": "boss/contact_candidate",    "label": "主动沟通",      "method": "POST", "description": "主动沟通候选人（search/geek/info → bossEnter，消耗 candidate_contact 配额）"},
    # bosszp-cli（无浏览器 httpx 版，cookie_id 在每次请求 body 中动态传入）
    {"ext_name": "bosszp-cli", "cmd_group": "状态检查", "path": "boss/check_login",         "label": "检查登录",     "method": "GET",  "description": "httpx: Cookie 有效性（get_user_info）"},
    {"ext_name": "bosszp-cli", "cmd_group": "状态检查", "path": "boss/get_session_status",  "label": "已加载账号",   "method": "GET",  "description": "列出当前池中所有 cookie_id"},
    {"ext_name": "bosszp-cli", "cmd_group": "状态检查", "path": "boss/init_session",        "label": "重载 Cookie",  "method": "POST", "description": "强制重新从 DB 加载 Cookie"},
    {"ext_name": "bosszp-cli", "cmd_group": "状态检查", "path": "boss/logout",              "label": "退出账号",     "method": "POST", "description": "从池中移除指定 cookie_id"},
    {"ext_name": "bosszp-cli", "cmd_group": "求职",     "path": "boss/search_jobs",         "label": "搜索职位",     "method": "POST", "description": "httpx: 职位搜索"},
    {"ext_name": "bosszp-cli", "cmd_group": "求职",     "path": "boss/get_job_detail",      "label": "职位详情",     "method": "GET",  "description": "httpx: 职位详情"},
    {"ext_name": "bosszp-cli", "cmd_group": "求职",     "path": "boss/start_chat",          "label": "发起沟通",     "method": "POST", "description": "httpx: 申请沟通（add_friend）"},
    {"ext_name": "bosszp-cli", "cmd_group": "职位发现", "path": "boss/get_recommend_jobs",  "label": "推荐职位",     "method": "GET",  "description": "httpx: 推荐职位列表"},
    {"ext_name": "bosszp-cli", "cmd_group": "职位发现", "path": "boss/get_job_card",        "label": "职位卡片",     "method": "GET",  "description": "httpx: 轻量职位详情"},
    {"ext_name": "bosszp-cli", "cmd_group": "职位发现", "path": "boss/get_job_history",     "label": "浏览历史",     "method": "GET",  "description": "httpx: 最近浏览职位"},
    {"ext_name": "bosszp-cli", "cmd_group": "个人中心", "path": "boss/get_resume_baseinfo", "label": "简历基本信息", "method": "GET",  "description": "httpx: 简历基础信息"},
    {"ext_name": "bosszp-cli", "cmd_group": "个人中心", "path": "boss/get_resume_expect",   "label": "求职期望",     "method": "GET",  "description": "httpx: 求职期望"},
    {"ext_name": "bosszp-cli", "cmd_group": "个人中心", "path": "boss/get_resume_status",   "label": "投递状态",     "method": "GET",  "description": "httpx: 简历投递状态"},
    {"ext_name": "bosszp-cli", "cmd_group": "个人中心", "path": "boss/get_deliver_list",    "label": "已投递列表",   "method": "GET",  "description": "httpx: 已投递职位"},
    {"ext_name": "bosszp-cli", "cmd_group": "个人中心", "path": "boss/get_interview_data",  "label": "面试邀请",     "method": "GET",  "description": "httpx: 面试邀请数据"},
    {"ext_name": "bosszp-cli", "cmd_group": "社交",     "path": "boss/get_friend_list",     "label": "好友列表",     "method": "GET",  "description": "httpx: 已沟通 Boss 列表"},
    {"ext_name": "bosszp-cli", "cmd_group": "社交",     "path": "boss/get_geek_job",        "label": "互动职位",     "method": "GET",  "description": "httpx: 互动职位信息"},
]


async def admin_list_command_registry(request: Request) -> JSONResponse:
    """GET /admin/command-registry — 列出所有命令配置，按 cmd_group 分组。"""
    if (err := _auth_required(request)): return err
    try:
        ext_name = request.query_params.get("ext_name") or None
        commands = await db.list_command_registry(ext_name)
        # 按 cmd_group 分组（保持插入顺序）
        groups: dict[str, list] = {}
        for cmd in commands:
            key = cmd.get("cmd_group") or cmd.get("ext_name") or "(未知)"
            groups.setdefault(key, []).append(cmd)
        return JSONResponse({"commands": commands, "groups": groups})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_update_command_registry(request: Request) -> JSONResponse:
    """PATCH /admin/command-registry/{id} — 更新 enabled / webhook_url / webhook_secret。"""
    if (err := _auth_required(request)): return err
    try:
        cmd_id = int(request.path_params.get("id", 0))
        body = await request.json()
        allowed = {"enabled", "webhook_url", "webhook_secret"}
        fields = {k: v for k, v in body.items() if k in allowed}
        if not fields:
            return JSONResponse({"ok": False, "error": "没有可更新的字段"}, status_code=400)
        await db.update_command_registry(cmd_id, **fields)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_login(request: Request) -> JSONResponse:
    """POST /admin/login — 密码登录，成功后设置 HttpOnly Cookie。"""
    if not ADMIN_PASSWORD:
        return JSONResponse({"ok": True, "message": "认证已禁用（未设置 ADMIN_PASSWORD）"})
    try:
        body = await request.json()
    except Exception as _e:
        log.debug("silently returning %s: %s", 'value', _e)
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    password = body.get("password", "")
    if not password or password != ADMIN_PASSWORD:
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=401)
    token = secrets.token_hex(32)
    _admin_tokens.add(token)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite="strict",
        path="/admin",
        max_age=7 * 24 * 3600,
    )
    return resp


async def admin_logout_admin(request: Request) -> JSONResponse:
    """POST /admin/logout — 清除 Cookie 和服务端 token。"""
    token = request.cookies.get(COOKIE_NAME, "")
    _admin_tokens.discard(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/admin")
    return resp


async def admin_me(request: Request) -> JSONResponse:
    """GET /admin/me — 检查当前认证状态（供前端导航守卫使用）。"""
    if not ADMIN_PASSWORD:
        return JSONResponse({"ok": True, "authenticated": True, "auth_enabled": False})
    token = request.cookies.get(COOKIE_NAME, "")
    if token and token in _admin_tokens:
        return JSONResponse({"ok": True, "authenticated": True, "auth_enabled": True})
    return JSONResponse({"ok": False, "authenticated": False, "auth_enabled": True}, status_code=401)


async def handle_admin_ws(websocket: WebSocket) -> None:
    """Admin 实时推送 WebSocket（/admin/ws）。"""
    # 认证检查（支持 Cookie 或 ?token= 查询参数）
    if ADMIN_PASSWORD:
        token = websocket.cookies.get(COOKIE_NAME, "") or websocket.query_params.get("token", "")
        if not token or token not in _admin_tokens:
            await websocket.close(code=4401)
            return
    await websocket.accept()
    await ab.admin_broadcaster.subscribe(websocket)
    # 发送初始快照
    try:
        sessions = session_store.list_all()
        agents = agent_tracker.list_all()
        await websocket.send_text(json.dumps({
            "event": "init_snapshot",
            "sessions": sessions,
            "agents": agents,
        }, ensure_ascii=False))
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass

    try:
        while True:
            # 保持连接活跃，客户端断开时会抛出异常
            await websocket.receive_text()
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass
    finally:
        await ab.admin_broadcaster.unsubscribe(websocket)


# ── Job Cache Admin REST ──────────────────────────────────────────────────────


async def admin_job_cache_list(request: Request) -> JSONResponse:
    """GET /admin/job-cache — 列出缓存的职位。"""
    if (err := _auth_required(request)): return err
    qs = request.query_params
    platform = qs.get("platform") or None
    keyword = qs.get("keyword") or None
    city_code = qs.get("city_code") or None
    has_detail_str = qs.get("has_detail") or None
    has_detail: bool | None = None
    if has_detail_str == "true":
        has_detail = True
    elif has_detail_str == "false":
        has_detail = False
    try:
        limit = min(int(qs.get("limit", 50)), 200)
        offset = int(qs.get("offset", 0))
    except ValueError:
        limit, offset = 50, 0
    try:
        jobs = await db.list_cached_jobs(
            platform=platform,
            keyword=keyword,
            city_code=city_code,
            has_detail=has_detail,
            limit=limit,
            offset=offset,
        )
        return JSONResponse({"jobs": jobs, "limit": limit, "offset": offset})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


async def admin_job_cache_detail(request: Request) -> JSONResponse:
    """GET /admin/job-cache/{platform}/{external_id} — 获取缓存职位详情。"""
    if (err := _auth_required(request)): return err
    platform = request.path_params["platform"]
    external_id = request.path_params["external_id"]
    try:
        job = await db.get_cached_job(platform, external_id)
        if job is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(job)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


async def admin_job_cache_fetch_detail(request: Request) -> JSONResponse:
    """POST /admin/job-cache/{platform}/{external_id}/fetch-detail
    通过当前已连接的扩展调用 Boss API 抓取职位详情并写入缓存。"""
    if (err := _auth_required(request)): return err
    external_id = request.path_params["external_id"]
    try:
        data = await cmd_get_job_detail(external_id)
        updated = await db.get_cached_job("boss", external_id)
        return JSONResponse({"ok": True, "job": updated})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Agent 生命周期记录 ────────────────────────────────────────────────────────


async def _record_agent_connect(agent_id: str, bound_session: str = "", bound_account: str = "") -> None:
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.upsert_agent_session(agent_id, status="connected")
        await db.log_session_event(None, agent_id, "agent_connected", None)
        payload: dict = {
            "event": "agent_connected",
            "agent_id": agent_id,
            "connected_at": now,
            "user_id": agent_tracker.get_user_id(agent_id),
        }
        if bound_session:
            payload["bound_session"] = bound_session
            payload["bound_account"] = bound_account
        await ab.admin_broadcaster.broadcast(payload)
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


async def _record_agent_disconnect(agent_id: str) -> None:
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.upsert_agent_session(agent_id, status="disconnected", disconnected_at=now)
        await db.log_session_event(None, agent_id, "agent_disconnected", None)
        await ab.admin_broadcaster.broadcast({
            "event": "agent_disconnected",
            "agent_id": agent_id,
            "disconnected_at": now,
            "user_id": agent_tracker.get_user_id(agent_id),
        })
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


class _AgentTrackingMiddleware:
    """
    ASGI 中间件：拦截 /mcp 端点请求，通过 mcp-session-id 追踪 Agent 连接生命周期。

    协议约定（MCP Streamable HTTP Transport）：
      - 首次 POST /mcp（无 mcp-session-id 请求头）→ 响应头携带新 mcp-session-id → agent_connected
      - 后续请求携带 mcp-session-id → 计数 + 确保已注册（网关重启恢复场景）
      - DELETE /mcp 携带 mcp-session-id → agent_disconnected
    """

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        # 非 HTTP 请求（WebSocket 等）直接透传
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self._app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        req_sid = headers.get(b"mcp-session-id", b"").decode()
        user_tier = headers.get(b"x-user-tier", b"").decode()
        user_id = headers.get(b"x-user-id", b"").decode()
        user_role = headers.get(b"x-user-role", b"").decode()  # Phase 2: jobseeker/recruiter
        gw_request_id = headers.get(b"x-request-id", b"").decode()
        # 注入请求级身份到 contextvars,供 MCP tool handler 读取(JSON-RPC 层无法访问 HTTP headers)
        if user_id:
            _current_user_id.set(user_id)
        if user_role:
            _current_role.set(user_role)
        method = scope.get("method", "")

        # ── MCP tool call 入参日志 ──────────────────────────────────────────
        if method == "POST":
            _body_chunks: list[bytes] = []
            _body_done = False
            _original_receive = receive

            async def _logging_receive():
                nonlocal _body_done
                msg = await _original_receive()
                if msg.get("type") == "http.request":
                    _body_chunks.append(msg.get("body", b""))
                    if not msg.get("more_body", False) and not _body_done:
                        _body_done = True
                        try:
                            full = json.loads(b"".join(_body_chunks))
                            if full.get("method") == "tools/call":
                                tname = full.get("params", {}).get("name", "?")
                                targs = full.get("params", {}).get("arguments", {})
                                log.info("[mcp→] sid=%s rid=%s tier=%s tool=%s args=%s",
                                         req_sid[:16] if req_sid else "-",
                                         gw_request_id or "-",
                                         user_tier or "-", tname,
                                         json.dumps(targs, ensure_ascii=False)[:400])
                        except Exception as _e:
                            log.debug("silently swallowed: %s", _e)
                            pass
                return msg

            receive = _logging_receive

        if method == "DELETE" and req_sid:
            # Agent 主动断开
            agent_tracker.on_disconnect(req_sid)
            asyncio.create_task(_record_agent_disconnect(req_sid))
        elif req_sid:
            # 已有 session 的请求（网关重启后也能重新注册）
            agent_tracker.on_connect(req_sid)   # idempotent：已存在则不覆盖
            agent_tracker.on_request(req_sid)
            if user_id:
                agent_tracker.set_user_id(req_sid, user_id)
            if user_tier:
                agent_tracker.set_user_tier(req_sid, user_tier)

        # 捕获响应头中的新 mcp-session-id（新会话首次 POST）
        new_sid: list[str] = []

        async def _wrapped_send(message):
            if message["type"] == "http.response.start" and not req_sid:
                for k, v in message.get("headers", []):
                    if k.lower() == b"mcp-session-id":
                        new_sid.append(v.decode())
                        break
            await send(message)

        await self._app(scope, receive, _wrapped_send)

        if new_sid:
            sid = new_sid[0]
            agent_tracker.on_connect(sid)
            if user_id:
                agent_tracker.set_user_id(sid, user_id)
            if user_tier:
                agent_tracker.set_user_tier(sid, user_tier)
            # 预绑定：若客户端传入了 x-ext-session-id，立即绑定而无需等待第一次工具调用
            boss_session_id = (headers.get(b"x-ext-session-id", b"") or headers.get(b"x-boss-session-id", b"")).decode()
            bound_session_for_connect = ""
            bound_account_for_connect = ""
            if boss_session_id:
                try:
                    agent_tracker.bind_session(sid, boss_session_id)
                    entry = session_store.get(boss_session_id)
                    account_name = entry.account_name if entry else ""
                    if account_name:
                        agent_tracker.set_bound_account(sid, account_name)
                    bound_session_for_connect = boss_session_id
                    bound_account_for_connect = account_name
                    print(f"[gateway] 预绑定 agent={sid[:16]} → session={boss_session_id[:16]}", flush=True)
                except Exception as e:
                    print(f"[gateway] 预绑定失败: {e}", flush=True)
            asyncio.create_task(_record_agent_connect(sid, bound_session_for_connect, bound_account_for_connect))


# ── CLI 端点 ─────────────────────────────────────────────────────────────────


async def _cli_set_quota_limit(params: dict) -> dict:
    """set_limit 是同步的，包一层 async 以符合 /cli dispatch 约定。"""
    quota_tracker.set_limit(params["quota_type"], params["limit"])
    return {"ok": True, "quota_type": params["quota_type"], "new_limit": params["limit"]}


async def handle_cli(request: Request) -> JSONResponse:
    """POST /cli - 直接调用命令函数，供 boss_cli.py 使用。"""
    try:
        body = await request.json()
    except Exception as _e:
        log.debug("silently returning %s: %s", 'value', _e)
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    tool = body.get("tool", "")
    params = body.get("params", {})
    sid_raw = (params.pop("session_id", "") or "").strip()
    app_user_id = (params.pop("app_user_id", "") or "").strip()

    print(f"[cli] tool={tool!r} sid={sid_raw[:16]!r} app_user_id={app_user_id!r}", flush=True)

    # 无需 session 的纯管理命令：不走 _http_resolve_session，直接 dispatch
    _SESSION_FREE_TOOLS = {
        "boss_list_sessions",
        "boss_list_agents",
        "boss_get_quota_status",
        "boss_set_quota_limit",
    }
    if tool in _SESSION_FREE_TOOLS:
        _session_free_dispatch = {
            "boss_list_sessions": lambda p: cmd_list_sessions(),
            "boss_list_agents": lambda p: cmd_list_agents(),
            "boss_get_quota_status": lambda p: cmd_get_quota_status(None),
            "boss_set_quota_limit": _cli_set_quota_limit,
        }
        handler = _session_free_dispatch.get(tool)
        if not handler:
            return JSONResponse({"ok": False, "error": f"未知工具: {tool}"}, status_code=400)
        try:
            result = await handler(params)
            return JSONResponse({"ok": True, "result": result})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    try:
        sid = await _http_resolve_session(sid_raw, app_user_id)
    except RuntimeError as e:
        print(f"[cli] session 解析失败 tool={tool!r}: {e}", flush=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    _dispatch = {
        "boss_check_login": lambda p: cmd_check_login(sid),
        "boss_login": lambda p: cmd_login(sid),
        "boss_init_session": lambda p: cmd_init_session(sid),
        "boss_search_jobs": lambda p: cmd_search_jobs(
            p["keyword"], p.get("city", 101010100), p.get("page", 1), session_id=sid,
            search_session_id=p.get("search_session_id", ""),
        ),
        "boss_get_job_detail": lambda p: cmd_get_job_detail(p["encrypt_job_id"], session_id=sid),
        "boss_start_chat": lambda p: cmd_start_chat(p["encrypt_job_id"], session_id=sid),
        "boss_send_message": lambda p: cmd_send_message(
            p["encrypt_job_id"], p["content"], session_id=sid
        ),
        "boss_get_chat_history": lambda p: cmd_get_chat_history(
            p["encrypt_job_id"], p.get("max_msg_id", ""), session_id=sid
        ),
        "boss_logout": lambda p: cmd_logout(sid),
        "boss_get_tokens": lambda p: cmd_get_tokens(sid),
        "boss_get_session_status": lambda p: cmd_get_session_status(sid),
        "boss_refresh_my_jobs": lambda p: cmd_boss_refresh_my_jobs(
            type=p.get("type", 0), search_str=p.get("search_str", ""), session_id=sid
        ),
        "boss_list_my_jobs": lambda p: cmd_boss_list_my_jobs(
            keyword=p.get("keyword", ""),
            job_status=p.get("job_status"),
            session_id=sid,
        ),
        "boss_rec_job_list": lambda p: cmd_boss_rec_job_list(session_id=sid),
        "boss_list_interacted_geeks": lambda p: cmd_boss_get_geek_list(
            tag=p.get("tag", 2),
            geek_apply_status=p.get("geek_apply_status", -1),
            chat_status=p.get("chat_status", -1),
            jobid=str(p.get("jobid", "-1")), page=p.get("page", 1), session_id=sid,
        ),
        "boss_contact_list": lambda p: cmd_boss_contact_list(
            filter_json=p.get("filter"), page=p.get("page", 1),
            source=p.get("source", 2), session_id=sid,
        ),
        "boss_view_geek_detail": lambda p: cmd_boss_view_geek_info(
            p["encrypt_jid"], p["expect_id"], p["security_id"],
            p.get("lid", ""), p.get("entrance", 2), session_id=sid,
        ),
        "boss_search_candidates": lambda p: cmd_search_candidates(
            p["encrypt_job_id"], p.get("keywords", ""), p.get("city", -1),
            p.get("page", 1), p.get("filters", {}), session_id=sid
        ),
        "boss_auto_suggest": lambda p: cmd_boss_auto_suggest(
            p["query"], p["encrypt_job_id"], session_id=sid
        ),
        "boss_get_candidate_detail": lambda p: cmd_get_candidate_detail(
            p["security_id"], encrypt_uid=p.get("encrypt_uid", ""), session_id=sid
        ),
        "boss_contact_candidate": lambda p: cmd_contact_candidate(
            p["encrypt_uid"], p.get("job_id", ""),
            security_id=p.get("security_id", ""), session_id=sid
        ),
        "boss_set_proxy": lambda p: cmd_set_proxy(p.get("proxy_url", ""), sid),
    }

    handler = _dispatch.get(tool)
    if not handler:
        print(f"[cli] 未知工具: {tool!r}", flush=True)
        return JSONResponse({"ok": False, "error": f"未知工具: {tool}"}, status_code=400)

    try:
        result = await handler(params)
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── WebSocket 端点（扩展连接） ────────────────────────────────────────────────

_heartbeat_counter: dict[str, int] = {}

# 最低兼容扩展版本(低于此版本会被 capabilities 阶段 kick)。
# Phase 2 (双 ext 独立版本):env var fallback,实际值按 ext_kind 从
# static/versions.json 取(jobseeker/recruiter 各自的 min_compatible)。
_MIN_EXT_VERSION = os.getenv("MIN_EXT_VERSION", "1.5.4").strip()


def _parse_ext_version(v: str) -> tuple[int, ...]:
    """'1.5.4' → (1, 5, 4);解析失败返回 (0,) 让任何 min 检查都判旧。"""
    if not v or not isinstance(v, str):
        return (0,)
    parts: list[int] = []
    for piece in v.strip().split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            return (0,)
    return tuple(parts) if parts else (0,)


def _min_version_for_kind(ext_kind: str) -> str:
    """读 static/versions.json 取该 ext_kind 的 min_compatible,fallback _MIN_EXT_VERSION。

    用于 WS capabilities 阶段:job-seeker-ext 报 jobseeker → 查 jobseeker.min_compatible
    (e.g. 2.0.0);job-recruiter-ext 报 recruiter → recruiter.min_compatible (e.g. 1.0.0)。
    没传 ext_kind 的老 ext → fallback 老 _MIN_EXT_VERSION (1.5.4)。
    """
    if not ext_kind or ext_kind not in ("jobseeker", "recruiter"):
        return _MIN_EXT_VERSION
    try:
        from pathlib import Path
        versions_path = Path(__file__).parent / "static" / "versions.json"
        if versions_path.exists():
            data = json.loads(versions_path.read_text())
            ext_data = data.get(ext_kind) or {}
            mc = (ext_data.get("min_compatible") or "").strip()
            if mc:
                return mc
    except Exception:
        pass
    return _MIN_EXT_VERSION


def ext_version_meets_min(reported: str | None, minimum: str | None = None,
                          ext_kind: str | None = None) -> bool:
    """空 / 解析失败 / 低于 minimum → False;其它 True。

    优先级:显式 minimum > ext_kind 查 versions.json > 全局 _MIN_EXT_VERSION。
    """
    if minimum is None:
        minimum = _min_version_for_kind(ext_kind) if ext_kind else _MIN_EXT_VERSION
    if not minimum:
        return True
    if not reported:
        return False
    return _parse_ext_version(reported) >= _parse_ext_version(minimum)


async def handle_extension_ws(websocket: WebSocket) -> None:
    """处理来自 job-seeker-ext / job-recruiter-ext 扩展的 WebSocket 连接。

    Phase 2: kick 策略改为同 (user_id, ext_kind) 才踢 — 让同一用户能同时跑
    job-seeker-ext + job-recruiter-ext 两个连接。老 ext 没传 ext_kind → 按
    (user_id, "") 踢,跟自己同种类的旧连接互踢(向下兼容)。
    """
    await websocket.accept()
    pending: dict[str, asyncio.Future] = {}
    ip_address = websocket.client.host if websocket.client else ""
    ext_name = websocket.query_params.get("name", "")
    ext_kind_q = (websocket.query_params.get("kind", "") or "").strip()  # Phase 2
    stable_browser_id = websocket.query_params.get("bid", "")
    user_id_from_ext = (websocket.query_params.get("user_id", "")
                        or websocket.query_params.get("uid", "")).strip()  # uid 兼容旧扩展
    # 同一 (user_id, ext_kind) 后连踢前连(其它 ext_kind 不动)
    if user_id_from_ext:
        for old_entry in session_store.get_sessions_by_user_id(user_id_from_ext):
            # Phase 2: ext_kind 不同 → 不踢(jobseeker + recruiter 共存)
            if (old_entry.ext_kind or "") != ext_kind_q:
                continue
            kick_reason = (
                f"另一台设备已用同账号登录(新 IP={ip_address or 'unknown'}),当前连接已断开"
            )
            print(f"[gateway] 踢出旧会话 {old_entry.session_id[:16]}(同 user_id={user_id_from_ext} kind={ext_kind_q!r} 新连接替换)", flush=True)
            # DB 审计：记录踢人事件（方便用户投诉排查 / 异常登录告警）
            try:
                await db.log_session_event(
                    old_entry.session_id, None, "kicked",
                    f"replaced_by_ip={ip_address or 'unknown'} user_id={user_id_from_ext}",
                )
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
            try:
                await old_entry.ws.send_text(json.dumps({
                    "type": "kicked",
                    "reason": kick_reason,
                }))
                await old_entry.ws.close()
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
            # WS 关闭后旧会话的 receive_text() 会抛异常进入 finally → unregister()，自动移除

    session_id = register_extension(websocket, pending, ip_address=ip_address, ext_name=ext_name,
                                    ext_kind=ext_kind_q,
                                    stable_browser_id=stable_browser_id)
    entry = session_store.get(session_id)
    display_id = entry.display_id if entry else 0
    browser_id = entry.browser_id if entry else (stable_browser_id or session_id[:16])
    platform = ext_name or "bosszp"
    # 写入 dinQ 系统 user_id
    if entry and user_id_from_ext:
        entry.user_id = user_id_from_ext
    print(f"[gateway] 扩展已连接: #{display_id} name={ext_name or '(unknown)'} sessionId={session_id[:16]} user_id={user_id_from_ext or '-'} ip={ip_address}", flush=True)

    # 写 DB（含 ip_address + display_id + ext_name + user_id 持久化）
    try:
        await db.upsert_session(
            session_id,
            browser_id=browser_id,
            status="connected",
            ip_address=ip_address,
            display_id=display_id,
            ext_name=ext_name,
            **({"user_id": user_id_from_ext} if user_id_from_ext else {}),
        )
        await db.log_session_event(session_id, None, "connected", f"browserId={session_id[:16]} name={ext_name}")
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass

    # 广播连接事件
    conn_at = datetime.now(timezone.utc).isoformat()
    await ab.admin_broadcaster.broadcast({
        "event": "session_connected",
        "session_id": session_id,
        "browser_id": browser_id,
        "connected_at": conn_at,
        "ip_address": ip_address,
        "display_id": display_id,
        "ext_name": ext_name,
        "user_id": user_id_from_ext,
    })
    # 通知 BrowserPool 扩展已连接
    asyncio.create_task(
        browser_pool.on_browser_connected(browser_id, platform, session_id, ip_address)
    )

    # 恢复 app_user_id 绑定（网关重启后 _user_session_map 为空）
    async def _restore_app_user_binding(_browser_id=browser_id, _session_id=session_id):
        try:
            slot = await db.get_browser_slot(_browser_id)
            if slot and slot.get("app_user_id"):
                session_store.bind_app_user(slot["app_user_id"], _session_id)
                print(f"[ws] 恢复绑定: app_user_id={slot['app_user_id']} → session={_session_id[:12]}", flush=True)
        except Exception as _e:
            log.debug("silently swallowed: %s", _e)
            pass
    asyncio.create_task(_restore_app_user_binding())

    # 注意：user_id_from_ext 是 dinQ 系统 user_id，不是平台 app_user_id
    # app_user_id 绑定在 check_login 成功后通过 _resolve_and_bind 流程完成

    # 从代理池分配代理并写入 session entry
    assigned_proxy = proxy_pool.assign(session_id, browser_id=stable_browser_id)
    if entry:
        entry.proxy_url = assigned_proxy

    # 发送 registered 消息（含 sessionId + displayId + extName + proxyUrl + appUserId）
    try:
        await websocket.send_text(json.dumps({
            "type": "registered",
            "browserId": session_id[:16],
            "sessionId": session_id,
            "displayId": display_id,
            "extName": ext_name,
            "proxyUrl": assigned_proxy,
            "userId": user_id_from_ext,
        }))
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass

    _heartbeat_counter[session_id] = 0

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            # 心跳
            if msg.get("type") == "ping":
                try:
                    await websocket.send_text(json.dumps({"type": "pong", "ts": msg.get("ts")}))
                except Exception as _e:
                    log.debug("silently swallowed: %s", _e)
                    pass
                _heartbeat_counter[session_id] = _heartbeat_counter.get(session_id, 0) + 1
                if _heartbeat_counter[session_id] % 10 == 0:
                    try:
                        await db.log_session_event(session_id, None, "heartbeat",
                                                   f"count={_heartbeat_counter[session_id]}")
                    except Exception as _e:
                        log.debug("silently swallowed: %s", _e)
                        pass
                    await ab.admin_broadcaster.broadcast({
                        "event": "heartbeat",
                        "session_id": session_id,
                        "ts": msg.get("ts", 0),
                    })
                continue

            # 扩展能力上报: {type:"capabilities", sites:[...], manifest_version:"x.y.z",
            #                ext_kind:"jobseeker"|"recruiter"}
            # —— 一个扩展可支持多个站点;版本字段在 v1.5.4 起新增。
            # Phase 2: ext_kind 让网关按 jobseeker / recruiter 查不同 min_compatible。
            if msg.get("type") == "capabilities":
                sites = msg.get("sites") or []
                if isinstance(sites, list):
                    session_store.set_sites(session_id, sites)
                reported_version = msg.get("manifest_version")
                ext_kind = (msg.get("ext_kind") or "").strip() or None
                # Phase 2: capabilities 里 ext_kind 也回写 SessionEntry
                # (query string 传过的话 register 时已设置,这里覆盖以防漂移)
                if ext_kind:
                    _entry = session_store.get(session_id)
                    if _entry and not _entry.ext_kind:
                        _entry.ext_kind = ext_kind
                min_required = _min_version_for_kind(ext_kind) if ext_kind else _MIN_EXT_VERSION
                if not ext_version_meets_min(reported_version, ext_kind=ext_kind):
                    reason = (
                        f"扩展版本过旧(当前 {reported_version or '未上报'},"
                        f"最低要求 {min_required})。请到 chrome://extensions "
                        "重新加载扩展,或安装最新版本(/ext/install)。"
                    )
                    print(
                        f"[gateway] 拒绝旧扩展 sid={session_id[:16]} kind={ext_kind!r} "
                        f"version={reported_version!r} min={min_required}",
                        flush=True,
                    )
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "kicked", "reason": reason,
                        }))
                    except Exception as _e:
                        log.debug("silently swallowed: %s", _e)
                        pass
                    try:
                        await websocket.close(code=4003)
                    except Exception as _e:
                        log.debug("silently swallowed: %s", _e)
                        pass
                    break
                continue

            # 单站点登录状态上报：{type:"site_status", site:"linkedin", app_user_id:"..."}
            if msg.get("type") == "site_status":
                site = (msg.get("site") or "").strip()
                aid = (msg.get("app_user_id") or "").strip()
                if site:
                    session_store.set_site_user(session_id, site, aid)
                continue

            # 扩展上报当前动态配置版本：{type:"config_version", version: str|null}
            # 若 gateway 有更新版本，主动推 config_update 让扩展对齐
            if msg.get("type") == "config_version":
                ext_version_str = msg.get("version") or None
                try:
                    latest = await db.get_latest_dynamic_config()
                except Exception as e:
                    print(f"[gateway] config_version 处理失败 sid={session_id[:16]}: {e}", flush=True)
                    continue
                if latest and latest.get("version") != ext_version_str:
                    payload = {
                        "type": "config_update",
                        "version": latest["version"],
                        "chains": latest.get("chains") or {},
                        "dynamic_commands": latest.get("dynamic_commands") or [],
                    }
                    try:
                        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                        print(f"[gateway] 自动补推 config sid={session_id[:16]} ext_v={ext_version_str} → latest={latest['version']}", flush=True)
                    except Exception as e:
                        print(f"[gateway] 补推失败 sid={session_id[:16]}: {e}", flush=True)
                continue

            # config_update 的 ACK 由扩展回，仅记录不打断
            if msg.get("type") == "config_ack":
                print(f"[gateway] config_ack sid={session_id[:16]} version={msg.get('version')} registered={msg.get('registered')} skipped={msg.get('skipped')}", flush=True)
                continue

            # 命令响应
            req_id = msg.get("id")
            if req_id is None:
                continue

            entry = session_store.get(session_id)
            if entry is None:
                continue

            if msg.get("ok") is True:
                fut = entry.pending.pop(req_id, None)
                if fut and not fut.done():
                    fut.set_result(msg.get("result"))
            elif msg.get("ok") is False:
                fut = entry.pending.pop(req_id, None)
                if fut and not fut.done():
                    fut.set_exception(RuntimeError(msg.get("error", "extension error")))

    except Exception as e:
        print(f"[gateway] 扩展 WS 异常 (sid={session_id[:16]}): {e}", flush=True)
    finally:
        unregister_extension(session_id)
        proxy_pool.release(session_id)
        _heartbeat_counter.pop(session_id, None)
        print(f"[gateway] 扩展已断开: sessionId={session_id[:16]}", flush=True)
        # 通知 BrowserPool 扩展已断开
        asyncio.create_task(
            browser_pool.on_browser_disconnected(browser_id)
        )
        try:
            now = datetime.now(timezone.utc).isoformat()
            await db.upsert_session(session_id, status="disconnected", disconnected_at=now)
            await db.log_session_event(session_id, None, "disconnected", None)
        except Exception as _e:
            log.debug("silently swallowed: %s", _e)
            pass
        await ab.admin_broadcaster.broadcast({
            "event": "session_disconnected",
            "session_id": session_id,
            "disconnected_at": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id_from_ext,
        })


# ── Boss API HTTP 接口（供外部直接调用，不经过 MCP）────────────────────────────


async def api_check_login(request: Request) -> JSONResponse:
    """GET /api/boss/check-login?session_id=&app_user_id=&cookie_id=
    检查当前是否已登录，返回 {logged_in, userId, name}。
    优先用 session_id；若只有 app_user_id 则通过 pool 查询对应 session。
    若用户尚未分配 session，返回 {logged_in: false, no_session: true}。"""
    session_id = (request.query_params.get("session_id") or "").strip()
    app_user_id = (request.query_params.get("app_user_id") or "").strip()
    cookie_id = request.query_params.get("cookie_id", "")
    force = request.query_params.get("force", "0") == "1"

    if not session_id and app_user_id:
        session_id = (await browser_pool.get_session_for_user(app_user_id)) or ""

    if not session_id:
        return JSONResponse({"ok": True, "data": {"logged_in": False, "no_session": True}})

    try:
        result = await cmd_check_login(session_id, cookie_id=cookie_id, force=force)
        return JSONResponse({"ok": True, "data": {**result, "session_id": session_id}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_logout(request: Request) -> JSONResponse:
    """POST /api/boss/logout?session_id=&app_user_id=
    退出当前会话登录，清除 cookies 并重置 job_store。"""
    session_id = (request.query_params.get("session_id") or "").strip()
    app_user_id = (request.query_params.get("app_user_id") or "").strip()
    try:
        sid = await _http_resolve_session(session_id, app_user_id)
        result = await cmd_logout(sid)
        return JSONResponse({"ok": True, "data": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_search_jobs(request: Request) -> JSONResponse:
    """GET /api/boss/jobs?keyword=&city=101010100&page=1&session_id=&app_user_id=&cookie_id=
    搜索职位列表，返回原始 jobList 数组（已写入缓存）。"""
    qs = request.query_params
    keyword = qs.get("keyword", "").strip()
    if not keyword:
        return JSONResponse({"ok": False, "error": "keyword 不能为空"}, status_code=400)
    try:
        city = int(qs.get("city", 101010100))
        page = int(qs.get("page", 1))
    except ValueError:
        return JSONResponse({"ok": False, "error": "city/page 须为整数"}, status_code=400)
    session_id = (qs.get("session_id") or "").strip()
    app_user_id = (qs.get("app_user_id") or "").strip()
    cookie_id = qs.get("cookie_id", "")
    try:
        sid = await _http_resolve_session(session_id, app_user_id)
        data = await cmd_search_jobs(keyword, city=city, page=page,
                                     cookie_id=cookie_id, session_id=sid)
        job_list = (data.get("raw") or {}).get("zpData", {}).get("jobList", []) if isinstance(data, dict) else []
        total = (data.get("raw") or {}).get("zpData", {}).get("totalCount") if isinstance(data, dict) else None
        return JSONResponse({"ok": True, "data": {"jobs": job_list, "total": total, "page": page}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_get_job_detail(request: Request) -> JSONResponse:
    """GET /api/boss/jobs/{encrypt_job_id}?session_id=&app_user_id=&cookie_id=
    获取指定职位的详情（已写入缓存）。"""
    encrypt_job_id = request.path_params["encrypt_job_id"]
    qs = request.query_params
    session_id = (qs.get("session_id") or "").strip()
    app_user_id = (qs.get("app_user_id") or "").strip()
    cookie_id = qs.get("cookie_id", "")
    security_id = qs.get("security_id", "")
    try:
        sid = await _http_resolve_session(session_id, app_user_id)
        data = await cmd_get_job_detail(encrypt_job_id, security_id=security_id,
                                        cookie_id=cookie_id, session_id=sid)
        job_info = (data.get("raw") or {}).get("zpData", {}).get("jobInfo") if isinstance(data, dict) else None
        return JSONResponse({"ok": True, "data": {"jobInfo": job_info, "raw": data}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_intro_send(request: Request) -> JSONResponse:
    """POST /api/boss/intro/send
    对一个职位发起打招呼（friend/add → session/enter）。
    Boss直聘 要求对方同意后才能发消息，因此此接口只做打招呼，不调用 send_message。
    content 字段保留供未来扩展（extension 支持自定义招呼语后透传）。
    Body: {encrypt_job_id, content, app_user_id?, session_id?}"""
    try:
        body = await request.json()
    except Exception as _e:
        log.debug("silently returning %s: %s", 'value', _e)
        return JSONResponse({"ok": False, "error": "请求体必须为 JSON"}, status_code=400)
    encrypt_job_id = (body.get("encrypt_job_id") or "").strip()
    content        = (body.get("content") or "").strip()  # 保留字段，暂未使用
    app_user_id    = (body.get("app_user_id") or "").strip()
    session_id     = (body.get("session_id") or "").strip()

    if not encrypt_job_id:
        return JSONResponse({"ok": False, "error": "encrypt_job_id 不能为空"}, status_code=400)

    try:
        sid = await _http_resolve_session(session_id, app_user_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    try:
        start_result = await cmd_start_chat(encrypt_job_id, session_id=sid)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"start_chat 失败: {e}"}, status_code=500)
    return JSONResponse({"ok": True, "data": {"start_chat": start_result}})


# ── LinkedIn HTTP API ────────────────────────────────────────────────────────


async def api_linkedin_check_login(request: Request) -> JSONResponse:
    """GET /api/linkedin/check-login?session_id="""
    session_id = (request.query_params.get("session_id") or "").strip()
    try:
        sid = session_id or _default_li_session()
        if not sid:
            return JSONResponse({"ok": True, "data": {"logged_in": False, "no_session": True}})
        result = await li_cmd.cmd_check_login(sid)
        return JSONResponse({"ok": True, "data": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_linkedin_search_jobs(request: Request) -> JSONResponse:
    """GET /api/linkedin/jobs?keywords=&geo_location_id=&page=1&session_id=

    geo_location_id 是 LinkedIn 的 geoUrn 数字 id（如 '103644278' 美国）。
    兼容旧调用：若传 location 参数且其为纯数字串，等价于 geo_location_id。
    """
    qs = request.query_params
    keywords = qs.get("keywords", "").strip()
    if not keywords:
        return JSONResponse({"ok": False, "error": "keywords 不能为空"}, status_code=400)
    geo_location_id = (qs.get("geo_location_id") or "").strip()
    if not geo_location_id:
        legacy_loc = (qs.get("location") or "").strip()
        if legacy_loc.isdigit():
            geo_location_id = legacy_loc
    page = int(qs.get("page", 1))
    page_size = int(qs.get("page_size", 25))
    session_id = (qs.get("session_id") or "").strip()
    try:
        sid = session_id or _default_li_session()
        if not sid:
            return JSONResponse({"ok": False, "error": "没有活跃的 LinkedIn 会话"}, status_code=503)
        data = await li_cmd.cmd_search_jobs(sid, keywords, geo_location_id, page, page_size)
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_linkedin_get_job_detail(request: Request) -> JSONResponse:
    """GET /api/linkedin/jobs/{job_id}?session_id="""
    job_id = request.path_params["job_id"]
    session_id = (request.query_params.get("session_id") or "").strip()
    try:
        sid = session_id or _default_li_session()
        if not sid:
            return JSONResponse({"ok": False, "error": "没有活跃的 LinkedIn 会话"}, status_code=503)
        data = await li_cmd.cmd_get_job_detail(sid, job_id)
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Indeed HTTP API ──────────────────────────────────────────────────────────


async def api_indeed_check_login(request: Request) -> JSONResponse:
    """GET /api/indeed/check-login?session_id="""
    session_id = (request.query_params.get("session_id") or "").strip()
    try:
        sid = session_id or _default_indeed_session()
        if not sid:
            return JSONResponse({"ok": True, "data": {"logged_in": False, "no_session": True}})
        result = await in_cmd.cmd_check_login(sid)
        return JSONResponse({"ok": True, "data": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_indeed_search_jobs(request: Request) -> JSONResponse:
    """GET /api/indeed/jobs?keywords=&location=&page=1&session_id="""
    qs = request.query_params
    keywords = qs.get("keywords", "").strip()
    if not keywords:
        return JSONResponse({"ok": False, "error": "keywords 不能为空"}, status_code=400)
    location = qs.get("location", "").strip()
    page = int(qs.get("page", 1))
    session_id = (qs.get("session_id") or "").strip()
    try:
        sid = session_id or _default_indeed_session()
        if not sid:
            return JSONResponse({"ok": False, "error": "没有活跃的 Indeed 会话"}, status_code=503)
        data = await in_cmd.cmd_search_jobs(sid, keywords, location, page)
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_indeed_get_job_detail(request: Request) -> JSONResponse:
    """GET /api/indeed/jobs/{job_id}?session_id="""
    job_id = request.path_params["job_id"]
    session_id = (request.query_params.get("session_id") or "").strip()
    try:
        sid = session_id or _default_indeed_session()
        if not sid:
            return JSONResponse({"ok": False, "error": "没有活跃的 Indeed 会话"}, status_code=503)
        data = await in_cmd.cmd_get_job_detail(sid, job_id)
        return JSONResponse({"ok": True, "data": data})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_system_status(request: Request) -> JSONResponse:
    """GET /admin/system-status — 系统级监控指标（内存、DB 连接池、uptime）。"""
    if (err := _auth_required(request)): return err
    mem = _PROCESS.memory_info()
    pool_stats = await db.get_pool_stats()
    return JSONResponse({
        "service": "job-api-gateway",
        "port": int(os.environ.get("BOSS_GATEWAY_PORT", 8767)),
        "uptime_sec": round(time.time() - _START_TIME, 1),
        "memory": {"rss_bytes": mem.rss, "vms_bytes": mem.vms},
        "db_pool": pool_stats,
    })


async def admin_get_execution_decisions(request: Request) -> JSONResponse:
    """GET /admin/execution-decisions — 查询执行决策日志。"""
    if (err := _auth_required(request)): return err
    session_id = request.query_params.get("session_id") or None
    action     = request.query_params.get("action") or None
    limit      = int(request.query_params.get("limit", 100))
    offset     = int(request.query_params.get("offset", 0))
    rows = await db.list_execution_decisions(
        session_id=session_id, action=action, limit=limit, offset=offset
    )
    return JSONResponse({"decisions": rows, "total": len(rows)})


async def admin_chat_records_list(request: Request) -> JSONResponse:
    """GET /admin/chat-records — 列出打招呼记录。"""
    if (err := _auth_required(request)): return err
    qs = request.query_params
    try:
        records = await db.list_chat_records(
            keyword=qs.get("keyword") or None,
            account_name=qs.get("account_name") or None,
            limit=int(qs.get("limit", 50)),
            offset=int(qs.get("offset", 0)),
        )
        return JSONResponse({"ok": True, "records": records})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_extension_version(request: Request) -> JSONResponse:
    """GET /admin/extension/version — 返回扩展当前版本和 git 信息。"""
    import subprocess
    from pathlib import Path
    ext_dir = Path(__file__).parent.parent / "job-seeker-ext"
    repo_dir = Path(__file__).parent.parent
    try:
        manifest_path = ext_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        version = manifest.get("version", "unknown")
    except Exception:
        version = "unknown"
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir, text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, text=True
        ).strip()
        log = subprocess.check_output(
            ["git", "log", "-1", "--format=%s", "HEAD"], cwd=repo_dir, text=True
        ).strip()
    except Exception:
        commit, branch, log = "unknown", "unknown", ""
    return JSONResponse({
        "version": version,
        "commit": commit,
        "branch": branch,
        "last_commit": log,
        "ext_dir": str(ext_dir),
    })


async def admin_extension_upgrade(request: Request) -> JSONResponse:
    """POST /admin/extension/upgrade — git pull 最新代码，返回新版本信息。"""
    if (err := _auth_required(request)): return err
    import subprocess
    from pathlib import Path
    repo_dir = Path(__file__).parent.parent
    ext_dir = repo_dir / "job-seeker-ext"
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_dir, capture_output=True, text=True, timeout=30
        )
        output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    # 读取升级后版本
    try:
        manifest = json.loads((ext_dir / "manifest.json").read_text())
        new_version = manifest.get("version", "unknown")
    except Exception:
        new_version = "unknown"
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir, text=True
        ).strip()
    except Exception:
        commit = "unknown"
    return JSONResponse({
        "ok": success,
        "version": new_version,
        "commit": commit,
        "output": output,
    })


# ── 动态命令配置（Phase 1a：admin → gateway → ext WS broadcast） ─────────────


def _validate_dynamic_config_payload(payload: dict) -> tuple[bool, str]:
    """轻量校验。深度 schema 校验交给 Phase 1b 的 yaml lint。"""
    if not isinstance(payload, dict):
        return False, "payload 必须是对象"
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        return False, "version 必填且为非空字符串"
    if len(version) > 64:
        return False, "version 长度 ≤ 64"
    chains = payload.get("chains", {})
    if not isinstance(chains, dict):
        return False, "chains 必须是对象"
    cmds = payload.get("dynamic_commands", [])
    if not isinstance(cmds, list):
        return False, "dynamic_commands 必须是数组"
    if not chains and not cmds:
        return False, "chains 与 dynamic_commands 至少一个非空"
    seen_paths: set[str] = set()
    for i, c in enumerate(cmds):
        if not isinstance(c, dict):
            return False, f"dynamic_commands[{i}] 必须是对象"
        path = c.get("path")
        if not isinstance(path, str) or "/" not in path:
            return False, f"dynamic_commands[{i}].path 必填且形如 'site/cmd_name'"
        if path in seen_paths:
            return False, f"dynamic_commands[{i}].path 重复: {path}"
        seen_paths.add(path)
        rb = c.get("requestBuilder")
        if not isinstance(rb, dict):
            return False, f"dynamic_commands[{i}].requestBuilder 必填"
        if not isinstance(rb.get("url"), str) or not rb["url"]:
            return False, f"dynamic_commands[{i}].requestBuilder.url 必填"
        method = (rb.get("method") or "").upper()
        if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            return False, f"dynamic_commands[{i}].requestBuilder.method 非法: {method!r}"
    return True, ""


async def admin_dynamic_config_push(request: Request) -> JSONResponse:
    """POST /admin/extension/config/push —— 把动态命令配置广播给所有扩展。

    body: { version: str, chains?: dict, dynamic_commands?: list, notes?: str,
            source_ref?: str }
    """
    if (err := _auth_required(request)): return err
    try:
        payload = await request.json()
    except Exception as _e:
        log.debug("silently returning %s: %s", 'value', _e)
        return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)
    ok, msg = _validate_dynamic_config_payload(payload)
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, status_code=400)

    user_id = _current_user_id(request) or ""
    version = payload["version"].strip()
    chains = payload.get("chains") or {}
    cmds = payload.get("dynamic_commands") or []
    notes = (payload.get("notes") or "").strip()
    source_ref = (payload.get("source_ref") or "").strip()

    try:
        await db.upsert_dynamic_config(
            version,
            chains=chains,
            dynamic_commands=cmds,
            notes=notes,
            source_ref=source_ref,
            created_by=user_id,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"DB 写入失败: {e}"}, status_code=500)

    # 刷新 in-memory dynamic-command-path 集合 —— 后续 ext_client.send_command_to
    # 会读这个集合给 command_log.is_dynamic 打标
    try:
        from dynamic_command_state import update_paths
        update_paths([c.get("path") for c in cmds if isinstance(c, dict) and c.get("path")])
    except Exception as e:
        print(f"[gateway] update dynamic-paths 失败（不影响推送）: {e}", flush=True)

    # Phase 2: 同步刷新本进程的 MCP tool 注册表，agent 端立刻能 tools/list 看到
    mcp_apply: dict | None = None
    try:
        import dynamic_mcp_registry
        mcp_apply = dynamic_mcp_registry.apply_config(cmds)
    except Exception as e:
        print(f"[gateway] dynamic MCP 注册刷新失败（不影响扩展推送）: {e}", flush=True)

    broadcast_payload = {
        "type": "config_update",
        "version": version,
        "chains": chains,
        "dynamic_commands": cmds,
    }
    try:
        from ext_client import broadcast_to_all_extensions
        ack = await broadcast_to_all_extensions(broadcast_payload, ext_name="bosszp")
    except Exception as e:
        return JSONResponse({
            "ok": False,
            "error": f"DB 已写入但广播失败: {e}",
            "version": version,
        }, status_code=500)

    sent = sum(1 for r in ack if r.get("sent"))
    resp: dict = {
        "ok": True,
        "version": version,
        "total_sessions": len(ack),
        "sent": sent,
        "failed": len(ack) - sent,
        "ack": ack,
    }
    if mcp_apply is not None:
        resp["mcp"] = {
            "added": mcp_apply.get("added", []),
            "failed": mcp_apply.get("failed", []),
            "skipped": mcp_apply.get("skipped", []),
        }
    return JSONResponse(resp)


async def admin_dynamic_config_current(request: Request) -> JSONResponse:
    """GET /admin/extension/config/current —— 当前生产版本（最新一行）。"""
    if (err := _auth_required(request)): return err
    try:
        latest = await db.get_latest_dynamic_config()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if not latest:
        return JSONResponse({"ok": True, "config": None})
    return JSONResponse({"ok": True, "config": latest})


async def admin_dynamic_config_history(request: Request) -> JSONResponse:
    """GET /admin/extension/config/history?limit=&offset= —— 历史版本简表。"""
    if (err := _auth_required(request)): return err
    try:
        limit = int(request.query_params.get("limit", 50))
        offset = int(request.query_params.get("offset", 0))
    except ValueError:
        return JSONResponse({"ok": False, "error": "limit/offset 必须是整数"}, status_code=400)
    try:
        rows = await db.list_dynamic_configs(limit=limit, offset=offset)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "items": rows})


async def admin_dynamic_config_by_version(request: Request) -> JSONResponse:
    """GET /admin/extension/config/{version} —— 单版本完整 payload，用于回滚。"""
    if (err := _auth_required(request)): return err
    version = request.path_params.get("version", "").strip()
    if not version:
        return JSONResponse({"ok": False, "error": "version 必填"}, status_code=400)
    try:
        row = await db.get_dynamic_config_by_version(version)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    if not row:
        return JSONResponse({"ok": False, "error": f"未找到版本 {version}"}, status_code=404)
    return JSONResponse({"ok": True, "config": row})


async def admin_dynamic_config_active_sessions(request: Request) -> JSONResponse:
    """GET /admin/extension/config/active-sessions —— 即将受推送影响的扩展简表。"""
    if (err := _auth_required(request)): return err
    items = []
    for info in session_store.list_all():
        if info.get("ext_name") and info.get("ext_name") != "bosszp":
            continue
        if info.get("status") != "connected":
            continue
        items.append({
            "session_id": info.get("session_id"),
            "browser_id": info.get("browser_id"),
            "account_name": info.get("account_name"),
            "user_id": info.get("user_id"),
            "ext_name": info.get("ext_name"),
            "display_id": info.get("display_id"),
        })
    return JSONResponse({"ok": True, "total": len(items), "sessions": items})


async def ext_version(request: Request) -> JSONResponse:
    """GET /ext/version?ext=<jobseeker|recruiter> — 公开接口,供扩展自检 + 前端 onboarding 查询。

    Phase 2: 双扩展独立版本号。?ext= 缺省时默认 jobseeker(向下兼容老前端)。

    响应 shape:
      ext                 'jobseeker' | 'recruiter' (echo back)
      version             最新可用版本(通常 = dist 里的 manifest.version)
      commit              可选,对应 git short sha
      min_compatible      低于此版本前端把用户挡在 onboarding S2 强升
      download_url        自建 zip 下载地址(空则前端不显示下载按钮)
      chrome_store_url    Chrome 商店地址

    文件来源:static/versions.json(双 ext 版),格式:
      { "jobseeker": {version, min_compatible, ...},
        "recruiter": {version, min_compatible, ...} }
    fallback: 读 static/version.json 单 ext 旧文件;再 fallback 读 ext 目录下 manifest.json。
    """
    from pathlib import Path

    ext_kind = (request.query_params.get("ext") or "jobseeker").strip()
    if ext_kind not in ("jobseeker", "recruiter"):
        ext_kind = "jobseeker"

    default_payload: dict = {
        "ext": ext_kind,
        "version": "unknown",
        "commit": "",
        "min_compatible": "",
        "download_url": "",
        "chrome_store_url": "",
    }

    static_dir = Path(__file__).parent / "static"

    # 1. 优先读 versions.json (per-ext 复合)
    versions_path = static_dir / "versions.json"
    if versions_path.exists():
        try:
            data = json.loads(versions_path.read_text())
            ext_data = data.get(ext_kind) or {}
            if ext_data:
                return JSONResponse({**default_payload, **ext_data, "ext": ext_kind})
        except Exception as _e:
            log.debug("silently swallowed: %s", _e)

    # 2. 老 version.json (单 ext 旧文件,jobseeker 兼容)
    if ext_kind == "jobseeker":
        legacy_path = static_dir / "version.json"
        if legacy_path.exists():
            try:
                data = json.loads(legacy_path.read_text())
                return JSONResponse({**default_payload, **data, "ext": "jobseeker"})
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)

    # 3. 兜底:读对应 ext 目录的 manifest.json
    ext_dir_name = "job-seeker-ext" if ext_kind == "jobseeker" else "job-recruiter-ext"
    try:
        manifest_path = Path(__file__).parent.parent / ext_dir_name / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        default_payload["version"] = manifest.get("version", "unknown")
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
    return JSONResponse(default_payload)


async def ext_install_page(request: Request) -> HTMLResponse:
    """GET /ext/install — 扩展安装引导页。"""
    from pathlib import Path
    ext_dir = Path(__file__).parent / "static"
    zip_exists = (ext_dir / "job-seeker-ext.zip").exists()
    try:
        manifest = json.loads((Path(__file__).parent.parent / "job-seeker-ext" / "manifest.json").read_text())
        version = manifest.get("version", "unknown")
    except Exception:
        version = "unknown"

    download_btn = (
        '<a class="btn" href="/ext/download">下载扩展包 (v{ver})</a>'.format(ver=version)
        if zip_exists else
        '<p class="warn">扩展包尚未上传，请运行 <code>bash deploy/package-ext.sh</code></p>'
    )
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Boss API 扩展安装</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 640px; margin: 60px auto; padding: 0 24px; color: #1a1a1a; }}
  h1 {{ font-size: 24px; margin-bottom: 8px; }}
  .sub {{ color: #666; margin-bottom: 32px; }}
  .steps {{ counter-reset: step; list-style: none; padding: 0; }}
  .steps li {{ counter-increment: step; padding: 14px 0 14px 52px; position: relative;
               border-bottom: 1px solid #eee; }}
  .steps li::before {{ content: counter(step); position: absolute; left: 0;
                       background: #2563eb; color: #fff; width: 32px; height: 32px;
                       border-radius: 50%; display: flex; align-items: center;
                       justify-content: center; font-weight: 700; font-size: 14px; }}
  .steps li:last-child {{ border-bottom: none; }}
  .steps strong {{ display: block; margin-bottom: 4px; }}
  .steps p {{ color: #555; margin: 4px 0 0; font-size: 14px; line-height: 1.5; }}
  code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
  .btn {{ display: inline-block; background: #2563eb; color: #fff; padding: 12px 24px;
          border-radius: 8px; text-decoration: none; font-weight: 600; margin: 24px 0; }}
  .btn:hover {{ background: #1d4ed8; }}
  .warn {{ background: #fef3c7; border: 1px solid #fcd34d; padding: 12px 16px;
           border-radius: 6px; font-size: 14px; }}
  .tip {{ background: #eff6ff; border: 1px solid #bfdbfe; padding: 12px 16px;
          border-radius: 6px; font-size: 14px; margin-top: 24px; }}
</style>
</head>
<body>
<h1>Boss API 扩展安装</h1>
<p class="sub">当前版本 <strong>v{ver}</strong> &nbsp;·&nbsp; Chrome MV3 扩展</p>

{download_btn}

<ol class="steps">
  <li>
    <strong>下载并解压扩展包</strong>
    <p>点击上方按钮下载 ZIP，解压到一个固定目录（不要删除，扩展需要持续读取此目录）。</p>
  </li>
  <li>
    <strong>打开 Chrome 扩展管理页面</strong>
    <p>在地址栏输入 <code>chrome://extensions</code> 并回车。</p>
  </li>
  <li>
    <strong>开启开发者模式</strong>
    <p>页面右上角打开「开发者模式」开关（Developer mode）。</p>
  </li>
  <li>
    <strong>加载扩展</strong>
    <p>点击「加载已解压的扩展程序」（Load unpacked），选择刚才解压的 <code>job-seeker-ext</code> 文件夹。</p>
  </li>
  <li>
    <strong>确认安装成功</strong>
    <p>扩展列表出现 <strong>Boss API Extension</strong>，图标显示在工具栏即安装完成。</p>
  </li>
</ol>

<div class="tip">
  <strong>升级说明：</strong> 点击扩展弹窗中的「检查并升级」可自动获取最新版本，无需重新安装。
</div>
</body>
</html>""".format(ver=version, download_btn=download_btn)
    return HTMLResponse(html)


async def ext_download(request: Request) -> Response:
    """GET /ext/download — 下载扩展 zip 包。"""
    from pathlib import Path
    zip_path = Path(__file__).parent / "static" / "job-seeker-ext.zip"
    if not zip_path.exists():
        return JSONResponse({"error": "扩展包尚未打包，请运行 deploy/package-ext.sh"}, status_code=404)
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename="job-seeker-ext.zip",
    )


async def api_chat_records(request: Request) -> JSONResponse:
    """GET /api/boss/chat-records?keyword=&account_name=&limit=&offset=
    获取打招呼记录列表（外部调用，需 Bearer token）。"""
    qs = request.query_params
    try:
        records = await db.list_chat_records(
            keyword=qs.get("keyword") or None,
            account_name=qs.get("account_name") or None,
            limit=int(qs.get("limit", 50)),
            offset=int(qs.get("offset", 0)),
        )
        return JSONResponse({"ok": True, "records": records})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── BrowserPool REST API ──────────────────────────────────────────────────────


async def pool_acquire(request: Request) -> JSONResponse:
    """POST /api/pool/acquire — 外部用户获取浏览器槽位。"""
    try:
        body = await request.json()
        app_user_id = body.get("app_user_id", "")
        platform = body.get("platform", "bosszp")
        timeout_sec = int(body.get("timeout_sec", 60))
        if not app_user_id:
            return JSONResponse({"ok": False, "error": "app_user_id 必填"}, status_code=400)
        result = await browser_pool.acquire(app_user_id, platform, timeout_sec)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def pool_release(request: Request) -> JSONResponse:
    """POST /api/pool/release — 外部用户释放浏览器槽位。"""
    try:
        body = await request.json()
        app_user_id = body.get("app_user_id", "")
        if not app_user_id:
            return JSONResponse({"ok": False, "error": "app_user_id 必填"}, status_code=400)
        result = await browser_pool.release(app_user_id)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def pool_get_session(request: Request) -> JSONResponse:
    """GET /api/pool/session/{app_user_id} — 查询用户当前 session_id。"""
    app_user_id = request.path_params.get("app_user_id", "")
    try:
        session_id = await browser_pool.get_session_for_user(app_user_id)
        if session_id:
            return JSONResponse({"ok": True, "app_user_id": app_user_id, "session_id": session_id})
        return JSONResponse({"ok": False, "error": f"用户 {app_user_id} 没有已分配的浏览器"}, status_code=404)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_pool_status(request: Request) -> JSONResponse:
    """GET /admin/pool/status — 管理员查看池状态。"""
    if (err := _auth_required(request)): return err
    try:
        platform = request.query_params.get("platform") or None
        stats = await browser_pool.get_pool_stats(platform)
        slots = await db.list_browser_slots(platform=platform)
        queue_list = await browser_pool.list_queue(platform)
        return JSONResponse({"ok": True, "stats": stats, "slots": slots, "queue": queue_list})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_pool_capacity(request: Request) -> JSONResponse:
    """PATCH /admin/pool/capacity — 管理员更新平台容量配置。"""
    if (err := _auth_required(request)): return err
    try:
        body = await request.json()
        platform = body.get("platform", "bosszp")
        max_slots = int(body.get("max_slots", 50))
        idle_timeout_sec = body.get("idle_timeout_sec")
        if idle_timeout_sec is not None:
            idle_timeout_sec = int(idle_timeout_sec)
        await browser_pool.set_platform_capacity(platform, max_slots, idle_timeout_sec)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_pool_force_release(request: Request) -> JSONResponse:
    """POST /admin/pool/slots/{browser_id}/force-release — 强制释放槽位。"""
    if (err := _auth_required(request)): return err
    browser_id = request.path_params.get("browser_id", "")
    try:
        result = await browser_pool.force_release(browser_id)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def admin_pool_slots(request: Request) -> JSONResponse:
    """GET /admin/pool/slots — 管理员查看所有槽位列表。"""
    if (err := _auth_required(request)): return err
    try:
        platform = request.query_params.get("platform") or None
        state = request.query_params.get("state") or None
        slots = await db.list_browser_slots(platform=platform, state=state)
        return JSONResponse({"ok": True, "slots": slots})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)



# ── Agent Gateway 代理路由 ────────────────────────────────────────────────────

async def _agent_gw_get(path: str) -> tuple[dict, int]:
    """向 job-agent-gateway 发出 GET 请求，返回 (json_data, status_code)。"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{AGENT_GATEWAY_URL}{path}")
            return resp.json(), resp.status_code
    except Exception as e:
        return {"error": f"agent-gateway 不可达: {e}"}, 503


async def _agent_gw_post(path: str) -> tuple[dict, int]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{AGENT_GATEWAY_URL}{path}")
            return resp.json(), resp.status_code
    except Exception as e:
        return {"error": f"agent-gateway 不可达: {e}"}, 503


async def _agent_gw_delete(path: str) -> tuple[dict, int]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(f"{AGENT_GATEWAY_URL}{path}")
            return resp.json(), resp.status_code
    except Exception as e:
        return {"error": f"agent-gateway 不可达: {e}"}, 503


async def admin_agent_gw_status(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    data, code = await _agent_gw_get("/status")
    return JSONResponse(data, status_code=code)


async def admin_agent_gw_sessions(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    data, code = await _agent_gw_get("/sessions")
    if code != 200:
        return JSONResponse(data, status_code=code)

    # join: 用 app_user_id 在 session_store 中找对应的浏览器 session
    mem_sessions = {s["app_user_id"]: s for s in session_store.list_all() if s.get("app_user_id")}
    for sess in data.get("sessions", []):
        app_uid = sess.get("app_user_id") or ""
        browser = mem_sessions.get(app_uid)
        sess["bound_browser_session_id"] = browser["session_id"] if browser else None
        sess["account_name"] = browser.get("account_name", "") if browser else None
    return JSONResponse(data)


async def admin_agent_gw_session_detail(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    user_id = request.path_params["user_id"]
    data, code = await _agent_gw_get(f"/sessions/{user_id}")
    return JSONResponse(data, status_code=code)


async def admin_agent_gw_abort(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    user_id = request.path_params["user_id"]
    data, code = await _agent_gw_post(f"/sessions/{user_id}/abort")
    return JSONResponse(data, status_code=code)


async def admin_agent_gw_delete(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    user_id = request.path_params["user_id"]
    data, code = await _agent_gw_delete(f"/sessions/{user_id}")
    return JSONResponse(data, status_code=code)


async def admin_agent_gw_history(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    qs = request.query_params
    path = "/history"
    params = {k: qs[k] for k in ("user_id", "limit", "offset") if k in qs}
    if params:
        from urllib.parse import urlencode
        path += "?" + urlencode(params)
    data, code = await _agent_gw_get(path)
    return JSONResponse(data, status_code=code)


async def admin_agent_gw_history_events(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    session_id = request.path_params["session_id"]
    data, code = await _agent_gw_get(f"/history/{session_id}")
    return JSONResponse(data, status_code=code)


async def admin_agent_gw_users(request: Request) -> JSONResponse:
    if (err := _auth_required(request)): return err
    qs = request.query_params
    path = "/users"
    params = {k: qs[k] for k in ("limit", "offset") if k in qs}
    if params:
        from urllib.parse import urlencode
        path += "?" + urlencode(params)
    data, code = await _agent_gw_get(path)
    return JSONResponse(data, status_code=code)


async def get_session_by_app_user(request: Request):
    """GET /users/{app_user_id}/session — 用 app_user_id 查当前绑定的 session_id。
    优先查内存绑定，回退查 DB browser_slots。
    """
    uid = request.path_params.get("app_user_id", "").strip()
    if not uid:
        return JSONResponse({"ok": False, "error": "app_user_id 必填"}, status_code=400)

    # 优先查内存绑定
    sid = session_store.get_session_for_app_user(uid)
    if sid:
        entry = session_store.get(sid)
        return JSONResponse({
            "ok": True,
            "session_id": sid,
            "account_name": entry.account_name if entry else "",
        })

    # 回退查 DB（browser_slots）
    try:
        slot = await db.get_browser_slot_by_app_user(uid)
    except Exception:
        slot = None
    if slot and slot.get("last_session_id"):
        return JSONResponse({
            "ok": True,
            "session_id": slot["last_session_id"],
            "from_db": True,
        })

    return JSONResponse({"ok": False, "error": f"用户 {uid} 无绑定会话"}, status_code=404)


async def get_session_by_stable_browser_id(request: Request):
    """GET /sessions/by-stable/{browser_id} — 用稳定 browser_id 查当前在线 session_id。
    供 job-agent-gateway 在会话失效时主动恢复 boss_session_id。
    """
    bid = request.path_params.get("browser_id", "").strip()
    if not bid:
        return JSONResponse({"ok": False, "error": "browser_id 必填"}, status_code=400)
    entry = session_store.get_by_browser_id(bid)
    if entry is None:
        return JSONResponse({"ok": False, "found": False, "error": "未找到在线会话"}, status_code=404)
    return JSONResponse({
        "ok": True,
        "found": True,
        "session_id": entry.session_id,
        "browser_id": entry.browser_id,
        "account_name": entry.account_name,
    })


async def api_mark_job_interested(request: Request) -> JSONResponse:
    """POST /api/jobs/mark-interested
    标记一个或多个职位为"感兴趣"，同时记录匹配分和元数据。

    Body (新格式，推荐):
      { app_user_id: str, status: str, jobs: [{ encrypt_job_id, match_score, job_name, company_name, salary_desc, city }] }
    Body (旧格式，兼容):
      { app_user_id: str, encrypt_job_ids: [str] }
    """
    try:
        body = await request.json()
    except Exception as _e:
        log.debug("silently returning %s: %s", 'value', _e)
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    app_user_id = (body.get("app_user_id") or "").strip()
    if not app_user_id:
        return JSONResponse({"ok": False, "error": "app_user_id 必填"}, status_code=400)

    status = (body.get("status") or "viewed").strip()
    body_platform = (body.get("platform") or "boss").strip()

    # 新格式：jobs 列表（含元数据）
    jobs_list = body.get("jobs")
    if isinstance(jobs_list, list) and jobs_list:
        count = 0
        for job in jobs_list:
            jid = (job.get("encrypt_job_id") or "").strip()
            if not jid:
                continue
            job_platform = (job.get("platform") or body_platform).strip()
            fields: dict = {"interested": True, "status": status, "platform": job_platform}
            for key in ("job_name", "company_name", "salary_desc", "city", "notes"):
                val = job.get(key)
                if val:
                    fields[key] = str(val)
            score = job.get("match_score")
            if score is not None:
                try:
                    fields["match_score"] = max(0, min(100, int(score)))
                except (TypeError, ValueError):
                    pass
            await db.upsert_user_job_interest(app_user_id, jid, **fields)
            count += 1
        return JSONResponse({"ok": True, "marked": count})

    # 旧格式：encrypt_job_ids 列表（仅 id）
    encrypt_job_ids = body.get("encrypt_job_ids") or []
    if not isinstance(encrypt_job_ids, list) or not encrypt_job_ids:
        return JSONResponse({"ok": False, "error": "jobs 或 encrypt_job_ids 必填"}, status_code=400)
    for jid in encrypt_job_ids:
        if not jid:
            continue
        await db.upsert_user_job_interest(app_user_id, str(jid), interested=True, status=status, platform=body_platform)
    return JSONResponse({"ok": True, "marked": len(encrypt_job_ids)})


async def api_boss_geek_mark_job_interest(request: Request) -> JSONResponse:
    """POST /api/boss/geek/mark-job-interest
    Body: { app_user_id, job_id, collect=true, security_id='', session_id='' }
    """
    try:
        body = await request.json()
    except Exception as _e:
        log.debug("silently returning %s: %s", 'value', _e)
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    app_user_id = (body.get("app_user_id") or "").strip()
    job_id = (body.get("job_id") or "").strip()
    if not app_user_id or not job_id:
        return JSONResponse({"ok": False, "error": "app_user_id 和 job_id 必填"}, status_code=400)
    collect = bool(body.get("collect", True))
    security_id = (body.get("security_id") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    try:
        result = await cmd_geek_mark_job_interest(
            job_id, collect=collect, security_id=security_id,
            session_id=session_id or None, agent_id="", app_user_id=app_user_id,
        )
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── 抓包数据接收器 (api-recorder-v2) ─────────────────────────────────────────
# session_id → { session_id, tab_url, started_at, captures[] }
_capture_store: dict[str, dict] = {}


async def api_recorder_health(request):
    return JSONResponse({"status": "ok", "service": "job-api-gateway"})


async def api_recorder_session_start(request):
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)
    _capture_store[session_id] = {
        "session_id": session_id,
        "tab_url": body.get("tab_url", ""),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "captures": [],
    }
    return JSONResponse({"ok": True})


async def api_recorder_capture(request):
    body = await request.json()
    session_id = body.get("session_id")
    capture = body.get("capture")
    if not session_id or not capture:
        return JSONResponse({"error": "session_id and capture required"}, status_code=400)
    if session_id not in _capture_store:
        _capture_store[session_id] = {
            "session_id": session_id,
            "tab_url": body.get("tab_url", ""),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "captures": [],
        }
    _capture_store[session_id]["captures"].append(capture)
    return JSONResponse({"ok": True, "count": len(_capture_store[session_id]["captures"])})


async def api_recorder_sessions_list(request):
    sessions = [
        {
            "session_id": s["session_id"],
            "tab_url": s["tab_url"],
            "started_at": s["started_at"],
            "count": len(s["captures"]),
        }
        for s in _capture_store.values()
    ]
    sessions.sort(key=lambda x: x["started_at"], reverse=True)
    return JSONResponse({"sessions": sessions})


async def api_recorder_session_download(request):
    session_id = request.path_params["session_id"]
    session = _capture_store.get(session_id)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)
    content = json.dumps(session, ensure_ascii=False, indent=2)
    filename = f"captures_{session_id}.json"
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




def register_routes(app):
    """注册所有 HTTP/WebSocket 路由。"""
    # OAuth + 登录页 + 状态
    app.add_route("/oauth/linkedin/authorize", oauth_linkedin_authorize, methods=["GET"])
    app.add_route("/oauth/linkedin/callback", oauth_linkedin_callback, methods=["GET"])
    app.add_route("/status", http_status, methods=["GET"])
    app.add_route("/login-qr", http_login_qr, methods=["POST"])
    app.add_route("/refresh-qr", http_refresh_qr, methods=["POST"])
    app.add_route("/refresh-qr/{session_id}", http_refresh_qr_session, methods=["POST"])
    app.add_route("/qr", http_qr_image, methods=["GET"])
    app.add_route("/qr/{session_id}", http_qr_image_session, methods=["GET"])
    app.add_route("/login", http_login_page, methods=["GET"])
    app.add_route("/login/{session_id}", http_login_page_session, methods=["GET"])
    # WebSocket
    app.add_websocket_route("/ext/ws", handle_extension_ws)
    app.add_websocket_route("/api/v1/job-api/ext/ws", handle_extension_ws)  # 兼容 dinq-gateway 路径
    app.add_websocket_route("/admin/ws", handle_admin_ws)
    app.add_route("/admin/login", admin_login, methods=["POST"])
    app.add_route("/admin/logout", admin_logout_admin, methods=["POST"])
    app.add_route("/admin/me", admin_me, methods=["GET"])
    app.add_route("/users/{app_user_id}/session", get_session_by_app_user, methods=["GET"])
    app.add_route("/jobs/mark-interested", api_mark_job_interested, methods=["POST"])
    app.add_route("/boss/geek/mark-job-interest", api_boss_geek_mark_job_interest, methods=["POST"])
    app.add_route("/sessions/by-stable/{browser_id}", get_session_by_stable_browser_id, methods=["GET"])
    app.add_route("/admin/sessions", admin_get_sessions, methods=["GET"])
    app.add_route("/admin/sessions/{id}", admin_get_session, methods=["GET"])
    app.add_route("/admin/sessions/{id}/logout", admin_logout_session, methods=["POST"])
    app.add_route("/admin/sessions/{id}/disconnect", admin_disconnect_session, methods=["POST"])
    app.add_route("/admin/sessions/{id}/command", admin_custom_command, methods=["POST"])
    app.add_route("/admin/sessions/{id}/set-proxy", admin_set_proxy, methods=["POST"])
    app.add_route("/admin/proxy-pool", admin_get_proxy_pool, methods=["GET"])
    app.add_route("/admin/proxy-pool", admin_update_proxy_pool, methods=["POST"])
    app.add_route("/admin/agents", admin_get_agents, methods=["GET"])
    app.add_route("/admin/commands", admin_get_commands, methods=["GET"])
    app.add_route("/admin/errors",   admin_get_errors,   methods=["GET"])
    app.add_route("/admin/stats", admin_get_stats, methods=["GET"])
    app.add_route("/admin/quota", admin_get_quota, methods=["GET"])
    app.add_route("/admin/command-registry", admin_list_command_registry, methods=["GET"])
    app.add_route("/admin/command-registry/{id:int}", admin_update_command_registry, methods=["PATCH"])
    app.add_route("/admin/system-status", admin_system_status, methods=["GET"])
    app.add_route("/cli", handle_cli, methods=["POST"])
    app.add_route("/boss/check-login", api_check_login, methods=["GET"])
    app.add_route("/boss/logout", api_logout, methods=["POST"])
    app.add_route("/boss/jobs", api_search_jobs, methods=["GET"])
    app.add_route("/boss/jobs/{encrypt_job_id}", api_get_job_detail, methods=["GET"])
    app.add_route("/boss/intro/send", api_intro_send, methods=["POST"])
    # LinkedIn HTTP API
    app.add_route("/linkedin/check-login", api_linkedin_check_login, methods=["GET"])
    app.add_route("/linkedin/jobs", api_linkedin_search_jobs, methods=["GET"])
    app.add_route("/linkedin/jobs/{job_id}", api_linkedin_get_job_detail, methods=["GET"])
    # Indeed HTTP API
    app.add_route("/indeed/check-login", api_indeed_check_login, methods=["GET"])
    app.add_route("/indeed/jobs", api_indeed_search_jobs, methods=["GET"])
    app.add_route("/indeed/jobs/{job_id}", api_indeed_get_job_detail, methods=["GET"])
    app.add_route("/pool/acquire", pool_acquire, methods=["POST"])
    app.add_route("/pool/release", pool_release, methods=["POST"])
    app.add_route("/pool/session/{app_user_id}", pool_get_session, methods=["GET"])
    app.add_route("/admin/pool/status", admin_pool_status, methods=["GET"])
    app.add_route("/admin/pool/capacity", admin_pool_capacity, methods=["PATCH"])
    app.add_route("/admin/pool/slots/{browser_id}/force-release", admin_pool_force_release, methods=["POST"])
    app.add_route("/admin/pool/slots", admin_pool_slots, methods=["GET"])
    # Agent Gateway 代理路由
    app.add_route("/admin/agent-gateway/status", admin_agent_gw_status, methods=["GET"])
    app.add_route("/admin/agent-gateway/sessions", admin_agent_gw_sessions, methods=["GET"])
    app.add_route("/admin/agent-gateway/sessions/{user_id}", admin_agent_gw_session_detail, methods=["GET"])
    app.add_route("/admin/agent-gateway/sessions/{user_id}/abort", admin_agent_gw_abort, methods=["POST"])
    app.add_route("/admin/agent-gateway/sessions/{user_id}", admin_agent_gw_delete, methods=["DELETE"])
    app.add_route("/admin/agent-gateway/history", admin_agent_gw_history, methods=["GET"])
    app.add_route("/admin/agent-gateway/history/{session_id}", admin_agent_gw_history_events, methods=["GET"])
    app.add_route("/admin/agent-gateway/users", admin_agent_gw_users, methods=["GET"])
    app.add_route("/admin/job-cache", admin_job_cache_list, methods=["GET"])
    app.add_route("/admin/job-cache/{platform}/{external_id}", admin_job_cache_detail, methods=["GET"])
    app.add_route("/admin/job-cache/{platform}/{external_id}/fetch-detail", admin_job_cache_fetch_detail, methods=["POST"])
    app.add_route("/admin/execution-decisions", admin_get_execution_decisions, methods=["GET"])
    app.add_route("/admin/chat-records", admin_chat_records_list, methods=["GET"])
    app.add_route("/admin/extension/version", admin_extension_version, methods=["GET"])
    app.add_route("/admin/extension/upgrade", admin_extension_upgrade, methods=["POST"])
    app.add_route("/admin/extension/config/push",            admin_dynamic_config_push,            methods=["POST"])
    app.add_route("/admin/extension/config/current",         admin_dynamic_config_current,         methods=["GET"])
    app.add_route("/admin/extension/config/history",         admin_dynamic_config_history,         methods=["GET"])
    app.add_route("/admin/extension/config/active-sessions", admin_dynamic_config_active_sessions, methods=["GET"])
    app.add_route("/admin/extension/config/{version}",       admin_dynamic_config_by_version,      methods=["GET"])
    app.add_route("/ext/version", ext_version, methods=["GET"])
    app.add_route("/ext/install", ext_install_page, methods=["GET"])
    app.add_route("/ext/download", ext_download, methods=["GET"])
    app.add_route("/boss/chat-records", api_chat_records, methods=["GET"])
    app.add_route("/health", api_recorder_health, methods=["GET"])
    app.add_route("/capture/start", api_recorder_session_start, methods=["POST"])
    app.add_route("/capture", api_recorder_capture, methods=["POST"])
    app.add_route("/capture/sessions", api_recorder_sessions_list, methods=["GET"])
    app.add_route("/capture/sessions/{session_id}/download", api_recorder_session_download, methods=["GET"])

    # ── 收藏 API（PRD §4 / Sprint A1-A6） ──
    import bookmark_routes  # noqa: E402  延迟 import 避免循环依赖
    app.add_route("/bookmarks",                    bookmark_routes.list_bookmarks,     methods=["GET"])
    app.add_route("/bookmarks",                    bookmark_routes.create_bookmark,    methods=["POST"])
    app.add_route("/bookmarks/stats",              bookmark_routes.bookmark_stats,     methods=["GET"])
    app.add_route("/bookmarks/{bm_id:int}",        bookmark_routes.get_bookmark,       methods=["GET"])
    app.add_route("/bookmarks/{bm_id:int}",        bookmark_routes.update_bookmark,    methods=["PATCH"])
    app.add_route("/bookmarks/{bm_id:int}",        bookmark_routes.delete_bookmark,    methods=["DELETE"])
    app.add_route("/bookmarks/{bm_id:int}/reanalyze", bookmark_routes.reanalyze_bookmark, methods=["POST"])

    # ── Billing：Stripe Checkout + Customer Portal + Webhook（PRD §8 / Sprint B3-B6） ──
    from billing import routes as billing_routes  # noqa: E402
    app.add_route("/billing/plans",    billing_routes.billing_plans,    methods=["GET"])
    app.add_route("/billing/status",   billing_routes.billing_status,   methods=["GET"])
    app.add_route("/billing/ledger",   billing_routes.billing_ledger,   methods=["GET"])
    app.add_route("/billing/checkout", billing_routes.billing_checkout, methods=["POST"])
    app.add_route("/billing/portal",   billing_routes.billing_portal,   methods=["GET"])
    app.add_route("/billing/webhook",  billing_routes.billing_webhook,  methods=["POST"])
    # 内部端点（agent-gw 调；不暴露给前端）
    app.add_route("/billing/check",    billing_routes.billing_check,    methods=["POST"])
    app.add_route("/billing/charge",   billing_routes.billing_charge,   methods=["POST"])

