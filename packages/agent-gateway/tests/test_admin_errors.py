"""GET /admin/errors（agent-gateway 侧）handler 测试。

校验：
  - 默认 limit/offset
  - since_iso / user_id / keyword 透传给 db.list_recent_errors
  - 响应结构 {errors, categories, limit, offset}
  - DB 失败 → 503
  - limit 非整数 → 400
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

import server  # noqa: E402


class _FakeURL:
    """server.py 的 log.exception 用 request.url.path 填日志参数,需要 mock。"""
    def __init__(self, path: str = "/admin/errors"):
        self.path = path


class _FakeRequest:
    def __init__(self, query: dict | None = None, path: str = "/admin/errors"):
        self._query = query or {}
        self.url = _FakeURL(path)

    @property
    def query_params(self):
        return self._query


def _decode(resp):
    import json
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_default_returns_empty(monkeypatch):
    monkeypatch.setattr(server.db, "list_recent_errors",
                        AsyncMock(return_value=[]), raising=False)
    monkeypatch.setattr(server.db, "list_error_categories",
                        AsyncMock(return_value=[]), raising=False)
    resp = await server.admin_list_errors(_FakeRequest({}))
    body = _decode(resp)
    assert resp.status_code == 200
    assert body == {"errors": [], "categories": [], "limit": 100, "offset": 0}


@pytest.mark.asyncio
async def test_filters_passthrough(monkeypatch):
    fake = AsyncMock(return_value=[
        {"id": 7, "session_id": 12, "user_id": "u1",
         "error_category": "rate_limit", "error_message": "[rate_limit] 429",
         "duration_ms": 300, "mode": "search", "created_at": "2026-04-26T10:00:00+00:00"},
    ])
    fake_cat = AsyncMock(return_value=["rate_limit", "auth"])
    monkeypatch.setattr(server.db, "list_recent_errors", fake, raising=False)
    monkeypatch.setattr(server.db, "list_error_categories", fake_cat, raising=False)

    resp = await server.admin_list_errors(_FakeRequest({
        "since_iso": "2026-04-26T00:00:00Z",
        "user_id": "u1",
        "keyword": "429",
        "limit": "20", "offset": "5",
    }))
    body = _decode(resp)
    assert resp.status_code == 200
    assert body["errors"][0]["error_category"] == "rate_limit"
    assert body["categories"] == ["rate_limit", "auth"]
    assert body["limit"] == 20
    assert body["offset"] == 5

    kwargs = fake.await_args.kwargs
    assert kwargs["since_iso"] == "2026-04-26T00:00:00Z"
    assert kwargs["user_id"] == "u1"
    assert kwargs["keyword"] == "429"
    assert kwargs["limit"] == 20
    assert kwargs["offset"] == 5
    # categories 也用同一 since_iso
    assert fake_cat.await_args.kwargs["since_iso"] == "2026-04-26T00:00:00Z"


@pytest.mark.asyncio
async def test_invalid_limit_returns_400(monkeypatch):
    monkeypatch.setattr(server.db, "list_recent_errors", AsyncMock(), raising=False)
    monkeypatch.setattr(server.db, "list_error_categories", AsyncMock(), raising=False)
    resp = await server.admin_list_errors(_FakeRequest({"limit": "xyz"}))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_db_failure_returns_503(monkeypatch):
    monkeypatch.setattr(server.db, "list_recent_errors",
                        AsyncMock(side_effect=RuntimeError("pg down")),
                        raising=False)
    monkeypatch.setattr(server.db, "list_error_categories",
                        AsyncMock(return_value=[]), raising=False)
    resp = await server.admin_list_errors(_FakeRequest({}))
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_limit_clamped_to_500(monkeypatch):
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(server.db, "list_recent_errors", fake, raising=False)
    monkeypatch.setattr(server.db, "list_error_categories",
                        AsyncMock(return_value=[]), raising=False)
    await server.admin_list_errors(_FakeRequest({"limit": "9999"}))
    assert fake.await_args.kwargs["limit"] == 500
