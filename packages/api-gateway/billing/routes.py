"""Billing HTTP 路由处理器。

注册见 http_routes.register_routes()：
    POST /billing/checkout    创建 Checkout Session（subscription / topup）
    POST /billing/webhook     Stripe 事件回调（幂等）
    GET  /billing/portal      Customer Portal redirect
    GET  /billing/status      当前订阅 + Credit 余额
    GET  /billing/ledger      Credit 流水（前端账单用）
    GET  /billing/plans       套餐 + Credit 费率公开信息（前端定价页用）

约定：
- 鉴权沿用 app_user_id query/body 参数模式（与现有 /jobs/mark-interested 一致）。
- 错误响应 {"ok": False, "error": "..."} + 4xx/5xx 状态码。
- 成功响应 {"ok": True, ...payload}。
"""
from __future__ import annotations

import json
import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

import db
from . import credit_ledger, pricing, stripe_client

log = logging.getLogger(__name__)


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _need_user(body_or_query: dict) -> tuple[str, JSONResponse | None]:
    uid = (body_or_query.get("app_user_id") or "").strip()
    if not uid:
        return "", _err("app_user_id required", 400)
    return uid, None


# ════════════════════════════════════════════════════════════════════════════
# GET /billing/plans —— 公开定价信息
# ════════════════════════════════════════════════════════════════════════════
async def billing_plans(request: Request) -> JSONResponse:
    """返回三个套餐的定义 + Credit SKU 费率。前端定价页 + 升级引导卡片用。
    不需要鉴权。"""
    return JSONResponse({
        "ok": True,
        "plans": {pid: {**pdef, "id": pid} for pid, pdef in pricing.PLANS.items()},
        "credit_costs": pricing.CREDIT_COSTS,
        "topup_packs": pricing.TOPUP_PACKS,
    })


# ════════════════════════════════════════════════════════════════════════════
# GET /billing/status?app_user_id=xxx —— 当前订阅 + Credit
# ════════════════════════════════════════════════════════════════════════════
async def billing_status(request: Request) -> JSONResponse:
    uid, err = _need_user(dict(request.query_params))
    if err:
        return err
    # 在返回前主动 refill 一次（用户切到此页大概率也要扣费）
    sub = await db.get_subscription(uid)
    plan = (sub.get("plan") if sub else pricing.PLAN_FREE) or pricing.PLAN_FREE
    try:
        await credit_ledger.refill_if_due(uid, plan)
    except Exception as e:
        log.warning("refill_if_due failed for %s: %s", uid, e)
    summary = await credit_ledger.get_balance_summary(uid)
    return JSONResponse({"ok": True, **summary})


# ════════════════════════════════════════════════════════════════════════════
# GET /billing/ledger?app_user_id=xxx&limit=50&offset=0 —— Credit 流水
# ════════════════════════════════════════════════════════════════════════════
async def billing_ledger(request: Request) -> JSONResponse:
    qp = dict(request.query_params)
    uid, err = _need_user(qp)
    if err:
        return err
    try:
        limit = int(qp.get("limit", "50"))
        offset = int(qp.get("offset", "0"))
    except ValueError:
        return _err("limit/offset must be int", 400)
    rows = await db.list_credit_ledger(uid, limit=limit, offset=offset)
    return JSONResponse({"ok": True, "items": rows, "count": len(rows)})


# ════════════════════════════════════════════════════════════════════════════
# POST /billing/checkout —— 创建 Checkout Session
# Body: { app_user_id, plan?: 'jobseeker_pro'|'recruiter_pro', topup?: 'credit_100',
#         email?: '', name?: '', success_url?, cancel_url? }
# Returns: { ok, url, mode }
# ════════════════════════════════════════════════════════════════════════════
async def billing_checkout(request: Request) -> JSONResponse:
    if not stripe_client.is_enabled():
        return _err("Stripe not configured on this server", 503)
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON", 400)
    uid, err = _need_user(body)
    if err:
        return err

    plan = (body.get("plan") or "").strip()
    topup = (body.get("topup") or "").strip()
    if not (plan or topup):
        return _err("plan or topup required", 400)
    if plan and topup:
        return _err("specify either plan or topup, not both", 400)

    success_url = (body.get("success_url") or os.getenv("BILLING_SUCCESS_URL", "")).strip()
    cancel_url = (body.get("cancel_url") or os.getenv("BILLING_CANCEL_URL", "")).strip()
    if not success_url or not cancel_url:
        return _err("success_url and cancel_url required (set BILLING_SUCCESS_URL/CANCEL_URL or pass in body)", 400)

    try:
        customer_id = await stripe_client.ensure_customer(
            uid,
            email=body.get("email", ""),
            name=body.get("name", ""),
        )
    except Exception as e:
        log.exception("ensure_customer failed for %s", uid)
        return _err(f"stripe customer error: {e}", 502)

    price_map = pricing.stripe_price_to_plan_or_sku()
    # 反向找：plan → price_id
    if plan:
        if plan not in (pricing.PLAN_JOBSEEKER_PRO, pricing.PLAN_RECRUITER_PRO):
            return _err(f"unknown plan: {plan}", 400)
        price_id = next((pid for pid, (k, v) in price_map.items() if k == "subscription" and v == plan), "")
        if not price_id:
            return _err(f"price for {plan} not configured (set STRIPE_PRICE_{plan.upper()})", 503)
        try:
            url = stripe_client.create_subscription_checkout(
                customer_id, price_id, success_url, cancel_url,
                app_user_id=uid, plan=plan,
            )
        except Exception as e:
            log.exception("stripe subscription checkout failed")
            return _err(f"stripe error: {e}", 502)
        return JSONResponse({"ok": True, "url": url, "mode": "subscription"})

    # topup
    if topup not in pricing.TOPUP_PACKS:
        return _err(f"unknown topup pack: {topup}", 400)
    price_id = next((pid for pid, (k, v) in price_map.items() if k == "topup" and v == topup), "")
    if not price_id:
        return _err(f"price for {topup} not configured (set STRIPE_PRICE_CREDIT_100)", 503)
    try:
        url = stripe_client.create_topup_checkout(
            customer_id, price_id, success_url, cancel_url,
            app_user_id=uid, sku=topup,
        )
    except Exception as e:
        log.exception("stripe topup checkout failed")
        return _err(f"stripe error: {e}", 502)
    return JSONResponse({"ok": True, "url": url, "mode": "payment"})


# ════════════════════════════════════════════════════════════════════════════
# GET /billing/portal?app_user_id=xxx —— Customer Portal redirect
# ════════════════════════════════════════════════════════════════════════════
async def billing_portal(request: Request) -> Response:
    if not stripe_client.is_enabled():
        return _err("Stripe not configured", 503)
    uid, err = _need_user(dict(request.query_params))
    if err:
        return err
    sub = await db.get_subscription(uid)
    if not sub or not sub.get("stripe_customer_id"):
        return _err("no stripe customer for this user (subscribe first)", 404)
    return_url = (request.query_params.get("return_url")
                  or os.getenv("BILLING_PORTAL_RETURN_URL", "")
                  or os.getenv("BILLING_SUCCESS_URL", "")).strip()
    if not return_url:
        return _err("return_url required (set BILLING_PORTAL_RETURN_URL)", 400)
    try:
        url = stripe_client.create_portal_session(sub["stripe_customer_id"], return_url)
    except Exception as e:
        log.exception("create_portal_session failed")
        return _err(f"stripe error: {e}", 502)
    # 默认 302 redirect 让浏览器直接跳；如果调用方想拿 URL，传 ?json=1
    if request.query_params.get("json") == "1":
        return JSONResponse({"ok": True, "url": url})
    return RedirectResponse(url=url, status_code=303)


# ════════════════════════════════════════════════════════════════════════════
# POST /billing/webhook —— Stripe 事件回调
# 必须用原始 body 验签；幂等去重见 db.record_stripe_event
# ════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════
# POST /billing/check —— 内部端点：检查余额是否够某 SKU（不扣费）
# Body: { app_user_id, sku, count?: 1, model_id?: '' }
# 当 model_id 提供时优先用 pricing.model_to_sku() 映射；否则用 sku。
# Returns: { ok, can_afford, sku, cost, balance, plan }
# ════════════════════════════════════════════════════════════════════════════
async def billing_check(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON", 400)
    uid, err = _need_user(body)
    if err:
        return err
    sku = (body.get("sku") or "").strip()
    model_id = (body.get("model_id") or "").strip()
    if not sku and not model_id:
        return _err("sku or model_id required", 400)
    if model_id:
        sku = pricing.model_to_sku(model_id)
    count = max(int(body.get("count", 1)), 1)
    cost = pricing.cost_for_sku(sku) * count

    sub = await db.get_subscription(uid)
    plan = (sub.get("plan") if sub else pricing.PLAN_FREE) or pricing.PLAN_FREE
    # 先 refill 再读余额，避免每日重置点附近误判
    try:
        await credit_ledger.refill_if_due(uid, plan)
    except Exception as e:
        log.debug("refill_if_due during check failed: %s", e)
    acct = await db.get_credit_account(uid)
    balance = int(acct["balance"]) if acct else 0
    return JSONResponse({
        "ok": True,
        "can_afford": balance >= cost,
        "sku": sku, "model_id": model_id, "count": count,
        "cost": cost, "balance": balance, "plan": plan,
    })


# ════════════════════════════════════════════════════════════════════════════
# POST /billing/charge —— 内部端点：实际扣费（每次 LLM 调用 / 每个 task item）
# Body: { app_user_id, sku?, model_id?, count?: 1, meta?: {} }
# model_id 提供时按模型分档；否则按 sku（如 batch_analyze）。
# Returns:
#   200 + { ok:true,  charged: true,  cost, balance_before, balance_after, ledger_id }
#   402 + { ok:false, error:'insufficient_credit', cost, balance, plan, upgrade_hint }
#   400 + { ok:false, error:'...' }                参数错误
# 失败默认 fail-closed（402 不扣费且 caller 应阻断），caller 可由配置选择 fail-open。
# ════════════════════════════════════════════════════════════════════════════
async def billing_charge(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON", 400)
    uid, err = _need_user(body)
    if err:
        return err
    sku = (body.get("sku") or "").strip()
    model_id = (body.get("model_id") or "").strip()
    if not sku and not model_id:
        return _err("sku or model_id required", 400)
    count = max(int(body.get("count", 1)), 1)
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}

    sub = await db.get_subscription(uid)
    plan = (sub.get("plan") if sub else pricing.PLAN_FREE) or pricing.PLAN_FREE

    if model_id:
        result = await credit_ledger.charge_for_model(uid, model_id, plan=plan, meta={**meta, "_count": count})
        sku = pricing.model_to_sku(model_id)
    else:
        result = await credit_ledger.charge_for_sku(uid, sku, count, plan=plan, meta=meta)

    if not result.get("allowed"):
        return JSONResponse({
            "ok": False,
            "error": "insufficient_credit",
            "sku": sku, "cost": result.get("cost", 0),
            "balance": result.get("balance_after", 0),
            "plan": plan,
            "upgrade_hint": (
                "升级 Pro 解锁无限对话 / 追加 100cr/$2" if plan == pricing.PLAN_FREE
                else "本月套餐余额不足，可追加 100cr/$2"
            ),
        }, status_code=402)
    return JSONResponse({
        "ok": True,
        "charged": True,
        "sku": sku, "cost": result.get("cost", 0),
        "balance_before": result.get("balance_before", 0),
        "balance_after": result.get("balance_after", 0),
        "ledger_id": result.get("ledger_id"),
    })


async def billing_webhook(request: Request) -> Response:
    if not stripe_client.is_enabled():
        return _err("Stripe not configured", 503)
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not sig:
        return _err("missing stripe-signature header", 400)
    try:
        event = stripe_client.verify_webhook(payload, sig)
    except Exception as e:
        log.warning("stripe webhook verify failed: %s", e)
        return _err(f"signature verify failed: {e}", 400)

    event_id = event.get("id", "")
    event_type = event.get("type", "")
    if not event_id:
        return _err("event id missing", 400)

    # 幂等先记 raw（重放/重试也只成功一次）
    is_new = await db.record_stripe_event(event_id, event_type, event)
    if not is_new:
        log.info("stripe webhook replay ignored: %s", event_id)
        return JSONResponse({"ok": True, "replay": True})

    error = ""
    try:
        await _dispatch_stripe_event(event)
    except Exception as e:
        log.exception("stripe webhook dispatch failed: %s", event_id)
        error = str(e)
    await db.mark_stripe_event_processed(event_id, error=error)
    if error:
        # 返回 200 即可（Stripe 会按业务约定重试，但我们已落库 audit）；
        # 也可以返回 5xx 让 Stripe 自动重试，但这会导致 stripe_events 表里 processed_at 不为 NULL 但 error 字段有内容。
        # 这里选择"返回 200 + 内部 error 字段"，由人工/任务巡检 stripe_events.process_error 兜底
        return JSONResponse({"ok": False, "error": error, "event_id": event_id}, status_code=200)
    return JSONResponse({"ok": True, "event_id": event_id})


async def _dispatch_stripe_event(event: dict) -> None:
    """根据事件类型更新订阅/Credit 状态。"""
    etype = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    event_id = event.get("id", "")

    if etype == "checkout.session.completed":
        await _on_checkout_completed(data, event_id)
    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        await _on_subscription_update(data, event_id)
    elif etype == "customer.subscription.deleted":
        await _on_subscription_deleted(data, event_id)
    elif etype == "invoice.paid":
        await _on_invoice_paid(data, event_id)
    elif etype == "charge.refunded":
        await _on_charge_refunded(data, event_id)
    else:
        log.info("stripe event %s ignored (type=%s)", event_id, etype)


def _meta_get(obj: dict, key: str, default: str = "") -> str:
    """从 Stripe object 取 metadata.<key>。metadata 可能是 dict 或 None。"""
    md = obj.get("metadata") or {}
    return (md.get(key) or default).strip() if isinstance(md, dict) else default


async def _on_checkout_completed(session: dict, event_id: str) -> None:
    """Checkout 完成：根据 mode 走订阅 grant 或 topup grant。"""
    uid = (session.get("client_reference_id") or _meta_get(session, "app_user_id")).strip()
    if not uid:
        log.warning("checkout.session.completed without app_user_id (event %s)", event_id)
        return
    mode = session.get("mode", "")
    customer_id = (session.get("customer") or "").strip()

    if mode == "subscription":
        plan = _meta_get(session, "plan")
        if not plan:
            log.warning("subscription checkout without plan metadata (event %s)", event_id)
            return
        sub_id = (session.get("subscription") or "").strip()
        # 拉一次 subscription 拿 period_end 等
        period_end = None
        status = "active"
        if sub_id:
            try:
                sub_obj = stripe_client.retrieve_subscription(sub_id)
                period_end_ts = sub_obj.get("current_period_end")
                if period_end_ts:
                    from datetime import datetime, timezone
                    period_end = datetime.fromtimestamp(int(period_end_ts), tz=timezone.utc)
                status = sub_obj.get("status", "active")
            except Exception as e:
                log.warning("retrieve subscription %s failed: %s", sub_id, e)
        await db.upsert_subscription(
            uid,
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub_id,
            plan=plan,
            status=status,
            current_period_end=period_end,
            cancel_at_period_end=False,
        )
        await credit_ledger.grant_subscription(uid, plan, stripe_event_id=event_id)
        return

    if mode == "payment":
        sku = _meta_get(session, "sku") or "credit_100"
        pack = pricing.TOPUP_PACKS.get(sku)
        if not pack:
            log.warning("topup completed with unknown sku %s (event %s)", sku, event_id)
            return
        # 确保 customer 关联（首次 topup 用户可能还没 subscription 行）
        if customer_id:
            await db.upsert_subscription(uid, stripe_customer_id=customer_id)
        await credit_ledger.grant_topup(uid, pack["credits"], sku=sku, stripe_event_id=event_id)


async def _on_subscription_update(sub: dict, event_id: str) -> None:
    """订阅状态变化：同步 status / current_period_end / cancel_at_period_end。
    不在这里 grant credit（grant 由 invoice.paid / checkout.completed 触发，避免重复 grant）。"""
    customer_id = (sub.get("customer") or "").strip()
    if not customer_id:
        return
    existing = await db.get_subscription_by_customer(customer_id)
    if not existing:
        log.warning("subscription update for unknown customer %s (event %s)", customer_id, event_id)
        return
    period_end = None
    period_end_ts = sub.get("current_period_end")
    if period_end_ts:
        from datetime import datetime, timezone
        period_end = datetime.fromtimestamp(int(period_end_ts), tz=timezone.utc)
    await db.upsert_subscription(
        existing["app_user_id"],
        stripe_subscription_id=sub.get("id", ""),
        status=sub.get("status", "active"),
        current_period_end=period_end,
        cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
    )


async def _on_subscription_deleted(sub: dict, event_id: str) -> None:
    """订阅彻底失效（取消 + 过期）：plan 降级到 free，clear 套餐余量。"""
    customer_id = (sub.get("customer") or "").strip()
    existing = await db.get_subscription_by_customer(customer_id)
    if not existing:
        return
    uid = existing["app_user_id"]
    await db.upsert_subscription(
        uid,
        plan=pricing.PLAN_FREE,
        status="canceled",
        stripe_subscription_id="",
        current_period_end=None,
        cancel_at_period_end=False,
    )
    await credit_ledger.revoke_subscription_grant(uid, stripe_event_id=event_id)


async def _on_invoice_paid(invoice: dict, event_id: str) -> None:
    """月度续费：grant 当月 Credit。
    首次 checkout.session.completed 也会带这条 event；幂等：grant_subscription 会 reset 套餐 portion 到 monthly_grant，
    重复触发也只会把套餐 portion 维持在 monthly_grant，不会双倍。
    （理论上 stripe_events 表已做幂等去重，这里再加一层语义防御。）"""
    customer_id = (invoice.get("customer") or "").strip()
    existing = await db.get_subscription_by_customer(customer_id)
    if not existing:
        return
    plan = existing.get("plan", pricing.PLAN_FREE)
    if plan == pricing.PLAN_FREE:
        return
    await credit_ledger.grant_subscription(existing["app_user_id"], plan, stripe_event_id=event_id)


async def _on_charge_refunded(charge: dict, event_id: str) -> None:
    """退款：保守起见仅记 ledger，不自动扣回 Credit（避免误伤），由运营人工处理。"""
    log.info("stripe charge.refunded: %s (event %s) — manual review", charge.get("id"), event_id)
