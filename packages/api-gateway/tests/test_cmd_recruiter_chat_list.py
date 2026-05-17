"""cmd_recruiter_chat_list / boss_filter_by_label 区分回归。

新工具（recruiter_chat_list）和原工具（filter_by_label）必须严格区分：
  - 新：wire path 'boss/recruiter_chat_list'，body 不含 encrypt_job_id
  - 旧：wire path 'boss/filter_label'，encrypt_job_id 必填
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
    entry.session_id = "sess-rec"
    entry.account_name = "rec-acct"
    entry.rate_limiter = MagicMock()
    entry.rate_limiter.wait = AsyncMock(return_value=None)
    monkeypatch.setattr(commands.session_store, "resolve_session",
                        lambda _sid=None: entry)
    return entry


def _ext_envelope(rows: list) -> dict:
    return {"ok": True, "code": 200, "message": "",
            "data": {"raw": {"code": 0, "message": "Success",
                              "zpData": {"result": rows}}}}


async def _drain():
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_wire_path_and_body_correct(fake_session, monkeypatch):
    """关键回归：扩展端 boss/filter_label 强校验 encrypt_job_id。
    新工具必须走 boss/recruiter_chat_list，body 不带 encrypt_job_id。"""
    send = AsyncMock(return_value=_ext_envelope([]))
    monkeypatch.setattr(commands, "send_command_to", send)
    monkeypatch.setattr(commands, "_cache_recruiter_chat_list", AsyncMock())

    await commands.cmd_recruiter_chat_list(label_id=1)

    args, kwargs = send.await_args
    # send_command_to(session_id, method, path, body, ...)
    sid, method, path, body = args[:4]
    assert sid == "sess-rec"
    assert method == "POST"
    assert path == "boss/recruiter_chat_list"
    assert "encrypt_job_id" not in body
    assert body["label_id"] == 1
    assert body["sort"] == ""


@pytest.mark.asyncio
async def test_rate_limit_bucket_is_detail(fake_session, monkeypatch):
    """与 cmd_geek_filter_by_label 对齐用 'detail'，不要用 'search'。"""
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=_ext_envelope([])))
    monkeypatch.setattr(commands, "_cache_recruiter_chat_list", AsyncMock())
    await commands.cmd_recruiter_chat_list()
    fake_session.rate_limiter.wait.assert_awaited_with("detail")


@pytest.mark.asyncio
async def test_cache_hook_triggered_with_account_and_label(fake_session, monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value=_ext_envelope([{"encryptFriendId": "x"}])))
    monkeypatch.setattr(commands, "_cache_recruiter_chat_list", spy)
    await commands.cmd_recruiter_chat_list(label_id=8)
    await _drain()
    spy.assert_awaited_once()
    args, _ = spy.await_args
    assert args[1] == "rec-acct"
    assert args[2] == 8


@pytest.mark.asyncio
async def test_filter_by_label_still_requires_encrypt_job_id(fake_session, monkeypatch):
    """回归：原 cmd_filter_by_label 用 wire path boss/filter_label。
    扩展端会在 encrypt_job_id 为空时抛错；本测试确认 wire path 没动。"""
    send = AsyncMock(return_value={"ok": True, "code": 200, "message": "",
                                    "data": {"raw": {"code": 0}}})
    monkeypatch.setattr(commands, "send_command_to", send)
    await commands.cmd_filter_by_label(label_id=1, encrypt_job_id="some-job-id")
    args, _ = send.await_args
    sid, method, path, body = args[:4]
    assert path == "boss/filter_label"        # 旧路径不变
    assert method == "POST"
    assert body["encrypt_job_id"] == "some-job-id"
