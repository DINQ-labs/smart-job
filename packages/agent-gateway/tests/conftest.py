"""测试 bootstrap — 把 repo-root 加到 sys.path,让 `from job_common.* import ...`
在 pytest 收集前就生效。

audit P2 fix(2026-05-10):之前 test_task_runner.py 用 `sys.path.insert(0, '../job-api-gateway')`
反向引 risk_signals,改用 job_common 共享包。conftest.py 是 pytest 唯一保证 ALL
测试文件加载前都跑一次的钩子。
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 也把 job-agent-gateway 自身加到 path,这样 `import db / config` 等 service-内
# 模块在 pytest 顶层也能找到(不用每个 test 重复 sys.path.insert(0, '..'))
_GATEWAY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GATEWAY not in sys.path:
    sys.path.insert(0, _GATEWAY)
