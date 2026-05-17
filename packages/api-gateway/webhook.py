"""
webhook.py: 命令结果 Webhook 转发。

当扩展命令执行完成且该命令配置了 webhook_url 时，
异步（fire-and-forget）将结果以 JSON POST 转发到目标地址。

Payload 格式:
  {
    "event":      "command_result",
    "ext_name":   "bosszp",
    "path":       "boss/search_jobs",
    "session_id": "...",
    "request_id": "...",
    "ok":         true,
    "duration_ms": 1234.5,
    "result":     { ... }   // 完整结果，不截断
  }

签名（配置 webhook_secret 时）:
  Header X-Webhook-Signature: sha256=<HMAC-SHA256(secret, body)>
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sign(secret: str, body: bytes) -> str:
    """计算 HMAC-SHA256 签名，返回 'sha256=<hex>'。"""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def fire_webhook_sync(
    url: str,
    secret: str,
    ext_name: str,
    path: str,
    session_id: str,
    request_id: str,
    ok: bool,
    result: Any,
    duration_ms: float | None,
) -> None:
    """同步 webhook POST（在 executor 线程中调用）。"""
    payload: dict[str, Any] = {
        "event": "command_result",
        "ext_name": ext_name,
        "path": path,
        "session_id": session_id,
        "request_id": request_id,
        "ok": ok,
        "duration_ms": duration_ms,
        "result": result,
        "fired_at": _utc_now(),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    if secret:
        headers["X-Webhook-Signature"] = _sign(secret, body)

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            print(f"[webhook] {path} → {url} {status}", flush=True)
    except Exception as e:
        print(f"[webhook] POST {url} 失败: {e}", flush=True)


async def fire_webhook(
    url: str,
    secret: str,
    ext_name: str,
    path: str,
    session_id: str,
    request_id: str,
    ok: bool,
    result: Any,
    duration_ms: float | None = None,
) -> None:
    """异步 webhook（在 asyncio executor 中非阻塞执行）。"""
    import asyncio

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        fire_webhook_sync,
        url, secret, ext_name, path, session_id, request_id, ok, result, duration_ms,
    )
