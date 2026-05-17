"""tasks/templates/ — 业务流程编排(每个文件是一个逻辑模板)。

每个 template 用 `steps_by_platform` 字段为不同平台编排不同 step 列表。
平台共享 step 来自 tasks/steps/common.py,平台独有 step 来自 tasks/steps/{platform}.py。

加新平台:在已有 template 文件的 `steps_by_platform` 加一行
  `'linkedin': [TaskStep(...)]`,引用 linkedin step 即可。模板代码不动公共逻辑。
"""

# 触发 template 注册(import 即 register_template)
from tasks.templates import jobseeker_find_best_jobs  # noqa: F401
from tasks.templates import recruiter_hiring_pipeline  # noqa: F401
from tasks.templates import recruiter_inbox_triage  # noqa: F401
# Sprint 2 新增（GAP_ANALYSIS gap #2）
from tasks.templates import jobseeker_batch_apply           # noqa: F401
from tasks.templates import jobseeker_track_applications    # noqa: F401
from tasks.templates import recruiter_bulk_analyze_candidates  # noqa: F401
