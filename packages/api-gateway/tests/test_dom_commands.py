"""DOM 视觉 + 点击 + 导航 cmd_* dispatcher 测试。

校验 6 个通用 dispatcher 把 site 前缀拼到 path 上、参数原样透传给扩展。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import server  # noqa: F401  conftest bootstrap
import commands


@pytest.fixture
def fake_send(monkeypatch):
    async def _fake(*args, **kwargs):
        return {"ok": True, "raw": {"clickables": [], "snapshot_id": "snap_x"}}
    fake = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(commands, "send_command_to", fake)
    return fake


@pytest.mark.asyncio
async def test_get_clickables_routes_to_site_path(fake_send):
    await commands.cmd_get_clickables(
        site="boss", root_selector=".main", include_hidden=True, max_items=50,
        session_id="sess1", agent_id="agentA",
    )
    args, kwargs = fake_send.await_args.args, fake_send.await_args.kwargs
    assert args[0] == "sess1"
    assert args[1] == "POST"
    assert args[2] == "boss/get_clickables"
    assert args[3] == {"rootSelector": ".main", "includeHidden": True, "maxItems": 50}
    assert kwargs["tool_name"] == "boss_get_clickables"
    assert kwargs["agent_id"] == "agentA"


@pytest.mark.asyncio
async def test_get_clickables_linkedin_site_prefix(fake_send):
    await commands.cmd_get_clickables(site="linkedin", session_id="s2")
    assert fake_send.await_args.args[2] == "linkedin/get_clickables"


@pytest.mark.asyncio
async def test_get_clickables_indeed_site_prefix(fake_send):
    await commands.cmd_get_clickables(site="indeed", session_id="s3")
    assert fake_send.await_args.args[2] == "indeed/get_clickables"


@pytest.mark.asyncio
async def test_get_dom_snapshot_passthrough(fake_send):
    await commands.cmd_get_dom_snapshot(
        site="boss", max_depth=4, max_nodes=200, include_text=False,
        session_id="s",
    )
    body = fake_send.await_args.args[3]
    assert body == {"rootSelector": "body", "maxDepth": 4,
                    "maxNodes": 200, "includeText": False}


@pytest.mark.asyncio
async def test_click_by_idx_passthrough(fake_send):
    await commands.cmd_click_by_idx(
        site="boss", snapshot_id="snap_a", idx=7,
        timeout_ms=8000, fallback_text=False, session_id="s",
    )
    args = fake_send.await_args.args
    body = args[3]
    assert args[2] == "boss/click_by_idx"
    assert body["snapshot_id"] == "snap_a"
    assert body["idx"] == 7
    assert body["timeout_ms"] == 8000
    assert body["fallback_text"] is False


@pytest.mark.asyncio
async def test_click_by_text_passthrough(fake_send):
    await commands.cmd_click_by_text(
        site="indeed", text="立即沟通", tag="button", exact=True, nth=2,
        session_id="s",
    )
    args = fake_send.await_args.args
    body = args[3]
    assert args[2] == "indeed/click_by_text"
    assert body["text"] == "立即沟通"
    assert body["tag"] == "button"
    assert body["exact"] is True
    assert body["nth"] == 2


@pytest.mark.asyncio
async def test_wait_for_element_passthrough(fake_send):
    await commands.cmd_wait_for_element(
        site="linkedin", selector=".chat-list", timeout_ms=12000,
        session_id="s",
    )
    args = fake_send.await_args.args
    body = args[3]
    assert args[2] == "linkedin/wait_for"
    assert body == {"selector": ".chat-list", "timeout_ms": 12000}


@pytest.mark.asyncio
async def test_navigate_to_passthrough(fake_send):
    await commands.cmd_navigate_to(
        site="boss", url="https://www.zhipin.com/web/chat/index",
        wait_for_selector=".chat-panel", timeout_ms=20000,
        session_id="s",
    )
    args = fake_send.await_args.args
    body = args[3]
    assert args[2] == "boss/navigate_to"
    assert body["url"] == "https://www.zhipin.com/web/chat/index"
    assert body["wait_for_selector"] == ".chat-panel"
    assert body["timeout_ms"] == 20000


@pytest.mark.asyncio
async def test_timeout_ms_extension_for_long_ops(fake_send):
    """cmd_click_by_idx 应给出比 timeout_ms 多 5s 的 send_command_to 超时缓冲。"""
    await commands.cmd_click_by_idx(
        site="boss", snapshot_id="snap_b", idx=0, timeout_ms=30000,
        session_id="s",
    )
    kwargs = fake_send.await_args.kwargs
    assert kwargs["timeout_ms"] >= 30000 + 5000


@pytest.mark.asyncio
async def test_mcp_tools_imports_work():
    """3 个 mcp_tools_*.py 都成功 import 了 6 个新 cmd_*。"""
    import mcp_tools_boss
    import mcp_tools_linkedin
    import mcp_tools_indeed
    # register 函数能调用就说明 @mcp.tool 装饰没坏（conftest 里 mcp 是 mock）
    from unittest.mock import MagicMock
    fake = MagicMock()
    fake.tool = lambda **_kw: (lambda f: f)
    mcp_tools_boss.register(fake)
    mcp_tools_linkedin.register(fake)
    mcp_tools_indeed.register(fake)
