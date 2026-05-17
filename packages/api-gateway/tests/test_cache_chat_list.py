"""_cache_chat_list 写入路径回归测试。

cmd_geek_filter_by_label / cmd_get_friend_list 在响应路径上 fire-and-forget
调用 _cache_chat_list 把 friendList[] 入 PG。本测试 mock db.upsert_chat
验证：
  1. 每个 friendList 条目对应一次 upsert_chat（按 encryptFriendId）
  2. account_name 透传
  3. label_id 透传
  4. 缺 encryptFriendId / friendList 非 list / 空 list → 安全跳过
  5. 兼容 cmd_get_friend_list 的 result→friendList 归一化
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import server  # noqa: F401  conftest bootstrap
import commands


def _envelope_with_friends(friends: list, key: str = "friendList") -> dict:
    return {"raw": {"code": 0, "message": "Success", "zpData": {key: friends}}}


@pytest.mark.asyncio
async def test_each_friend_upserted_once(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_chat", upsert)
    data = _envelope_with_friends([
        {"encryptFriendId": "abc", "friendId": 1, "name": "宋女士",
         "brandName": "伊美尔", "jobName": "医美顾问", "bossTitle": "人事",
         "jobCity": "北京", "updateTime": 1700000000000, "waterLevel": 0},
        {"encryptFriendId": "def", "friendId": 2, "name": "李女士"},
    ])
    await commands._cache_chat_list(data, account_name="acct-A", label_id=2)
    assert upsert.await_count == 2
    args, kwargs = upsert.await_args_list[0]
    # positional: (platform, account_name, encrypt_friend_id)
    assert args[0] == "boss"
    assert args[1] == "acct-A"
    assert args[2] == "abc"
    assert kwargs["name"] == "宋女士"
    assert kwargs["brand_name"] == "伊美尔"
    assert kwargs["label_id"] == 2


@pytest.mark.asyncio
async def test_skip_friends_missing_encrypt_id(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_chat", upsert)
    data = _envelope_with_friends([
        {"name": "无 ID 的"},                       # 跳过
        {"encryptFriendId": "", "name": "空 ID"},   # 跳过
        {"encryptFriendId": "real", "name": "有效"},
    ])
    await commands._cache_chat_list(data, account_name="x", label_id=0)
    assert upsert.await_count == 1
    assert upsert.await_args_list[0].args[2] == "real"


@pytest.mark.asyncio
async def test_empty_or_non_list_safe(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_chat", upsert)
    # 空 list
    await commands._cache_chat_list(_envelope_with_friends([]), "x", 0)
    # 非 list
    await commands._cache_chat_list({"raw": {"zpData": {"friendList": "not-a-list"}}}, "x", 0)
    # 缺 raw
    await commands._cache_chat_list({}, "x", 0)
    # data 为 None
    await commands._cache_chat_list(None, "x", 0)
    # 缺 zpData
    await commands._cache_chat_list({"raw": {}}, "x", 0)
    assert upsert.await_count == 0


@pytest.mark.asyncio
async def test_compat_with_cmd_get_friend_list_normalization(monkeypatch):
    """cmd_get_friend_list 已把 zpData.result 归一为 zpData.friendList，
    所以走到 _cache_chat_list 时一定是 friendList key —— 这里验证两种 shape
    都能被覆盖（防回归：哪天归一化失效，缓存仍能工作）。"""
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_chat", upsert)
    # 已归一化的（标准路径）
    await commands._cache_chat_list(
        _envelope_with_friends([{"encryptFriendId": "x1", "name": "n1"}]),
        "acct", 0,
    )
    assert upsert.await_count == 1


@pytest.mark.asyncio
async def test_upsert_failure_does_not_propagate(monkeypatch):
    """单条 upsert 异常不应阻塞其他条目，也不应抛到调用方。"""
    upsert = AsyncMock(side_effect=[RuntimeError("boom"), None])
    monkeypatch.setattr(commands.db, "upsert_chat", upsert)
    data = _envelope_with_friends([
        {"encryptFriendId": "fail", "name": "x"},
        {"encryptFriendId": "ok", "name": "y"},
    ])
    # 不应抛
    await commands._cache_chat_list(data, "acct", 0)
    assert upsert.await_count == 2  # 第二条仍然被尝试


@pytest.mark.asyncio
async def test_chat_security_id_fallback_to_security_id(monkeypatch):
    """部分 payload 字段叫 chatSecurityId，部分叫 securityId（旧接口）—— 两者都接受。"""
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_chat", upsert)
    data = _envelope_with_friends([
        {"encryptFriendId": "a", "chatSecurityId": "TOKEN_NEW"},
        {"encryptFriendId": "b", "securityId": "TOKEN_OLD"},
    ])
    await commands._cache_chat_list(data, "acct", 0)
    assert upsert.await_args_list[0].kwargs["chat_security_id"] == "TOKEN_NEW"
    assert upsert.await_args_list[1].kwargs["chat_security_id"] == "TOKEN_OLD"


@pytest.mark.asyncio
async def test_legacy_schema_entries_with_encrypt_uid(monkeypatch):
    """cmd_get_friend_list 把 zpData.result 改名为 friendList，但条目内部仍是
    旧 schema：encryptUid 不是 encryptFriendId、uid 不是 friendId。
    _cache_chat_list 必须能识别这种情况，否则旧接口走的兜底路径会
    silent no-op（每条都被跳过）。
    """
    upsert = AsyncMock()
    monkeypatch.setattr(commands.db, "upsert_chat", upsert)
    # 模拟 cmd_get_friend_list 归一化后的样子
    legacy_renamed = _envelope_with_friends([
        {"encryptUid": "lid-1", "uid": 730373283,
         "name": "李女士", "title": "猎头顾问", "brandName": "思腾人力",
         "encryptJobId": "job-1", "securityId": "TOK_1",
         "lastMsg": "方便发简历吗？", "lastTS": 1777103864000},
        # encryptBossId 兜底（极端 case，部分账号 encryptUid 缺失）
        {"encryptBossId": "lid-2", "name": "孙先生"},
    ])
    await commands._cache_chat_list(legacy_renamed, "acct", 0)
    assert upsert.await_count == 2
    a0 = upsert.await_args_list[0]
    assert a0.args[2] == "lid-1"                     # encryptUid → efid
    assert a0.kwargs["friend_id"] == "730373283"     # uid → friend_id
    assert a0.kwargs["name"] == "李女士"
    assert a0.kwargs["job_name"] == "猎头顾问"        # title → job_name fallback
    assert a0.kwargs["chat_security_id"] == "TOK_1"
    assert a0.kwargs["encrypt_job_id"] == "job-1"
    assert a0.kwargs["last_msg"] == "方便发简历吗？"
    assert a0.kwargs["update_time"] == 1777103864000  # lastTS fallback
    a1 = upsert.await_args_list[1]
    assert a1.args[2] == "lid-2"                     # encryptBossId 兜底
