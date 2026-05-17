"""E2-2 LinkedIn check_applied —— 投递去重 guard rail。

_extract_applied_flag() 从 LinkedIn Voyager 职位详情响应里提取 applied 状态。
LinkedIn 内部模型字段位置因版本不同多变，本函数防御性扫描常见路径。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import server  # noqa: F401  conftest bootstrap
import linkedin_commands as li_cmd
from linkedin_commands import _extract_applied_flag


class TestExtractFlag:

    def test_top_level_bool(self):
        assert _extract_applied_flag({"applied": True}) is True
        assert _extract_applied_flag({"applied": False}) is False

    def test_applyingInfo_nested(self):
        assert _extract_applied_flag({"applyingInfo": {"applied": True}}) is True

    def test_applyingInfo_timestamp_implies_applied(self):
        """appliedAt 时间戳存在 → 视为已投（即使没有 applied bool）"""
        assert _extract_applied_flag({"applyingInfo": {"appliedAt": 1700000000}}) is True
        assert _extract_applied_flag({"applyingInfo": {"appliedTimestamp": 1700000000}}) is True

    def test_applicantsInfo_isApplied(self):
        assert _extract_applied_flag({"applicantsInfo": {"isApplied": True}}) is True

    def test_jobApplyingInfo_nested(self):
        assert _extract_applied_flag({"jobApplyingInfo": {"applied": False}}) is False

    def test_none_when_no_signal(self):
        assert _extract_applied_flag({}) is None
        assert _extract_applied_flag({"title": "Engineer"}) is None

    def test_none_for_non_dict(self):
        assert _extract_applied_flag(None) is None
        assert _extract_applied_flag("") is None
        assert _extract_applied_flag([]) is None

    def test_prefers_explicit_bool_over_timestamp(self):
        """显式 applied=false + 有 appliedAt timestamp → 认 applied=false
        （时间戳路径是兜底，显式字段优先）"""
        raw = {"applied": False, "applyingInfo": {"appliedAt": 1700000000}}
        assert _extract_applied_flag(raw) is False


class TestCmdCheckApplied:

    @pytest.mark.asyncio
    async def test_returns_applied_true(self, monkeypatch):
        async def fake_get_detail(sid, jid):
            assert jid == "4123456789"
            return {"jobId": jid, "raw": {"applied": True}}
        monkeypatch.setattr(li_cmd, "cmd_get_job_detail", fake_get_detail)

        r = await li_cmd.cmd_check_applied("sid", "4123456789")
        assert r["applied"] is True
        assert r["job_id"] == "4123456789"
        assert "note" not in r

    @pytest.mark.asyncio
    async def test_returns_applied_false(self, monkeypatch):
        async def fake_get_detail(sid, jid):
            return {"raw": {"applyingInfo": {"applied": False}}}
        monkeypatch.setattr(li_cmd, "cmd_get_job_detail", fake_get_detail)

        r = await li_cmd.cmd_check_applied("sid", "job-2")
        assert r["applied"] is False

    @pytest.mark.asyncio
    async def test_returns_null_when_field_missing(self, monkeypatch):
        """响应里无可识别字段 → applied=None + 带 note"""
        async def fake_get_detail(sid, jid):
            return {"raw": {"title": "x"}}
        monkeypatch.setattr(li_cmd, "cmd_get_job_detail", fake_get_detail)

        r = await li_cmd.cmd_check_applied("sid", "job-3")
        assert r["applied"] is None
        assert "note" in r

    @pytest.mark.asyncio
    async def test_handles_malformed_detail_response(self, monkeypatch):
        """cmd_get_job_detail 返回非 dict → 不 crash，返回 applied=None"""
        async def fake_get_detail(sid, jid):
            return None
        monkeypatch.setattr(li_cmd, "cmd_get_job_detail", fake_get_detail)

        r = await li_cmd.cmd_check_applied("sid", "job-x")
        assert r["applied"] is None
        assert r["job_id"] == "job-x"
