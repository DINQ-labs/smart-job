"""D1 Prompt ADDON 层对称化 — 结构与去重验收

共用行为规则（LOGIN_LINK_RULES / EXT_NOT_CONNECTED_RULE / RESUME_PRIORITY_RULES）
从 Boss/LinkedIn/Indeed 三份 ADDON 里抽出，三平台走同一条 compose 路径。本文件
验证抽取后的结构契约：

1. 三个共用片段存在、不引用任何平台工具名
2. BOSS_ADDON 是 BASE_TOOLS_PROMPT 的向后兼容别名
3. 三平台 compose 输出均包含共用片段
4. Boss mode 里原先的 AGENT_INTRO + BASE_TOOLS_PROMPT 头部被 _strip 剥干净，
   不出现两份 Boss 工具清单（重复检测）
5. LinkedIn/Indeed ADDON 不再包含"未登录时的对话处理"段（抽到共用规则）
6. 跨平台示例 CROSS_PLATFORM_SCENARIOS 仍然只对 cross 模式注入（D2 不回归）
"""
from pathlib import Path
import sys

_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from modes import get_mode  # noqa: E402
from modes.base import (  # noqa: E402
    BASE_TOOLS_PROMPT,
    BOSS_ADDON,
    EVAL_RULES,
    EVAL_RULES_JOBSEEKER,
    EVAL_RULES_RECRUITER,
    EXT_NOT_CONNECTED_RULE,
    INDEED_ADDON,
    INDEED_EMPLOYER_ADDON,
    LINKEDIN_ADDON,
    LOGIN_LINK_RULES,
    RESUME_PRIORITY_RULES,
    compose_system_prompt,
)


# ── Shared fragments ──────────────────────────────────────────────────────


class TestSharedFragments:

    def test_login_link_rules_has_no_platform_tool_names(self):
        """LOGIN_LINK_RULES 必须是纯行为规则，不含具体 boss_/linkedin_/indeed_ 工具名"""
        # 允许出现工具名占位符（如 "*_check_login"），但不允许具体前缀
        forbidden = ["boss_check_login", "linkedin_check_login", "indeed_check_login",
                     "boss_login", "linkedin_apply_job", "indeed_search_jobs"]
        for name in forbidden:
            assert name not in LOGIN_LINK_RULES, (
                f"LOGIN_LINK_RULES 不应包含具体工具名 {name!r}（应该用 *_check_login 占位）"
            )

    def test_ext_not_connected_rule_exists(self):
        assert "扩展未连接" in EXT_NOT_CONNECTED_RULE
        assert len(EXT_NOT_CONNECTED_RULE) > 50

    def test_resume_priority_rules_exists(self):
        assert "简历" in RESUME_PRIORITY_RULES or "偏好" in RESUME_PRIORITY_RULES
        # 应该提到"从零"（保留原有行为性断言）
        assert "从零" in RESUME_PRIORITY_RULES


# ── Back-compat alias ─────────────────────────────────────────────────────


class TestBossAddonAlias:

    def test_boss_addon_is_base_tools_prompt(self):
        """modes/*.py 仍 import BASE_TOOLS_PROMPT，别名必须保持有效"""
        assert BOSS_ADDON is BASE_TOOLS_PROMPT


# ── ADDON clean-up ────────────────────────────────────────────────────────


class TestAddonsCleanedUp:

    def test_linkedin_addon_drops_dedicated_login_section(self):
        """LINKEDIN_ADDON 不再有独立的"未登录时的对话处理"小节（抽到共用规则）"""
        assert "### 未登录时的对话处理" not in LINKEDIN_ADDON

    def test_indeed_addon_drops_dedicated_login_section(self):
        assert "### 未登录时的对话处理" not in INDEED_ADDON

    def test_linkedin_keeps_its_detail_vs_apply_warning(self):
        """按 D1 方案，'查看详情 ≠ 投递' 的平台陷阱留在 LinkedIn/Indeed ADDON 里"""
        assert "查看详情 ≠ 投递" in LINKEDIN_ADDON

    def test_indeed_addon_is_no_apply_policy(self):
        """2026-04-25 起 Indeed agent 不做投递 —— ADDON 不应再有 apply 流程，
        而是显式声明"投递功能已下线 / 不做投递" 的产品策略。"""
        assert "Indeed 不做投递" in INDEED_ADDON or "投递功能已下线" in INDEED_ADDON
        # 别让旧的 apply 工具名再被 LLM 在 prompt 里看到
        assert "indeed_apply_job" not in INDEED_ADDON
        assert "indeed_get_apply_form" not in INDEED_ADDON
        assert "indeed_fill_fields" not in INDEED_ADDON
        assert "indeed_prepare_apply" not in INDEED_ADDON

    def test_no_mode_prompts_instruct_indeed_apply(self):
        """没有任何 mode prompt 应该指导 LLM 调"调用 indeed_apply_job"等工具
        （它们已被 _INDEED_NO_GO 从工具集移除，调了会 fail）。
        允许出现工具名作为"⚠️ 不要调"的负面引用 —— 这种引用本身有"不要"
        / "已下线" / "已移除" 等限定词，模型不会去尝试调。"""
        from modes import get_mode

        FORBIDDEN_TOOLS = [
            "indeed_apply_job", "indeed_get_apply_form", "indeed_fill_fields",
            "indeed_prepare_apply", "indeed_check_applied",
            "indeed_get_resume_section", "indeed_update_job_app_status",
        ]
        # 出现这些"提示词"附近的工具名是合法的（说明是负面引用而非指令）
        NEGATIVE_HINTS = [
            "不要", "不做", "已下线", "已移除", "DINQ 不", "禁止", "已被",
            "无法", "暂时不", "已废弃",
        ]

        for mode_name in ["search", "evaluate", "apply", "interview", "compare"]:
            mode = get_mode(mode_name)
            prompt = mode.system_prompt
            for tool in FORBIDDEN_TOOLS:
                idx = prompt.find(tool)
                while idx != -1:
                    # 看工具名前后 80 字符内有无负面提示词
                    window_start = max(0, idx - 80)
                    window_end = min(len(prompt), idx + len(tool) + 80)
                    window = prompt[window_start:window_end]
                    has_negative = any(h in window for h in NEGATIVE_HINTS)
                    assert has_negative, (
                        f"mode={mode_name} 里出现 {tool!r}（位置 {idx}）但前后无负面"
                        f"限定词。这会让 LLM 把它当指令调用。窗口内容:\n{window!r}"
                    )
                    idx = prompt.find(tool, idx + 1)

    def test_build_job_list_card_always_renders(self):
        """2026-04-25 修订：不再后端硬过滤低分职位。card 永远渲染所有有效职位
        让用户在 UI 层一直有 checkbox / 详情 / 投递等可点击 action；推荐
        逻辑（"<60 不要推荐"）只在 agent 文字回复层面生效。
        matched 字段 = ≥60 的数量，作为 UI 上"推荐数量"提示。"""
        from agent_loop import _build_job_list_card

        # 全 < 60：card 仍然渲染，matched=0 让前端 UI 表达"扫了 N 个但都不够好"
        all_low = {"jobs": [
            {"jobKey": f"k{i}", "title": f"T{i}", "company": "C", "location": "X",
             "salary": "", "match_percent": p}
            for i, p in enumerate([35, 28, 22, 12, 5])
        ]}
        card = _build_job_list_card("indeed_search_jobs", all_low)
        assert card is not None, "全 < 60 也要渲染 card（让用户有可点击入口）"
        assert card["scanned"] == 5
        assert len(card["jobs"]) == 5, "全部 5 个都展示在 card 里"
        assert card["matched"] == 0, "matched = ≥60 的数量"

        # 混合：≥60 跟 <60 共存，全部展示
        mixed = {"jobs": [
            {"jobKey": "a", "title": "A", "company": "C", "location": "X",
             "salary": "", "match_percent": 85},
            {"jobKey": "b", "title": "B", "company": "C", "location": "X",
             "salary": "", "match_percent": 30},
            {"jobKey": "c", "title": "C", "company": "C", "location": "X",
             "salary": "", "match_percent": 65},
        ]}
        card = _build_job_list_card("indeed_search_jobs", mixed)
        assert card is not None
        assert card["scanned"] == 3
        assert len(card["jobs"]) == 3, "三个都进 card"
        assert card["matched"] == 2, "≥60 的有 A(85) 和 C(65)"

        # 无评分（用户没传简历）：跟之前一样，全展示，matched=0
        unscored = {"jobs": [
            {"jobKey": "x", "title": "X", "company": "C", "location": "L",
             "salary": "", "match_percent": None},
            {"jobKey": "y", "title": "Y", "company": "C", "location": "L",
             "salary": "", "match_percent": None},
        ]}
        card = _build_job_list_card("indeed_search_jobs", unscored)
        assert card is not None
        assert len(card["jobs"]) == 2
        assert card["matched"] == 0

    def test_addons_still_have_their_login_url(self):
        """URL 本体是平台机制，仍在各 ADDON 里保留"""
        assert "linkedin.com/login" in LINKEDIN_ADDON
        assert "secure.indeed.com" in INDEED_ADDON
        assert "zhipin.com/web/user" in BOSS_ADDON


# ── compose_system_prompt: three-platform symmetric path ──────────────────


class TestComposeInjectsSharedRules:

    def test_boss_prompt_has_all_three_shared_fragments(self):
        prompt = compose_system_prompt("any mode", platform="boss")
        assert "登录链接规则（三平台通用" in prompt
        assert "扩展未连接处理（三平台通用" in prompt
        assert "简历与偏好优先（三平台通用" in prompt

    def test_linkedin_prompt_has_all_three_shared_fragments(self):
        prompt = compose_system_prompt("any mode", platform="linkedin")
        assert "登录链接规则（三平台通用" in prompt
        assert "扩展未连接处理（三平台通用" in prompt
        assert "简历与偏好优先（三平台通用" in prompt

    def test_indeed_prompt_has_all_three_shared_fragments(self):
        prompt = compose_system_prompt("any mode", platform="indeed")
        assert "登录链接规则（三平台通用" in prompt
        assert "扩展未连接处理（三平台通用" in prompt
        assert "简历与偏好优先（三平台通用" in prompt

    def test_cross_prompt_has_all_three_shared_fragments(self):
        prompt = compose_system_prompt("any mode", platform="cross")
        assert "登录链接规则（三平台通用" in prompt
        assert "扩展未连接处理（三平台通用" in prompt
        assert "简历与偏好优先（三平台通用" in prompt


# ── compose_system_prompt: no duplication ─────────────────────────────────


class TestNoDuplication:
    """Phase 2 之后 mode 文件不再内嵌 AGENT_INTRO + BASE_TOOLS_PROMPT，
    所以 BOSS_ADDON 永远只出现一次。"""

    def test_boss_mode_does_not_double_include_boss_addon(self):
        search_mode = get_mode("search")
        prompt = compose_system_prompt(search_mode.system_prompt, platform="boss")
        # "## 多会话支持" 是 BOSS_ADDON 的顶部段落标题，理应只出现一次
        assert prompt.count("## 多会话支持") == 1
        assert prompt.count("## 令牌链（自动维护") == 1

    def test_mode_files_no_longer_embed_base_tools(self):
        """mode 文件的 system_prompt 本身不应含 BOSS_ADDON 内容（Phase 2 拆净后）"""
        for mode_name in ("search", "evaluate", "apply", "interview", "compare", "recruiter"):
            md = get_mode(mode_name)
            assert "## 多会话支持" not in md.system_prompt, (
                f"{mode_name} mode 的 system_prompt 还嵌着 BOSS_ADDON 顶部段"
            )


# ── Phase 2：LinkedIn/Indeed mode differentiation ─────────────────────────


class TestMultiPlatformModeDifferentiation:
    """Phase 2 后 LinkedIn/Indeed 也走 mode_prompt 路径，不同 mode 输出不同 prompt"""

    def test_linkedin_search_vs_interview_differ(self):
        s = compose_system_prompt(get_mode("search").system_prompt, platform="linkedin")
        i = compose_system_prompt(get_mode("interview").system_prompt, platform="linkedin")
        assert s != i, "LinkedIn 下 search 和 interview 应产出不同 prompt"
        # interview 必有 STAR，search 不应有
        assert "STAR" in i
        assert "STAR" not in s

    def test_indeed_search_vs_evaluate_differ(self):
        s = compose_system_prompt(get_mode("search").system_prompt, platform="indeed")
        e = compose_system_prompt(get_mode("evaluate").system_prompt, platform="indeed")
        assert s != e
        # evaluate 必有"深度职位评估"这个独有段
        assert "深度职位评估" in e
        assert "深度职位评估" not in s

    def test_indeed_interview_has_star(self):
        """Phase 1 之前 LinkedIn/Indeed 下 mode_prompt 被跳过，interview 也没 STAR。
        Phase 2 后应该有。"""
        prompt = compose_system_prompt(
            get_mode("interview").system_prompt, platform="indeed",
        )
        assert "STAR" in prompt


# ── D2 regression ─────────────────────────────────────────────────────────


class TestCrossPlatformScenariosRegression:
    """D2 的"仅 cross 注入"行为不能被 D1 改动打破"""

    def test_cross_still_injects_scenarios(self):
        prompt = compose_system_prompt("mode", platform="cross")
        assert "## 跨平台场景（多平台 Agent 专属）" in prompt

    def test_boss_does_not_inject_scenarios(self):
        prompt = compose_system_prompt("mode", platform="boss")
        assert "## 跨平台场景（多平台 Agent 专属）" not in prompt

    def test_linkedin_does_not_inject_scenarios(self):
        prompt = compose_system_prompt("mode", platform="linkedin")
        assert "## 跨平台场景（多平台 Agent 专属）" not in prompt


# ── Size sanity check ────────────────────────────────────────────────────


class TestSizeIsReasonable:

    def test_boss_prompt_not_bloated(self):
        """Boss 单平台 prompt 不应显著膨胀（D1 目标：持平或略降）。
        经验阈值：一轮含简历 + 搜索 mode 的 prompt < 11K 字符
        2026-04-25 第二批 bump：加 code-24 工具感知错误处理表（~250 字符）。
        2026-04-25 再 bump：加 code-24 prompt 工具方向配错保护例外段（~330 字符）+
        Step 2B 求职者侧调 boss_get_friend_list 的修正注释（~120 字符）。
        2026-04-27 加 WELCOME_TEMPLATE_RULES（spec 双语欢迎模板）→ ~16748，
        threshold 上调到 18500 留浮动。
        2026-04-27 第二批：加 Phase 6 自定义搜索 4 步分轮对话（~1100 字符）+
        Phase 9 IDENTITY_MISMATCH_AUTO_LOGOUT_RULES（~1700 字符）→ ~21000,
        threshold 上调到 22000 留浮动。
        2026-04-27 第三批：加 BOSS_ADDON chatSecurityId 缺失禁止链（~860 字符）→
        threshold 上调到 23000。
        2026-04-27 第四批：加 LinkedIn 5-action 结果 chip 模板 + Step 2B
        时间窗口 chip(24h/3d/7d 双语)→ ~22500,threshold 上调到 24000。
        2026-04-27 第五批:加 Indeed Step 0 意图选择 chip + 双登录守卫说明
        + Indeed 招聘流程特例段(申请人筛选 + 5-action chip + compose 协议)→
        ~25000,threshold 上调到 26000。
        2026-04-27 第六批: Step 1 成功模板对齐 Boss 截图(简历段 + 已保存
        偏好段 + 平台标签按 platform 替换)→ ~31600,threshold 上调到 32500。"""
        search_mode = get_mode("search")
        prompt = compose_system_prompt(
            search_mode.system_prompt, platform="boss",
            resume_summary="## 我的简历\n- 期望职位: Python 后端",
        )
        assert len(prompt) < 43_000, f"Boss prompt 过大：{len(prompt)} 字符"

    def test_linkedin_lighter_than_cross(self):
        """LinkedIn 单平台 < cross 全量（符合 platform-locked 的 token 预算优势）"""
        li = compose_system_prompt("mode", platform="linkedin")
        cross = compose_system_prompt("mode", platform="cross")
        assert len(li) < len(cross)

    def test_phase3_lite_addon_slim_budget(self):
        """D1 Phase 3-lite 回归锁：ADDON 尺寸不能再膨胀（MCP schemas 已提供工具签名，
        ADDON 只留 workflow / 风控 / 令牌链 / 平台常量）。
        每个 ADDON 的尺寸预算留了约 +15% 冗余应对未来小增补。
        2026-04-25 后 bump：三平台都加了"⚡ 强制 first-action check_login"
        段落（约 300-400 字符 / 平台），是登录环境隔离的硬性规则，不属于
        ADDON 膨胀，所以预算同步上抬。
        2026-04-25 第三批 bump：BOSS_ADDON 加求职者消息中心链路说明
        （geek_filter_by_label / geek_get_boss_data，~250 字符）
        + code-24 工具方向配错保护例外段（~330 字符）。
        """
        from modes.base import (
            BOSS_ADDON, LINKEDIN_ADDON, INDEED_ADDON, INDEED_EMPLOYER_ADDON,
        )
        # 2026-04-27 第三批 bump：加 chatSecurityId 缺失禁止链 + 列表为空处理段
        # （~860 字符），堵住 Agent 硬塞空 security_id 浪费 ext round-trip 的坑。
        # 2026-04-27 第四批 bump：LINKEDIN_ADDON 加大陆 451 死循环识别段
        # （~700 字符），处理 linkedin.com→linkedin.cn 运营商劫持的 N 次失败循环。
        # 2026-04-28 第四批 bump:把 chatSecurityId 禁止链段重写为 A/B 双类
        # 规则(A 类 ext fallback 留空合法 / B 类硬要求),区分 boss_start_chat
        # 等有 fallback 的工具,~250 字符净增。
        # 2026-04-28 第五批 bump:加"主动给 boss 发消息"3 步标准流程段
        # (从消息列表进入会话 → geek_get_boss_data → compose modal →
        # send_message,不要走 start_chat 误路径),~700 字符。
        assert len(BOSS_ADDON)             < 9000, f"BOSS_ADDON={len(BOSS_ADDON)}"
        # 2026-04-28: 加 region_blocked 双语处理段(扩展侧 region-block 缓存
        # 信号 + LLM 不要重试 + 用户开 VPN 后从 popup 重试的引导)+850 chars
        # 2026-04-28 第二批: 加 force_reset 参数说明 + 重试链路约束 +500 chars
        assert len(LINKEDIN_ADDON)         < 5000, f"LINKEDIN_ADDON={len(LINKEDIN_ADDON)}"
        assert len(INDEED_ADDON)           < 2400, f"INDEED_ADDON={len(INDEED_ADDON)}"
        assert len(INDEED_EMPLOYER_ADDON)  < 3000, f"INDEED_EMPLOYER_ADDON={len(INDEED_EMPLOYER_ADDON)}"

    def test_boss_search_prompt_under_threshold(self):
        """Boss/search 的 compose 输出预算（Phase 2 基线 ~8528；Phase 3-lite ~6700；
        2026-04-25 加 first-action check_login + match_percent < 60 + chip 模板 →
        ~8650；再加 code-24 工具感知错误处理表 → ~9700；
        加 Wizard Step 5 freeform 描述识别段 → ~12641；
        2026-04-27 加 WELCOME_TEMPLATE_RULES（spec 对齐"我要找工作 / 我要招人"
        二选一 chip + 双语欢迎模板）→ ~16034。
        2026-04-27 第二批：加 Phase 6 自定义搜索 4 步分轮对话（~1100 字符）+
        Phase 9 IDENTITY_MISMATCH_AUTO_LOGOUT_RULES（~1700 字符）→ ~20800,
        threshold 上调到 22000 留 +5% 浮动。
        2026-04-27 第三批: LinkedIn spec 4.2/4.3 对齐 — 登录后意图选择 chip
        [我要找工作, 我要找人] / [I want to look for jobs, ...]
        + 求职端结果 5-action chip [分析/面试准备/详情/消息招聘经理/翻页]
        分平台模板 → ~22200,threshold 上调到 23000。
        2026-04-27 第四批:加 Step 2B 时间窗口 chip + LinkedIn 5 步 compose
        流程详细规则(linkedin_request_compose + __linkedin_compose_send__
        回流分支)→ ~23200,threshold 上调到 24500。
        2026-04-27 第五批:加 geek-side 看 boss 详情/聊天历史 securityId 缺失
        引导(扩展先 intercept friend/add 拿 chatSecurityId 的提示模板)→ ~25500,
        threshold 上调到 26000。
        2026-04-28: WELCOME_TEMPLATE_RULES 三平台专属欢迎语展开 (Indeed zh/en +
        LinkedIn zh/en, ~600 chars net) → ~26346, threshold 上调到 27000。
        2026-04-28 第二批: 匹配度规则强化禁词清单 + 反例 + Top Matches 封顶
        (~1100 chars) → ~28133, threshold 上调到 29500。
        2026-04-28 第三批: Step 2A 加 Boss/LinkedIn/Indeed × zh/en 6 套 chip
        模板 + 硬约束 + 平台单位强制说明 (~1700 chars) → ~29841, threshold
        上调到 31000。
        2026-04-28 第四批: Step 1 chip 硬约束 + 反例 + Step 1 vs Step 2A 区分
        + Step 2A 触发条件细化(用户消息必须是 chip 原文,不是意图表达)
        (~1500 chars) → ~32577, threshold 上调到 33000。"""
        search_mode = get_mode("search")
        prompt = compose_system_prompt(search_mode.system_prompt, platform="boss")
        assert len(prompt) < 42_500, f"Boss/search prompt={len(prompt)} 超出预算"


# ── 2026-04-25: 分步推进规则 ─────────────────────────────────────────────


class TestStepByStepGuidance:
    """新规则锁：搜索 / 招聘 mode 都要带"⚡ 分步推进"段落，每步停一拍 chip。"""

    def test_search_mode_has_step_rule(self):
        from modes import get_mode

        prompt = get_mode("search").system_prompt
        assert "⚡ 强制：分步推进" in prompt, "search.py 缺少分步推进规则"
        # Step 1 = identity verification gate, NOT search start
        assert "Step 1 — 身份验证 gate" in prompt
        assert "Step 2A — 读简历偏好" in prompt
        # 2026-04-27:chip 文案统一为 "查看最近消息"(对齐 Indeed/LinkedIn spec)
        assert "Step 2B — 查看最近消息" in prompt
        assert "Step 3 — 真正搜索" in prompt
        # Step 1 chip must be the two-action format (not "开始搜工作 / 换账号")
        assert "[搜索工作, 查看最近消息]" in prompt
        # 一轮一工具的硬约束
        assert "最多调一个工具" in prompt
        # chip 必须带具体值（不能写"就这么搜"这种泛指）
        assert "搜索 产品运营实习生" in prompt
        # Step 2A 用 [自定义搜索] 替代了 [改职位/改城市/改薪资/完全换方向]
        assert "自定义搜索" in prompt
        assert "Custom search" in prompt
        assert "改职位" not in prompt, "Step 2A 不应再有'改职位'/'改城市'等零散字段 chip"
        assert "改城市" not in prompt
        assert "完全换方向" not in prompt

    def test_recruiter_mode_has_step_rule(self):
        from modes import get_mode

        prompt = get_mode("recruiter").system_prompt
        assert "⚡ 强制：分步推进" in prompt, "recruiter.py 缺少分步推进规则"
        assert "Step 1 — 身份验证 gate" in prompt
        assert "Step 2A — 列已发布职位" in prompt
        assert "Step 2B — 查看最近沟通" in prompt
        assert "Step 3 — 真正搜候选人" in prompt
        assert "[搜索候选人, 查看最近沟通]" in prompt
        assert "最多调一个工具" in prompt

    def test_both_modes_ban_escape_chip_in_step_rule(self):
        """分步推进规则里要明文禁止"其他 / 我自己说"等 chip 逃生选项。"""
        from modes import get_mode

        for mode_name in ["search", "recruiter"]:
            prompt = get_mode(mode_name).system_prompt
            # 必须提"chip 数组里禁止"并列出至少一个常见逃生项
            has_ban = "禁止" in prompt and ("其他" in prompt or "我自己说" in prompt)
            assert has_ban, f"{mode_name}.py 缺少 chip 逃生选项的禁令"


# ── E2-4: Recruiter EVAL_RULES injection ─────────────────────────────────


class TestEvalRulesRoleBasedInjection:
    """EVAL_RULES 按 role_type 选版本：
      - role=recruiter → EVAL_RULES_RECRUITER（候选人 vs 招聘职位）
      - 其他 + resume_summary → EVAL_RULES_JOBSEEKER（职位 vs 我的简历）
    """

    _SENT_JOBSEEKER = "职位评估规则（求职者视角"
    _SENT_RECRUITER = "候选人评估规则（招聘方视角"

    def test_recruiter_role_injects_recruiter_rules(self):
        prompt = compose_system_prompt("mode", platform="boss",
                                       role_type="recruiter")
        assert self._SENT_RECRUITER in prompt
        assert self._SENT_JOBSEEKER not in prompt

    def test_recruiter_role_injects_even_without_resume(self):
        """招聘方本来就不上传自己简历，EVAL_RULES_RECRUITER 应独立注入"""
        prompt = compose_system_prompt("mode", platform="boss",
                                       role_type="recruiter",
                                       resume_summary="")
        assert self._SENT_RECRUITER in prompt

    def test_jobseeker_role_with_resume_injects_jobseeker_rules(self):
        prompt = compose_system_prompt(
            "mode", platform="boss",
            resume_summary="## 我的简历\n- Python 后端",
            role_type="jobseeker",
        )
        assert self._SENT_JOBSEEKER in prompt
        assert self._SENT_RECRUITER not in prompt

    def test_jobseeker_role_without_resume_skips_eval(self):
        """求职者无简历 → 不注入评估规则（防止 LLM 硬编无据打分）"""
        prompt = compose_system_prompt("mode", platform="boss",
                                       role_type="jobseeker")
        assert self._SENT_JOBSEEKER not in prompt
        assert self._SENT_RECRUITER not in prompt

    def test_default_role_empty_with_resume_still_jobseeker(self):
        """role_type 空（默认）+ 有 resume → 按 jobseeker 处理（向后兼容原行为）"""
        prompt = compose_system_prompt(
            "mode", platform="boss",
            resume_summary="## 我的简历\n- X",
            role_type="",
        )
        assert self._SENT_JOBSEEKER in prompt

    def test_eval_rules_alias_backcompat(self):
        """EVAL_RULES 符号保持 = EVAL_RULES_JOBSEEKER（外部 import 不破坏）"""
        assert EVAL_RULES is EVAL_RULES_JOBSEEKER


class TestResumePriorityRulesRoleAware:
    """RESUME_PRIORITY_RULES 只对求职者有用（用'我的简历'锚定搜索）；
    recruiter role 下用户是招聘方，注入会引向'用自己简历搜职位'的错误路径。"""

    _SENT = "简历与偏好优先"

    def test_jobseeker_role_injects_resume_priority(self):
        prompt = compose_system_prompt("mode", platform="boss",
                                       role_type="jobseeker")
        assert self._SENT in prompt

    def test_empty_role_injects_resume_priority(self):
        """role_type 空（老会话 / 未明确）也按求职者注入（向后兼容）"""
        prompt = compose_system_prompt("mode", platform="boss", role_type="")
        assert self._SENT in prompt

    def test_recruiter_role_skips_resume_priority(self):
        prompt = compose_system_prompt("mode", platform="boss",
                                       role_type="recruiter")
        assert self._SENT not in prompt


class TestQuickReplyNoEscapeOptions:
    """防止 prompt 示例里重新混入"我自己说 / 其他"等 chip 逃生选项。

    Agent 以前会在 chip 里加"其他"让用户跳出 chip 自由输入，但前端聊天框
    本来就能输入，这个 chip 是冗余还占位。QUICK_REPLIES_RULES 里有硬性
    禁令 + 所有示例都应合规。"""

    _ESCAPE_PHRASES = (
        "我自己说", "我自己定", "让我自己定", "让我自己说",
        "我来说", "我自己输入", "自由输入",
    )

    def test_base_prompt_has_no_escape_option_example(self):
        """base.py 的 RESUME_PRIORITY_RULES / QUICK_REPLIES_RULES / 其它段里
        示例 chip 不应出现逃生选项（只禁在 chip 数组里，禁令文字里允许提及）"""
        from modes.base import (
            RESUME_PRIORITY_RULES, QUICK_REPLIES_RULES,
        )
        import re
        # 提取所有反引号包裹的 [选项, 选项, ...] chip 数组
        pattern = re.compile(r"`\[([^\]]+)\]`")
        for src, name in [(RESUME_PRIORITY_RULES, "RESUME_PRIORITY_RULES"),
                          (QUICK_REPLIES_RULES, "QUICK_REPLIES_RULES")]:
            for m in pattern.finditer(src):
                chip_text = m.group(1)
                for phrase in self._ESCAPE_PHRASES:
                    assert phrase not in chip_text, (
                        f"{name} 的示例 chip `[{chip_text}]` 含禁止逃生选项 {phrase!r}"
                    )

    def test_mode_workflow_has_no_escape_option_example(self):
        """各 mode 的 system_prompt 示例 chip 也应合规"""
        from modes import get_mode
        import re
        pattern = re.compile(r"`\[([^\]]+)\]`")
        for mode_name in ("search", "evaluate", "apply", "interview",
                          "compare", "recruiter"):
            md = get_mode(mode_name)
            if not md:
                continue
            for m in pattern.finditer(md.system_prompt):
                chip_text = m.group(1)
                for phrase in self._ESCAPE_PHRASES:
                    assert phrase not in chip_text, (
                        f"{mode_name} mode chip `[{chip_text}]` 含禁止逃生选项 {phrase!r}"
                    )

    def test_ban_rule_explicitly_stated(self):
        """QUICK_REPLIES_RULES 里必须显式禁止逃生选项"""
        from modes.base import QUICK_REPLIES_RULES
        assert "禁止" in QUICK_REPLIES_RULES
        assert "我自己说" in QUICK_REPLIES_RULES  # 点名禁列表
        assert "其他" in QUICK_REPLIES_RULES       # 点名禁列表
