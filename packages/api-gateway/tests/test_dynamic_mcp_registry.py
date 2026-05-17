"""dynamic_mcp_registry 测试（Phase 2）。

用真 FastMCP（非 conftest 里的 MagicMock），断言：
  - register/clear/replace 行为
  - tools/list 含注册的工具，schema 反映 yaml params
  - apply_config 全量替换
  - apply_latest_from_db 兜底空 DB

注意：conftest.py 把 fastmcp setdefault 成 MagicMock；这里在 import 我们
模块前先 pop 掉 mock，再 import 真 fastmcp，让 dynamic_mcp_registry 用真的。
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest

# 1) 先确保所有 conftest 早期 patch 完成（import server 触发 conftest 里的 setdefault）
import server  # noqa: F401  conftest bootstrap

# 2) 替换 fastmcp 为真模块（pop 掉 MagicMock）
for _m in [
    "fastmcp", "fastmcp.tools", "fastmcp.tools.tool",
    "dynamic_mcp_registry", "commands",
]:
    sys.modules.pop(_m, None)
import fastmcp  # noqa: E402  真模块
from fastmcp import FastMCP  # noqa: E402

# 3) commands.send_command_to 在真 import 时还没 mock；test 里 monkeypatch 即可
import commands  # noqa: E402
import dynamic_mcp_registry as reg  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_mcp_per_test(monkeypatch):
    """每个 test 开新的 FastMCP，避免 tool 名跨 test 串号。"""
    mcp = FastMCP(name="test-gateway")
    reg.set_mcp(mcp)
    # 清空模块级 _registered（前一个 test 残留的话）
    reg._registered.clear()  # type: ignore[attr-defined]
    # 让 cmd_dynamic_dispatch 不真的发 WS：替换 send_command_to + resolve_session
    class _Entry:
        session_id = "sess-x"
        rate_limiter = type("RL", (), {"wait": staticmethod(lambda *_a, **_kw: _noop_async())})()
    monkeypatch.setattr(commands.session_store, "resolve_session",
                        lambda sid=None: _Entry, raising=True)
    monkeypatch.setattr(commands, "send_command_to",
                        AsyncMock(return_value={"ok": True, "raw": {"code": 0}}))
    return mcp


async def _noop_async():
    return None


def _cmd_def_basic() -> dict:
    return {
        "path": "boss/test_ping",
        "description": "动态命令链路自检 GET wt token",
        "mcp": {"name": "boss_test_ping", "params": []},
        "requestBuilder": {"method": "GET", "url": "/wapi/zppassport/get/wt"},
    }


def _cmd_def_with_params() -> dict:
    return {
        "path": "boss/recruiter_chat_list",
        "description": "招聘方全局聊天列表（POST filterByLabel）",
        "mcp": {
            "name": "boss_recruiter_chat_list",
            "params": [
                {"name": "label_id", "type": "int", "default": 0, "required": False},
                {"name": "sort", "type": "str", "default": "", "required": False},
                {"name": "must_have", "type": "str", "required": True},
            ],
        },
        "requestBuilder": {
            "method": "POST",
            "url": "/wapi/zprelation/friend/filterByLabel",
            "contentType": "form",
            "body": {"labelId": "{{body.label_id}}"},
        },
    }


@pytest.mark.asyncio
async def test_register_basic_appears_in_tools_list(_fresh_mcp_per_test):
    mcp = _fresh_mcp_per_test
    name = reg.register_dynamic_mcp_tool(_cmd_def_basic())
    assert name == "boss_test_ping"
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert "boss_test_ping" in names
    t = next(t for t in tools if t.name == "boss_test_ping")
    assert "动态命令链路自检" in (t.description or "")


@pytest.mark.asyncio
async def test_register_params_reflect_in_schema(_fresh_mcp_per_test):
    mcp = _fresh_mcp_per_test
    reg.register_dynamic_mcp_tool(_cmd_def_with_params())
    tools = await mcp.list_tools()
    t = next(t for t in tools if t.name == "boss_recruiter_chat_list")
    schema = t.parameters
    props = schema["properties"]
    # 业务参数都在 schema 里
    assert "label_id" in props
    assert "sort" in props
    assert "must_have" in props
    # 默认值 / required
    assert props["label_id"]["default"] == 0
    assert props["sort"]["default"] == ""
    required = set(schema.get("required", []))
    assert "must_have" in required
    assert "label_id" not in required
    # session_id / app_user_id 由 _run_boss_tool 暴露的兼容字段，但不是必填
    assert "session_id" in props
    assert "app_user_id" in props
    assert "session_id" not in required


def test_register_skips_missing_mcp_name(_fresh_mcp_per_test):
    bad = _cmd_def_basic()
    bad["mcp"] = {}
    assert reg.register_dynamic_mcp_tool(bad) is None
    assert reg.list_registered() == []


def test_register_skips_missing_url(_fresh_mcp_per_test):
    bad = _cmd_def_basic()
    bad["requestBuilder"] = {"method": "GET"}
    assert reg.register_dynamic_mcp_tool(bad) is None


@pytest.mark.asyncio
async def test_re_register_replaces_old(_fresh_mcp_per_test):
    mcp = _fresh_mcp_per_test
    reg.register_dynamic_mcp_tool(_cmd_def_basic())
    # 改 description 重注册
    new = _cmd_def_basic()
    new["description"] = "改过描述了"
    reg.register_dynamic_mcp_tool(new)
    tools = await mcp.list_tools()
    matches = [t for t in tools if t.name == "boss_test_ping"]
    assert len(matches) == 1
    assert "改过描述了" in (matches[0].description or "")


@pytest.mark.asyncio
async def test_clear_removes_all(_fresh_mcp_per_test):
    mcp = _fresh_mcp_per_test
    reg.register_dynamic_mcp_tool(_cmd_def_basic())
    reg.register_dynamic_mcp_tool(_cmd_def_with_params())
    assert len(reg.list_registered()) == 2
    n = reg.clear_dynamic_mcp_tools()
    assert n == 2
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert "boss_test_ping" not in names
    assert "boss_recruiter_chat_list" not in names


@pytest.mark.asyncio
async def test_apply_config_replaces_old_set(_fresh_mcp_per_test):
    mcp = _fresh_mcp_per_test
    reg.apply_config([_cmd_def_basic(), _cmd_def_with_params()])
    assert {x["name"] for x in reg.list_registered()} == {
        "boss_test_ping", "boss_recruiter_chat_list",
    }
    # 新版本只保留 test_ping
    result = reg.apply_config([_cmd_def_basic()])
    assert result["added"] == ["boss_test_ping"]
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "boss_test_ping" in names
    assert "boss_recruiter_chat_list" not in names


def test_apply_config_collects_failures(_fresh_mcp_per_test):
    bad_no_url = {
        "path": "boss/missing_url",
        "description": "no url",
        "mcp": {"name": "boss_no_url", "params": []},
        "requestBuilder": {"method": "GET"},
    }
    result = reg.apply_config([_cmd_def_basic(), bad_no_url, "not a dict"])
    assert "boss_test_ping" in result["added"]
    assert "boss/missing_url" in result["skipped"]
    assert "boss_no_url" not in {x["name"] for x in reg.list_registered()}


@pytest.mark.asyncio
async def test_apply_latest_from_db_empty(_fresh_mcp_per_test, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_latest_dynamic_config",
                        AsyncMock(return_value=None), raising=False)
    out = await reg.apply_latest_from_db()
    assert out["added"] == []


@pytest.mark.asyncio
async def test_apply_latest_from_db_loads_cmds(_fresh_mcp_per_test, monkeypatch):
    import db
    cfg = {"version": "v9", "dynamic_commands": [_cmd_def_basic()]}
    monkeypatch.setattr(db, "get_latest_dynamic_config",
                        AsyncMock(return_value=cfg), raising=False)
    out = await reg.apply_latest_from_db()
    assert out["added"] == ["boss_test_ping"]


@pytest.mark.asyncio
async def test_apply_latest_from_db_swallows_errors(_fresh_mcp_per_test, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_latest_dynamic_config",
                        AsyncMock(side_effect=RuntimeError("db down")),
                        raising=False)
    out = await reg.apply_latest_from_db()
    assert out == {"added": [], "failed": [], "skipped": []}


def test_set_mcp_required(monkeypatch):
    monkeypatch.setattr(reg, "_mcp", None)
    with pytest.raises(RuntimeError, match="未注入"):
        reg.register_dynamic_mcp_tool(_cmd_def_basic())
