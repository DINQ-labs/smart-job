"""/admin/errors endpoint 测试。

校验：
  - 鉴权
  - 默认参数透传给 db.list_commands（response_ok=0）
  - tool_name / since_iso / keyword / agent_id / session_id 透传
  - 响应结构含 {errors, tool_names, limit, offset}
  - limit / offset 边界 + 非整数报 400
  - _parse_response_ok 三态语义
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import server  # noqa: F401  conftest bootstrap
import http_routes


class _FakeRequest:
    def __init__(self, query: dict | None = None, *, auth_ok: bool = True):
        self._query = query or {}
        self._auth_ok = auth_ok

    @property
    def query_params(self):
        return self._query


def _patch_auth(monkeypatch, ok: bool = True):
    if ok:
        monkeypatch.setattr(http_routes, "_auth_required", lambda req: None)
    else:
        from starlette.responses import JSONResponse
        monkeypatch.setattr(http_routes, "_auth_required",
                            lambda req: JSONResponse({"error": "unauthorized"},
                                                     status_code=401))


def _decode(resp):
    import json
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_unauthorized(monkeypatch):
    _patch_auth(monkeypatch, ok=False)
    resp = await http_routes.admin_get_errors(_FakeRequest({}))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_default_filters(monkeypatch):
    _patch_auth(monkeypatch)
    fake_list = AsyncMock(return_value=[
        {"id": 1, "tool_name": "boss_get_job_detail", "error": "code 37"},
    ])
    fake_tool_names = AsyncMock(return_value=["boss_get_job_detail", "boss_search_jobs"])
    monkeypatch.setattr(http_routes.db, "list_commands", fake_list)
    monkeypatch.setattr(http_routes.db, "list_command_tool_names", fake_tool_names)

    resp = await http_routes.admin_get_errors(_FakeRequest({}))
    assert resp.status_code == 200
    body = _decode(resp)
    assert "errors" in body
    assert body["errors"][0]["tool_name"] == "boss_get_job_detail"
    assert body["tool_names"] == ["boss_get_job_detail", "boss_search_jobs"]
    assert body["limit"] == 100
    assert body["offset"] == 0
    # response_ok=0 必须默认透传
    kwargs = fake_list.await_args.kwargs
    assert kwargs.get("response_ok") == 0


@pytest.mark.asyncio
async def test_filter_passthrough(monkeypatch):
    _patch_auth(monkeypatch)
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(http_routes.db, "list_commands", fake)
    monkeypatch.setattr(http_routes.db, "list_command_tool_names",
                        AsyncMock(return_value=[]))

    await http_routes.admin_get_errors(_FakeRequest({
        "session_id": "sess-1",
        "agent_id": "agent-A",
        "tool_name": "boss_get_job_detail",
        "since_iso": "2026-04-26T00:00:00Z",
        "keyword": "code 37",
        "limit": "50",
        "offset": "100",
    }))
    args, kwargs = fake.await_args.args, fake.await_args.kwargs
    # list_commands(sid, aid, limit, offset, response_ok=, tool_name=, since_iso=, keyword=)
    assert args[0] == "sess-1"
    assert args[1] == "agent-A"
    assert args[2] == 50
    assert args[3] == 100
    assert kwargs["response_ok"] == 0
    assert kwargs["tool_name"] == "boss_get_job_detail"
    assert kwargs["since_iso"] == "2026-04-26T00:00:00Z"
    assert kwargs["keyword"] == "code 37"


@pytest.mark.asyncio
async def test_tool_names_uses_since_iso(monkeypatch):
    """tool_names 列表的 since_iso 应跟 errors 用一样的过滤窗口。"""
    _patch_auth(monkeypatch)
    monkeypatch.setattr(http_routes.db, "list_commands",
                        AsyncMock(return_value=[]))
    fake_tn = AsyncMock(return_value=["x"])
    monkeypatch.setattr(http_routes.db, "list_command_tool_names", fake_tn)

    await http_routes.admin_get_errors(_FakeRequest(
        {"since_iso": "2026-04-26T00:00:00Z"}
    ))
    kwargs = fake_tn.await_args.kwargs
    assert kwargs["since_iso"] == "2026-04-26T00:00:00Z"
    assert kwargs["response_ok"] == 0


@pytest.mark.asyncio
async def test_invalid_limit_offset(monkeypatch):
    _patch_auth(monkeypatch)
    resp = await http_routes.admin_get_errors(_FakeRequest({"limit": "abc"}))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_limit_clamped_to_500(monkeypatch):
    _patch_auth(monkeypatch)
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(http_routes.db, "list_commands", fake)
    monkeypatch.setattr(http_routes.db, "list_command_tool_names",
                        AsyncMock(return_value=[]))

    await http_routes.admin_get_errors(_FakeRequest({"limit": "9999"}))
    assert fake.await_args.args[2] == 500


@pytest.mark.asyncio
async def test_db_failure_returns_500(monkeypatch):
    _patch_auth(monkeypatch)
    monkeypatch.setattr(http_routes.db, "list_commands",
                        AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(http_routes.db, "list_command_tool_names",
                        AsyncMock(return_value=[]))
    resp = await http_routes.admin_get_errors(_FakeRequest({}))
    assert resp.status_code == 500


# ── _parse_response_ok 三态 ──

def test_parse_response_ok_truthy():
    assert http_routes._parse_response_ok("1") == 1
    assert http_routes._parse_response_ok("true") == 1
    assert http_routes._parse_response_ok("True") == 1


def test_parse_response_ok_falsy():
    assert http_routes._parse_response_ok("0") == 0
    assert http_routes._parse_response_ok("false") == 0
    assert http_routes._parse_response_ok("False") == 0


def test_parse_response_ok_unfiltered():
    """空、null、all、None、未知值 → None（不过滤）。"""
    assert http_routes._parse_response_ok(None) is None
    assert http_routes._parse_response_ok("") is None
    assert http_routes._parse_response_ok("null") is None
    assert http_routes._parse_response_ok("all") is None
    assert http_routes._parse_response_ok("any") is None
    assert http_routes._parse_response_ok("garbage") is None


# ── /admin/commands 端点也接受新过滤参数 ──

@pytest.mark.asyncio
async def test_admin_commands_passes_new_filters(monkeypatch):
    _patch_auth(monkeypatch)
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(http_routes.db, "list_commands", fake)

    await http_routes.admin_get_commands(_FakeRequest({
        "response_ok": "0",
        "tool_name": "boss_search_jobs",
        "keyword": "rate",
    }))
    kwargs = fake.await_args.kwargs
    assert kwargs["response_ok"] == 0
    assert kwargs["tool_name"] == "boss_search_jobs"
    assert kwargs["keyword"] == "rate"
