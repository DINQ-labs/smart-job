"""tasks/templates/jobseeker_batch_apply.py — 批量投递（C3 / GAP gap #2）。

业务流程（4 步）：
  1. 搜索职位（复用 find_best_jobs 的 search step）
  2. 拉详情（投递 form 字段需要 — Indeed/LinkedIn 必须，Boss 可选）
  3. 投递（iter 每个 job：boss 走 chat 招呼；linkedin/indeed 走 quick_apply）
  4. 汇总（已投/失败/已投过自动跳过 三档统计）

Boss vs LinkedIn vs Indeed:
  Boss   "投递"=主动 send_message;无 apply 概念,完成 = 招呼发出
  LinkedIn linkedin_apply_job;Easy Apply 形式,unresolved_fields 需用户处理
  Indeed   indeed_apply_job;smartapply,部分场景需要 fill_fields
"""
from __future__ import annotations

from tasks.registry import TaskTemplate, TaskStep, register_template
from tasks.steps import common, boss, linkedin, indeed, sprint2


BOSS_STEPS = [
    TaskStep(id="search",       title="搜索职位", fn=boss.step_search_jobs,         op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",   fn=boss.step_fetch_job_detail,    op_type="detail", iter_items=True),
    TaskStep(id="apply",        title="发起投递", fn=sprint2.step_apply_boss,       op_type="chat",
             iter_items=True, credit_sku="batch_apply"),
    TaskStep(id="summarize",    title="生成报告", fn=sprint2.step_summarize_applied),
]

LINKEDIN_STEPS = [
    TaskStep(id="search",       title="搜索职位", fn=linkedin.step_search_jobs,      op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",   fn=linkedin.step_fetch_job_detail, op_type="detail", iter_items=True),
    TaskStep(id="apply",        title="Easy Apply", fn=sprint2.step_apply_linkedin, op_type="chat",
             iter_items=True, credit_sku="batch_apply"),
    TaskStep(id="summarize",    title="生成报告", fn=sprint2.step_summarize_applied),
]

INDEED_STEPS = [
    TaskStep(id="search",       title="搜索职位", fn=indeed.step_search_jobs,        op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",   fn=indeed.step_fetch_job_detail,   op_type="detail", iter_items=True),
    TaskStep(id="apply",        title="Quick Apply", fn=sprint2.step_apply_indeed,  op_type="chat",
             iter_items=True, credit_sku="batch_apply"),
    TaskStep(id="summarize",    title="生成报告", fn=sprint2.step_summarize_applied),
]


TEMPLATE = TaskTemplate(
    id="jobseeker:batch_apply",
    role="jobseeker",
    title="批量投递",
    description="搜职位 → 拉详情 → 批量投递（自动跳过已投）→ 投递结果统计",
    emoji="🚀",
    estimated_min=8,
    steps_by_platform={
        "boss":     BOSS_STEPS,
        "linkedin": LINKEDIN_STEPS,
        "indeed":   INDEED_STEPS,
    },
)

register_template(TEMPLATE)
