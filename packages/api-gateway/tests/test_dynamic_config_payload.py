"""_validate_dynamic_config_payload 回归测试。

校验 admin push endpoint 的 payload schema —— 在 DB 写入和广播之前拦掉
显然非法的输入。深度 schema 留给 Phase 1b 的 yaml lint。
"""
from __future__ import annotations

import server  # noqa: F401  conftest bootstrap
from http_routes import _validate_dynamic_config_payload as V


def _payload(**kw):
    base = {"version": "v1", "dynamic_commands": [_cmd()]}
    base.update(kw)
    return base


def _cmd(**kw):
    base = {"path": "boss/foo",
            "requestBuilder": {"method": "GET", "url": "/wapi/foo"}}
    base.update(kw)
    return base


def test_valid_minimal():
    ok, _ = V({"version": "v1", "dynamic_commands": [_cmd()]})
    assert ok


def test_valid_chains_only():
    ok, _ = V({"version": "v1", "chains": {"foo": {"namespace": "foo", "stages": []}}})
    assert ok


def test_payload_must_be_object():
    ok, msg = V("not a dict")
    assert not ok and "对象" in msg
    ok, _ = V(None)
    assert not ok


def test_version_required():
    ok, msg = V({"dynamic_commands": [_cmd()]})
    assert not ok and "version" in msg


def test_version_too_long():
    ok, msg = V({"version": "x" * 100, "dynamic_commands": [_cmd()]})
    assert not ok and "64" in msg


def test_chains_not_dict():
    ok, msg = V({"version": "v1", "chains": "not-dict", "dynamic_commands": [_cmd()]})
    assert not ok and "chains" in msg


def test_commands_not_list():
    ok, msg = V({"version": "v1", "dynamic_commands": "not-list"})
    assert not ok and "数组" in msg


def test_both_empty():
    ok, msg = V({"version": "v1"})
    assert not ok and "至少" in msg
    ok, _ = V({"version": "v1", "chains": {}, "dynamic_commands": []})
    assert not ok


def test_command_path_required():
    ok, msg = V(_payload(dynamic_commands=[{"requestBuilder": {"method": "GET", "url": "/x"}}]))
    assert not ok and "path" in msg


def test_command_path_format():
    ok, msg = V(_payload(dynamic_commands=[_cmd(path="no-slash")]))
    assert not ok and "site/cmd_name" in msg


def test_command_path_duplicate():
    ok, msg = V(_payload(dynamic_commands=[_cmd(), _cmd()]))
    assert not ok and "重复" in msg


def test_command_request_builder_required():
    ok, msg = V(_payload(dynamic_commands=[{"path": "boss/x"}]))
    assert not ok and "requestBuilder" in msg


def test_command_url_required():
    ok, msg = V(_payload(dynamic_commands=[
        _cmd(requestBuilder={"method": "GET", "url": ""}),
    ]))
    assert not ok and "url" in msg


def test_command_method_whitelist():
    ok, msg = V(_payload(dynamic_commands=[
        _cmd(requestBuilder={"method": "TRACE", "url": "/x"}),
    ]))
    assert not ok and "method" in msg


def test_method_case_insensitive():
    ok, _ = V(_payload(dynamic_commands=[
        _cmd(requestBuilder={"method": "post", "url": "/x"}),
    ]))
    assert ok
