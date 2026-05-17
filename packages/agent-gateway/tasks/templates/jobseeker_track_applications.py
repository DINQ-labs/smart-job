"""tasks/templates/jobseeker_track_applications.py — 投递进度追踪（C4 / GAP gap #2）。

业务流程（2 步）：
  1. 列出所有投递（list_applied_jobs）
  2. 汇总（按 sent / read / responded / interviewing / rejected 分桶）

平台覆盖：
  Indeed   indeed_list_applied_jobs        — 完整支持
  Boss     Boss API 无"我的投递"列表       — fallback 返回空 + summary 说明
  LinkedIn 暂无直接 API                    — 同上

后续 Sprint 可接入 LinkedIn GraphQL 拉投递历史 + Boss 通过 chat_history 推断投递状态。
"""
from __future__ import annotations

from tasks.registry import TaskTemplate, TaskStep, register_template
from tasks.steps import sprint2


BOSS_STEPS = [
    TaskStep(id="list",      title="列投递记录", fn=sprint2.step_list_apps_boss,    op_type="list"),
    TaskStep(id="summarize", title="生成报告",   fn=sprint2.step_summarize_tracked),
]

LINKEDIN_STEPS = [
    TaskStep(id="list",      title="列投递记录", fn=sprint2.step_list_apps_linkedin, op_type="list"),
    TaskStep(id="summarize", title="生成报告",   fn=sprint2.step_summarize_tracked),
]

INDEED_STEPS = [
    TaskStep(id="list",      title="列投递记录", fn=sprint2.step_list_apps_indeed,   op_type="list"),
    TaskStep(id="summarize", title="生成报告",   fn=sprint2.step_summarize_tracked),
]


TEMPLATE = TaskTemplate(
    id="jobseeker:track_applications",
    role="jobseeker",
    title="投递进度追踪",
    description="拉历史投递 → 按状态分桶（已投/已读/已回/面试/拒绝）",
    emoji="📊",
    estimated_min=2,
    steps_by_platform={
        "boss":     BOSS_STEPS,
        "linkedin": LINKEDIN_STEPS,
        "indeed":   INDEED_STEPS,
    },
)

register_template(TEMPLATE)
