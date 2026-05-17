"""tasks/templates/recruiter_bulk_analyze_candidates.py — 批量分析候选人（C5 / GAP gap #2）。

区别于 recruiter:hiring_pipeline（发招呼）—— 本模板**只评分不外联**：
  1. 搜候选人
  2. 拉详情
  3. 评分（启发式 work_year + degree + 关键词命中；后续 Sprint 替换为 LLM）
  4. 汇总（高/中/低分桶 + Top 10）

平台覆盖：
  Boss     boss_search_candidates → boss_get_candidate_detail
  LinkedIn linkedin_search_candidates → linkedin_get_profile
  Indeed   暂不支持（Indeed Employer Resume DB 模式与"搜+分析"流程不同；
           未来通过 indeed_employer_search_candidates 等 tool 接入）
"""
from __future__ import annotations

from tasks.registry import TaskTemplate, TaskStep, register_template
from tasks.steps import boss, linkedin, sprint2


BOSS_STEPS = [
    TaskStep(id="search",       title="搜候选人",   fn=boss.step_search_candidates,       op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",     fn=sprint2.step_fetch_candidate_detail, op_type="detail", iter_items=True),
    TaskStep(id="evaluate",     title="AI 评分",    fn=sprint2.step_evaluate_candidate,     op_type="default",
             iter_items=True, credit_sku="batch_analyze"),
    TaskStep(id="summarize",    title="生成报告",   fn=sprint2.step_summarize_candidate_analysis),
]

LINKEDIN_STEPS = [
    TaskStep(id="search",       title="搜候选人",   fn=linkedin.step_search_candidates,     op_type="search"),
    TaskStep(id="fetch_detail", title="拉详情",     fn=sprint2.step_fetch_candidate_detail, op_type="detail", iter_items=True),
    TaskStep(id="evaluate",     title="AI 评分",    fn=sprint2.step_evaluate_candidate,     op_type="default",
             iter_items=True, credit_sku="batch_analyze"),
    TaskStep(id="summarize",    title="生成报告",   fn=sprint2.step_summarize_candidate_analysis),
]


TEMPLATE = TaskTemplate(
    id="recruiter:bulk_analyze_candidates",
    role="recruiter",
    title="批量分析候选人",
    description="搜候选人 → 拉详情 → AI 评分（不发招呼）→ 高/中/低匹配分布",
    emoji="🔍",
    estimated_min=6,
    steps_by_platform={
        "boss":     BOSS_STEPS,
        "linkedin": LINKEDIN_STEPS,
        # indeed: 后续接入 indeed_employer 工具时补
    },
)

register_template(TEMPLATE)
