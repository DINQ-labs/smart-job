"""_cache_recruiter_chat_list 写入路径回归测试。

cmd_recruiter_chat_list 在响应路径上 fire-and-forget 触发本函数。
关键差异 vs 求职者侧：
  - 字段是 zpData.result[]（不是 friendList[]）
  - 单条字段稀疏（friendId/encryptFriendId/updateTime/waterLevel ± name）
  - name 缺失时必须传 None（不是 ""）以便 COALESCE 后续懒补
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import server  # noqa: F401  conftest bootstrap
import commands


def _envelope(rows: list) -> dict:
    return {"raw": {"code": 0, "message": "Success", "zpData": {"result": rows}}}


@pytest.mark.asyncio
async def test_each_row_upserted(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_recruiter_chat", upsert)
    data = _envelope([
        {"friendId": 45563488, "friendSource": 0,
         "encryptFriendId": "f9e5", "updateTime": 1777020857000, "waterLevel": 0},
        {"friendId": 899, "friendSource": 0,
         "encryptFriendId": "38bd", "name": "系统通知",
         "updateTime": 1777023278000, "waterLevel": 0},
    ])
    await commands._cache_recruiter_chat_list(data, "rec-acct", 1)
    assert upsert.await_count == 2
    a0 = upsert.await_args_list[0]
    assert a0.args == ("boss", "rec-acct", "f9e5")
    assert a0.kwargs["friend_id"] == "45563488"
    assert a0.kwargs["friend_source"] == 0
    assert a0.kwargs["update_time"] == 1777020857000
    assert a0.kwargs["water_level"] == 0
    assert a0.kwargs["label_id"] == 1


@pytest.mark.asyncio
async def test_missing_name_passes_none_not_empty_string(monkeypatch):
    """COALESCE 模式要求 None 才能被跳过；空字符串会覆盖现有 name —— 这是回归点。"""
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_recruiter_chat", upsert)
    data = _envelope([
        {"friendId": 1, "encryptFriendId": "noname"},                # 无 name
        {"friendId": 2, "encryptFriendId": "blank", "name": ""},     # 空 name
        {"friendId": 3, "encryptFriendId": "real", "name": "宋女士"},
    ])
    await commands._cache_recruiter_chat_list(data, "x", 0)
    assert upsert.await_args_list[0].kwargs["name"] is None
    assert upsert.await_args_list[1].kwargs["name"] is None  # "" → None
    assert upsert.await_args_list[2].kwargs["name"] == "宋女士"


@pytest.mark.asyncio
async def test_skip_rows_missing_encrypt_friend_id(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_recruiter_chat", upsert)
    await commands._cache_recruiter_chat_list(_envelope([
        {"friendId": 1, "name": "无 ID"},
        {"friendId": 2, "encryptFriendId": "", "name": "空 ID"},
        {"friendId": 3, "encryptFriendId": "ok"},
    ]), "x", 0)
    assert upsert.await_count == 1
    assert upsert.await_args_list[0].args[2] == "ok"


@pytest.mark.asyncio
async def test_empty_or_malformed_safe(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_recruiter_chat", upsert)
    await commands._cache_recruiter_chat_list(_envelope([]), "x", 0)
    await commands._cache_recruiter_chat_list({"raw": {"zpData": {"result": "not-a-list"}}}, "x", 0)
    await commands._cache_recruiter_chat_list({}, "x", 0)
    await commands._cache_recruiter_chat_list(None, "x", 0)
    assert upsert.await_count == 0


@pytest.mark.asyncio
async def test_label_id_propagates(monkeypatch):
    """招聘方 sweep 多个 labelId → 每次调用应透传不同 label_id。
    label_set 合并的 SQL 行为由 DB 层负责，本测试只验透传。"""
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_recruiter_chat", upsert)
    for lid in (0, 1, 8, 9):
        await commands._cache_recruiter_chat_list(
            _envelope([{"encryptFriendId": "z"}]), "acct", lid)
    seen = [c.kwargs["label_id"] for c in upsert.await_args_list]
    assert seen == [0, 1, 8, 9]


@pytest.mark.asyncio
async def test_upsert_failure_does_not_propagate(monkeypatch):
    upsert = AsyncMock(side_effect=[RuntimeError("boom"), None])
    monkeypatch.setattr(commands.db, "upsert_recruiter_chat", upsert)
    await commands._cache_recruiter_chat_list(_envelope([
        {"encryptFriendId": "fail"},
        {"encryptFriendId": "ok"},
    ]), "acct", 0)
    assert upsert.await_count == 2
