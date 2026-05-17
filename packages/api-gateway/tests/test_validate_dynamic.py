"""scripts/validate_dynamic.py 单元测试。

不依赖 conftest 的 mock —— 该脚本是纯模块，无 db / 网络副作用。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 保证 scripts/ 可 import
_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from scripts.validate_dynamic import (
    validate_command, validate_chain, lint_root, _validate_url_host,
    _BUILTIN_CHAIN_NAMESPACES,
)


def _good_cmd(**override) -> dict:
    base = {
        "path": "boss/foo",
        "description": "10+ 字符的描述说明",
        "mcp": {
            "name": "boss_foo",
            "params": [{"name": "x", "type": "int", "default": 0, "required": False}],
        },
        "requires": None,
        "produces": None,
        "requestBuilder": {
            "method": "POST",
            "url": "/wapi/foo",
            "contentType": "form",
            "body": {"x": "{{body.x}}"},
        },
        "metadata": {"owner": "tester", "added_at": "2026-04-26"},
    }
    base.update(override)
    return base


class TestValidateCommand:
    def test_happy_path(self):
        res = validate_command(_good_cmd(), "f.yaml", _BUILTIN_CHAIN_NAMESPACES)
        assert res.ok, res.errors
        assert not res.warnings

    def test_path_required(self):
        c = _good_cmd(); c["path"] = ""
        res = validate_command(c, "f.yaml", set())
        assert not res.ok

    def test_path_format(self):
        c = _good_cmd(); c["path"] = "BadPath"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok and "path" in res.errors[0]

    def test_path_collides_with_static(self):
        c = _good_cmd(); c["path"] = "boss/search_jobs"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok and any("撞静态" in e for e in res.errors)

    def test_description_too_short(self):
        c = _good_cmd(); c["description"] = "短"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok

    def test_mcp_name_format(self):
        c = _good_cmd(); c["mcp"]["name"] = "BAD-NAME"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok

    def test_mcp_name_static_collision(self):
        c = _good_cmd(); c["mcp"]["name"] = "boss_search_jobs"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok

    def test_method_whitelist(self):
        c = _good_cmd(); c["requestBuilder"]["method"] = "TRACE"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok

    def test_url_relative_ok(self):
        c = _good_cmd(); c["requestBuilder"]["url"] = "/wapi/x"
        res = validate_command(c, "f.yaml", set())
        assert res.ok, res.errors

    def test_url_absolute_whitelisted(self):
        c = _good_cmd(); c["requestBuilder"]["url"] = "https://www.zhipin.com/wapi/x"
        res = validate_command(c, "f.yaml", set())
        assert res.ok, res.errors

    def test_url_absolute_blocked(self):
        c = _good_cmd(); c["requestBuilder"]["url"] = "https://evil.example.com/x"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok and any("白名单" in e for e in res.errors)

    def test_url_invalid_scheme(self):
        c = _good_cmd(); c["requestBuilder"]["url"] = "ftp://x"
        res = validate_command(c, "f.yaml", set())
        assert not res.ok

    def test_template_var_undeclared_warning(self):
        c = _good_cmd()
        c["requestBuilder"]["body"] = {"y": "{{body.undeclared}}"}
        res = validate_command(c, "f.yaml", set())
        # 不阻止合并，但要 warn
        assert res.ok
        assert any("undeclared" in w for w in res.warnings)

    def test_requires_unknown_chain(self):
        c = _good_cmd()
        c["requires"] = {"chain": "nonexistent", "stage": "list"}
        res = validate_command(c, "f.yaml", _BUILTIN_CHAIN_NAMESPACES)
        assert not res.ok

    def test_requires_known_chain_ok(self):
        c = _good_cmd()
        c["requires"] = {"chain": "jobs", "stage": "list", "keyParam": "encrypt_job_id"}
        res = validate_command(c, "f.yaml", _BUILTIN_CHAIN_NAMESPACES)
        assert res.ok, res.errors

    def test_produces_extractor_invalid_type(self):
        c = _good_cmd()
        c["produces"] = {"chain": "jobs", "stage": "list",
                         "extractor": {"type": "regex"}}
        res = validate_command(c, "f.yaml", _BUILTIN_CHAIN_NAMESPACES)
        assert not res.ok

    def test_produces_extractor_named_ok(self):
        c = _good_cmd()
        c["produces"] = {"chain": "jobs", "stage": "list",
                         "extractor": {"type": "named:custom_extractor"}}
        res = validate_command(c, "f.yaml", _BUILTIN_CHAIN_NAMESPACES)
        assert res.ok, res.errors

    def test_owner_missing_warning(self):
        c = _good_cmd(); c["metadata"] = {}
        res = validate_command(c, "f.yaml", set())
        assert res.ok  # warning 不挂
        assert any("owner" in w for w in res.warnings)


class TestValidateChain:
    def test_happy(self):
        chain = {
            "namespace": "companies",
            "entityKey": "encryptCompanyId",
            "stages": [
                {"name": "search", "field": "searchSecurityId"},
                {"name": "detail", "field": "detailSecurityId", "requires": "search"},
            ],
        }
        res = validate_chain(chain, "c.yaml")
        assert res.ok, res.errors

    def test_namespace_collides_with_builtin(self):
        chain = {"namespace": "jobs", "entityKey": "x",
                 "stages": [{"name": "a", "field": "f"}]}
        res = validate_chain(chain, "c.yaml")
        assert not res.ok

    def test_stages_empty(self):
        chain = {"namespace": "x", "entityKey": "y", "stages": []}
        res = validate_chain(chain, "c.yaml")
        assert not res.ok

    def test_stage_requires_forward_reference(self):
        chain = {
            "namespace": "x", "entityKey": "y",
            "stages": [
                {"name": "a", "field": "fa", "requires": "b"},  # b 还未定义
                {"name": "b", "field": "fb"},
            ],
        }
        res = validate_chain(chain, "c.yaml")
        assert not res.ok and any("顺序错" in e for e in res.errors)

    def test_stage_name_duplicate(self):
        chain = {
            "namespace": "x", "entityKey": "y",
            "stages": [
                {"name": "a", "field": "fa"},
                {"name": "a", "field": "fb"},
            ],
        }
        res = validate_chain(chain, "c.yaml")
        assert not res.ok


class TestUrlHost:
    def test_relative(self):
        assert _validate_url_host("/wapi/x") is None

    def test_subdomain_whitelisted(self):
        assert _validate_url_host("https://www.zhipin.com/x") is None
        assert _validate_url_host("https://api.linkedin.com/x") is None

    def test_blocked_host(self):
        msg = _validate_url_host("https://attacker.com/x")
        assert msg and "白名单" in msg

    def test_no_host(self):
        msg = _validate_url_host("not-a-url")
        assert msg


class TestLintRoot:
    def test_lints_real_dynamic_commands_dir(self, tmp_path):
        """构造一个最小 dir 跑端到端。"""
        (tmp_path / "boss").mkdir()
        (tmp_path / "boss" / "ok.yaml").write_text(
            "path: boss/test_op\n"
            "description: '足够长的描述说明文本超过十个字符'\n"
            "mcp: {name: boss_test_op, params: []}\n"
            "requestBuilder: {method: GET, url: /wapi/x}\n"
            "metadata: {owner: t}\n",
            encoding="utf-8",
        )
        res = lint_root(tmp_path)
        assert res.ok, res.errors

    def test_duplicate_path_across_files(self, tmp_path):
        (tmp_path / "boss").mkdir()
        for i in (1, 2):
            (tmp_path / "boss" / f"f{i}.yaml").write_text(
                "path: boss/dup\n"
                "description: '足够长的描述说明文本超过十个字符'\n"
                "mcp: {name: boss_dup_a, params: []}\n"
                "requestBuilder: {method: GET, url: /x}\n"
                "metadata: {owner: t}\n",
                encoding="utf-8",
            )
        res = lint_root(tmp_path)
        assert not res.ok and any("重复" in e for e in res.errors)

    def test_examples_dir_not_walked(self, tmp_path):
        """_examples/ 不参与 lint。"""
        (tmp_path / "_examples").mkdir()
        (tmp_path / "_examples" / "bad.yaml").write_text("not yaml at all", encoding="utf-8")
        res = lint_root(tmp_path)
        assert res.ok
