"""cmd_dynamic_dispatch 单测（Phase 2）。

验证：
  - 转发的 (method, path, body) 与入参一致
  - tool_name 默认为 path，可被覆盖
  - rate_limiter.wait 被调
  - _unwrap 后返回业务字段
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import server  # noqa: F401  conftest bootstrap
import commands


class _FakeEntry:
    def __init__(self, sid: str = "sess-1"):
        self.session_id = sid
        self.rate_limiter = MagicMock()
        self.rate_limiter.wait = AsyncMock()


def _patch_resolve(monkeypatch, entry: _FakeEntry):
    monkeypatch.setattr(commands.session_store, "resolve_session",
                        lambda sid=None: entry, raising=True)


def _patch_send(monkeypatch, response: dict | None = None):
    """用 AsyncMock 替换 send_command_to。返回 mock 以便断言入参。"""
    if response is None:
        response = {"ok": True, "raw": {"code": 0, "zpData": {"x": 1}}}
    fake = AsyncMock(return_value=response)
    monkeypatch.setattr(commands, "send_command_to", fake)
    return fake


@pytest.mark.asyncio
async def test_dispatch_get_no_body(monkeypatch):
    entry = _FakeEntry()
    _patch_resolve(monkeypatch, entry)
    sender = _patch_send(monkeypatch)

    out = await commands.cmd_dynamic_dispatch(
        ext_path="boss/test_ping",
        method="GET",
        cmd_kwargs=None,
        session_id="sess-1",
        agent_id="agent-A",
    )
    sender.assert_awaited_once()
    args, kwargs = sender.await_args.args, sender.await_args.kwargs
    # send_command_to(session_id, method, path, body, ...)
    assert args[0] == "sess-1"
    assert args[1] == "GET"
    assert args[2] == "boss/test_ping"
    assert args[3] == {}
    assert kwargs.get("agent_id") == "agent-A"
    assert kwargs.get("tool_name") == "boss/test_ping"  # 默认用 path
    entry.rate_limiter.wait.assert_awaited_once_with("default")
    # _unwrap 默认返回原 dict（除非 envelope）
    assert isinstance(out, dict)


@pytest.mark.asyncio
async def test_dispatch_post_forwards_kwargs_as_body(monkeypatch):
    entry = _FakeEntry()
    _patch_resolve(monkeypatch, entry)
    sender = _patch_send(monkeypatch)

    await commands.cmd_dynamic_dispatch(
        ext_path="boss/recruiter_chat_list",
        method="POST",
        cmd_kwargs={"label_id": 7, "sort": "recent"},
        tool_name="boss_recruiter_chat_list",
        session_id="sess-1",
    )
    args, kwargs = sender.await_args.args, sender.await_args.kwargs
    assert args[1] == "POST"
    assert args[3] == {"label_id": 7, "sort": "recent"}
    assert kwargs["tool_name"] == "boss_recruiter_chat_list"


@pytest.mark.asyncio
async def test_dispatch_custom_rate_bucket(monkeypatch):
    entry = _FakeEntry()
    _patch_resolve(monkeypatch, entry)
    _patch_send(monkeypatch)

    await commands.cmd_dynamic_dispatch(
        ext_path="boss/foo", rate_bucket="search",
        cmd_kwargs={}, session_id="sess-1",
    )
    entry.rate_limiter.wait.assert_awaited_once_with("search")


@pytest.mark.asyncio
async def test_dispatch_propagates_send_errors(monkeypatch):
    entry = _FakeEntry()
    _patch_resolve(monkeypatch, entry)
    monkeypatch.setattr(
        commands, "send_command_to",
        AsyncMock(side_effect=RuntimeError("ws closed")),
    )
    with pytest.raises(RuntimeError, match="ws closed"):
        await commands.cmd_dynamic_dispatch(
            ext_path="boss/foo", cmd_kwargs={}, session_id="sess-1",
        )


@pytest.mark.asyncio
async def test_dispatch_does_not_mutate_caller_kwargs(monkeypatch):
    entry = _FakeEntry()
    _patch_resolve(monkeypatch, entry)
    _patch_send(monkeypatch)

    payload = {"a": 1}
    await commands.cmd_dynamic_dispatch(
        ext_path="boss/foo", cmd_kwargs=payload, session_id="sess-1",
    )
    # body 是浅拷贝，agent 调用方传入的 dict 不应被改
    assert payload == {"a": 1}
