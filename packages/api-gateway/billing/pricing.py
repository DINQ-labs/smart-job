"""定价 / Plan / Credit 费率 —— 单一来源 SoT。

PRD §订阅+Credit / §8 对应：
- 三个套餐：free / jobseeker_pro($9/月) / recruiter_pro($29/月)
- 一次性追加：credit_100（$2 拿 100cr，永不过期）
- Credit 模型分档：mini=1 / standard=3 / opus=8
- 批量分析：2cr/人；批量招呼：1cr/条
"""
from __future__ import annotations

import os
from typing import Final


# ── Plan 标识 ────────────────────────────────────────────────────────────────
PLAN_FREE: Final            = "free"
PLAN_JOBSEEKER_PRO: Final   = "jobseeker_pro"
PLAN_RECRUITER_PRO: Final   = "recruiter_pro"

ALL_PLANS: Final = (PLAN_FREE, PLAN_JOBSEEKER_PRO, PLAN_RECRUITER_PRO)


# ── Plan 定义 ────────────────────────────────────────────────────────────────
# 每个 plan 给出：
#   monthly_grant       套餐每月配额（free=0，月度池在 refill 时 grant）
#   daily_grant         免费版每日配额（pro 用户 daily_grant=0）
#   bookmark_quota      收藏上限（None=无限）
#   allow_batch         是否允许批量任务
#   batch_size_limit    单次批量上限（None=无上限）
#   allow_reports       是否允许生成评估报告/对比报告
PLANS: Final = {
    PLAN_FREE: {
        "monthly_grant": 0,
        "daily_grant": 20,
        "bookmark_quota_jobseeker": 20,
        "bookmark_quota_recruiter": 10,
        "allow_batch": False,
        "batch_size_limit": 0,
        "allow_reports": False,
        "price_usd": 0,
    },
    PLAN_JOBSEEKER_PRO: {
        "monthly_grant": 500,
        "daily_grant": 0,
        "bookmark_quota_jobseeker": None,
        "bookmark_quota_recruiter": None,
        "allow_batch": True,
        "batch_size_limit": 50,
        "allow_reports": False,
        "price_usd": 9,
    },
    PLAN_RECRUITER_PRO: {
        "monthly_grant": 2000,
        "daily_grant": 0,
        "bookmark_quota_jobseeker": None,
        "bookmark_quota_recruiter": None,
        "allow_batch": True,
        "batch_size_limit": 100,
        "allow_reports": True,
        "price_usd": 29,
    },
}


# ── Credit SKU → 费率 ───────────────────────────────────────────────────────
# SKU 命名约定：
#   chat_<tier>        AI 对话单条消息（按模型档位）
#   batch_<task>       后台批量任务，按 item 计费
#   action_<name>      其它消费动作（如 reanalyze）
CREDIT_COSTS: Final[dict[str, int]] = {
    # 对话档位（参 PRD §8.2）
    "chat_mini":      1,   # GPT-4o-mini / Glm-5-turbo 等廉价模型
    "chat_standard":  3,   # Claude Sonnet / GPT-4o
    "chat_opus":      8,   # Claude Opus
    # 批量任务（按 item 单位）
    "batch_analyze":  2,   # 批量分析职位 / 候选人
    "batch_greeting": 1,   # 批量发招呼
    "batch_apply":    2,   # 批量投递
    # 单次动作
    "action_reanalyze": 2,  # 重新分析收藏项
    "action_screen":    2,  # 单条筛选
}


# ── 模型名 → SKU 映射 ───────────────────────────────────────────────────────
# agent-gateway 调 LLM 时上报模型 ID，billing 侧映射到 SKU 计费档位。
# 未匹配的模型默认归 chat_standard，避免漏计费。
MODEL_TO_SKU: Final[dict[str, str]] = {
    # mini 档
    "gpt-4o-mini":           "chat_mini",
    "z-ai/glm-5-turbo":      "chat_mini",
    "claude-haiku":          "chat_mini",
    "claude-haiku-4-5":      "chat_mini",
    # standard 档
    "gpt-4o":                "chat_standard",
    "claude-sonnet":         "chat_standard",
    "claude-sonnet-4-6":     "chat_standard",
    "claude-sonnet-4-5":     "chat_standard",
    # opus 档
    "claude-opus":           "chat_opus",
    "claude-opus-4-6":       "chat_opus",
    "claude-opus-4-7":       "chat_opus",
}


def model_to_sku(model_id: str) -> str:
    """模型 ID → SKU。未知模型按 chat_standard 计费（保守上限）。"""
    if not model_id:
        return "chat_standard"
    if model_id in MODEL_TO_SKU:
        return MODEL_TO_SKU[model_id]
    # 前缀 fallback：claude-opus-* 一律归 opus 档
    lid = model_id.lower()
    if "opus" in lid:
        return "chat_opus"
    if "sonnet" in lid or "gpt-4o" in lid:
        return "chat_standard"
    if "haiku" in lid or "mini" in lid or "glm" in lid:
        return "chat_mini"
    return "chat_standard"


def cost_for_sku(sku: str) -> int:
    """SKU → 单位 credit 成本。未知 SKU 返回 0（不扣费），调用方负责报警。"""
    return int(CREDIT_COSTS.get(sku, 0))


def plan_def(plan: str) -> dict:
    return PLANS.get(plan, PLANS[PLAN_FREE])


def bookmark_quota(plan: str, role: str) -> int | None:
    """根据 plan 和 role 返回收藏上限。None=无限。"""
    p = plan_def(plan)
    if role == "jobseeker":
        return p["bookmark_quota_jobseeker"]
    if role == "recruiter":
        return p["bookmark_quota_recruiter"]
    return p["bookmark_quota_jobseeker"]


# ── Stripe Price ID → Plan / SKU 映射 ────────────────────────────────────────
# Stripe 的 price_id 是字符串（如 price_1Q...），在 webhook 里反查我们的 plan。
# 这些 env 在 .env 里配置，部署时切换 test / live key。
def stripe_price_to_plan_or_sku() -> dict[str, tuple[str, str]]:
    """生成 price_id → (kind, value) 映射。
    kind='subscription' 时 value 是 plan_id；kind='topup' 时 value 是 sku 数量描述。"""
    out: dict[str, tuple[str, str]] = {}
    pid_js = os.getenv("STRIPE_PRICE_JOBSEEKER_PRO", "").strip()
    pid_rc = os.getenv("STRIPE_PRICE_RECRUITER_PRO", "").strip()
    pid_tu = os.getenv("STRIPE_PRICE_CREDIT_100", "").strip()
    if pid_js:
        out[pid_js] = ("subscription", PLAN_JOBSEEKER_PRO)
    if pid_rc:
        out[pid_rc] = ("subscription", PLAN_RECRUITER_PRO)
    if pid_tu:
        out[pid_tu] = ("topup", "credit_100")
    return out


# ── 一次性追加包定义 ─────────────────────────────────────────────────────────
TOPUP_PACKS: Final = {
    "credit_100": {"credits": 100, "price_usd": 2},
}
