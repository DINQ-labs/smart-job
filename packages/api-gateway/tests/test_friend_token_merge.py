"""_merge_friend_security_tokens 回归测试。

新接口 geekFilterByLabel friendList[] 缺 securityId / encryptJobId / lastMsg；
旧接口 getGeekFriendList.json result[] 含这些。本函数把两者按
encryptFriendId == encryptUid 做 join 后 merge，让 agent 一次拿到完整 token chain。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import server  # noqa: F401  conftest bootstrap
import commands


def _new_envelope(friends: list) -> dict:
    """新接口 _unwrap 后形状。"""
    return {"raw": {"code": 0, "message": "Success",
                    "zpData": {"friendList": friends}}}


def _old_envelope(results: list) -> dict:
    """旧接口 _unwrap 后形状（zpData.result[]）。"""
    return {"raw": {"code": 0, "message": "Success",
                    "zpData": {"result": results}}}


class TestMergeFunction:
    def test_basic_match_by_encrypt_friend_id(self):
        """key 匹配 → securityId / encryptJobId / lastMsg 全 merge。"""
        new = _new_envelope([
            {"encryptFriendId": "abc", "name": "李女士", "brandName": "思腾人力"},
        ])
        old = _old_envelope([
            {"encryptUid": "abc", "securityId": "TOKEN_xyz",
             "encryptJobId": "JOB_1", "lastMsg": "方便发简历吗？",
             "lastTime": "15:57", "lastTS": 1777103864280,
             "unreadMsgCount": 0, "isTop": 0},
        ])
        commands._merge_friend_security_tokens(new, old)
        f = new["raw"]["zpData"]["friendList"][0]
        assert f["chatSecurityId"] == "TOKEN_xyz"
        assert f["encryptJobId"] == "JOB_1"
        assert f["lastMsg"] == "方便发简历吗？"
        assert f["lastTS"] == 1777103864280
        # 原有字段不动
        assert f["name"] == "李女士"
        assert f["brandName"] == "思腾人力"

    def test_fallback_to_encrypt_boss_id(self):
        """encryptUid 缺时 fallback 到 encryptBossId。"""
        new = _new_envelope([{"encryptFriendId": "abc"}])
        old = _old_envelope([
            {"encryptBossId": "abc", "securityId": "TOK"},
        ])
        commands._merge_friend_security_tokens(new, old)
        assert new["raw"]["zpData"]["friendList"][0]["chatSecurityId"] == "TOK"

    def test_no_match_keeps_new_unchanged(self):
        """旧接口里没有对应 key → 该条好友不 merge，但其他字段保留。"""
        new = _new_envelope([
            {"encryptFriendId": "abc", "name": "宋女士"},
            {"encryptFriendId": "def", "name": "李女士"},
        ])
        old = _old_envelope([{"encryptUid": "def", "securityId": "T"}])
        commands._merge_friend_security_tokens(new, old)
        friends = new["raw"]["zpData"]["friendList"]
        assert "chatSecurityId" not in friends[0]   # abc 没匹配，不动
        assert friends[1]["chatSecurityId"] == "T"  # def 匹配上

    def test_does_not_overwrite_existing_fields(self):
        """新接口已有字段（例如 chatSecurityId 突然新接口也开始返回了）→ 不被旧接口覆盖。"""
        new = _new_envelope([
            {"encryptFriendId": "abc",
             "chatSecurityId": "NEW_TOKEN",
             "encryptJobId": "NEW_JOB"},
        ])
        old = _old_envelope([
            {"encryptUid": "abc", "securityId": "OLD_TOKEN",
             "encryptJobId": "OLD_JOB"},
        ])
        commands._merge_friend_security_tokens(new, old)
        f = new["raw"]["zpData"]["friendList"][0]
        assert f["chatSecurityId"] == "NEW_TOKEN"
        assert f["encryptJobId"] == "NEW_JOB"

    def test_handles_missing_or_malformed(self):
        """各种异常输入安全跳过，不抛错、不改动 new_data。"""
        # 旧接口失败（dict 但无 zpData）
        new = _new_envelope([{"encryptFriendId": "x"}])
        commands._merge_friend_security_tokens(new, {})
        assert "chatSecurityId" not in new["raw"]["zpData"]["friendList"][0]
        # None 输入
        commands._merge_friend_security_tokens(new, None)
        commands._merge_friend_security_tokens(None, _old_envelope([]))
        # result 不是 list
        commands._merge_friend_security_tokens(
            new, {"raw": {"zpData": {"result": "not-a-list"}}})
        # friendList 不是 list
        commands._merge_friend_security_tokens(
            {"raw": {"zpData": {"friendList": "not-a-list"}}}, _old_envelope([]))


class TestCmdGeekFilterByLabelMerge:
    """端到端：cmd_geek_filter_by_label 现在并发调两个接口，merge 后再缓存。"""

    @pytest.fixture
    def fake_session(self, monkeypatch):
        entry = MagicMock()
        entry.session_id = "sess"
        entry.account_name = "acct"
        entry.rate_limiter = MagicMock()
        entry.rate_limiter.wait = AsyncMock(return_value=None)
        monkeypatch.setattr(commands.session_store, "resolve_session",
                            lambda _sid=None: entry)
        return entry

    def _ext(self, payload: dict) -> dict:
        """ext extOk 信封。"""
        return {"ok": True, "code": 200, "message": "", "data": payload}

    @pytest.mark.asyncio
    async def test_calls_both_apis_in_parallel(self, fake_session, monkeypatch):
        """新+旧接口都被调，且通过 asyncio.gather 不会顺序串行。"""
        send = AsyncMock(side_effect=[
            self._ext(_new_envelope([{"encryptFriendId": "a", "name": "n"}])),
            self._ext(_old_envelope([{"encryptUid": "a", "securityId": "S",
                                       "encryptJobId": "J"}])),
        ])
        monkeypatch.setattr(commands, "send_command_to", send)
        monkeypatch.setattr(commands, "_cache_chat_list", AsyncMock())

        out = await commands.cmd_geek_filter_by_label(label_id=0)
        assert send.await_count == 2
        # 第一个 call: GET geek_filter_by_label
        args1, _ = send.await_args_list[0]
        assert args1[2] == "boss/geek_filter_by_label"
        # 第二个 call: GET get_friend_list
        args2, _ = send.await_args_list[1]
        assert args2[2] == "boss/get_friend_list"
        # 输出 friendList[0] 应被 merge 上 securityId / encryptJobId
        f = out["raw"]["zpData"]["friendList"][0]
        assert f["chatSecurityId"] == "S"
        assert f["encryptJobId"] == "J"

    @pytest.mark.asyncio
    async def test_old_api_failure_is_best_effort(self, fake_session, monkeypatch):
        """旧接口失败 → 主路径仍返回新接口数据（无 token，但不阻塞）。"""
        async def faking_send(sid, method, path, body, **_kw):
            if path == "boss/geek_filter_by_label":
                return self._ext(_new_envelope([{"encryptFriendId": "a", "name": "n"}]))
            raise RuntimeError("old API down")
        monkeypatch.setattr(commands, "send_command_to", AsyncMock(side_effect=faking_send))
        monkeypatch.setattr(commands, "_cache_chat_list", AsyncMock())

        out = await commands.cmd_geek_filter_by_label(label_id=0)
        f = out["raw"]["zpData"]["friendList"][0]
        # 主数据完整
        assert f["name"] == "n"
        # token 缺失（旧接口失败，没 merge 上）
        assert "chatSecurityId" not in f

    @pytest.mark.asyncio
    async def test_new_api_failure_propagates(self, fake_session, monkeypatch):
        """新接口失败 → 抛出（主路径不能吞）。"""
        async def faking_send(sid, method, path, body, **_kw):
            if path == "boss/geek_filter_by_label":
                raise RuntimeError("new API down")
            return self._ext(_old_envelope([]))
        monkeypatch.setattr(commands, "send_command_to", AsyncMock(side_effect=faking_send))
        monkeypatch.setattr(commands, "_cache_chat_list", AsyncMock())

        with pytest.raises(RuntimeError, match="new API down"):
            await commands.cmd_geek_filter_by_label(label_id=0)

    @pytest.mark.asyncio
    async def test_cache_hook_receives_merged_data(self, fake_session, monkeypatch):
        """fire-and-forget cache 写入应拿到 merge 后的数据，让 encrypt_job_id 能入库。"""
        send = AsyncMock(side_effect=[
            self._ext(_new_envelope([{"encryptFriendId": "abc"}])),
            self._ext(_old_envelope([{"encryptUid": "abc", "securityId": "T",
                                       "encryptJobId": "J", "lastMsg": "hi",
                                       "lastTS": 1700000000000}])),
        ])
        monkeypatch.setattr(commands, "send_command_to", send)
        spy = AsyncMock()
        monkeypatch.setattr(commands, "_cache_chat_list", spy)

        await commands.cmd_geek_filter_by_label(label_id=2)
        # drain ensure_future
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        spy.assert_awaited_once()
        cached_data, account, lid = spy.await_args.args
        f = cached_data["raw"]["zpData"]["friendList"][0]
        # cache hook 看到的是 merge 后的 friendList
        assert f["chatSecurityId"] == "T"
        assert f["encryptJobId"] == "J"
        assert f["lastMsg"] == "hi"
        assert account == "acct"
        assert lid == 2
