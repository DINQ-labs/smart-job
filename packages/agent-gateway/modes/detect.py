"""Mode detection engine — scoring-based with sticky fallback.

Detection priority (Phase 2):
  1. Manual override: "/mode evaluate" or "/评估模式" or "切换到 X 模式"
  2. role_type='recruiter' routing
  3. Keyword scoring (每个 mode 命中次数，负面前缀扣除)
     + tool_usage_hint scoring (上一轮用过的亲和工具加分，阈值 ≥2 才算)
     + sticky bonus（current_mode 加 0.5）
  4. Tie-break order: evaluate > apply > compare > interview > recruiter > search
     （"决策在投之前"漏斗原则）
  5. Sticky: 全 0 分时保持 current_mode
  6. Default: "search"

Mid-turn mode upgrade（agent_loop 侧实现，不在本文件）：
  每个 tool_use block 之后根据 TOOL_MODE_AFFINITY 升级 current_mode
  （search → evaluate → apply），emit mode_detected 事件给前端实时切 badge。
"""
from __future__ import annotations

import re

# ── Mode keyword patterns ────────────────────────────────────────────────────
# 每 mode 多个短语；命中次数作为得分。所有关键词 substring + case-insensitive
# 匹配。关键词尽量具体，避免和其他 mode 产生假阳性。
#
# Phase 2 补充：
#   evaluate += 查看详情 / 详情 / 介绍一下 / 了解一下
#   apply    += 联系 / 联系他 / 联系 TA / 找他聊 / 私信 / 主动沟通 / 打声招呼
# 覆盖"search → 查看 → 联系"的真实用户用词。

_MODE_KEYWORDS: dict[str, list[str]] = {
    "evaluate": [
        "评估", "评价", "打分", "分析这个职位", "分析一下", "匹配度",
        "适不适合", "值不值得投", "值得投吗", "帮我分析",
        "查看详情", "详情", "看一下这个", "介绍一下", "了解一下",
        "evaluate", "score this", "how good is",
    ],
    "apply": [
        "打招呼", "投递", "申请", "发消息给", "开聊", "帮我投",
        "联系", "联系他", "联系 ta", "找他聊", "找 ta 聊", "私信",
        "主动沟通", "打声招呼",
        "apply", "send greeting", "start chat",
    ],
    "interview": [
        "面试准备", "面试", "star", "准备面试", "模拟面试",
        "interview prep", "prepare for interview", "mock interview",
    ],
    "compare": [
        "对比", "比较", "横向对比", "哪个好", "哪个更好",
        "compare", "which is better", "side by side",
    ],
}

# Tie-break 顺序：靠前的 mode 在平分时胜出。"决策在投之前"原则 —— 用户看起来
# 还在评估阶段就不要过早跳到 apply。
_MODE_TIEBREAK_ORDER = ["evaluate", "apply", "compare", "interview", "recruiter", "search"]


# ── Tool → mode affinity（用于 tool_usage_hint 打分 + agent_loop mid-turn 升级）──
#
# 说明（审核补丁 #F1）：compare / interview 两个 mode **故意没有** affinity 映射。
# 这两个 mode 代表的是纯粹的认知动作（横向对比职位 / 面试准备），没有对应的
# MCP 工具能独立代表它们 —— 查看多份职位详情就映射到 evaluate，真去投递 / 沟通
# 就映射到 apply。因此 compare / interview 只能由 turn 开头的**关键词检测**进入
# （_MODE_KEYWORDS 里有"对比 / 面试"等词），一旦进入，后续用 apply 亲和工具
# 仍会正常触发 mid-turn 升级到 apply（见 _MODE_LADDER 中 compare=1、interview=2，
# apply=2；test_mode_ladder.py 场景 8 覆盖 compare→apply 这条链路）。
#
# 结论：ladder 包含 compare/interview 是为了让从它们起步的 turn 也能享受
# mid-turn 升级**离开**；affinity 留空是因为没有工具能合理**进入**它们。

TOOL_MODE_AFFINITY: dict[str, str] = {
    # apply：真正触发沟通 / 投递动作
    "boss_start_chat": "apply",
    "boss_send_message": "apply",
    "boss_contact_candidate": "apply",
    "linkedin_apply_job": "apply",
    "linkedin_send_message": "apply",
    "linkedin_reply_to_conversation": "apply",
    "linkedin_recruiter_send_inmail": "apply",
    "indeed_apply_job": "apply",
    "indeed_employer_send_message": "apply",
    # evaluate：查看职位详情（读类行为）
    "boss_get_job_detail": "evaluate",
    "boss_get_cached_job": "evaluate",
    "boss_view_geek_detail": "evaluate",
    "linkedin_get_job_detail": "evaluate",
    "linkedin_get_profile": "evaluate",
    "indeed_get_job_detail": "evaluate",
    # recruiter：招聘方专属搜 / 看候选人
    "boss_rec_geek_list": "recruiter",
    "boss_search_candidates": "recruiter",
    "boss_boss_enter": "recruiter",
    "linkedin_recruiter_search": "recruiter",
    "linkedin_recruiter_get_profile": "recruiter",
    "indeed_employer_search_candidates": "recruiter",
    "indeed_employer_get_candidate": "recruiter",
}


# ── Manual override + negation ─────────────────────────────────────────────

_MANUAL_OVERRIDE_RE = re.compile(
    r"^/mode\s+(\w+)"          # /mode evaluate
    r"|^/(\w+)模式"            # /评估模式
    r"|^切换到(\w+)模式",      # 切换到评估模式
    re.IGNORECASE,
)

# 把 Chinese 模式显示名映射到内部 name
_DISPLAY_TO_NAME = {
    "搜索": "search",
    "评估": "evaluate",
    "投递": "apply",
    "面试": "interview",
    "对比": "compare",
    "比较": "compare",
    "招聘": "recruiter",
}

# 出现在某个 keyword 之前的负面前缀 → 该 keyword 不计分。窗口 4 个字符
# （足够覆盖"不 / 别 / 不要 / 暂时不 / 不想 / 先不 / 还不"）。
_NEGATION_WINDOW = 4
_NEGATION_PATTERNS = ("不", "别", "勿", "非")


def _is_negated(text: str, idx: int) -> bool:
    """检查 text[idx] 前 4 字符窗口内是否有否定词。"""
    start = max(0, idx - _NEGATION_WINDOW)
    window = text[start:idx]
    for p in _NEGATION_PATTERNS:
        if p in window:
            return True
    return False


# ── Scoring + resolution ───────────────────────────────────────────────────


def _score_keyword_matches(text: str) -> dict[str, int]:
    """返回 {mode_name: score}。一个 keyword 命中一次得 1 分；带否定前缀时不计分。"""
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for mode_name, keywords in _MODE_KEYWORDS.items():
        hits = 0
        for kw in keywords:
            kw_l = kw.lower()
            start = 0
            while True:
                idx = text_lower.find(kw_l, start)
                if idx == -1:
                    break
                if not _is_negated(text_lower, idx):
                    hits += 1
                start = idx + len(kw_l)
        if hits:
            scores[mode_name] = hits
    return scores


def _score_tool_usage(
    usage: set[str] | frozenset[str] | list[str] | None,
    threshold: int = 2,
) -> dict[str, int]:
    """返回 {mode_name: score}。usage 是上一轮用过的工具名集合。

    注意：当前 usage 是集合（去重），所以同一工具不会重复计分。若某 mode 的
    亲和工具在 usage 里出现 ≥threshold 种，该 mode +1 分。这避免了用户偶然
    点一次 detail 就被切到 evaluate 的抖动。
    """
    if not usage:
        return {}
    per_mode_count: dict[str, int] = {}
    for tool in usage:
        m = TOOL_MODE_AFFINITY.get(tool)
        if m:
            per_mode_count[m] = per_mode_count.get(m, 0) + 1
    return {m: 1 for m, n in per_mode_count.items() if n >= threshold}


def _resolve_best_mode(
    kw_scores: dict[str, int],
    tool_scores: dict[str, int],
    current_mode: str,
) -> str:
    """融合打分：keyword 权重 2，tool hint 权重 1，sticky current_mode +0.5。
    全 0 分 → 返回 current_mode 或 search。tie 按 _MODE_TIEBREAK_ORDER 取优先的。
    """
    combined: dict[str, float] = {}
    for mode, s in kw_scores.items():
        combined[mode] = combined.get(mode, 0) + s * 2
    for mode, s in tool_scores.items():
        combined[mode] = combined.get(mode, 0) + s * 1
    if current_mode and current_mode in combined:
        combined[current_mode] += 0.5
    if not combined:
        return current_mode or "search"

    best_score = max(combined.values())
    tied = [m for m, s in combined.items() if s == best_score]
    if len(tied) == 1:
        return tied[0]
    # Tie-break：按 _MODE_TIEBREAK_ORDER 取第一个
    for m in _MODE_TIEBREAK_ORDER:
        if m in tied:
            return m
    return tied[0]


# ── Manual override ────────────────────────────────────────────────────────


def _parse_manual_override(text: str) -> str | None:
    m = _MANUAL_OVERRIDE_RE.match(text.strip())
    if not m:
        return None
    raw = m.group(1) or m.group(2) or m.group(3)
    if not raw:
        return None
    raw = raw.lower().strip()
    if raw in ("search", "evaluate", "apply", "interview", "compare", "recruiter"):
        return raw
    return _DISPLAY_TO_NAME.get(raw)


# ── Public API ─────────────────────────────────────────────────────────────


def detect_mode(
    user_text: str,
    current_mode: str = "search",
    role_type: str = "",
    tool_usage_hint: set[str] | frozenset[str] | list[str] | None = None,
) -> str:
    """Detect mode for this turn.

    Args:
      user_text: 用户本轮消息原文
      current_mode: 上一轮结束时的 mode（含 mid-turn 升级的结果）
      role_type: "jobseeker" / "recruiter" / ""
      tool_usage_hint: 上一轮用过的工具名集合（来自 session.last_turn_tool_usage）

    Returns: mode name string。
    """
    # 1. Manual override（最高优先）
    manual = _parse_manual_override(user_text)
    if manual:
        return manual

    # 2. Scoring：keyword + tool hint
    kw_scores = _score_keyword_matches(user_text)
    tool_scores = _score_tool_usage(tool_usage_hint)

    # 3. role_type routing
    # E2-4 更新：recruiter role 不再强制锁 recruiter mode —— 若 user 明示要
    # evaluate / interview / compare / apply（keyword 命中），允许切过去
    # （例如用户说"评估这个候选人"应该进 evaluate；本轮 EVAL_RULES_RECRUITER
    # 会按 role 自动注入招聘方版评估规则）。
    # 未明示时才回落到 recruiter 作为默认起点。
    if role_type == "recruiter":
        return _resolve_best_mode(kw_scores, tool_scores, current_mode or "recruiter")

    # 4. 其他 role：按 normal scoring + sticky current_mode
    return _resolve_best_mode(kw_scores, tool_scores, current_mode or "")
