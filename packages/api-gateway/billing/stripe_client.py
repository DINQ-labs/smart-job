"""Stripe SDK 封装：Checkout Session / Customer Portal / Webhook 签名校验。

模式：所有 Stripe 调用集中在这里。route handler 只编排，不直接 import stripe。
未配置 STRIPE_SECRET_KEY 时退化为"Stripe disabled"模式（route 返回 503，便于本地开发）。
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


try:
    import stripe  # type: ignore
except ImportError:  # pragma: no cover
    stripe = None  # type: ignore


def is_enabled() -> bool:
    return bool(stripe and os.getenv("STRIPE_SECRET_KEY", "").strip())


def _ensure_configured() -> None:
    if not is_enabled():
        raise RuntimeError(
            "Stripe not configured. Set STRIPE_SECRET_KEY in .env "
            "(see BILLING_SETUP.md for full setup)."
        )
    # 幂等设置：每次调用都 sync 一下 api_key（支持运行时切换 test/live）
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"].strip()


# ── Customer 管理 ───────────────────────────────────────────────────────────

async def ensure_customer(app_user_id: str, *, email: str = "", name: str = "") -> str:
    """返回 stripe_customer_id。
    优先复用 user_subscriptions 表里已存的 customer_id；没有则新建。
    （注意：Stripe SDK 是同步的，这里 wrap 成 async 仅为接口对称；调用方在 asyncio 任务里跑会被阻塞——
     如果未来 QPS 高需要换 stripe-asyncio 或 to_thread。）"""
    import db  # local import 避免循环依赖
    sub = await db.get_subscription(app_user_id)
    if sub and sub.get("stripe_customer_id"):
        return sub["stripe_customer_id"]
    _ensure_configured()
    customer = stripe.Customer.create(
        email=email or None,
        name=name or None,
        metadata={"app_user_id": app_user_id},
    )
    await db.upsert_subscription(app_user_id, stripe_customer_id=customer.id)
    return customer.id


# ── Checkout Session ────────────────────────────────────────────────────────

def create_subscription_checkout(
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    *,
    app_user_id: str,
    plan: str,
) -> str:
    """创建订阅模式 Checkout。返回 session.url（前端 redirect）。"""
    _ensure_configured()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url + ("&" if "?" in success_url else "?") + "session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
        client_reference_id=app_user_id,
        metadata={"app_user_id": app_user_id, "plan": plan, "kind": "subscription"},
        # 允许通过 promotion code（PRD 未要求，但留口）
        allow_promotion_codes=True,
    )
    return session.url


def create_topup_checkout(
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    *,
    app_user_id: str,
    sku: str,
) -> str:
    """创建一次性付款 Checkout（Credit 追加包）。"""
    _ensure_configured()
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url + ("&" if "?" in success_url else "?") + "session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
        client_reference_id=app_user_id,
        metadata={"app_user_id": app_user_id, "sku": sku, "kind": "topup"},
    )
    return session.url


# ── Customer Portal ─────────────────────────────────────────────────────────

def create_portal_session(customer_id: str, return_url: str) -> str:
    _ensure_configured()
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return session.url


# ── Webhook 校验 ───────────────────────────────────────────────────────────

def verify_webhook(payload: bytes, sig_header: str) -> dict:
    """校验 webhook 签名并解析事件。失败抛 ValueError / stripe.error.SignatureVerificationError。"""
    _ensure_configured()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not configured")
    event = stripe.Webhook.construct_event(payload, sig_header, secret)
    # event 是 dict-like (stripe.Event)；统一转 dict 便于 JSON 序列化进表
    return dict(event) if hasattr(event, "to_dict") else event  # type: ignore


# ── Subscription detail ─────────────────────────────────────────────────────

def retrieve_subscription(subscription_id: str) -> dict[str, Any]:
    _ensure_configured()
    sub = stripe.Subscription.retrieve(subscription_id)
    return sub.to_dict() if hasattr(sub, "to_dict") else dict(sub)


def retrieve_session(session_id: str) -> dict[str, Any]:
    _ensure_configured()
    sess = stripe.checkout.Session.retrieve(session_id)
    return sess.to_dict() if hasattr(sess, "to_dict") else dict(sess)
