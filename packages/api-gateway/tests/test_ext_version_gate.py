"""扩展版本兼容性闸（http_routes.ext_version_meets_min）回归测试。

目的：在 capabilities 阶段拒绝低于 MIN_EXT_VERSION 的扩展。1.5.4 起新增了
boss/geek_filter_by_label 命令，更早版本会触发"未知命令: GET ..."。
"""
from __future__ import annotations

import server  # noqa: F401  conftest bootstrap
from http_routes import _parse_ext_version, ext_version_meets_min


class TestParseExtVersion:
    def test_normal_semver(self):
        assert _parse_ext_version("1.5.4") == (1, 5, 4)

    def test_two_parts(self):
        assert _parse_ext_version("2.0") == (2, 0)

    def test_four_parts(self):
        assert _parse_ext_version("1.5.4.10") == (1, 5, 4, 10)

    def test_empty_returns_zero(self):
        assert _parse_ext_version("") == (0,)
        assert _parse_ext_version(None) == (0,)  # type: ignore[arg-type]

    def test_garbage_returns_zero(self):
        assert _parse_ext_version("v1.5.x") == (0,)
        assert _parse_ext_version("not-a-version") == (0,)


class TestExtVersionMeetsMin:
    def test_equal_version_passes(self):
        assert ext_version_meets_min("1.5.4", "1.5.4") is True

    def test_higher_version_passes(self):
        assert ext_version_meets_min("1.5.5", "1.5.4") is True
        assert ext_version_meets_min("2.0.0", "1.5.4") is True

    def test_lower_version_blocked(self):
        assert ext_version_meets_min("1.5.3", "1.5.4") is False
        assert ext_version_meets_min("1.3.1", "1.5.4") is False
        assert ext_version_meets_min("0.9.0", "1.5.4") is False

    def test_missing_version_blocked(self):
        # 老扩展不上报 manifest_version → 视为不合规
        assert ext_version_meets_min(None, "1.5.4") is False
        assert ext_version_meets_min("", "1.5.4") is False

    def test_unparseable_blocked(self):
        assert ext_version_meets_min("garbage", "1.5.4") is False

    def test_unequal_length_compares_lexicographically(self):
        # (1,5) vs (1,5,4) → (1,5) < (1,5,4)，被判旧
        assert ext_version_meets_min("1.5", "1.5.4") is False
        # (1,6) vs (1,5,4) → (1,6) > (1,5,4)，通过
        assert ext_version_meets_min("1.6", "1.5.4") is True

    def test_empty_min_disables_gate(self):
        # MIN_EXT_VERSION='' 用作运维兜底（出问题快速放开）
        assert ext_version_meets_min("0.1.0", "") is True
        assert ext_version_meets_min(None, "") is True
