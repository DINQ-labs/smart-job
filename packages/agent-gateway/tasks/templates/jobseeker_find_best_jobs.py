"""tasks/templates/jobseeker_find_best_jobs.py — 找最匹配的职位(逻辑模板)

业务流程(4 步):
  1. 搜索职位(平台特定:keyword + 地点 + 平台 API)
  2. 拉详情(平台特定 iter:为每个 job 拉完整 JD,用于和用户简历做匹配)
     ↪ 失败容忍:某个 item detail 拉不到不阻塞整个 task,score 用 search item 字段兜底
  3. 评分排序(平台无关:优先用 detail.salary*,缺失用 item.salaryDesc 等)
  4. 生成报告(平台无关:用 ranked + skipped)

**为什么保留 fetch_detail**:
  - search 列表的 salaryDesc 是粗的字符串("15-25K"),detail 才有 salary_min/max + JD 文本
  - 后续上 LLM resume↔JD 匹配时必须有 detail 全文,不然只能按 title 配
  - fetch_detail_with_fallback 让单 item 失败不影响其它,真风控才暂停
"""
from __future__ import annotations

from tasks.registry import TaskTemplate, TaskStep, register_template
from tasks.steps import common, boss, linkedin, indeed


BOSS_STEPS = [
    TaskStep(id="search",       title="搜索职位", fn=boss.step_search_jobs,         op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",   fn=boss.step_fetch_job_detail,
             op_type="detail", iter_items=True, credit_sku="batch_analyze"),
    TaskStep(id="score",        title="评分排序", fn=common.step_score_by_salary),
    TaskStep(id="summarize",    title="生成报告", fn=common.step_summarize_top_jobs),
]

LINKEDIN_STEPS = [
    TaskStep(id="search",       title="搜索职位", fn=linkedin.step_search_jobs,      op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",   fn=linkedin.step_fetch_job_detail,
             op_type="detail", iter_items=True, credit_sku="batch_analyze"),
    TaskStep(id="score",        title="评分排序", fn=common.step_score_by_salary),
    TaskStep(id="summarize",    title="生成报告", fn=common.step_summarize_top_jobs),
]

INDEED_STEPS = [
    TaskStep(id="search",       title="搜索职位", fn=indeed.step_search_jobs,        op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",   fn=indeed.step_fetch_job_detail,
             op_type="detail", iter_items=True, credit_sku="batch_analyze"),
    TaskStep(id="score",        title="评分排序", fn=common.step_score_by_salary),
    TaskStep(id="summarize",    title="生成报告", fn=common.step_summarize_top_jobs),
]


TEMPLATE = TaskTemplate(
    id="jobseeker:find_best_jobs",
    role="jobseeker",
    title="找最匹配的职位",
    description="搜职位 → 拉详情(用于和简历匹配)→ 按薪资挑 Top 5",
    emoji="🎯",
    estimated_min=4,
    steps_by_platform={
        "boss":     BOSS_STEPS,
        "linkedin": LINKEDIN_STEPS,
        "indeed":   INDEED_STEPS,
    },
)

register_template(TEMPLATE)
