"""审核补丁 #4：_err() 非 DEBUG 模式下屏蔽内部异常栈。

业务手写错误（明确、可理解）应原样透传；带堆栈 / 模块路径 / DB 内部信息的
系统级错误应被屏蔽为通用消息，避免生产环境泄露实现细节。
"""
from __future__ import annotations

import json
import os

import pytest

# 需要在 import server_helpers 之前就设环境变量。这些用例动态切换模式,
# 通过 monkeypatch server_helpers._API_DEBUG 避免进程级污染。
import server  # noqa: F401  conftest bootstrap
import server_helpers
from server_helpers import _err


def _decode(s: str) -> dict:
    return json.loads(s)


class TestBusinessMessagesPassThrough:
    """业务手写消息永远透传（不含 SYS_ERROR_MARKERS 任何特征）"""

    def test_session_required(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err("session_id 必填，请通过 pool.acquire() 获取后显式传入")
        assert _decode(out) == {
            "ok": False,
            "error": "session_id 必填，请通过 pool.acquire() 获取后显式传入",
        }

    def test_login_required(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err("未登录，请先调用 boss_login")
        assert _decode(out)["error"] == "未登录，请先调用 boss_login"

    def test_quota_exceeded(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err("配额已满，请明日重置后重试")
        assert _decode(out)["error"] == "配额已满，请明日重置后重试"

    def test_simple_validation(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err("参数 encrypt_job_id 不能为空")
        assert _decode(out)["error"] == "参数 encrypt_job_id 不能为空"


class TestSystemErrorsFiltered:
    """含系统错误特征的 str(Exception) PROD 模式屏蔽"""

    def test_traceback_filtered(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        raw = 'Traceback (most recent call last):\n  File "/app/db.py", line 42, in fetch\n    rows = ...'
        out = _err(raw)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_psycopg_error_filtered(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        raw = "psycopg.OperationalError: connection to server at \"localhost\" port 5432 failed"
        out = _err(raw)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_asyncpg_error_filtered(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        raw = "asyncpg.exceptions.UniqueViolationError: duplicate key on idx_rgi_comp"
        out = _err(raw)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_file_path_leak_filtered(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        raw = 'Unexpected error at File "/Users/joyhouse/internal/code.py":89'
        out = _err(raw)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_keyerror_leak_filtered(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        raw = "KeyError: 'internal_user_uuid'"
        out = _err(raw)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"


class TestDebugModeDisablesFilter:
    """设 API_DEBUG=1 则所有错误原样输出（开发排障用）"""

    def test_debug_shows_traceback(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", True)
        raw = 'Traceback (most recent call last):\n  File "/app/x.py"'
        out = _err(raw)
        assert _decode(out)["error"] == raw

    def test_debug_shows_psycopg_error(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", True)
        raw = "psycopg.OperationalError: connection refused"
        out = _err(raw)
        assert _decode(out)["error"] == raw


class TestForceSafeOverride:
    """force_safe=True 强制屏蔽（用于极敏感场景，即使 DEBUG 也不能泄露）"""

    def test_force_safe_in_prod(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err("这是正常的业务消息", force_safe=True)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_force_safe_even_in_debug(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", True)
        out = _err("这是正常的业务消息", force_safe=True)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"


class TestExceptionTypeDetection:
    """传 Exception 实例时按类型识别系统错误，比字符串匹配更准。"""

    def test_asyncio_timeout_exception_filtered(self, monkeypatch):
        import asyncio
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err(asyncio.TimeoutError("boss.zhipin.com 5s"))
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_connection_error_exception_filtered(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err(ConnectionError("Connection refused"))
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_os_error_exception_filtered(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err(OSError(111, "Connection refused"))
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_asyncpg_postgres_error_type_filtered(self, monkeypatch):
        """asyncpg 异常按类型命中，即使 str() 是干净的业务文本。"""
        import asyncpg
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        exc = asyncpg.PostgresError("some db problem")
        out = _err(exc)
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_business_exception_type_passes_through(self, monkeypatch):
        """普通 ValueError（业务手 raise）且消息干净 → 透传。"""
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err(ValueError("参数 encrypt_job_id 不能为空"))
        assert _decode(out)["error"] == "参数 encrypt_job_id 不能为空"

    def test_generic_exception_with_sys_marker_str_filtered(self, monkeypatch):
        """类型不是 sys，但 str() 带 marker → 仍然字符串兜底过滤。"""
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err(RuntimeError("KeyError: 'missing_field'"))
        assert _decode(out)["error"] == "操作失败，请稍后重试。"

    def test_debug_mode_shows_full_exception(self, monkeypatch):
        import asyncio
        monkeypatch.setattr(server_helpers, "_API_DEBUG", True)
        out = _err(asyncio.TimeoutError("timed out"))
        assert _decode(out)["error"] == "timed out"


class TestBusinessPrefixExemption:
    """以业务前缀开头的消息永远透传，避免误伤（如 'Invalid KeyError: ...'）。"""

    def test_invalid_prefix_exempt_even_with_marker(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err("Invalid salary format: KeyError: 'min'")
        assert _decode(out)["error"] == "Invalid salary format: KeyError: 'min'"

    def test_missing_prefix_exempt(self, monkeypatch):
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        out = _err("Missing required field: encrypt_job_id")
        assert _decode(out)["error"] == "Missing required field: encrypt_job_id"

    def test_chinese_business_prefix_exempt(self, monkeypatch):
        """中文业务前缀（"缺少 / 无效 / 非法"）同样豁免。"""
        monkeypatch.setattr(server_helpers, "_API_DEBUG", False)
        for msg in ("缺少参数 encrypt_geek_id", "无效的 encrypt_job_id", "非法的 session_id"):
            out = _err(msg)
            assert _decode(out)["error"] == msg, msg
