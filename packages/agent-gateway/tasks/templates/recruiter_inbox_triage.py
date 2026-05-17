"""tasks/templates/recruiter_inbox_triage.py — 批量处理收件箱(逻辑模板)

业务场景:雇主在 Indeed/LinkedIn 发布职位 → 候选人主动 apply → 雇主 inbox 累积
NEW/PENDING 待 review 的 application。这个模板帮雇主一键扫完所有 inbox。

跟 hiring_pipeline 的区别:
  - hiring_pipeline = 主动 outreach(搜候选人 → 发邀请)
  - inbox_triage   = 被动 review(列 inbox → 看简历摘要 → 后续手动处理)

支持平台:
  - Indeed:Indeed Employer 简历库 inbox(主战场)
  - LinkedIn:Recruiter Inbox(future,需要 LinkedIn Recruiter 订阅)
  - Boss:Boss 直接是双向 chat 模式,没有"inbox"概念,跳过

流程(Indeed):
  1. list_pending  列雇主在招 job + 平铺 NEW/PENDING 候选人
  2. fetch_preview iter,对每个候选人拉详情(含简历摘要 _preview)
  3. summarize     按 job 分组列出待处理候选人 + 简历亮点
"""
from __future__ import annotations

from tasks.registry import TaskTemplate, TaskStep, register_template
from tasks.steps import indeed


INDEED_STEPS = [
    TaskStep(id="list",      title="扫描 inbox",     fn=indeed.step_list_pending_candidates,
             op_type="search"),
    TaskStep(id="preview",   title="拉简历摘要",     fn=indeed.step_fetch_candidate_preview,
             iter_items=True, op_type="detail"),
    TaskStep(id="summarize", title="生成报告",       fn=indeed.step_summarize_inbox),
]


TEMPLATE = TaskTemplate(
    id="recruiter:inbox_triage",
    role="recruiter",
    title="批量处理收件箱",
    description="一键扫雇主所有在招 job 的 NEW/PENDING 候选人 + 简历摘要",
    emoji="📥",
    estimated_min=4,
    steps_by_platform={
        "indeed": INDEED_STEPS,
        # "linkedin": LINKEDIN_RECRUITER_INBOX_STEPS,  # 需 LinkedIn Recruiter 订阅
        # boss 不支持(没 inbox 概念)
    },
)

register_template(TEMPLATE)
