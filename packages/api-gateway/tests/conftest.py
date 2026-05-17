"""共享测试 bootstrap — mock 重型外部依赖后暴露 server / http_routes。

放在 `conftest.py` 里 pytest 会在所有测试收集前自动加载，这样各测试文件不再
重复 MagicMock 样板（原 test_http_resolve.py 的 50 行模块级 mock 已下沉至此）。

保留的真模块：session_store、browser_pool、server_helpers、http_routes、
commands（后者在单测里仍可按需 monkeypatch 具体函数）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

# audit P2 fix:也把 repo-root 上 path,让 `from job_common.* import ...` 可用
_REPO_ROOT = _GW.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _mock_mod(**attrs):
    m = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


# FastMCP: 模块级调用 FastMCP()，必须最早 mock
_mcp_inst = MagicMock()
_mcp_inst.tool = lambda **_kw: (lambda f: f)  # no-op decorator
_mcp_inst.http_app = MagicMock(return_value=MagicMock())
sys.modules.setdefault("fastmcp", _mock_mod(
    FastMCP=MagicMock(return_value=_mcp_inst),
    Context=MagicMock(),
))

sys.modules.setdefault("dotenv", _mock_mod(load_dotenv=lambda: None))
sys.modules.setdefault("db", MagicMock())

# 带网络/FS 副作用的本地模块
for _m in [
    "ext_client", "agent_tracker", "admin_broadcaster",
    "webhook", "proxy_pool",
]:
    sys.modules.setdefault(_m, MagicMock())

# admin_broadcaster 以 `ab` 引入，server 调 ab.admin_broadcaster.broadcast
_ab = MagicMock()
_ab.admin_broadcaster = AsyncMock()
_ab.admin_broadcaster.broadcast = AsyncMock()
sys.modules["admin_broadcaster"] = _ab


# quota_tracker.QuotaExceededError 必须是真正的 Exception 子类
class _QuotaExceededError(Exception):
    pass


_qt_mod = MagicMock()
_qt_mod.quota_tracker = MagicMock()
_qt_mod.QuotaExceededError = _QuotaExceededError
sys.modules.setdefault("quota_tracker", _qt_mod)
