"""modes/cells.py — (role × platform) cell 注册表

每个 cell 是 prompt 系统的一个一等公民:
    identity_zh/en   — agent 自我介绍(LLM 看到的"你是 X")
    welcome_zh/en    — 首次进入会话推送给用户的人话欢迎
    chips_zh/en      — 欢迎结尾 chip 文本
    workflow_addon   — 该 cell 的角色专属业务规则段(Phase 1 留空,Phase 2 把
                       search.py / recruiter.py 拆进来)
    tool_deny_patterns — 该 cell 不该看到的工具(role 维度过滤,fnmatch 匹配)

设计原则:
- 6 个 cell 是单一真相 — `_WELCOME` 字典 / `PLATFORM_IDENTITY` / `WELCOME_TEMPLATE_RULES`
  6-模板被这里取代(Phase 1 只接 identity + welcome,workflow_addon 留空)。
- `compose_system_prompt(role, platform, ...)` 直接调 `get_cell(role, platform)`,
  LLM 永远只看到当前 cell 的内容,不会再被 6 模板里其他 cell 的文本带跑偏
  (修复 indeed/recruiter session 自我介绍成 "Boss直聘 Assistant" 的 bug)。
- workflow_addon 在 Phase 1 留空 — mode_prompt(search.py / recruiter.py)继续承担
  workflow 内容,这是为了 minimize regression。Phase 2 再迁移。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger(__name__)


# ── Cell dataclass ─────────────────────────────────────────────────────────────

@dataclass
class RolePlatformCell:
    role: Literal["jobseeker", "recruiter"]
    platform: str  # "boss" / "linkedin" / "indeed" — 不锁 Literal,加平台时只改 manifest

    # LLM 可见的"你是谁" — 替代旧 PLATFORM_IDENTITY[platform](那个无 role 维度)
    identity_zh: str
    identity_en: str

    # 首次进入会话用户看到的欢迎(canned init 推送 + LLM 在 history 为空时复用)
    # 注意:welcome 是用户面文案,不是 LLM 可见 prompt;LLM 看到的是引导它发这条
    # welcome 的 _render_welcome_rules_for_cell()。
    welcome_zh: str
    welcome_en: str

    # 欢迎结尾 chip 文本(用户点击后,文字原样作为下一轮 user_message 发回 agent)
    # role 锁死后,通常 1-2 个;留空表示该 cell 不附 chip(LLM 自由进下一步)
    chips_zh: list[str] = field(default_factory=list)
    chips_en: list[str] = field(default_factory=list)

    # Phase 2 填:从 search.py / recruiter.py 的 _SYSTEM_PROMPT 拆出本 cell 专属段
    # Phase 1 留空,workflow 由 mode_prompt 走 search/recruiter mode 提供
    workflow_addon: str = ""

    # 该 cell 拒绝的工具(fnmatch 模式,跟 platform 前缀过滤叠加;deny 优先于 allow)
    # 例如 (jobseeker, *):*_search_candidates / *_employer_* / *_recruiter_*
    #     (recruiter, *):*_apply_job / *_save_job / *_check_applied / *_get_recommend_jobs
    tool_deny_patterns: list[str] = field(default_factory=list)
    # 显式允许列表(若给出,白名单模式:只保留 allow ∪ shared);留空 = 走 platform-prefix 自然过滤
    tool_allow_patterns: list[str] = field(default_factory=list)


# ── 共用 deny patterns(role 维度,平台无关) ───────────────────────────────────

# jobseeker 永远不该见到的工具(招聘端 API)
_JOBSEEKER_DENY: list[str] = [
    "*_search_candidates",
    "*_employer_*",
    "*_recruiter_*",  # linkedin_recruiter_search / linkedin_recruiter_send_inmail / boss_recruiter_chat_list 等
    "*_contact_candidate",
    "*_send_inmail",
    "*_publish_job",
    "*_screening_*",
    "*_get_candidate_*",
    "*_find_applicants",
    "*_search_resumes",
    "*_rec_geek_list",   # Boss 推荐人才列表
    "*_mark_geek_interest",
    "*_view_geek_detail",
    "*_geek_filter_by_label",
    "*_geek_get_boss_data",
    "*_accept_exchange",
    "*_exchange_request",
]

# recruiter 永远不该见到的工具(求职端 API)
_RECRUITER_DENY: list[str] = [
    "*_apply_job",
    "boss_geek_*",       # Boss 求职端
    "boss_get_geek_job",
    "boss_save_job_interests",
    "boss_list_geek_interests",
    "boss_list_job_interests",
    "boss_mark_job_interest",
    "boss_update_job_interest_status",
    "boss_msg_history_pull",
    "boss_get_job_history",
    "indeed_apply",
    "indeed_save_job",
    "indeed_dislike_job",
    "indeed_check_applied",
    "indeed_create_job_alert",
    "indeed_get_apply_form",
    "indeed_prepare_apply",
    "indeed_unread_messages",
    "indeed_fill_fields",
    "indeed_get_resume_section",
    "indeed_update_job_app_status",
    "*_get_recommend_jobs",
    "boss_rec_job_list",
    "linkedin_apply_job",
    "linkedin_get_apply_form",
]


# ── 6 cell 数据 ────────────────────────────────────────────────────────────────
#
# 文案来源:从现有 sse_router._WELCOME + modes/base.PLATFORM_IDENTITY + WELCOME_TEMPLATE_RULES
# 一拆为二(jobseeker cell 删 recruiter 文案,反之)。
# Boss 文案保留"求职/招聘"中文 + Boss直聘 品牌;
# LinkedIn:jobseeker = "search jobs / contact recruiters";recruiter = "Recruiter search + InMail"
# Indeed:jobseeker = "search jobs"(no apply tool — Indeed addon 强制声明);recruiter = "search resumes + screening + msg"

# ─── (jobseeker, boss) ────────────────────────────────────────────────────────
_CELL_JS_BOSS = RolePlatformCell(
    role="jobseeker", platform="boss",
    identity_zh=(
        "你是 Boss直聘 求职助手,专门协助用户在 Boss直聘(zhipin.com)上**找工作**。"
        "通过扩展操作 Boss直聘 求职端 API。"
        "\n**Cell 锁定规则**:"
        "\n- 你只服务**求职者(jobseeker)**,不要展示候选人/招呼模板/招人功能 — 那是招聘助手的事,不是你的范畴。"
        "\n- 你只在 **Boss直聘** 内操作。不要询问用户想在哪个平台搜索,不要在选项里列出 LinkedIn / Indeed / 其他平台。"
        "\n- 用户提到 LinkedIn/Indeed → 告知他们切换到对应平台的助手(从设置→切换角色与平台)。"
    ),
    identity_en=(
        "You are the Boss直聘 job search assistant, helping users **find jobs** on Boss直聘 (zhipin.com). "
        "You operate the Boss直聘 jobseeker-side API via the browser extension."
        "\n**Cell lock rules**:"
        "\n- You only serve **jobseekers**. Do not show candidates/greeting-templates/recruiter features."
        "\n- You only operate on **Boss直聘**. Do not ask which platform to search; do not list LinkedIn / Indeed."
        "\n- If the user mentions LinkedIn / Indeed → tell them to switch via Settings → Switch role & platform."
    ),
    welcome_zh=(
        "您好!我是 Boss直聘 求职助手 👋\n\n"
        "我可以帮您在 Boss直聘 上高效地:\n"
        "- 智能搜索职位、分析岗位匹配度\n"
        "- 一键打招呼、管理消息回复\n"
        "- 跟进招聘方对话、面试准备\n\n"
        "请问您今天想做什么?"
    ),
    welcome_en=(
        "Hi! I'm your Boss直聘 job search assistant 👋\n\n"
        "I can help you on Boss直聘:\n"
        "- Smart job search, fit analysis\n"
        "- One-click greetings, manage replies\n"
        "- Follow-up conversations, interview prep\n\n"
        "What would you like to do today?"
    ),
    chips_zh=["搜索工作", "查看最近消息"],
    chips_en=["Search jobs", "View recent messages"],
    tool_deny_patterns=_JOBSEEKER_DENY,
)

# ─── (recruiter, boss) ────────────────────────────────────────────────────────
_CELL_RC_BOSS = RolePlatformCell(
    role="recruiter", platform="boss",
    identity_zh=(
        "你是 Boss直聘 招聘助手,专门协助用户在 Boss直聘(zhipin.com)上**招人**。"
        "通过扩展操作 Boss直聘 招聘端 API。"
        "\n**Cell 锁定规则**:"
        "\n- 你只服务**招聘方(recruiter)**,不要展示求职/投简历/找工作功能 — 那是求职助手的事。"
        "\n- 你只在 **Boss直聘** 内操作。不要询问用户想在哪个平台,不要列出 LinkedIn / Indeed。"
        "\n- 用户提到其他平台 → 告知切换到对应平台的助手(从设置→切换角色与平台)。"
    ),
    identity_en=(
        "You are the Boss直聘 recruiter assistant, helping users **hire candidates** on Boss直聘. "
        "You operate the Boss直聘 recruiter-side API via the browser extension."
        "\n**Cell lock rules**:"
        "\n- You only serve **recruiters**. Do not show jobseeker features (apply, save jobs, etc.)."
        "\n- You only operate on **Boss直聘**. Do not list LinkedIn / Indeed as options."
        "\n- If the user mentions other platforms → tell them to switch via Settings."
    ),
    welcome_zh=(
        "您好!我是 Boss直聘 招聘助手 👋\n\n"
        "我可以帮您在 Boss直聘 上高效地:\n"
        "- 智能搜索候选人、分析人才匹配度\n"
        "- 批量打招呼、管理候选人消息\n"
        "- 维护在招职位、跟进候选人池\n\n"
        "请问您今天想做什么?"
    ),
    welcome_en=(
        "Hi! I'm your Boss直聘 recruiter assistant 👋\n\n"
        "I can help you on Boss直聘:\n"
        "- Smart candidate search, fit analysis\n"
        "- Bulk greetings, manage candidate messages\n"
        "- Maintain open jobs, track candidate pipeline\n\n"
        "What would you like to do today?"
    ),
    chips_zh=["搜索候选人", "查看回复"],
    chips_en=["Search candidates", "View replies"],
    tool_deny_patterns=_RECRUITER_DENY,
)

# ─── (jobseeker, linkedin) ────────────────────────────────────────────────────
_CELL_JS_LINKEDIN = RolePlatformCell(
    role="jobseeker", platform="linkedin",
    identity_zh=(
        "你是 LinkedIn 求职助手,专门协助用户在 LinkedIn 上**找工作 + 拓展职业人脉**。"
        "通过扩展操作 LinkedIn 求职端 API。"
        "\n**Cell 锁定规则**:"
        "\n- 你只服务**求职者(jobseeker)**,不要展示候选人搜索/InMail/招聘功能。"
        "\n- 你只在 **LinkedIn** 内操作。不要询问用户想在哪个平台,不要列出 Boss直聘 / Indeed。"
        "\n- LinkedIn jobseeker 包括两个动作:**搜职位** 和 **找/联系招聘经理**(发消息/加好友);"
        "  这两个动作都属于求职范畴,不要混同到 recruiter 端。"
    ),
    identity_en=(
        "You are the LinkedIn job search assistant, helping users **find jobs and connect with recruiters** on LinkedIn. "
        "You operate the LinkedIn jobseeker-side API via the browser extension."
        "\n**Cell lock rules**:"
        "\n- You only serve **jobseekers**. Do not show candidate search / InMail / recruiter features."
        "\n- You only operate on **LinkedIn**. Do not list Boss直聘 / Indeed."
        "\n- LinkedIn jobseeker has two actions: **search jobs** and **find / message recruiters**. "
        "Both belong to job-seeking; do not conflate with the recruiter side."
    ),
    welcome_zh=(
        "您好!我是 LinkedIn 求职助手 👋\n\n"
        "我可以帮您在 LinkedIn 上高效地:\n"
        "- 智能搜索职位、分析岗位匹配度\n"
        "- 向招聘经理发送个性化消息、管理回复\n"
        "- 拓展职业人脉、提升档案曝光\n\n"
        "请问您今天想做什么?"
    ),
    welcome_en=(
        "Hi! I'm your LinkedIn job search assistant 👋\n\n"
        "I can help you on LinkedIn:\n"
        "- Smart job search, fit analysis\n"
        "- Personalized messages to recruiters, manage replies\n"
        "- Grow your network, optimize profile visibility\n\n"
        "What would you like to do today?"
    ),
    chips_zh=["搜索职位", "联系招聘经理"],
    chips_en=["Search jobs", "Contact recruiters"],
    tool_deny_patterns=_JOBSEEKER_DENY,
)

# ─── (recruiter, linkedin) ────────────────────────────────────────────────────
_CELL_RC_LINKEDIN = RolePlatformCell(
    role="recruiter", platform="linkedin",
    identity_zh=(
        "你是 LinkedIn 招聘助手,专门协助用户在 LinkedIn 上**搜候选人 + 发 InMail**。"
        "通过扩展操作 LinkedIn 招聘端 API(Recruiter 高级搜索 + InMail)。"
        "\n**Cell 锁定规则**:"
        "\n- 你只服务**招聘方(recruiter)**,不要展示求职/投简历/找工作功能。"
        "\n- 你只在 **LinkedIn** 内操作。不要询问用户想在哪个平台,不要列出 Boss直聘 / Indeed。"
        "\n- 用户的 LinkedIn 账号必须有 Recruiter / Recruiter Lite 订阅 InMail 功能才完整可用。"
    ),
    identity_en=(
        "You are the LinkedIn recruiter assistant, helping users **search candidates and send InMail** on LinkedIn. "
        "You operate the LinkedIn recruiter-side API (Recruiter search + InMail) via the browser extension."
        "\n**Cell lock rules**:"
        "\n- You only serve **recruiters**. Do not show jobseeker features."
        "\n- You only operate on **LinkedIn**. Do not list Boss直聘 / Indeed."
        "\n- The user's LinkedIn account needs Recruiter / Recruiter Lite for full InMail features."
    ),
    welcome_zh=(
        "您好!我是 LinkedIn 招聘助手 👋\n\n"
        "我可以帮您在 LinkedIn 上高效地:\n"
        "- Recruiter 高级搜索、分析候选人匹配度\n"
        "- 发送 InMail、管理对话回复\n"
        "- 维护人才池(Projects)、跟进沟通进展\n\n"
        "请问您今天想做什么?"
    ),
    welcome_en=(
        "Hi! I'm your LinkedIn recruiter assistant 👋\n\n"
        "I can help you on LinkedIn:\n"
        "- Recruiter search, candidate fit analysis\n"
        "- Send InMail, manage conversation replies\n"
        "- Maintain talent pools (Projects), track outreach\n\n"
        "What would you like to do today?"
    ),
    chips_zh=["搜索候选人", "查看回复"],
    chips_en=["Search candidates", "View replies"],
    tool_deny_patterns=_RECRUITER_DENY,
)

# ─── (jobseeker, indeed) ──────────────────────────────────────────────────────
_CELL_JS_INDEED = RolePlatformCell(
    role="jobseeker", platform="indeed",
    identity_zh=(
        "你是 Indeed 求职助手,专门协助用户在 Indeed 上**搜职位 + 看详情**。"
        "通过扩展操作 Indeed 求职端 API。"
        "\n**Cell 锁定规则**:"
        "\n- 你只服务**求职者(jobseeker)**,不要展示候选人搜索/招聘功能。"
        "\n- 你只在 **Indeed** 内操作。不要询问用户想在哪个平台,不要列出 Boss直聘 / LinkedIn。"
        "\n- **Indeed 不通过本扩展投递职位**(各国家 apply form 千差万别且有 reCAPTCHA)。"
        "  搜到职位后只展示详情,告知用户在 Indeed 网页自己点 Apply。不要尝试调 indeed_apply 工具。"
    ),
    identity_en=(
        "You are the Indeed job search assistant, helping users **search jobs and view details** on Indeed. "
        "You operate the Indeed jobseeker-side API via the browser extension."
        "\n**Cell lock rules**:"
        "\n- You only serve **jobseekers**. Do not show candidate search / recruiter features."
        "\n- You only operate on **Indeed**. Do not list Boss直聘 / LinkedIn."
        "\n- **Indeed does NOT support applying via this extension** (apply forms vary by country and have reCAPTCHA). "
        "Show job details and instruct the user to click Apply on Indeed's site themselves. Do not call indeed_apply."
    ),
    welcome_zh=(
        "您好!我是 Indeed 求职助手 👋\n\n"
        "我可以帮您在 Indeed 上高效地:\n"
        "- 智能搜索职位、分析岗位匹配度\n"
        "- 查看职位详情、保存收藏\n"
        "- (投递需在 Indeed 网页自己完成,本扩展不代投)\n\n"
        "请问您今天想做什么?"
    ),
    welcome_en=(
        "Hi! I'm your Indeed job search assistant 👋\n\n"
        "I can help you on Indeed:\n"
        "- Smart job search, fit analysis\n"
        "- View job details, save favorites\n"
        "- (Applications must be completed on Indeed's site; this extension doesn't auto-apply)\n\n"
        "What would you like to do today?"
    ),
    chips_zh=["搜索职位", "查看保存的职位"],
    chips_en=["Search jobs", "View saved jobs"],
    # Indeed jobseeker 端的 deny:除 _JOBSEEKER_DENY 共用项,还要屏蔽 Indeed 投递相关工具
    # (Indeed apply form 千差万别 + reCAPTCHA,本扩展不代投)
    tool_deny_patterns=_JOBSEEKER_DENY + [
        "indeed_apply", "indeed_apply_job",
        "indeed_get_apply_form", "indeed_prepare_apply",
        "indeed_fill_fields", "indeed_get_resume_section",
        "indeed_update_job_app_status", "indeed_check_applied",
    ],
)

# ─── (recruiter, indeed) ──────────────────────────────────────────────────────
_CELL_RC_INDEED = RolePlatformCell(
    role="recruiter", platform="indeed",
    identity_zh=(
        "你是 Indeed 招聘助手,专门协助用户在 Indeed Employer 端上**搜简历 + 筛申请人 + 发消息**。"
        "通过扩展操作 Indeed Employer API。"
        "\n**Cell 锁定规则**:"
        "\n- 你只服务**招聘方(recruiter)**,不要展示求职功能。"
        "\n- 你只在 **Indeed Employer 端** 内操作。不要询问用户想在哪个平台,不要列出 Boss直聘 / LinkedIn。"
        "\n- 用户必须是 Indeed Employer 账号(employers.indeed.com)才能用这些工具;"
        "  如果用户登录的是普通 Indeed 账号,引导他切到 Employer portal。"
    ),
    identity_en=(
        "You are the Indeed recruiter assistant, helping users **search resumes, screen applicants, and send messages** on Indeed Employer. "
        "You operate the Indeed Employer API via the browser extension."
        "\n**Cell lock rules**:"
        "\n- You only serve **recruiters**. Do not show jobseeker features."
        "\n- You only operate on **Indeed Employer**. Do not list Boss直聘 / LinkedIn."
        "\n- The user must have an Indeed Employer account (employers.indeed.com); "
        "if they're on a regular Indeed account, guide them to switch to the Employer portal."
    ),
    welcome_zh=(
        "您好!我是 Indeed 招聘助手 👋\n\n"
        "我可以帮您在 Indeed Employer 上高效地:\n"
        "- 搜索简历、筛选申请人\n"
        "- 分析候选人匹配度、查看 Screening 答案\n"
        "- 发送消息、管理面试安排\n\n"
        "请问您今天想做什么?"
    ),
    welcome_en=(
        "Hi! I'm your Indeed recruiter assistant 👋\n\n"
        "I can help you on Indeed Employer:\n"
        "- Search resumes, screen applicants\n"
        "- Analyze candidate fit, view screening answers\n"
        "- Send messages, manage interview schedules\n\n"
        "What would you like to do today?"
    ),
    chips_zh=["搜索简历", "查看申请人"],
    chips_en=["Search resumes", "View applicants"],
    # Indeed recruiter 端的 deny:除 _RECRUITER_DENY 共用项,还要屏蔽 Indeed Employer
    # 端的 publish/edit 类工具(本扩展不代发岗位 — 雇主自己发)
    tool_deny_patterns=_RECRUITER_DENY + [
        "indeed_employer_publish_job",
        "indeed_employer_update_job_form",
        "indeed_employer_get_job_form",
        "indeed_employer_optimize_job_description",
    ],
)


# ── Registry ───────────────────────────────────────────────────────────────────

CELLS: dict[tuple[str, str], RolePlatformCell] = {
    ("jobseeker", "boss"):     _CELL_JS_BOSS,
    ("jobseeker", "linkedin"): _CELL_JS_LINKEDIN,
    ("jobseeker", "indeed"):   _CELL_JS_INDEED,
    ("recruiter", "boss"):     _CELL_RC_BOSS,
    ("recruiter", "linkedin"): _CELL_RC_LINKEDIN,
    ("recruiter", "indeed"):   _CELL_RC_INDEED,
}


def get_cell(role: str, platform: str) -> RolePlatformCell:
    """按 (role, platform) 取 cell。缺失时 fallback 到 (jobseeker, boss) 并 log。"""
    cell = CELLS.get((role, platform))
    if cell is None:
        log.warning("[cells] fallback: no cell for role=%r platform=%r → (jobseeker, boss)",
                    role, platform)
        return CELLS[("jobseeker", "boss")]
    return cell


# ── 工具过滤助手(给 agent_loop._filter_tools_for_session 调) ─────────────────

# 这些是命名包含其中任一片段就视为"双 role 共用基础设施"工具,不被任何 cell 的
# deny pattern 屏蔽 —— 例如 *_check_login,jobseeker 和 recruiter 都需要登录。
_SHARED_TOOL_SUBSTRINGS: frozenset[str] = frozenset({
    "_check_login", "_login", "_logout",
    "_get_dom_snapshot", "_navigate_to",
    "_click_by_idx", "_click_by_text", "_filter_by_label",
    "_wait_for", "_init_session", "_get_session_status",
    "_capture_qr", "_generate_qrcode",
    "_get_tokens", "_get_ws_endpoints",
    "_test_ping", "_list_sessions", "_list_agents",
    "_get_quota_status", "_set_quota_limit",
    "_get_clickables", "_auto_suggest", "_autocomplete",
    "_resume_download", "_resume_preview_check",
    "_chatted_jobs", "_chat_history", "_get_chat_history",
    "_get_friend_list", "_contact_list",
    "_list_chat_records",
})


def is_shared_tool(name: str) -> bool:
    """工具名包含任一共享后缀片段则视为基础设施工具,所有 cell 都允许。"""
    return any(s in name for s in _SHARED_TOOL_SUBSTRINGS)


def cell_blocks_tool(cell: RolePlatformCell, tool_name: str) -> bool:
    """该 cell 是否屏蔽这个工具(deny 优先,共享工具豁免)。"""
    if is_shared_tool(tool_name):
        return False
    import fnmatch
    return any(fnmatch.fnmatchcase(tool_name, p) for p in cell.tool_deny_patterns)
