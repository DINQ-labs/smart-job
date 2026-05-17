"""验证 cmd_geek_filter_by_label / cmd_get_friend_list 在主路径上确实触发了
fire-and-forget 的 _cache_chat_list 调用，并且 entry.account_name + label_id
被正确透传。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import server  # noqa: F401  conftest bootstrap
import commands


@pytest.fixture
def fake_session(monkeypatch):
    entry = MagicMock()
    entry.session_id = "sess-fake"
    entry.account_name = "acct-Z"
    entry.rate_limiter = MagicMock()
    entry.rate_limiter.wait = AsyncMock(return_value=None)
    monkeypatch.setattr(commands.session_store, "resolve_session",
                        lambda _sid=None: entry)
    return entry


def _envelope(friends: list, key: str = "friendList") -> dict:
    """模拟 ext 端 extOk + raw 信封 → 经 _unwrap 后的 data。"""
    return {
        "ok": True, "code": 200, "message": "",
        "data": {"raw": {"code": 0, "message": "Success",
                          "zpData": {key: friends}}},
    }


async def _drain():
    """让 ensure_future 排进事件循环执行完毕。"""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_geek_filter_by_label_triggers_cache_write(fake_session, monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(commands, "_cache_chat_list", spy)
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=_envelope([{"encryptFriendId": "x"}])))
    await commands.cmd_geek_filter_by_label(label_id=4)
    await _drain()
    spy.assert_awaited_once()
    args, kwargs = spy.await_args
    # 第二位是 account_name，第三位是 label_id
    assert args[1] == "acct-Z"
    assert args[2] == 4


@pytest.mark.asyncio
async def test_get_friend_list_triggers_cache_with_label_zero(fake_session, monkeypatch):
    """旧接口没有 label 概念 → 强制 label_id=0。"""
    spy = AsyncMock()
    monkeypatch.setattr(commands, "_cache_chat_list", spy)
    # 旧接口返回 zpData.result —— cmd_get_friend_list 内部归一化为 friendList
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=_envelope([{"encryptFriendId": "y"}], key="result")))
    out = await commands.cmd_get_friend_list()
    await _drain()
    # 归一化检查：result 已被改写为 friendList
    assert "friendList" in out["raw"]["zpData"]
    assert "result" not in out["raw"]["zpData"]
    # cache hook 被调用且 label_id=0
    spy.assert_awaited_once()
    args, _ = spy.await_args
    assert args[1] == "acct-Z"
    assert args[2] == 0


@pytest.mark.asyncio
async def test_geek_filter_by_label_label_id_propagates(fake_session, monkeypatch):
    """每个 label 拉一遍后，缓存里这条好友的 label_set 应能合并出 [0,1,4]。
    单元测试只验证 label_id 透传到 _cache_chat_list，合并行为在 DB 层 SQL
    （手动 / DB 集成测试覆盖）。"""
    seen: list[int] = []
    async def spy(_data, _account, label_id):
        seen.append(label_id)
    monkeypatch.setattr(commands, "_cache_chat_list", spy)
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=_envelope([{"encryptFriendId": "z"}])))
    for lid in (0, 1, 4):
        await commands.cmd_geek_filter_by_label(label_id=lid)
        await _drain()
    assert seen == [0, 1, 4]
