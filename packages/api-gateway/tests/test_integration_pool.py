"""Pool 模式端到端集成测试 (C1)

用 httpx.ASGITransport 打一个真实的 Starlette app（由 http_routes.register_routes
注册路由），覆盖池化流程：acquire → check-login → search → intro/send → release。

mock 范围：
  - fastmcp / dotenv / db / ext_client / admin_broadcaster / quota_tracker（conftest）
  - browser_pool 的 acquire / release / get_session_for_user（按需 patch）
  - commands.cmd_* （按需 patch 返回 canned 数据）

不 mock 的真实组件：
  - Starlette routing / 路由分发 / 请求解析 / JSON 响应
  - session_store（真实内存实现）
  - _http_resolve_session 路径选择逻辑
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport
from starlette.applications import Starlette

# conftest 已 mock 重型依赖
import server  # noqa: F401  - ensure module loaded with mocks in place
import http_routes


@pytest.fixture
def app():
    """真 Starlette app + register_routes，绕开 MagicMock http_app。"""
    app = Starlette()
    http_routes.register_routes(app)
    return app


@pytest.fixture
def client(app):
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _fake_entry(sid: str = "sid-real-1"):
    """最小 SessionEntry 替身 —— 只要 .session_id / .get 返回它即可。"""
    e = MagicMock()
    e.session_id = sid
    return e


class TestPoolAcquireRelease:
    """POST /pool/acquire + /pool/release — 路由到 browser_pool 的合约。"""

    @pytest.mark.asyncio
    async def test_acquire_missing_app_user_id_returns_400(self, client):
        async with client:
            resp = await client.post("/pool/acquire", json={})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False
        assert "app_user_id" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_acquire_routes_to_browser_pool(self, client):
        with patch.object(
            http_routes.browser_pool, "acquire",
            new=AsyncMock(return_value={"ok": True, "session_id": "sid-pool"}),
        ) as mock_acq:
            async with client:
                resp = await client.post(
                    "/pool/acquire",
                    json={"app_user_id": "alice", "platform": "bosszp", "timeout_sec": 30},
                )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "session_id": "sid-pool"}
        mock_acq.assert_awaited_once_with("alice", "bosszp", 30)

    @pytest.mark.asyncio
    async def test_release_routes_to_browser_pool(self, client):
        with patch.object(
            http_routes.browser_pool, "release",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_rel:
            async with client:
                resp = await client.post("/pool/release", json={"app_user_id": "alice"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_rel.assert_awaited_once_with("alice")

    @pytest.mark.asyncio
    async def test_pool_get_session_hit(self, client):
        with patch.object(
            http_routes.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value="sid-pool-2"),
        ):
            async with client:
                resp = await client.get("/pool/session/alice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["session_id"] == "sid-pool-2"

    @pytest.mark.asyncio
    async def test_pool_get_session_miss(self, client):
        with patch.object(
            http_routes.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value=None),
        ):
            async with client:
                resp = await client.get("/pool/session/bob")
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False


class TestCheckLoginThroughPool:
    """GET /boss/check-login — pool 路径：先 get_session_for_user → cmd_check_login"""

    @pytest.mark.asyncio
    async def test_no_session_returns_no_session_flag(self, client):
        with patch.object(
            http_routes.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value=None),
        ):
            async with client:
                resp = await client.get("/boss/check-login?app_user_id=alice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["no_session"] is True
        assert body["data"]["logged_in"] is False

    @pytest.mark.asyncio
    async def test_login_success_flows_through(self, client):
        with patch.object(
            http_routes.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value="sid-pool"),
        ):
            with patch("http_routes.cmd_check_login",
                       new=AsyncMock(return_value={"logged_in": True, "name": "Alice", "userId": "u1"})):
                async with client:
                    resp = await client.get("/boss/check-login?app_user_id=alice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["logged_in"] is True
        assert body["data"]["name"] == "Alice"
        assert body["data"]["session_id"] == "sid-pool"


class TestSearchJobsThroughPool:
    """GET /boss/jobs — 验证 pool 模式下 search 流全链路路由"""

    @pytest.mark.asyncio
    async def test_search_missing_keyword_returns_400(self, client):
        async with client:
            resp = await client.get("/boss/jobs?keyword=")
        assert resp.status_code == 400
        assert "keyword" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_search_resolves_pool_session_and_dispatches(self, client):
        canned = {"raw": {"zpData": {"jobList": [{"encryptJobId": "j1"}], "totalCount": 1}}}
        with patch.object(
            http_routes.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value="sid-pool"),
        ):
            with patch("http_routes.cmd_search_jobs",
                       new=AsyncMock(return_value=canned)) as mock_search:
                async with client:
                    resp = await client.get(
                        "/boss/jobs?keyword=python&city=101010100&page=1&app_user_id=alice"
                    )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["total"] == 1
        assert body["data"]["jobs"][0]["encryptJobId"] == "j1"
        # 验证 session 已被 pool 解析并透传
        _, kw = mock_search.call_args
        assert kw["session_id"] == "sid-pool"
        assert kw["city"] == 101010100

    @pytest.mark.asyncio
    async def test_search_app_user_without_acquire_surfaces_acquire_hint(self, client):
        with patch.object(
            http_routes.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value=None),
        ):
            async with client:
                resp = await client.get("/boss/jobs?keyword=python&app_user_id=bob")
        assert resp.status_code == 500
        body = resp.json()
        assert body["ok"] is False
        assert "acquire" in body["error"]


class TestIntroSendThroughPool:
    """POST /boss/intro/send — pool 路径 + start_chat dispatch"""

    @pytest.mark.asyncio
    async def test_missing_encrypt_job_id_returns_400(self, client):
        async with client:
            resp = await client.post("/boss/intro/send", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_success_with_pool_session(self, client):
        with patch.object(
            http_routes.browser_pool, "get_session_for_user",
            new=AsyncMock(return_value="sid-pool"),
        ):
            with patch("http_routes.cmd_start_chat",
                       new=AsyncMock(return_value={"greeting": "hi there"})):
                async with client:
                    resp = await client.post(
                        "/boss/intro/send",
                        json={"encrypt_job_id": "j1", "app_user_id": "alice"},
                    )
        body = resp.json()
        assert body["ok"] is True
        assert "start_chat" in body["data"]
        assert body["data"]["start_chat"]["greeting"] == "hi there"


class TestPoolLifecycleEndToEnd:
    """acquire → check-login → search → intro/send → release 串起来的回归用例。"""

    @pytest.mark.asyncio
    async def test_full_happy_path(self, client):
        search_data = {"raw": {"zpData": {"jobList": [{"encryptJobId": "j42"}], "totalCount": 1}}}

        with patch.object(http_routes.browser_pool, "acquire",
                          new=AsyncMock(return_value={"ok": True, "session_id": "sid-full"})):
            with patch.object(http_routes.browser_pool, "get_session_for_user",
                              new=AsyncMock(return_value="sid-full")):
                with patch.object(http_routes.browser_pool, "release",
                                  new=AsyncMock(return_value={"ok": True})):
                    with patch("http_routes.cmd_check_login",
                               new=AsyncMock(return_value={"logged_in": True, "name": "A"})):
                        with patch("http_routes.cmd_search_jobs",
                                   new=AsyncMock(return_value=search_data)):
                            with patch("http_routes.cmd_start_chat",
                                       new=AsyncMock(return_value={"greeting": "hi"})):
                                async with client:
                                    r1 = await client.post("/pool/acquire",
                                                           json={"app_user_id": "alice"})
                                    assert r1.json()["session_id"] == "sid-full"

                                    r2 = await client.get("/boss/check-login?app_user_id=alice")
                                    assert r2.json()["data"]["logged_in"] is True

                                    r3 = await client.get(
                                        "/boss/jobs?keyword=python&app_user_id=alice"
                                    )
                                    assert r3.json()["data"]["jobs"][0]["encryptJobId"] == "j42"

                                    r4 = await client.post(
                                        "/boss/intro/send",
                                        json={"encrypt_job_id": "j42", "app_user_id": "alice"},
                                    )
                                    assert r4.json()["ok"] is True

                                    r5 = await client.post("/pool/release",
                                                           json={"app_user_id": "alice"})
                                    assert r5.json()["ok"] is True
