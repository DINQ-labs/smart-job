"""tasks/templates/recruiter_hiring_pipeline.py — 批量联系候选人(逻辑模板)

Boss 业务流程(本平台特化):
  1. 搜候选人
  2. 发招呼(iter)
  3. (可选)等回复 → 拉简历 — 当前 MVP 不做,Phase later

LinkedIn 流程(未来):
  1. 搜候选人
  2. 检查 InMail 配额(LinkedIn 独有)
  3. 检查 connection degree(LinkedIn 独有)
  4. 1°/2° 用 connect+message,3° 用 InMail
  5. 排程 followup

Indeed 流程(未来):
  1. 搜简历库(直接拿到简历,不需要先联系)
  2. 通过简历 contact

各平台流程结构本质不同 → 在 steps_by_platform 用不同 step 列表表达。
"""
from __future__ import annotations

from tasks.registry import TaskTemplate, TaskStep, register_template
from tasks.steps import common, boss, linkedin


BOSS_STEPS = [
    TaskStep(id="search",   title="搜候选人",   fn=boss.step_search_candidates,  op_type="search"),
    TaskStep(id="contact",  title="批量打招呼", fn=boss.step_contact_candidate,
             iter_items=True, op_type="chat", credit_sku="batch_greeting"),
    TaskStep(id="summarize", title="生成报告",   fn=common.step_summarize_pipeline),
]

# LinkedIn 流程跟 Boss 本质不同:
#   - 先 search
#   - 对每个候选人 check 一下 connection degree(LinkedIn 独有)
#   - 1°/2° 用 connect+message,3°+ 跳过(需要 Premium 才能 InMail,MVP 暂不接)
LINKEDIN_STEPS = [
    TaskStep(id="search",  title="搜候选人",     fn=linkedin.step_search_candidates,  op_type="search"),
    TaskStep(id="degree",  title="检查关系度数", fn=linkedin.step_check_degree,
             iter_items=True, op_type="default"),
    TaskStep(id="invite",  title="发送邀请",     fn=linkedin.step_invite_or_skip,
             iter_items=True, op_type="chat", credit_sku="batch_greeting"),
    TaskStep(id="summarize", title="生成报告",   fn=common.step_summarize_pipeline),  # 跨平台共享
]

# Indeed 不实现:Indeed Employer 业务模式是"候选人主动 apply 后 inbox triage",
# 不是"主动外联",不属于 hiring_pipeline 范畴。Indeed 适合的模板是
# `recruiter:inbox_triage`(future),那个模板会用 indeed_employer_* 工具。


TEMPLATE = TaskTemplate(
    id="recruiter:hiring_pipeline",
    role="recruiter",
    title="批量联系候选人",
    description="按职位关键词搜候选人 → 按平台规则批量发邀请/招呼",
    emoji="👥",
    estimated_min=5,
    steps_by_platform={
        "boss":     BOSS_STEPS,
        "linkedin": LINKEDIN_STEPS,
        # "indeed":  Indeed 没 outreach 模式,跳过
    },
)

register_template(TEMPLATE)
