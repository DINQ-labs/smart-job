"""
Tests for _http_resolve_session and related HTTP-layer changes.

Run:
    cd job-api-gateway
    pip install -r requirements-test.txt
    pytest tests/ -v

模块级 mock 下沉到 tests/conftest.py —— 本文件只关心用例自身的 patch 逻辑。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import server  # noqa: F401  conftest 已 mock fastmcp/db/ext_client 等
import http_routes
from server_helpers import _http_resolve_session

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_dict(sid: str, status: str = "connected") -> dict:
    return {"session_id": sid, "status": status}


def _fake_entry():
    """Minimal SessionEntry stand-in (just needs to be truthy)."""
    return object()


def _make_json_request(body: dict) -> MagicMock:
    """Mock Starlette Request whose .json() returns body."""
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    return req


# ── _http_resolve_session — 7 branches ───────────────────────────────────────

class TestHttpResolveSession:
    """Pool-mode routing: one function, seven outcomes."""

    @pytest.mark.asyncio
    async def test_session_id_exists(self):
        """Explicit session_id that exists → return it as-is."""
        with patch.object(server.session_store, "get", return_value=_fake_entry()):
            assert await _http_resolve_session("sid_abc", "") == "sid_abc"

    @pytest.mark.asyncio
    async def test_session_id_missing(self):
        """Explicit session_id that is gone → RuntimeError."""
        with patch.object(server.session_store, "get", return_value=None):
            with pytest.raises(RuntimeError, match="session 不存在或已断开"):
                await _http_resolve_session("deadbeef", "")

    @pytest.mark.asyncio
    async def test_app_user_pool_hit(self):
        """app_user_id with active pool assignment → session from pool."""
        with patch.object(
            server.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value="pool_sid"),
        ):
            assert await _http_resolve_session("", "alice") == "pool_sid"

    @pytest.mark.asyncio
    async def test_app_user_not_acquired(self):
        """app_user_id without pool assignment → error with acquire hint."""
        with patch.object(
            server.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="请先调用 POST /api/pool/acquire"):
                await _http_resolve_session("", "bob")

    @pytest.mark.asyncio
    async def test_no_id_single_session(self):
        """No id given, exactly one connected session → auto-select."""
        with patch.object(
            server.session_store, "list_all",
            return_value=[_session_dict("only_one")],
        ):
            assert await _http_resolve_session("", "") == "only_one"

    @pytest.mark.asyncio
    async def test_no_id_zero_sessions(self):
        """No id, no extensions connected → RuntimeError."""
        with patch.object(server.session_store, "list_all", return_value=[]):
            with pytest.raises(RuntimeError, match="没有已连接的扩展"):
                await _http_resolve_session("", "")

    @pytest.mark.asyncio
    async def test_no_id_multi_session(self):
        """No id, multiple extensions → RuntimeError listing available ids."""
        sessions = [_session_dict("aaa" * 6), _session_dict("bbb" * 6)]
        with patch.object(server.session_store, "list_all", return_value=sessions):
            with pytest.raises(RuntimeError, match="多会话模式必须指定"):
                await _http_resolve_session("", "")


# ── handle_cli: session-free whitelist ───────────────────────────────────────

class TestHandleCliSessionFreeWhitelist:
    """boss_list_sessions / boss_list_agents must work with zero extensions connected."""

    @pytest.mark.asyncio
    async def test_list_sessions_bypasses_resolve(self):
        req = _make_json_request({"tool": "boss_list_sessions", "params": {}})
        with patch.object(server.session_store, "list_all", return_value=[]):
            with patch("http_routes.cmd_list_sessions", new=AsyncMock(return_value=[])):
                resp = await http_routes.handle_cli(req)
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["ok"] is True

    @pytest.mark.asyncio
    async def test_list_agents_bypasses_resolve(self):
        req = _make_json_request({"tool": "boss_list_agents", "params": {}})
        with patch.object(server.session_store, "list_all", return_value=[]):
            with patch("http_routes.cmd_list_agents", new=AsyncMock(return_value=[])):
                resp = await http_routes.handle_cli(req)
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["ok"] is True

    @pytest.mark.asyncio
    async def test_session_required_tool_fails_without_session(self):
        """A session-required tool (boss_check_login) fails when no extensions connected."""
        req = _make_json_request({"tool": "boss_check_login", "params": {}})
        with patch.object(server.session_store, "list_all", return_value=[]):
            resp = await http_routes.handle_cli(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert body["ok"] is False


# ── api_intro_send: only start_chat, no send_message ─────────────────────────

class TestApiIntroSend:
    """api_intro_send only calls start_chat (friend/add).
    Boss直聘 requires the other party to accept before messages can be sent."""

    @pytest.mark.asyncio
    async def test_resolve_failure(self):
        """Session resolution failure surfaces clearly."""
        req = _make_json_request({"encrypt_job_id": "job1", "app_user_id": "nobody"})
        with patch.object(
            server.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value=None),
        ):
            resp = await http_routes.api_intro_send(req)
        body = json.loads(resp.body)
        assert body["ok"] is False
        assert "acquire" in body["error"]

    @pytest.mark.asyncio
    async def test_start_chat_failure_labeled(self):
        """cmd_start_chat failure is labeled 'start_chat 失败'."""
        req = _make_json_request({"encrypt_job_id": "job1", "session_id": "sid1"})
        with patch.object(server.session_store, "get", return_value=_fake_entry()):
            with patch("http_routes.cmd_start_chat",
                       new=AsyncMock(side_effect=RuntimeError("token expired"))):
                resp = await http_routes.api_intro_send(req)
        body = json.loads(resp.body)
        assert body["ok"] is False
        assert "start_chat 失败" in body["error"]
        assert "token expired" in body["error"]

    @pytest.mark.asyncio
    async def test_success_no_send_message(self):
        """start_chat succeeds → ok=true, data has only start_chat (no send_message)."""
        req = _make_json_request({
            "encrypt_job_id": "job1", "content": "自我介绍", "session_id": "sid1"
        })
        with patch.object(server.session_store, "get", return_value=_fake_entry()):
            with patch("http_routes.cmd_start_chat",
                       new=AsyncMock(return_value={"greeting": "希望和你聊聊这个职位"})):
                resp = await http_routes.api_intro_send(req)
        body = json.loads(resp.body)
        assert body["ok"] is True
        assert "start_chat" in body["data"]
        assert "send_message" not in body["data"]

    @pytest.mark.asyncio
    async def test_content_not_required(self):
        """content is optional — reserved for future custom greeting support."""
        req = _make_json_request({"encrypt_job_id": "job1", "session_id": "sid1"})
        with patch.object(server.session_store, "get", return_value=_fake_entry()):
            with patch("http_routes.cmd_start_chat",
                       new=AsyncMock(return_value={"greeting": "default"})):
                resp = await http_routes.api_intro_send(req)
        body = json.loads(resp.body)
        assert body["ok"] is True


# ── api_check_login: no longer writes to session_store ───────────────────────

class TestApiCheckLoginNoNonPoolWrite:
    """After the fix, api_check_login must NOT call session_store.bind_app_user."""

    @pytest.mark.asyncio
    async def test_no_bind_app_user_on_login_success(self):
        """bind_app_user must never be called, even when logged_in=True."""
        from starlette.datastructures import QueryParams

        req = MagicMock()
        req.query_params = QueryParams("app_user_id=alice&session_id=sid1")

        with patch.object(server.session_store, "get", return_value=_fake_entry()):
            with patch("http_routes.cmd_check_login",
                       new=AsyncMock(return_value={"logged_in": True, "name": "Alice"})):
                with patch.object(
                    server.session_store, "bind_app_user"
                ) as mock_bind:
                    await http_routes.api_check_login(req)
                    mock_bind.assert_not_called()
