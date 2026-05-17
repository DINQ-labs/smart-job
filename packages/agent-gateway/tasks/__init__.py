"""tasks/ — 长任务系统

模块结构:
  engine.py         TaskRunner 主循环 + 风控 retry/pause/skip/abort 处置
  registry.py       TaskTemplate(逻辑模板) + TaskStep + 注册表
  lease.py          Redis lease(多实例并发控制)
  mcp_client.py     Task 范围的 MCP session 封装
  steps/            可组合的原子 step 库(per-platform)
    common.py       跨平台 step(score / summarize / mcp helper)
    boss.py         Boss直聘专属 step
    linkedin.py     LinkedIn 专属(stub)
    indeed.py       Indeed 专属(stub)
  templates/        业务流程编排(每个文件 = 1 个逻辑模板)
    jobseeker_find_best_jobs.py
    recruiter_hiring_pipeline.py
"""

from tasks.engine import TaskRunner, run_task_in_background
from tasks.registry import TEMPLATES, register_template, get_template

# 触发 template 注册(import 包 → 包 __init__ import 各文件 → register)
from tasks import templates  # noqa: F401

__all__ = [
    "TaskRunner",
    "run_task_in_background",
    "TEMPLATES",
    "register_template",
    "get_template",
]
