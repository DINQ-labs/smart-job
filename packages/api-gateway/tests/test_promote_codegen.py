"""promote_to_static.py 的 codegen helpers（Phase 2）。

不测 evaluate / fetch_stats（已被 test_promote_helpers 覆盖或不在范围）。
专注：_gen_ext_handler / _gen_cmd_fn / _gen_mcp_tool / _write_patches 输出
是否 syntactically 合理 + 包含关键串。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 不依赖 conftest（promote_to_static 是 standalone 脚本，不 import server）
_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from scripts import promote_to_static as ps  # noqa: E402


def _basic_def() -> dict:
    return {
        "path": "boss/test_ping",
        "description": "动态命令链路自检（GET wt token）",
        "mcp": {"name": "boss_test_ping", "params": []},
        "requestBuilder": {"method": "GET", "url": "/wapi/zppassport/get/wt"},
    }


def _post_def_with_params() -> dict:
    return {
        "path": "boss/recruiter_chat_list",
        "description": "招聘方全局聊天列表",
        "mcp": {
            "name": "boss_recruiter_chat_list",
            "params": [
                {"name": "label_id", "type": "int", "default": 0, "required": False},
                {"name": "sort", "type": "str", "default": "", "required": False},
            ],
        },
        "requestBuilder": {
            "method": "POST",
            "url": "/wapi/zprelation/friend/filterByLabel",
            "contentType": "form",
            "body": {"labelId": "{{body.label_id}}", "sort": "{{body.sort}}"},
        },
    }


def test_cmd_fn_name_slug():
    assert ps._cmd_fn_name("boss/test_ping") == "cmd_boss_test_ping"
    assert ps._cmd_fn_name("boss/recruiter-chat") == "cmd_boss_recruiter_chat"


def test_site_from_path():
    assert ps._site_from_path("boss/foo") == "boss"
    assert ps._site_from_path("linkedin/x") == "linkedin"
    assert ps._site_from_path("indeed/y") == "indeed"
    assert ps._site_from_path("unknown/z") == "boss"  # fallback


def test_py_param_signature_required_first():
    sig = ps._py_param_signature([
        {"name": "must", "type": "str", "required": True},
        {"name": "opt", "type": "int", "default": 5, "required": False},
    ])
    assert "must: str" in sig
    assert "opt: int = 5" in sig


def test_gen_cmd_fn_basic():
    out = ps._gen_cmd_fn(_basic_def())
    assert "async def cmd_boss_test_ping" in out
    assert 'send_command_to' in out
    assert '"GET"' in out
    assert '"boss/test_ping"' in out
    assert 'tool_name="boss_test_ping"' in out
    # 应该是合法 Python 语法
    compile(out, "<gen>", "exec")


def test_gen_cmd_fn_with_params():
    out = ps._gen_cmd_fn(_post_def_with_params())
    assert "label_id: int = 0" in out
    assert "sort: str = ''" in out or 'sort: str = ""' in out
    assert '"POST"' in out
    assert '"label_id": label_id' in out
    compile(out, "<gen>", "exec")


def test_gen_mcp_tool_basic():
    out = ps._gen_mcp_tool(_basic_def())
    assert "@mcp.tool()" in out
    assert "async def boss_test_ping(ctx: Context" in out
    assert "_run_boss_tool" in out
    assert "cmd_boss_test_ping" in out
    compile(out, "<gen>", "exec")


def test_gen_mcp_tool_with_params_passes_kwargs():
    out = ps._gen_mcp_tool(_post_def_with_params())
    assert "label_id=label_id" in out
    assert "sort=sort" in out
    compile(out, "<gen>", "exec")


def test_gen_ext_handler_get_no_body():
    out = ps._gen_ext_handler(_basic_def())
    assert "defineCommand" in out
    assert '"boss/test_ping"' in out
    assert 'method: "GET"' in out
    assert "body: undefined" in out


def test_gen_ext_handler_post_form():
    out = ps._gen_ext_handler(_post_def_with_params())
    assert 'method: "POST"' in out
    assert "URLSearchParams" in out
    assert "x-www-form-urlencoded" in out
    assert "{{body.label_id}}" in out  # 模板字符串保留，扩展端运行时再渲染


def test_write_patches_creates_three_files(tmp_path):
    written = ps._write_patches(_basic_def(), tmp_path)
    assert len(written) == 3
    target = tmp_path / "boss_test_ping"
    files = sorted(p.name for p in target.iterdir())
    assert files == [
        "01_ext_command_boss.js",
        "02_commands_py_fn.py",
        "03_mcp_tool_boss.py",
    ]
    # 写入内容非空
    for p in target.iterdir():
        assert p.read_text(encoding="utf-8").strip()


def test_codegen_cli_apply_flow(tmp_path, monkeypatch, capsys):
    """跑 main(['--codegen', 'boss/test_ping', '--write', tmp]) 应能成功。"""
    # 用真实的 dynamic-commands 目录跑（test_ping 已存在）
    rc = ps.main([
        "--codegen", "boss/test_ping",
        "--write", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "boss/test_ping" in out
    assert (tmp_path / "boss_test_ping" / "02_commands_py_fn.py").is_file()


def test_codegen_unknown_path_returns_1(tmp_path, capsys):
    rc = ps.main(["--codegen", "boss/__no_such_command__"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "找不到" in captured.err
