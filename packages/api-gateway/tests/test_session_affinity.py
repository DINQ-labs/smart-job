"""E1 会话亲和 —— active session 状态 + _resolve_and_bind / _default_*_session 集成

覆盖：
  1. SessionStore.set/get/list/clear_active_session 基础契约
  2. 多租户安全：不允许把别人的 session 设为自己的 active
  3. 惰性清理：被 active 的 session 下线后 get 返回 None 且自动移除
  4. _resolve_and_bind 在多会话场景下优先用 active session（否则仍抛错）
  5. _default_li_session / _default_indeed_session 优先用 active
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import server  # noqa: F401  conftest bootstrap
from session_store import SessionEntry, session_store


def _make_entry(sid: str, user_id: str, sites=None) -> SessionEntry:
    entry = SessionEntry(
        session_id=sid,
        browser_id=sid[:16],
        ws=MagicMock(),  # 非 None → 视为 connected
        pending={},
        connected_at=datetime.now(timezone.utc).isoformat(),
        user_id=user_id,
    )
    if sites:
        entry.sites = set(sites)
    session_store._sessions[sid] = entry
    return entry


@pytest.fixture
def clean_store():
    """每个测试独立清空 store 状态（避免跨测试污染）。"""
    saved_sessions = dict(session_store._sessions)
    saved_active = {k: dict(v) for k, v in session_store._active_sessions.items()}
    session_store._sessions.clear()
    session_store._active_sessions.clear()
    yield session_store
    session_store._sessions.clear()
    session_store._active_sessions.clear()
    session_store._sessions.update(saved_sessions)
    session_store._active_sessions.update(saved_active)


class TestActiveSessionBasics:

    def test_set_and_get(self, clean_store):
        _make_entry("sid-A", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-A")
        assert clean_store.get_active_session("u1", "boss") == "sid-A"

    def test_get_returns_none_when_unset(self, clean_store):
        assert clean_store.get_active_session("u1", "boss") is None

    def test_platform_is_case_insensitive(self, clean_store):
        _make_entry("sid-A", user_id="u1")
        clean_store.set_active_session("u1", "BOSS", "sid-A")
        assert clean_store.get_active_session("u1", "boss") == "sid-A"

    def test_per_platform_independence(self, clean_store):
        _make_entry("sid-A", user_id="u1")
        _make_entry("sid-B", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-A")
        clean_store.set_active_session("u1", "linkedin", "sid-B")
        assert clean_store.get_active_session("u1", "boss") == "sid-A"
        assert clean_store.get_active_session("u1", "linkedin") == "sid-B"
        assert clean_store.get_active_session("u1", "indeed") is None

    def test_clear_single_platform(self, clean_store):
        _make_entry("sid-A", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-A")
        clean_store.set_active_session("u1", "linkedin", "sid-A")
        clean_store.clear_active_session("u1", "boss")
        assert clean_store.get_active_session("u1", "boss") is None
        assert clean_store.get_active_session("u1", "linkedin") == "sid-A"

    def test_clear_all_platforms(self, clean_store):
        _make_entry("sid-A", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-A")
        clean_store.set_active_session("u1", "linkedin", "sid-A")
        clean_store.clear_active_session("u1")
        assert clean_store.list_active_sessions("u1") == {}

    def test_list_active_sessions(self, clean_store):
        _make_entry("sid-A", user_id="u1")
        _make_entry("sid-B", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-A")
        clean_store.set_active_session("u1", "linkedin", "sid-B")
        out = clean_store.list_active_sessions("u1")
        assert out == {"boss": "sid-A", "linkedin": "sid-B"}


class TestActiveSessionMultiTenantSafety:
    """session 必须归属指定 user 才能设为 active —— 防跨租户污染。"""

    def test_rejects_wrong_user(self, clean_store):
        _make_entry("sid-other", user_id="u2")
        with pytest.raises(RuntimeError, match="多租户隔离"):
            clean_store.set_active_session("u1", "boss", "sid-other")

    def test_rejects_nonexistent_session(self, clean_store):
        with pytest.raises(RuntimeError, match="不存在"):
            clean_store.set_active_session("u1", "boss", "sid-ghost")

    def test_allows_session_without_user_id(self, clean_store):
        """entry.user_id 为空（扩展未上报）时允许设定，随后的调用会绑定 user_id。"""
        _make_entry("sid-new", user_id="")
        clean_store.set_active_session("u1", "boss", "sid-new")
        assert clean_store.get_active_session("u1", "boss") == "sid-new"


class TestActiveSessionLazyCleanup:
    """active session 的 session 下线后应返回 None 并从内部映射里移除。"""

    def test_offline_session_returns_none(self, clean_store):
        entry = _make_entry("sid-off", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-off")
        entry.ws = None  # 模拟断线
        assert clean_store.get_active_session("u1", "boss") is None
        # 验证内部映射已清理
        assert "boss" not in clean_store._active_sessions.get("u1", {})

    def test_list_skips_offline(self, clean_store):
        online = _make_entry("sid-on", user_id="u1")
        offline = _make_entry("sid-off", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-on")
        clean_store.set_active_session("u1", "linkedin", "sid-off")
        offline.ws = None
        out = clean_store.list_active_sessions("u1")
        assert out == {"boss": "sid-on"}


class TestResolveAndBindUsesActive:
    """_resolve_and_bind 在多活会话场景下应优先使用 active session。"""

    @pytest.mark.asyncio
    async def test_multi_active_uses_pinned(self, clean_store, monkeypatch):
        from server_helpers import _resolve_and_bind, _current_user_id
        _make_entry("sid-1", user_id="u1")
        _make_entry("sid-2", user_id="u1")
        clean_store.set_active_session("u1", "boss", "sid-2")
        token = _current_user_id.set("u1")
        try:
            sid = await _resolve_and_bind(agent_id="", session_id="", app_user_id="")
        finally:
            _current_user_id.reset(token)
        assert sid == "sid-2"

    @pytest.mark.asyncio
    async def test_multi_active_without_pin_still_errors(self, clean_store):
        from server_helpers import _resolve_and_bind, _current_user_id
        _make_entry("sid-1", user_id="u1")
        _make_entry("sid-2", user_id="u1")
        token = _current_user_id.set("u1")
        try:
            with pytest.raises(RuntimeError, match="有 2 个扩展会话"):
                await _resolve_and_bind(agent_id="", session_id="", app_user_id="")
        finally:
            _current_user_id.reset(token)

    @pytest.mark.asyncio
    async def test_single_active_ignores_pin(self, clean_store):
        """只有一个活会话时，根本不走多活分支，pin 无副作用。"""
        from server_helpers import _resolve_and_bind, _current_user_id
        _make_entry("sid-1", user_id="u1")
        token = _current_user_id.set("u1")
        try:
            sid = await _resolve_and_bind(agent_id="", session_id="", app_user_id="")
        finally:
            _current_user_id.reset(token)
        assert sid == "sid-1"


class TestDefaultPlatformSessionUsesActive:
    """mcp_tools_linkedin._default_li_session / indeed 优先返回 active session。"""

    def test_linkedin_default_prefers_active(self, clean_store):
        from server_helpers import _current_user_id
        from mcp_tools_linkedin import _default_li_session
        _make_entry("sid-first",  user_id="u1", sites={"linkedin"})
        _make_entry("sid-pinned", user_id="u1", sites={"linkedin"})
        clean_store.set_active_session("u1", "linkedin", "sid-pinned")
        token = _current_user_id.set("u1")
        try:
            assert _default_li_session() == "sid-pinned"
        finally:
            _current_user_id.reset(token)

    def test_linkedin_falls_back_when_no_pin(self, clean_store):
        from server_helpers import _current_user_id
        from mcp_tools_linkedin import _default_li_session
        e = _make_entry("sid-only", user_id="u1", sites={"linkedin"})
        token = _current_user_id.set("u1")
        try:
            assert _default_li_session() == "sid-only"
        finally:
            _current_user_id.reset(token)

    def test_indeed_default_prefers_active(self, clean_store):
        from server_helpers import _current_user_id
        from mcp_tools_indeed import _default_indeed_session
        _make_entry("sid-first",  user_id="u1", sites={"indeed"})
        _make_entry("sid-pinned", user_id="u1", sites={"indeed"})
        clean_store.set_active_session("u1", "indeed", "sid-pinned")
        token = _current_user_id.set("u1")
        try:
            assert _default_indeed_session() == "sid-pinned"
        finally:
            _current_user_id.reset(token)
