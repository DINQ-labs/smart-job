"""B2 _run_boss_tool / _run_site_tool 的契约测试。

这两个 helper 是 mcp_tools_*.py 的共享骨架。确保：
  1. 成功路径：sid 正确解析、cmd_fn 被 await、结果包成 _ok
  2. 失败路径：解析失败返回 _err（Boss），sid 为空返回 hint（site）
  3. pass_agent_id 开关按约定传递 / 省略 agent_id 关键字
  4. cmd_fn 抛异常时返回 _err
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import server  # noqa: F401  conftest bootstrap
from server_helpers import _run_boss_tool, _run_site_tool, _parse_json_arg


def _parse(resp: str) -> dict:
    """_ok / _err 返回的是 JSON 字符串 —— 解析回 dict 便于断言。"""
    return json.loads(resp)


class TestRunSiteTool:
    """LinkedIn / Indeed 风格（session_id + _default_*_session 兜底）"""

    @pytest.mark.asyncio
    async def test_success_with_explicit_session_id(self):
        default_fn = MagicMock(return_value="fallback-sid")
        cmd_fn = AsyncMock(return_value={"ok": True, "result": "hello"})
        resp = await _run_site_tool("explicit-sid", default_fn, cmd_fn, "arg1", kw="kw1")
        data = _parse(resp)
        # _ok 返回的是 cmd 的原始结果（字符串已 JSON-dump），没有外层 ok 字段
        # default_fn 被 bypass
        default_fn.assert_not_called()
        cmd_fn.assert_awaited_once_with("explicit-sid", "arg1", kw="kw1")

    @pytest.mark.asyncio
    async def test_success_via_default_fallback(self):
        default_fn = MagicMock(return_value="fallback-sid")
        cmd_fn = AsyncMock(return_value={"greeted": True})
        resp = await _run_site_tool("", default_fn, cmd_fn)
        # _ok 直接 JSON-dump cmd 的返回值（不再包 {"ok":True,...}）
        assert _parse(resp) == {"greeted": True}
        default_fn.assert_called_once()
        cmd_fn.assert_awaited_once_with("fallback-sid")

    @pytest.mark.asyncio
    async def test_empty_sid_returns_no_session_hint(self):
        """_default_*_session 返回 "" 时提前报错，不调 cmd_fn（避免产生无意义的 session '' 错）"""
        default_fn = MagicMock(return_value="")
        cmd_fn = AsyncMock()
        resp = await _run_site_tool(
            "", default_fn, cmd_fn, no_session_hint="自定义提示：请登录",
        )
        data = _parse(resp)
        assert data["ok"] is False
        assert data["error"] == "自定义提示：请登录"
        cmd_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_hint_when_not_provided(self):
        default_fn = MagicMock(return_value="")
        cmd_fn = AsyncMock()
        resp = await _run_site_tool("", default_fn, cmd_fn)
        data = _parse(resp)
        assert data["ok"] is False
        assert "扩展会话" in data["error"]

    @pytest.mark.asyncio
    async def test_cmd_exception_wrapped_as_err(self):
        default_fn = MagicMock(return_value="sid")
        cmd_fn = AsyncMock(side_effect=RuntimeError("cmd 炸了"))
        resp = await _run_site_tool("", default_fn, cmd_fn)
        data = _parse(resp)
        assert data["ok"] is False
        assert "cmd 炸了" in data["error"]


class TestRunBossTool:
    """Boss 风格（_resolve_and_bind + agent_id 注入）"""

    @pytest.mark.asyncio
    async def test_success_passes_session_id_and_agent_id(self, monkeypatch):
        import server_helpers
        monkeypatch.setattr(server_helpers, "_get_agent_id",
                            lambda ctx: "agent-42")
        monkeypatch.setattr(server_helpers, "_resolve_and_bind",
                            AsyncMock(return_value="resolved-sid"))
        cmd_fn = AsyncMock(return_value={"k": "v"})
        resp = await _run_boss_tool(
            MagicMock(), session_id="", app_user_id="alice",
            cmd_fn=cmd_fn, encrypt_job_id="j1",
        )
        assert _parse(resp) == {"k": "v"}
        cmd_fn.assert_awaited_once_with(
            session_id="resolved-sid", agent_id="agent-42", encrypt_job_id="j1",
        )

    @pytest.mark.asyncio
    async def test_pass_agent_id_false_omits_agent_id(self, monkeypatch):
        """部分 cmd（如 linkedin_recruiter_*）不接受 agent_id 参数 → 不传"""
        import server_helpers
        monkeypatch.setattr(server_helpers, "_get_agent_id",
                            lambda ctx: "agent-7")
        monkeypatch.setattr(server_helpers, "_resolve_and_bind",
                            AsyncMock(return_value="sid-1"))
        cmd_fn = AsyncMock(return_value={"ok": True})
        resp = await _run_boss_tool(
            MagicMock(), "", "", cmd_fn,
            pass_agent_id=False, project_urn="urn:1",
        )
        assert _parse(resp) == {"ok": True}
        cmd_fn.assert_awaited_once_with(session_id="sid-1", project_urn="urn:1")
        # 明确没有 agent_id
        assert "agent_id" not in cmd_fn.call_args.kwargs

    @pytest.mark.asyncio
    async def test_resolve_failure_returns_err_without_calling_cmd(self, monkeypatch):
        import server_helpers
        monkeypatch.setattr(server_helpers, "_get_agent_id", lambda ctx: "")
        monkeypatch.setattr(server_helpers, "_resolve_and_bind",
                            AsyncMock(side_effect=RuntimeError("没有找到你的扩展会话")))
        cmd_fn = AsyncMock()
        resp = await _run_boss_tool(MagicMock(), "", "", cmd_fn)
        data = _parse(resp)
        assert data["ok"] is False
        assert "没有找到你的扩展会话" in data["error"]
        cmd_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cmd_exception_wrapped_as_err(self, monkeypatch):
        import server_helpers
        monkeypatch.setattr(server_helpers, "_get_agent_id", lambda ctx: "")
        monkeypatch.setattr(server_helpers, "_resolve_and_bind",
                            AsyncMock(return_value="sid"))
        cmd_fn = AsyncMock(side_effect=ValueError("bad input"))
        resp = await _run_boss_tool(MagicMock(), "", "", cmd_fn)
        data = _parse(resp)
        assert data["ok"] is False
        assert "bad input" in data["error"]

    @pytest.mark.asyncio
    async def test_site_filter_propagates_to_resolve(self, monkeypatch):
        """site 参数用于多站点匹配 —— 必须透传给 _resolve_and_bind"""
        import server_helpers
        monkeypatch.setattr(server_helpers, "_get_agent_id", lambda ctx: "")
        resolve_mock = AsyncMock(return_value="sid-li")
        monkeypatch.setattr(server_helpers, "_resolve_and_bind", resolve_mock)
        cmd_fn = AsyncMock(return_value={})
        await _run_boss_tool(
            MagicMock(), "sid-hint", "alice", cmd_fn,
            site="linkedin", pass_agent_id=False,
        )
        resolve_mock.assert_awaited_once_with("", "sid-hint", "alice", site="linkedin")


class TestParseJsonArg:
    """_parse_json_arg: 空串→默认值 / 已解析对象透传 / JSON 字符串解析 / 失败抛 ValueError"""

    def test_empty_string_returns_default(self):
        assert _parse_json_arg("", "x", {}) == {}
        assert _parse_json_arg("", "x", []) == []
        assert _parse_json_arg("", "x", None) is None

    def test_none_returns_default(self):
        assert _parse_json_arg(None, "x", {"k": "v"}) == {"k": "v"}

    def test_already_parsed_passthrough(self):
        """Agent 偶尔直接塞 dict/list 时不再 loads，原样返回。"""
        assert _parse_json_arg({"a": 1}, "x", {}) == {"a": 1}
        assert _parse_json_arg([1, 2], "x", []) == [1, 2]

    def test_valid_json_string_parsed(self):
        assert _parse_json_arg('{"a": 1}', "x", None) == {"a": 1}
        assert _parse_json_arg('[1, 2, 3]', "x", []) == [1, 2, 3]

    def test_invalid_json_raises_value_error_with_field_name(self):
        with pytest.raises(ValueError, match="profile_data JSON 解析失败"):
            _parse_json_arg("{not json}", "profile_data", None)
