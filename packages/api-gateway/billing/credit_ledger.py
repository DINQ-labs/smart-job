"""Credit 入账 / 扣费 / 重置 高层 API。

设计：
- 余额模型：balance = "套餐余量" + topup_balance（topup 永不过期，套餐到期重置）
- 重置策略：
    * 免费版：每日 0 点重置套餐余量 = daily_grant
    * Pro 版：每月订阅日重置套餐余量 = monthly_grant
- 所有变动均通过 db.credit_charge() 走单一原子事务 + ledger 记账
- refill_if_due() 由扣费路径在尝试扣费前调用，乐观幂等（DATE 比较）
- grant_subscription / grant_topup 由 webhook 调用，事件 id 写入 ledger 供 audit
"""
from __future__ import annotations

from datetime import date
from typing import Any

import db
from . import pricing


# ── 重置：每次扣费前自动调用 ─────────────────────────────────────────────────

async def refill_if_due(app_user_id: str, plan: str) -> dict | None:
    """到期则补足套餐余量。幂等：同一天/同一月重复调用只生效第一次。

    返回 db.credit_charge() 结果（含 balance_before / balance_after / ledger_id），
    None 表示未触发 refill。"""
    acct = await db.get_credit_account(app_user_id)
    if not acct:
        return None

    plan_info = pricing.plan_def(plan)
    today_iso = date.today().isoformat()

    if plan == pricing.PLAN_FREE:
        last = acct.get("last_daily_reset")
        # _row_to_dict_pg 把 date 转成 ISO 字符串 "YYYY-MM-DD"
        if isinstance(last, str) and last >= today_iso:
            return None
        daily = int(plan_info["daily_grant"])
        package_balance = int(acct["balance"]) - int(acct["topup_balance"])
        delta = daily - package_balance
        # 即使 delta=0 也要走一遍 charge 以刷新 last_daily_reset 日期，避免重复触发
        return await db.credit_charge(
            app_user_id, delta,
            reason="refill_daily", sku=plan,
            refill_pack={
                "reset_kind": "daily",
                "daily_grant": daily,
                "monthly_grant": 0,
            },
        )

    if plan in (pricing.PLAN_JOBSEEKER_PRO, pricing.PLAN_RECRUITER_PRO):
        last = acct.get("last_monthly_reset")
        if isinstance(last, str) and last:
            try:
                last_dt = date.fromisoformat(last)
                if (last_dt.year, last_dt.month) == (date.today().year, date.today().month):
                    return None
            except ValueError:
                pass
        monthly = int(plan_info["monthly_grant"])
        package_balance = int(acct["balance"]) - int(acct["topup_balance"])
        delta = monthly - package_balance
        return await db.credit_charge(
            app_user_id, delta,
            reason="refill_monthly", sku=plan,
            refill_pack={
                "reset_kind": "monthly",
                "monthly_grant": monthly,
                "daily_grant": 0,
            },
        )

    return None


# ── 扣费：业务调用入口 ──────────────────────────────────────────────────────

async def charge_for_sku(
    app_user_id: str,
    sku: str,
    count: int = 1,
    *,
    plan: str | None = None,
    meta: dict | None = None,
) -> dict:
    """统一扣费入口：
      1. 若给了 plan，先尝试 refill
      2. 计算总成本 = cost_for_sku(sku) * count
      3. 调 db.credit_charge() 扣费
      4. 余额不足时返回 allowed=False，业务方需引导用户升级/追加

    Returns: {
        'allowed': bool,
        'cost': int,
        'balance_before': int,
        'balance_after': int,
        'sku': str,
        'reason': str (可选，未知 SKU 时)
    }
    """
    if plan:
        await refill_if_due(app_user_id, plan)

    cost = pricing.cost_for_sku(sku) * max(int(count), 1)
    if cost <= 0:
        # 未配置成本的 SKU：放行但记录 audit ledger（delta=0），便于发现遗漏
        await db.credit_charge(
            app_user_id, 0,
            reason="consume_unknown", sku=sku, meta={"count": count, **(meta or {})},
        )
        return {"allowed": True, "cost": 0, "sku": sku, "reason": "unknown_sku",
                "balance_before": -1, "balance_after": -1}

    result = await db.credit_charge(
        app_user_id, -cost,
        reason=f"consume_{sku}", sku=sku,
        meta={"count": count, **(meta or {})},
    )
    return {**result, "cost": cost, "sku": sku}


async def charge_for_model(
    app_user_id: str,
    model_id: str,
    *,
    plan: str | None = None,
    meta: dict | None = None,
) -> dict:
    """LLM 对话扣费。模型 ID → SKU → 单价。"""
    sku = pricing.model_to_sku(model_id)
    return await charge_for_sku(
        app_user_id, sku, 1,
        plan=plan, meta={"model": model_id, **(meta or {})},
    )


# ── 入账：webhook 调用 ──────────────────────────────────────────────────────

async def grant_subscription(
    app_user_id: str,
    plan: str,
    *,
    stripe_event_id: str = "",
) -> dict | None:
    """订阅生效 / 续费：把套餐余量 reset 到 monthly_grant，并同步 monthly_grant 字段。

    幂等性：webhook 由 stripe_event_id 去重；同一事件不会重复 grant。"""
    plan_info = pricing.plan_def(plan)
    monthly = int(plan_info["monthly_grant"])
    daily = int(plan_info["daily_grant"])

    acct = await db.get_credit_account(app_user_id)
    if not acct:
        return None
    package_balance = int(acct["balance"]) - int(acct["topup_balance"])
    delta = monthly - package_balance

    return await db.credit_charge(
        app_user_id, delta,
        reason="grant_subscription", sku=plan,
        stripe_event_id=stripe_event_id,
        refill_pack={
            "reset_kind": "monthly",
            "monthly_grant": monthly,
            "daily_grant": daily,
        },
    )


async def grant_topup(
    app_user_id: str,
    credits: int,
    *,
    sku: str = "credit_100",
    stripe_event_id: str = "",
) -> dict | None:
    """一次性追加：计入 topup_balance（永不过期）。"""
    if credits <= 0:
        return None
    return await db.credit_charge(
        app_user_id, credits,
        reason="topup", sku=sku,
        stripe_event_id=stripe_event_id,
        is_topup=True,
    )


async def revoke_subscription_grant(
    app_user_id: str,
    *,
    stripe_event_id: str = "",
) -> dict | None:
    """订阅取消 / 退款：把套餐余量清零，topup 部分保留。"""
    acct = await db.get_credit_account(app_user_id)
    if not acct:
        return None
    package_balance = int(acct["balance"]) - int(acct["topup_balance"])
    if package_balance <= 0:
        return None
    return await db.credit_charge(
        app_user_id, -package_balance,
        reason="revoke_subscription", sku="",
        stripe_event_id=stripe_event_id,
        refill_pack={
            "reset_kind": "monthly",
            "monthly_grant": 0,
            "daily_grant": pricing.plan_def(pricing.PLAN_FREE)["daily_grant"],
        },
    )


# ── 查询：给 /billing/status 用 ──────────────────────────────────────────────

async def get_balance_summary(app_user_id: str) -> dict:
    """返回 credit + plan 概览，前端 Header / 我的页用。"""
    acct = await db.get_credit_account(app_user_id)
    sub = await db.get_subscription(app_user_id)
    plan = sub["plan"] if sub else pricing.PLAN_FREE
    plan_info = pricing.plan_def(plan)

    balance = int(acct["balance"]) if acct else 0
    topup = int(acct["topup_balance"]) if acct else 0
    return {
        "plan": plan,
        "plan_price_usd": plan_info["price_usd"],
        "subscription_status": sub.get("status") if sub else "",
        "current_period_end": sub.get("current_period_end") if sub else None,
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")) if sub else False,
        "credit_balance": balance,
        "credit_topup_balance": topup,
        "credit_package_balance": max(balance - topup, 0),
        "credit_monthly_grant": int(acct["monthly_grant"]) if acct else 0,
        "credit_daily_grant": int(acct["daily_grant"]) if acct else plan_info["daily_grant"],
        "last_daily_reset": acct.get("last_daily_reset") if acct else None,
        "last_monthly_reset": acct.get("last_monthly_reset") if acct else None,
        "lifetime_purchased": int(acct["lifetime_purchased"]) if acct else 0,
        "lifetime_consumed": int(acct["lifetime_consumed"]) if acct else 0,
        # PRD §限额
        "allow_batch": bool(plan_info["allow_batch"]),
        "batch_size_limit": plan_info["batch_size_limit"],
        "allow_reports": bool(plan_info["allow_reports"]),
        "bookmark_quota_jobseeker": plan_info["bookmark_quota_jobseeker"],
        "bookmark_quota_recruiter": plan_info["bookmark_quota_recruiter"],
    }
