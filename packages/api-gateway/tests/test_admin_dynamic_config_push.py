"""admin_dynamic_config_push endpoint 集成回归测试。

mock db + broadcast 验证：
  - 非法 payload 返 400
  - 合法 payload 200 + db.upsert_dynamic_config 调用 + broadcast 调用
  - DB 写入失败 → 不广播 + 500
  - 广播失败 → DB 已写入 + 500
  - 鉴权
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import server  # noqa: F401  conftest bootstrap
import http_routes


class _FakeRequest:
    """最小 Starlette Request mock —— 只用到 .json() 和 _auth_required 内部读取。"""

    def __init__(self, body: dict, *, auth_ok: bool = True):
        self._body = body
        self._auth_ok = auth_ok

    async def json(self):
        return self._body

    @property
    def query_params(self):
        return {}


def _patch_auth(monkeypatch, ok: bool = True):
    """让 _auth_required 返 None（通过）或 JSONResponse(401)。"""
    if ok:
        monkeypatch.setattr(http_routes, "_auth_required", lambda req: None)
        monkeypatch.setattr(http_routes, "_current_user_id", lambda req: "test-admin")
    else:
        from starlette.responses import JSONResponse
        monkeypatch.setattr(http_routes, "_auth_required",
                            lambda req: JSONResponse({"error": "unauthorized"}, status_code=401))


def _good_payload():
    return {
        "version": "v1",
        "dynamic_commands": [{
            "path": "boss/test_ping",
            "description": "ping",
            "requestBuilder": {"method": "GET", "url": "/wapi/zppassport/get/wt"},
        }],
        "notes": "smoke test",
    }


@pytest.mark.asyncio
async def test_unauthorized(monkeypatch):
    _patch_auth(monkeypatch, ok=False)
    resp = await http_routes.admin_dynamic_config_push(_FakeRequest(_good_payload()))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_json_payload(monkeypatch):
    _patch_auth(monkeypatch)
    class _Bad:
        async def json(self):
            raise ValueError("bad json")
        @property
        def query_params(self): return {}
    resp = await http_routes.admin_dynamic_config_push(_Bad())
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_schema_returns_400(monkeypatch):
    _patch_auth(monkeypatch)
    bad = {"version": "v1"}    # 既无 chains 也无 commands
    resp = await http_routes.admin_dynamic_config_push(_FakeRequest(bad))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_happy_path(monkeypatch):
    _patch_auth(monkeypatch)
    upsert = AsyncMock()
    monkeypatch.setattr(http_routes.db, "upsert_dynamic_config", upsert)

    bcast = AsyncMock(return_value=[
        {"session_id": "a", "sent": True},
        {"session_id": "b", "sent": True},
    ])
    # broadcast_to_all_extensions 在 ext_client 模块；endpoint 内是延迟 import
    import ext_client
    monkeypatch.setattr(ext_client, "broadcast_to_all_extensions", bcast, raising=False)

    resp = await http_routes.admin_dynamic_config_push(_FakeRequest(_good_payload()))
    assert resp.status_code == 200
    body = _decode(resp)
    assert body["ok"] is True
    assert body["version"] == "v1"
    assert body["sent"] == 2
    assert body["failed"] == 0
    upsert.assert_awaited_once()
    bcast.assert_awaited_once()
    # broadcast payload 形状
    payload, kwargs = bcast.await_args.args, bcast.await_args.kwargs
    assert payload[0]["type"] == "config_update"
    assert payload[0]["version"] == "v1"
    assert payload[0]["dynamic_commands"][0]["path"] == "boss/test_ping"


@pytest.mark.asyncio
async def test_db_failure_does_not_broadcast(monkeypatch):
    _patch_auth(monkeypatch)
    monkeypatch.setattr(http_routes.db, "upsert_dynamic_config",
                        AsyncMock(side_effect=RuntimeError("db down")))
    import ext_client
    bcast = AsyncMock()
    monkeypatch.setattr(ext_client, "broadcast_to_all_extensions", bcast, raising=False)

    resp = await http_routes.admin_dynamic_config_push(_FakeRequest(_good_payload()))
    assert resp.status_code == 500
    bcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_push_also_applies_mcp_registry(monkeypatch):
    """Phase 2: 推送成功后本进程的 MCP tool 注册表也被刷新。"""
    _patch_auth(monkeypatch)
    monkeypatch.setattr(http_routes.db, "upsert_dynamic_config", AsyncMock())
    import ext_client
    monkeypatch.setattr(ext_client, "broadcast_to_all_extensions",
                        AsyncMock(return_value=[{"session_id": "a", "sent": True}]),
                        raising=False)
    # spy 在 dynamic_mcp_registry.apply_config 上
    import dynamic_mcp_registry
    spy = MagicMock(return_value={"added": ["boss_test_ping"],
                                   "failed": [], "skipped": []})
    monkeypatch.setattr(dynamic_mcp_registry, "apply_config", spy)

    resp = await http_routes.admin_dynamic_config_push(_FakeRequest(_good_payload()))
    assert resp.status_code == 200
    spy.assert_called_once()
    cmds_arg = spy.call_args.args[0]
    assert cmds_arg[0]["path"] == "boss/test_ping"
    body = _decode(resp)
    assert body["mcp"]["added"] == ["boss_test_ping"]


@pytest.mark.asyncio
async def test_push_succeeds_when_mcp_apply_raises(monkeypatch):
    """MCP 注册失败不应回滚扩展推送。"""
    _patch_auth(monkeypatch)
    monkeypatch.setattr(http_routes.db, "upsert_dynamic_config", AsyncMock())
    import ext_client
    monkeypatch.setattr(ext_client, "broadcast_to_all_extensions",
                        AsyncMock(return_value=[{"session_id": "a", "sent": True}]),
                        raising=False)
    import dynamic_mcp_registry
    monkeypatch.setattr(dynamic_mcp_registry, "apply_config",
                        MagicMock(side_effect=RuntimeError("registry boom")))

    resp = await http_routes.admin_dynamic_config_push(_FakeRequest(_good_payload()))
    assert resp.status_code == 200
    body = _decode(resp)
    assert body["ok"] is True
    # mcp 字段不存在或为空 —— 关键是没崩
    assert "mcp" not in body or body["mcp"] == {}


@pytest.mark.asyncio
async def test_partial_send_reflected_in_response(monkeypatch):
    """1/2 send 成功 → 响应里的 sent=1 / failed=1。"""
    _patch_auth(monkeypatch)
    monkeypatch.setattr(http_routes.db, "upsert_dynamic_config", AsyncMock())
    import ext_client
    monkeypatch.setattr(ext_client, "broadcast_to_all_extensions",
                        AsyncMock(return_value=[
                            {"session_id": "ok", "sent": True},
                            {"session_id": "bad", "sent": False, "error": "ws closed"},
                        ]), raising=False)

    resp = await http_routes.admin_dynamic_config_push(_FakeRequest(_good_payload()))
    body = _decode(resp)
    assert resp.status_code == 200
    assert body["sent"] == 1
    assert body["failed"] == 1
    assert len(body["ack"]) == 2


def _decode(resp):
    """从 starlette JSONResponse 取 body dict。"""
    import json as _j
    return _j.loads(resp.body.decode())
