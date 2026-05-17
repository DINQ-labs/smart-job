"""
ext_client.py: 向扩展发送命令，支持多会话路由 + DB 埋点。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import db
import admin_broadcaster as ab
import webhook as wh
from session_store import session_store
from execution_guard import execution_guard, Decision
from agent_tracker import agent_tracker


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 向后兼容：注册/注销/列举 ─────────────────────────────────────────────────

def register_extension(ws: Any, pending: dict[str, asyncio.Future],
                       ip_address: str = "", ext_name: str = "",
                       ext_kind: str = "",
                       stable_browser_id: str = "") -> str:
    """注册扩展连接,返回 session_id。stable_browser_id 跨重连保持不变。
    ext_kind: Phase 2 双 ext 标识 (jobseeker / recruiter / 空=老 ext)。
    """
    return session_store.register(ws, pending, ip_address=ip_address, ext_name=ext_name,
                                  ext_kind=ext_kind,
                                  stable_browser_id=stable_browser_id)


def unregister_extension(session_id: str) -> None:
    session_store.unregister(session_id)


def has_any_ext_connected(ext_name: str = "bosszp") -> bool:
    """检查是否有任意指定类型的扩展已连接。"""
    return session_store.has_any_connected(ext_name)


def list_extensions() -> list[dict[str, Any]]:
    return session_store.list_all()


# ── 广播（Phase 1a：动态命令配置推送 + 未来其它管理消息）──────────────────────


async def broadcast_to_all_extensions(
    payload: dict,
    ext_name: str = "",
) -> list[dict[str, Any]]:
    """把 payload 通过 WS 广播给所有连接的扩展。

    - ext_name 为空 → 推所有扩展（bosszp + 其它站点）；指定则只推匹配
    - 返回 per-session 结果数组：每条 {session_id, ext_name, account_name,
      browser_id, sent: bool, error?: str}
    - 单条发送失败不阻断其它会话
    """
    text = json.dumps(payload, ensure_ascii=False)
    results: list[dict[str, Any]] = []
    for info in session_store.list_all():
        if ext_name and info.get("ext_name") != ext_name:
            continue
        sid = info.get("session_id") or ""
        entry = session_store.get(sid)
        ws = getattr(entry, "ws", None) if entry else None
        item = {
            "session_id": sid,
            "ext_name": info.get("ext_name") or "",
            "account_name": info.get("account_name") or "",
            "browser_id": info.get("browser_id") or "",
        }
        if ws is None:
            item["sent"] = False
            item["error"] = "ws closed"
            results.append(item)
            continue
        try:
            await ws.send_text(text)
            item["sent"] = True
        except Exception as e:
            item["sent"] = False
            item["error"] = str(e)
        results.append(item)
    return results


# ── Webhook 触发 ──────────────────────────────────────────────────────────────


async def _maybe_fire_webhook(
    ext_name: str,
    path: str,
    session_id: str,
    request_id: str,
    ok: bool,
    result: Any,
    duration_ms: float,
) -> None:
    """查命令注册表，若有 webhook_url 则异步转发结果。"""
    try:
        cmd_cfg = await db.get_command_registry(ext_name, path)
        if not cmd_cfg:
            return
        webhook_url = cmd_cfg.get("webhook_url", "")
        if not webhook_url:
            return
        await wh.fire_webhook(
            url=webhook_url,
            secret=cmd_cfg.get("webhook_secret", ""),
            ext_name=ext_name,
            path=path,
            session_id=session_id,
            request_id=request_id,
            ok=ok,
            result=result,
            duration_ms=duration_ms,
        )
    except Exception as e:
        print(f"[webhook] 触发异常 {path}: {e}", flush=True)


# ── 核心命令发送 ──────────────────────────────────────────────────────────────


async def send_command_to(
    session_id: str | None,
    method: str,
    path: str,
    body: Any = None,
    timeout_ms: int = 30000,
    tool_name: str = "",
    agent_id: str = "",
) -> Any:
    """
    向指定（或默认）扩展会话发送命令并等待结果。
    自动写 DB command_log 并广播 admin 事件。
    """
    entry = session_store.resolve_session(session_id)

    # 检查命令是否已在命令管理中禁用
    try:
        cmd_cfg = await db.get_command_registry(entry.ext_name, path)
        if cmd_cfg is not None and not cmd_cfg.get("enabled", 1):
            raise RuntimeError(f"命令 {path} 已被禁用（可在命令管理页面重新启用）")
    except RuntimeError:
        raise
    except Exception:
        pass  # DB 不可用时放行

    # ★ 执行决策评估
    decision: Decision = await execution_guard.evaluate(
        session_id=entry.session_id,
        agent_id=agent_id,
        tool_name=tool_name,
        path=path,
    )
    if decision.action == "block":
        raise RuntimeError(
            f"[ExecutionGuard] 已拒绝: {decision.reason} — {decision.detail}"
        )
    if decision.extra_delay_ms > 0:
        await asyncio.sleep(decision.extra_delay_ms / 1000)

    req_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    entry.pending[req_id] = future

    t_start = time.monotonic()
    started_at = _utc_now()

    # DB 埋点
    try:
        from dynamic_command_state import is_dynamic_path
        _is_dyn = is_dynamic_path(path)
    except Exception:
        _is_dyn = False
    try:
        await db.log_command_start(entry.session_id, agent_id, req_id, tool_name, method, path, body,
                                   user_tier=agent_tracker.get_user_tier(agent_id),
                                   is_dynamic=_is_dyn)
    except Exception:
        pass

    # 广播开始事件
    await ab.admin_broadcaster.broadcast({
        "event": "command_start",
        "session_id": entry.session_id,
        "agent_id": agent_id or None,
        "request_id": req_id,
        "tool_name": tool_name,
        "started_at": started_at,
    })

    if entry.ws is None:
        entry.pending.pop(req_id, None)
        raise RuntimeError(f"session {entry.session_id[:16]} 已断开，无法发送命令")

    payload = {"id": req_id, "method": method, "path": path, "body": body or {}}
    print(f"[ext_client] → 发送命令 session={entry.session_id[:16]} tool={tool_name or path} agent={agent_id or '-'} req_id={req_id} payload={json.dumps(payload, ensure_ascii=False)[:300]}", flush=True)

    try:
        await entry.ws.send_text(json.dumps(payload))
        result = await asyncio.wait_for(future, timeout=max(1.0, timeout_ms / 1000.0))
        duration = round((time.monotonic() - t_start) * 1000, 1)
        _result_preview = json.dumps(result, ensure_ascii=False)[:500] if result is not None else "None"
        print(f"[ext_client] ← 收到响应 req_id={req_id} duration={duration}ms result={_result_preview}", flush=True)

        try:
            await db.log_command_end(req_id, True, result, None)
        except Exception:
            pass
        await ab.admin_broadcaster.broadcast({
            "event": "command_end",
            "session_id": entry.session_id,
            "agent_id": agent_id or None,
            "request_id": req_id,
            "tool_name": tool_name,
            "path": path,
            "ok": True,
            "duration_ms": duration,
            "ended_at": _utc_now(),
        })
        # 触发 webhook（fire-and-forget，不阻塞命令返回）
        asyncio.create_task(
            _maybe_fire_webhook(entry.ext_name, path, entry.session_id, req_id, True, result, duration)
        )
        return result

    except asyncio.TimeoutError:
        entry.pending.pop(req_id, None)
        err = f"扩展命令超时: {method} {path}"
        print(f"[ext_client] ✗ 超时 req_id={req_id} {err}", flush=True)
        duration = round((time.monotonic() - t_start) * 1000, 1)
        try:
            await db.log_command_end(req_id, False, None, err)
        except Exception:
            pass
        await ab.admin_broadcaster.broadcast({
            "event": "command_end",
            "session_id": entry.session_id,
            "agent_id": agent_id or None,
            "request_id": req_id,
            "tool_name": tool_name,
            "path": path,
            "ok": False,
            "duration_ms": duration,
            "ended_at": _utc_now(),
            "error": err,
        })
        raise RuntimeError(err)

    except Exception as e:
        entry.pending.pop(req_id, None)
        err = str(e)
        print(f"[ext_client] ✗ 异常 req_id={req_id} {err}", flush=True)
        duration = round((time.monotonic() - t_start) * 1000, 1)
        try:
            await db.log_command_end(req_id, False, None, err)
        except Exception:
            pass
        await ab.admin_broadcaster.broadcast({
            "event": "command_end",
            "session_id": entry.session_id,
            "agent_id": agent_id or None,
            "request_id": req_id,
            "tool_name": tool_name,
            "path": path,
            "ok": False,
            "duration_ms": duration,
            "ended_at": _utc_now(),
            "error": err,
        })
        raise


async def send_command(
    method: str,
    path: str,
    body: Any = None,
    timeout_ms: int = 30000,
    tool_name: str = "",
    agent_id: str = "",
) -> Any:
    """向后兼容包装：使用默认会话。"""
    return await send_command_to(None, method, path, body, timeout_ms, tool_name, agent_id)
