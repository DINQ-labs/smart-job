"""Phase 2 mode detection —— scoring / 否定 / tie-break / tool hint 覆盖

detect_mode 现在的决策链：
  1. manual override（/mode xxx / /评估模式 / 切换到 X 模式）
  2. role_type=recruiter → recruiter
  3. keyword 打分（否定前缀不计分）+ tool_usage_hint 辅助打分 + sticky bonus
  4. tie-break 按 _MODE_TIEBREAK_ORDER（evaluate > apply > compare > interview > recruiter > search）
  5. 全 0 分 → current_mode 或 search
"""
from pathlib import Path
import sys

_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from modes.detect import (  # noqa: E402
    detect_mode,
    TOOL_MODE_AFFINITY,
    _score_keyword_matches,
    _score_tool_usage,
    _resolve_best_mode,
)


# ── Manual override ──────────────────────────────────────────────────────


def test_manual_override_wins_over_everything():
    """/mode 指令必须覆盖所有其他信号。"""
    assert detect_mode("/mode evaluate", current_mode="search",
                       role_type="recruiter") == "evaluate"
    assert detect_mode("/评估模式 帮我投", current_mode="apply",
                       role_type="") == "evaluate"


def test_manual_override_chinese_display_names():
    assert detect_mode("/mode interview", current_mode="search") == "interview"
    assert detect_mode("切换到投递模式", current_mode="") == "apply"


# ── Role routing ─────────────────────────────────────────────────────────


def test_recruiter_role_allows_evaluate_switch():
    """E2-4 更新：recruiter role 下允许 user 明示切到 evaluate 做候选人评估
    （对应 EVAL_RULES_RECRUITER 注入）。之前强制锁 recruiter 的行为已改。"""
    assert detect_mode("评估一下这个候选人", current_mode="search",
                       role_type="recruiter") == "evaluate"


def test_recruiter_role_sticky_on_empty_signal():
    """无信号时 recruiter role 仍兜底到 recruiter（不跌落到 search）"""
    assert detect_mode("继续", current_mode="recruiter",
                       role_type="recruiter") == "recruiter"


def test_recruiter_role_cold_start_is_recruiter():
    """冷启动（current_mode 空 / 无关键词）→ recruiter 默认"""
    assert detect_mode("今天", current_mode="", role_type="recruiter") == "recruiter"


def test_recruiter_role_can_switch_to_compare():
    """user 明示 compare → 切 compare（不被 recruiter 吃掉）"""
    assert detect_mode("对比这两个候选人", current_mode="",
                       role_type="recruiter") == "compare"


def test_recruiter_role_can_switch_to_apply_via_contact_keyword():
    """user 说"联系 TA" → apply（E2-4 允许 recruiter 切其他 mode）"""
    assert detect_mode("联系 TA", current_mode="recruiter",
                       role_type="recruiter") == "apply"


# ── Negation handling ────────────────────────────────────────────────────


def test_negation_skips_mode():
    """'不要评估' 不应触发 evaluate"""
    assert detect_mode("不要评估", current_mode="search") == "search"


def test_negation_skips_indirect():
    """'先不投' 'bu 想投' 不应触发 apply"""
    assert detect_mode("先不投", current_mode="search") == "search"
    assert detect_mode("别再评估了", current_mode="search") == "search"


def test_negation_sticky_preserves_current():
    """current_mode=apply + '不想投' → 保持 apply（sticky bonus）。
    用户表达情绪 ≠ 切模式，切模式需要明示（manual override 或 keyword 命中）"""
    assert detect_mode("不想投", current_mode="apply") == "apply"


# ── Keyword scoring ──────────────────────────────────────────────────────


def test_keyword_count_scoring():
    """多 keyword 命中的 mode 胜过单 keyword"""
    # evaluate 有 3 命中（评估、分析一下、匹配度）；apply 0
    assert detect_mode("评估 分析一下 匹配度", current_mode="") == "evaluate"


def test_expanded_keywords_cover_real_phrasing():
    """D1 Phase 2 补充了"查看详情"/"联系"等真实用户措辞"""
    # evaluate 扩充词
    assert detect_mode("看一下这个", current_mode="search") == "evaluate"
    assert detect_mode("介绍一下", current_mode="search") == "evaluate"
    # apply 扩充词
    assert detect_mode("联系他", current_mode="search") == "apply"
    assert detect_mode("找 TA 聊聊", current_mode="search") == "apply"
    assert detect_mode("帮我私信这个人", current_mode="search") == "apply"


# ── Tool usage hint ──────────────────────────────────────────────────────


def test_tool_usage_hint_nudges_mode():
    """user 消息模糊，但上一轮调了多个 evaluate 亲和工具 → evaluate"""
    hint = {"boss_get_job_detail", "boss_get_cached_job"}
    assert detect_mode("继续", current_mode="search",
                       tool_usage_hint=hint) == "evaluate"


def test_tool_usage_hint_below_threshold_ignored():
    """只出现 1 次同类亲和工具不足以切模式（阈值 ≥2 种）"""
    hint = {"boss_get_job_detail"}  # 只 1 个 evaluate 工具
    assert detect_mode("继续", current_mode="search",
                       tool_usage_hint=hint) == "search"


def test_tool_usage_hint_apply_affinity():
    hint = {"boss_start_chat", "boss_send_message"}
    assert detect_mode("ok", current_mode="search",
                       tool_usage_hint=hint) == "apply"


def test_tool_affinity_map_covers_three_platforms():
    """affinity map 必须覆盖三平台的关键写操作工具（防止遗漏）"""
    apply_tools = [t for t, m in TOOL_MODE_AFFINITY.items() if m == "apply"]
    assert any(t.startswith("boss_") for t in apply_tools)
    assert any(t.startswith("linkedin_") for t in apply_tools)
    assert any(t.startswith("indeed_") for t in apply_tools)


def test_compare_interview_intentionally_have_no_tool_affinity():
    """审核补丁 #F1：compare / interview 没有对应工具是有意的设计。

    这两个 mode 是认知动作（横向对比 / 面试准备），只能由 keyword 进入。
    ladder 包含它们（离开可升级到 apply），affinity 不包含它们（没有工具
    能独立代表它们）。如果某天加了这类工具，在此断言处对应放宽即可。"""
    affinity_modes = set(TOOL_MODE_AFFINITY.values())
    assert "compare" not in affinity_modes
    assert "interview" not in affinity_modes
    # 确保三平台都没有「意外」把工具映射到这两个 mode
    for tool, mode in TOOL_MODE_AFFINITY.items():
        assert mode in {"apply", "evaluate", "recruiter"}, (
            f"{tool} → {mode}，只允许 apply/evaluate/recruiter"
        )


def test_compare_keyword_entry_then_upgrade_to_apply():
    """用户说"对比这两个职位" → compare（keyword 进入）。本次测试验证
    进入 compare 后，如果本轮调用 apply 亲和工具，ladder 能正确把
    inferred_mode 从 compare 升到 apply（agent_loop 侧逻辑，详见
    test_mode_ladder.py 场景 8；此处只是端到端关键词入口检查）。"""
    assert detect_mode("对比这两个职位哪个更好", current_mode="search") == "compare"
    assert detect_mode("面试准备", current_mode="search") == "interview"


# ── Tie-break & sticky ───────────────────────────────────────────────────


def test_tie_break_prefer_evaluate_over_apply():
    """evaluate vs apply 平分时取 evaluate（漏斗靠前）"""
    # evaluate 1 命中 vs apply 1 命中 → evaluate
    assert detect_mode("评估 申请", current_mode="") == "evaluate"


def test_sticky_on_empty_signal():
    """无 keyword / 无 hint → sticky current_mode"""
    assert detect_mode("好的继续", current_mode="evaluate") == "evaluate"
    assert detect_mode("嗯", current_mode="apply") == "apply"


def test_default_to_search_when_cold_start():
    """current_mode='' + 无信号 → search"""
    assert detect_mode("ok", current_mode="", role_type="") == "search"
    assert detect_mode("", current_mode="") == "search"


# ── Scenario: search → view detail → contact ─────────────────────────────


def test_scenario_search_then_view_and_contact():
    """完整场景：Turn 2 起点 + mid-turn 升级路径的 detect_mode 行为"""
    # Turn 1: 搜索
    assert detect_mode("帮我搜 Python 工程师", current_mode="",
                       role_type="") == "search"

    # Turn 2: user 明示 "联系他" → 直接命中 apply（不需要 mid-turn）
    assert detect_mode(
        "查看这个人详情，联系他",
        current_mode="search",
        role_type="",
        tool_usage_hint={"boss_search_jobs"},
    ) == "apply"

    # Turn 3: user 只说 "还有吗"，上轮 tool_usage 含多个 apply 工具 → apply
    assert detect_mode(
        "还有吗",
        current_mode="apply",
        role_type="",
        tool_usage_hint={"boss_start_chat", "boss_send_message"},
    ) == "apply"


# ── Lower-level helpers ──────────────────────────────────────────────────


def test_score_keyword_matches_counts_hits():
    scores = _score_keyword_matches("评估 匹配度")
    assert scores.get("evaluate", 0) >= 2


def test_score_keyword_matches_respects_negation():
    assert _score_keyword_matches("不要评估").get("evaluate", 0) == 0


def test_score_tool_usage_threshold():
    # 1 个 evaluate 工具 → 不计
    assert _score_tool_usage({"boss_get_job_detail"}) == {}
    # 2 个 evaluate 工具 → 计 1 分
    assert _score_tool_usage({"boss_get_job_detail", "boss_get_cached_job"}) \
        == {"evaluate": 1}


def test_resolve_best_mode_weights_keyword_over_tool():
    """keyword 权重 2×，tool 权重 1× —— 确保 keyword 信号更重"""
    # evaluate 1 keyword 命中（2 分） vs apply 1 tool 命中（1 分）→ evaluate
    best = _resolve_best_mode(
        kw_scores={"evaluate": 1},
        tool_scores={"apply": 1},
        current_mode="",
    )
    assert best == "evaluate"
