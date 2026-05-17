"""cmd_get_friend_list schema 归一化回归测试。

Boss 旧接口 (getGeekFriendList.json) 返回 zpData.result[]；新接口
(geekFilterByLabel) 返回 zpData.friendList[]。agent prompt 只教模型读
friendList[]，旧接口兜底时若不归一会被模型误判为"空"。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import server  # noqa: F401  conftest bootstrap
import commands


@pytest.fixture
def mock_session(monkeypatch):
    entry = MagicMock()
    entry.session_id = "sess-xyz"
    entry.rate_limiter = MagicMock()
    entry.rate_limiter.wait = AsyncMock(return_value=None)
    monkeypatch.setattr(commands.session_store, "resolve_session",
                        lambda _sid=None: entry)
    return entry


def _envelope(zp_data: dict) -> dict:
    """构造扩展标准响应（extOk 信封 + 内层 raw）。"""
    return {
        "ok": True,
        "code": 200,
        "message": "",
        "data": {
            "raw": {"code": 0, "message": "Success", "zpData": zp_data},
        },
    }


@pytest.mark.asyncio
async def test_legacy_result_normalized_to_friend_list(mock_session, monkeypatch):
    """旧接口 zpData.result → 应改名为 zpData.friendList。"""
    legacy = _envelope({
        "result": [
            {"friendId": 1, "encryptFriendId": "abc", "name": "宋女士"},
            {"friendId": 2, "encryptFriendId": "def", "name": "李女士"},
        ],
    })
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=legacy))
    out = await commands.cmd_get_friend_list()
    zp = out["raw"]["zpData"]
    assert "friendList" in zp
    assert "result" not in zp
    assert len(zp["friendList"]) == 2
    assert zp["friendList"][0]["name"] == "宋女士"


@pytest.mark.asyncio
async def test_new_friend_list_unchanged(mock_session, monkeypatch):
    """新接口已经是 friendList → 原样返回，不要二次改写。"""
    new = _envelope({
        "friendList": [{"friendId": 9, "name": "闫晓磊"}],
        "filterEncryptIdList": [],
        "foldText": "以上是30天内联系人",
    })
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=new))
    out = await commands.cmd_get_friend_list()
    zp = out["raw"]["zpData"]
    assert zp["friendList"][0]["name"] == "闫晓磊"
    assert zp["foldText"] == "以上是30天内联系人"
    assert "result" not in zp


@pytest.mark.asyncio
async def test_empty_zpdata_passthrough(mock_session, monkeypatch):
    """zpData={} → 原样穿透，不要凭空造 friendList。"""
    empty = _envelope({})
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=empty))
    out = await commands.cmd_get_friend_list()
    assert out["raw"]["zpData"] == {}


@pytest.mark.asyncio
async def test_both_present_keeps_friend_list(mock_session, monkeypatch):
    """同时存在 friendList 和 result（异常 case）→ friendList 优先，不动。"""
    both = _envelope({
        "friendList": [{"name": "new"}],
        "result": [{"name": "old"}],
    })
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=both))
    out = await commands.cmd_get_friend_list()
    zp = out["raw"]["zpData"]
    assert zp["friendList"] == [{"name": "new"}]
    assert zp["result"] == [{"name": "old"}]
