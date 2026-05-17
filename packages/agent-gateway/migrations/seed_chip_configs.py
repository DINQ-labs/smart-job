"""seed_chip_configs.py — 把 cells.py 6 cell 的 chips 种子化到 chip_configs 表.

幂等(每条 chip 走 db.chip_config_upsert,(key, role, platform) UNIQUE 约束保护)。
启动时也跑一次,保证表至少有最小数据。

跑法:
  cd job-agent-gateway && venv/bin/python3 migrations/seed_chip_configs.py
或后端 init_db 后自动 await seed_chip_configs.run()
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import db

log = logging.getLogger(__name__)


# ── Default chip pool 设计 ──────────────────────────────────────────
# 每个 cell 4-5 条 default chip + 2-3 条 onboarding-style chip(规则触发时
# InsertFirst 上提,不触发时仍保留在池里 weight 较低,用户也能滑到)
#
# id naming convention:
#   - default 行为:search_jobs / view_messages / interview_prep ...
#   - onboarding-style:upload_resume / set_preferences / bind_job ...
#
# weight:
#   90+ 极高(规则推上去后)
#   70-80 high(常用)
#   50-60 mid(default 池)
#   30-40 low(onboarding,平时不显)
#
# group:
#   default     常态 chip
#   onboarding  靠规则触发的引导 chip
#   shortcut    用户高频(规则可调上来)

SEED_DATA: list[dict] = [
    # ─── (jobseeker, boss) ─────────────────────────────────────────
    dict(key="search_jobs", role="jobseeker", platform="boss",
         label_zh="搜索工作", label_en="Search jobs",
         icon="🔍", weight=80),
    dict(key="view_messages", role="jobseeker", platform="boss",
         label_zh="查看最近消息", label_en="View recent messages",
         icon="📨", weight=70),
    dict(key="evaluate_current", role="jobseeker", platform="boss",
         label_zh="评估当前职位", label_en="Evaluate current job",
         send_text_zh="评估当前页面的职位", send_text_en="Evaluate the current job on this page",
         icon="🎯", weight=60),
    dict(key="interview_prep", role="jobseeker", platform="boss",
         label_zh="面试准备", label_en="Interview prep",
         icon="🎤", weight=50),
    dict(key="upload_resume", role="jobseeker", platform="boss",
         label_zh="📄 上传简历", label_en="📄 Upload resume",
         send_text_zh="我想上传简历", send_text_en="I want to upload my resume",
         weight=30, grp="onboarding",
         description="规则 onboard_no_resume 触发时 InsertFirst"),
    dict(key="set_preferences", role="jobseeker", platform="boss",
         label_zh="⚙️ 设置求职偏好", label_en="⚙️ Set preferences",
         send_text_zh="设置我的求职偏好", send_text_en="Set my job preferences",
         weight=30, grp="onboarding",
         description="规则 onboard_no_prefs 触发时 InsertFirst"),

    # ─── (jobseeker, linkedin) ─────────────────────────────────────
    dict(key="search_jobs", role="jobseeker", platform="linkedin",
         label_zh="搜索职位", label_en="Search jobs",
         icon="🔍", weight=80),
    dict(key="contact_recruiters", role="jobseeker", platform="linkedin",
         label_zh="联系招聘经理", label_en="Contact recruiters",
         icon="📨", weight=70),
    dict(key="evaluate_current", role="jobseeker", platform="linkedin",
         label_zh="评估当前职位", label_en="Evaluate current job",
         send_text_zh="评估当前页面的职位", send_text_en="Evaluate the current job",
         icon="🎯", weight=60),
    dict(key="interview_prep", role="jobseeker", platform="linkedin",
         label_zh="面试准备", label_en="Interview prep",
         icon="🎤", weight=50),
    dict(key="upload_resume", role="jobseeker", platform="linkedin",
         label_zh="📄 上传简历", label_en="📄 Upload resume",
         send_text_zh="我想上传简历", send_text_en="I want to upload my resume",
         weight=30, grp="onboarding"),
    dict(key="set_preferences", role="jobseeker", platform="linkedin",
         label_zh="⚙️ 设置求职偏好", label_en="⚙️ Set preferences",
         send_text_zh="设置我的求职偏好", send_text_en="Set my job preferences",
         weight=30, grp="onboarding"),

    # ─── (jobseeker, indeed) ───────────────────────────────────────
    dict(key="search_jobs", role="jobseeker", platform="indeed",
         label_zh="搜索职位", label_en="Search jobs",
         icon="🔍", weight=80),
    dict(key="view_saved_jobs", role="jobseeker", platform="indeed",
         label_zh="查看保存的职位", label_en="View saved jobs",
         icon="⭐", weight=70),
    dict(key="evaluate_current", role="jobseeker", platform="indeed",
         label_zh="评估当前职位", label_en="Evaluate current job",
         send_text_zh="评估当前页面的职位", send_text_en="Evaluate the current job",
         icon="🎯", weight=60),
    dict(key="interview_prep", role="jobseeker", platform="indeed",
         label_zh="面试准备", label_en="Interview prep",
         icon="🎤", weight=50),
    dict(key="upload_resume", role="jobseeker", platform="indeed",
         label_zh="📄 上传简历", label_en="📄 Upload resume",
         send_text_zh="我想上传简历", send_text_en="I want to upload my resume",
         weight=30, grp="onboarding"),
    dict(key="set_preferences", role="jobseeker", platform="indeed",
         label_zh="⚙️ 设置求职偏好", label_en="⚙️ Set preferences",
         send_text_zh="设置我的求职偏好", send_text_en="Set my job preferences",
         weight=30, grp="onboarding"),

    # ─── (recruiter, boss) ─────────────────────────────────────────
    dict(key="search_candidates", role="recruiter", platform="boss",
         label_zh="搜索候选人", label_en="Search candidates",
         icon="🔎", weight=80),
    dict(key="view_replies", role="recruiter", platform="boss",
         label_zh="查看回复", label_en="View replies",
         icon="💬", weight=70),
    dict(key="bulk_greeting", role="recruiter", platform="boss",
         label_zh="批量打招呼", label_en="Bulk greetings",
         icon="👋", weight=60),
    dict(key="evaluate_candidate", role="recruiter", platform="boss",
         label_zh="评估当前候选人", label_en="Evaluate current candidate",
         send_text_zh="评估当前页面的候选人", send_text_en="Evaluate the current candidate",
         icon="🎯", weight=50),
    dict(key="bind_job", role="recruiter", platform="boss",
         label_zh="🎯 关联在招职位", label_en="🎯 Bind a job",
         send_text_zh="我想关联一个在招职位", send_text_en="I want to bind an open job",
         weight=30, grp="onboarding",
         description="规则 recruiter_no_job_bound 触发时 InsertFirst"),
    dict(key="set_recruiter_template", role="recruiter", platform="boss",
         label_zh="✏️ 编辑招呼模板", label_en="✏️ Edit greeting template",
         send_text_zh="我想编辑招呼模板", send_text_en="I want to edit greeting templates",
         weight=30, grp="onboarding"),

    # ─── (recruiter, linkedin) ─────────────────────────────────────
    dict(key="search_candidates", role="recruiter", platform="linkedin",
         label_zh="搜索候选人", label_en="Search candidates",
         icon="🔎", weight=80),
    dict(key="view_replies", role="recruiter", platform="linkedin",
         label_zh="查看回复", label_en="View replies",
         icon="💬", weight=70),
    dict(key="send_inmail", role="recruiter", platform="linkedin",
         label_zh="发 InMail 邀约", label_en="Send InMail",
         icon="📧", weight=60),
    dict(key="evaluate_candidate", role="recruiter", platform="linkedin",
         label_zh="评估当前候选人", label_en="Evaluate current candidate",
         send_text_zh="评估当前页面的候选人", send_text_en="Evaluate the current candidate",
         icon="🎯", weight=50),
    dict(key="bind_job", role="recruiter", platform="linkedin",
         label_zh="🎯 关联在招职位", label_en="🎯 Bind a job",
         send_text_zh="我想关联一个在招职位", send_text_en="I want to bind an open job",
         weight=30, grp="onboarding"),
    dict(key="set_recruiter_template", role="recruiter", platform="linkedin",
         label_zh="✏️ 编辑招呼模板", label_en="✏️ Edit greeting template",
         send_text_zh="我想编辑招呼模板", send_text_en="I want to edit greeting templates",
         weight=30, grp="onboarding"),

    # ─── (recruiter, indeed) ───────────────────────────────────────
    dict(key="search_resumes", role="recruiter", platform="indeed",
         label_zh="搜索简历", label_en="Search resumes",
         icon="🔎", weight=80),
    dict(key="view_applicants", role="recruiter", platform="indeed",
         label_zh="查看申请人", label_en="View applicants",
         icon="👥", weight=70),
    dict(key="send_message", role="recruiter", platform="indeed",
         label_zh="发送消息", label_en="Send message",
         icon="📧", weight=60),
    dict(key="evaluate_candidate", role="recruiter", platform="indeed",
         label_zh="评估当前候选人", label_en="Evaluate current candidate",
         send_text_zh="评估当前页面的候选人", send_text_en="Evaluate the current candidate",
         icon="🎯", weight=50),
    dict(key="set_recruiter_template", role="recruiter", platform="indeed",
         label_zh="✏️ 编辑招呼模板", label_en="✏️ Edit greeting template",
         send_text_zh="我想编辑招呼模板", send_text_en="I want to edit greeting templates",
         weight=30, grp="onboarding"),
]


# ── PRD §10.3 9 种主动提示 chip pool 种子 ─────────────────────────────
# scenario 1-8 在 personalization/chips.py 的 ui_v3_* 规则下 InsertFirst,
# scenario 9 ("非平台页隐藏") 在 sidepanel/app.js 处理,不进 chip pool。
#
# weight=20 base — onboarding 组以下,默认不出现;只有命中对应 rule 后才上提。
# 这样 chip pool 不会因为多了 8 条把基础 6 条挤掉。

_UI_V3_CHIPS: list[dict] = [
    # PRD 114 — recruiter 列表页
    dict(key="bulk_analyze_candidates", role="recruiter",
         label_zh="批量分析候选人", label_en="Bulk analyze candidates",
         send_text_zh="批量分析当前页面的候选人", send_text_en="Bulk analyze candidates on this page",
         icon="🔍", weight=20, grp="contextual",
         description="PRD 114 — 规则 ui_v3_recruiter_list InsertFirst"),
    # PRD 116 — chat 页(jobseeker/recruiter 共用)
    dict(key="draft_chat_reply", role="jobseeker",
         label_zh="起草下一条回复", label_en="Draft next reply",
         send_text_zh="帮我起草下一条回复", send_text_en="Draft my next reply",
         icon="💬", weight=20, grp="contextual",
         description="PRD 116 — 规则 ui_v3_chat_page InsertFirst"),
    dict(key="draft_chat_reply", role="recruiter",
         label_zh="起草下一条回复", label_en="Draft next reply",
         send_text_zh="帮我起草下一条回复", send_text_en="Draft my next reply",
         icon="💬", weight=20, grp="contextual",
         description="PRD 116 — 规则 ui_v3_chat_page InsertFirst"),
    # PRD 117 — jobseeker 列表页
    dict(key="bulk_analyze_jobs", role="jobseeker",
         label_zh="批量分析职位", label_en="Bulk analyze jobs",
         send_text_zh="批量分析当前页面的职位", send_text_en="Bulk analyze jobs on this page",
         icon="🎯", weight=20, grp="contextual",
         description="PRD 117 — 规则 ui_v3_jobseeker_list InsertFirst"),
    # PRD 119 — 进行中任务(任意角色)
    dict(key="view_running_task", role="jobseeker",
         label_zh="🔄 查看进行中任务", label_en="🔄 View running task",
         send_text_zh="查看正在进行中的长任务", send_text_en="Show me the running task",
         weight=20, grp="contextual",
         description="PRD 119 — 规则 ui_v3_task_running InsertFirst"),
    dict(key="view_running_task", role="recruiter",
         label_zh="🔄 查看进行中任务", label_en="🔄 View running task",
         send_text_zh="查看正在进行中的长任务", send_text_en="Show me the running task",
         weight=20, grp="contextual",
         description="PRD 119 — 规则 ui_v3_task_running InsertFirst"),
    # PRD 120 — 任务需验证(任意角色)
    dict(key="handle_paused_task", role="jobseeker",
         label_zh="⚠️ 处理验证", label_en="⚠️ Handle verification",
         send_text_zh="任务暂停了,我来处理验证", send_text_en="Handle the paused task verification",
         weight=20, grp="contextual",
         description="PRD 120 — 规则 ui_v3_task_paused InsertFirst"),
    dict(key="handle_paused_task", role="recruiter",
         label_zh="⚠️ 处理验证", label_en="⚠️ Handle verification",
         send_text_zh="任务暂停了,我来处理验证", send_text_en="Handle the paused task verification",
         weight=20, grp="contextual",
         description="PRD 120 — 规则 ui_v3_task_paused InsertFirst"),
    # PRD 121 — 完成报告(任意角色)
    dict(key="view_completed_task", role="jobseeker",
         label_zh="✅ 查看完成报告", label_en="✅ View completed report",
         send_text_zh="给我看刚才完成的任务结果", send_text_en="Show me the recently completed task results",
         weight=20, grp="contextual",
         description="PRD 121 — 规则 ui_v3_task_completed InsertFirst"),
    dict(key="view_completed_task", role="recruiter",
         label_zh="✅ 查看完成报告", label_en="✅ View completed report",
         send_text_zh="给我看刚才完成的任务结果", send_text_en="Show me the recently completed task results",
         weight=20, grp="contextual",
         description="PRD 121 — 规则 ui_v3_task_completed InsertFirst"),
]

# 上述每条 chip 需要在每个 supported platform 落一行(boss/linkedin/indeed)。
# 用 fan-out 而不是手写 30 行,降低 drift。
_PLATFORMS = ("boss", "linkedin", "indeed")
for _base in _UI_V3_CHIPS:
    for _plat in _PLATFORMS:
        SEED_DATA.append(dict(_base, platform=_plat))


async def run() -> dict:
    """种子化(idempotent)。返回 {inserted_or_updated: N}."""
    n = 0
    for entry in SEED_DATA:
        try:
            await db.chip_config_upsert(updated_by="seed", **entry)
            n += 1
        except Exception as e:
            log.warning("seed chip failed key=%s role=%s platform=%s: %s",
                        entry.get("key"), entry.get("role"), entry.get("platform"), e)
    # bump version 一次,告诉前端有新 chip
    await db.chip_configs_bump_version()
    return {"upserted": n, "total_seed_rows": len(SEED_DATA)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    res = asyncio.run(run())
    print(res)
