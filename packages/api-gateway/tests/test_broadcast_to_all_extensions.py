"""broadcast_to_all_extensions 单元测试。

mock session_store.list_all() + entry.ws.send_text 验证：
  - 每个连接的扩展会被尝试推送一次
  - 单条失败不阻断其它会话
  - ext_name 过滤生效
  - 关闭/无 ws 的会话标 sent=False
"""
from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

import server  # noqa: F401  conftest bootstrap
# conftest.py 把 ext_client 当成 MagicMock —— broadcast 单测需要真实模块
sys.modules.pop("ext_client", None)
import ext_client  # noqa: E402  must follow pop above


def _session(sid, ext_name="bosszp", account="", ws=None):
    return {
        "session_id": sid,
        "browser_id": f"bid-{sid}",
        "account_name": account,
        "user_id": "",
        "app_user_id": "",
        "status": "connected" if ws else "disconnected",
        "ext_name": ext_name,
        "display_id": "",
        "ip_address": "",
        "job_store_count": 0,
        "geek_store_count": 0,
        "proxy_url": "",
        "sites": [],
        "site_users": {},
    }


@pytest.mark.asyncio
async def test_broadcast_to_all_connected(monkeypatch):
    ws_a = MagicMock(); ws_a.send_text = AsyncMock()
    ws_b = MagicMock(); ws_b.send_text = AsyncMock()

    sessions = [_session("sid-a", ws=ws_a), _session("sid-b", ws=ws_b)]
    monkeypatch.setattr(ext_client.session_store, "list_all", lambda: sessions)

    def _get(sid):
        e = MagicMock()
        e.ws = {"sid-a": ws_a, "sid-b": ws_b}[sid]
        return e
    monkeypatch.setattr(ext_client.session_store, "get", _get)

    payload = {"type": "config_update", "version": "v1"}
    results = await ext_client.broadcast_to_all_extensions(payload)
    assert len(results) == 2
    assert all(r["sent"] for r in results)
    ws_a.send_text.assert_awaited_once()
    ws_b.send_text.assert_awaited_once()
    # 序列化后内容一致
    args = ws_a.send_text.await_args.args[0]
    assert json.loads(args)["version"] == "v1"


@pytest.mark.asyncio
async def test_one_failure_does_not_block_others(monkeypatch):
    ws_ok = MagicMock(); ws_ok.send_text = AsyncMock()
    ws_bad = MagicMock(); ws_bad.send_text = AsyncMock(side_effect=RuntimeError("conn closed"))

    sessions = [_session("ok", ws=ws_ok), _session("bad", ws=ws_bad)]
    monkeypatch.setattr(ext_client.session_store, "list_all", lambda: sessions)
    monkeypatch.setattr(ext_client.session_store, "get",
                        lambda sid: type("E", (), {"ws": {"ok": ws_ok, "bad": ws_bad}[sid]})())

    results = await ext_client.broadcast_to_all_extensions({"type": "config_update", "version": "v1"})
    by_sid = {r["session_id"]: r for r in results}
    assert by_sid["ok"]["sent"] is True
    assert by_sid["bad"]["sent"] is False
    assert "conn closed" in by_sid["bad"]["error"]


@pytest.mark.asyncio
async def test_filter_by_ext_name(monkeypatch):
    ws_b = MagicMock(); ws_b.send_text = AsyncMock()
    ws_l = MagicMock(); ws_l.send_text = AsyncMock()
    sessions = [
        _session("boss-1", ext_name="bosszp", ws=ws_b),
        _session("li-1",   ext_name="linkedin", ws=ws_l),
    ]
    monkeypatch.setattr(ext_client.session_store, "list_all", lambda: sessions)
    monkeypatch.setattr(ext_client.session_store, "get",
                        lambda sid: type("E", (), {"ws": {"boss-1": ws_b, "li-1": ws_l}[sid]})())

    results = await ext_client.broadcast_to_all_extensions(
        {"type": "config_update", "version": "v1"}, ext_name="bosszp",
    )
    assert len(results) == 1
    assert results[0]["session_id"] == "boss-1"
    ws_b.send_text.assert_awaited_once()
    ws_l.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_with_no_ws(monkeypatch):
    """ws=None 时该会话标 sent=False / error='ws closed'。"""
    sessions = [_session("ghost", ws=None)]
    monkeypatch.setattr(ext_client.session_store, "list_all", lambda: sessions)
    monkeypatch.setattr(ext_client.session_store, "get",
                        lambda sid: type("E", (), {"ws": None})())

    results = await ext_client.broadcast_to_all_extensions({"type": "x", "version": "v1"})
    assert results[0]["sent"] is False
    assert "ws" in results[0]["error"]
