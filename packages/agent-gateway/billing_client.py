"""billing_client.py — agent-gateway 调 job-api-gateway billing 端点的薄客户端 (Sprint 6 / G2)。

跨服务 HTTP 调用，超时 5s。失败策略由 BILLING_ENFORCE 控制：
  BILLING_ENFORCE=1（生产推荐）→ check/charge 失败时 fail-closed，业务阻断 / 警告
  BILLING_ENFORCE=0（开发默认）→ check/charge 失败时 fail-open，仅 log，业务正常跑

调用模式（agent_loop.py / tasks/engine.py 使用）：

    from billing_client import check_balance_for_model, charge_for_model_async

    # LLM 调用前
    chk = await check_balance_for_model(user_id, model_id)
    if not chk["can_afford"] and chk["enforce"]:
        yield {"type": "insufficient_credit", **chk}
        return

    # LLM 调用后（fire-and-forget,不 await result，由 task spawn 异步）
    asyncio.create_task(charge_for_model_async(user_id, actual_model))
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)


def _api_url() -> str:
    return os.environ.get("AGENT_BILLING_API_URL", "http://127.0.0.1:8767").rstrip("/")


def _enforce() -> bool:
    """生产应设 BILLING_ENFORCE=1；默认 0（不阻断业务，仅日志，便于上线灰度）。"""
    return os.environ.get("BILLING_ENFORCE", "0").strip() == "1"


def _timeout_sec() -> float:
    return float(os.environ.get("BILLING_HTTP_TIMEOUT_SEC", "5.0"))


# ── 余额预检（不扣费） ──────────────────────────────────────────────────────

async def check_balance(
    user_id: str,
    *,
    sku: str = "",
    model_id: str = "",
    count: int = 1,
) -> dict[str, Any]:
    """检查余额能否支付一次 sku/model 调用。返回 dict 始终包含 can_afford / enforce。

    sku 与 model_id 二选一；都没给时返回 can_afford=True + reason='no_sku'，不阻塞。
    """
    if not user_id or (not sku and not model_id):
        return {"can_afford": True, "reason": "no_input", "enforce": _enforce(),
                "cost": 0, "balance": 0, "plan": ""}

    payload = {"app_user_id": user_id, "count": count}
    if model_id: payload["model_id"] = model_id
    if sku:      payload["sku"] = sku

    try:
        async with httpx.AsyncClient(timeout=_timeout_sec()) as client:
            r = await client.post(f"{_api_url()}/billing/check", json=payload)
        if r.status_code != 200:
            return _fail_open("check_http_" + str(r.status_code))
        data = r.json()
        return {
            "can_afford": bool(data.get("can_afford")),
            "cost":       int(data.get("cost", 0)),
            "balance":    int(data.get("balance", 0)),
            "plan":       data.get("plan", ""),
            "sku":        data.get("sku", sku),
            "model_id":   data.get("model_id", model_id),
            "enforce":    _enforce(),
        }
    except Exception as e:
        log.warning("[billing_client] check_balance failed: %s", e)
        return _fail_open("check_exception")


# ── 实际扣费 ────────────────────────────────────────────────────────────────

async def charge(
    user_id: str,
    *,
    sku: str = "",
    model_id: str = "",
    count: int = 1,
    meta: dict | None = None,
) -> dict[str, Any]:
    """同步扣费。返回 dict 含 charged + cost + balance_after。

    可在调用方 await 拿结果；也可包 asyncio.create_task() 做 fire-and-forget。
    HTTP 失败默认 fail-open（仅 log）。如需 fail-closed 校验 'charged' 字段。
    """
    if not user_id or (not sku and not model_id):
        return {"charged": False, "reason": "no_input"}
    payload = {"app_user_id": user_id, "count": count, "meta": meta or {}}
    if model_id: payload["model_id"] = model_id
    if sku:      payload["sku"] = sku

    try:
        async with httpx.AsyncClient(timeout=_timeout_sec()) as client:
            r = await client.post(f"{_api_url()}/billing/charge", json=payload)
        if r.status_code == 402:
            data = r.json()
            log.info("[billing_client] insufficient_credit user=%s sku=%s cost=%s balance=%s",
                     user_id[:8], data.get("sku"), data.get("cost"), data.get("balance"))
            return {
                "charged": False,
                "insufficient": True,
                "cost":     int(data.get("cost", 0)),
                "balance":  int(data.get("balance", 0)),
                "plan":     data.get("plan", ""),
                "upgrade_hint": data.get("upgrade_hint", ""),
            }
        if r.status_code != 200:
            log.warning("[billing_client] charge HTTP %s for user=%s", r.status_code, user_id[:8])
            return {"charged": False, "reason": f"http_{r.status_code}"}
        data = r.json()
        return {
            "charged":        bool(data.get("charged")),
            "cost":           int(data.get("cost", 0)),
            "balance_before": int(data.get("balance_before", 0)),
            "balance_after":  int(data.get("balance_after", 0)),
            "ledger_id":      data.get("ledger_id"),
        }
    except Exception as e:
        log.warning("[billing_client] charge failed for user=%s: %s", user_id[:8], e)
        return {"charged": False, "reason": "exception", "error": str(e)}


def charge_async(user_id: str, **kw) -> asyncio.Task:
    """Fire-and-forget 扣费。返回 task；调用方不需要 await（异常已 swallow + log）。"""
    return asyncio.create_task(charge(user_id, **kw))


# ── 容错 helper ─────────────────────────────────────────────────────────────

def _fail_open(reason: str) -> dict[str, Any]:
    """billing 服务不可达时的默认返回：can_afford=True（不阻塞业务），但带 reason。
    BILLING_ENFORCE=1 时 caller 应根据 enforce 字段决定是否阻断。"""
    return {
        "can_afford": True, "reason": reason, "enforce": _enforce(),
        "cost": 0, "balance": 0, "plan": "",
    }
