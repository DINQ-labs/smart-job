"""E2-3 跨平台候选人 preview 统一 shape 契约测试。

normalize_candidate_preview(raw, platform) 输入三平台原始 dict，输出相同 shape:
  {name, current_role, current_company, location, years, education,
   skills, summary, platform, platform_id}

覆盖：
- 每平台完整字段映射
- 字段缺失 → "" / [] / None
- platform 不识别 → 兜底
- 非 dict 输入 → 兜底
"""
from __future__ import annotations

import pytest

import server  # noqa: F401  conftest bootstrap
from candidate_preview import normalize_candidate_preview


# ── 各平台常见 shape 的真实字段一致性 ────────────────────────────────────

_EXPECTED_KEYS = {
    "name", "current_role", "current_company", "location", "years",
    "education", "skills", "summary", "platform", "platform_id",
}


def _assert_shape(out: dict):
    assert isinstance(out, dict)
    missing = _EXPECTED_KEYS - set(out.keys())
    assert not missing, f"normalize 输出缺字段: {missing}"


# ── Boss ─────────────────────────────────────────────────────────────────


class TestBoss:

    def test_full_fields(self):
        raw = {
            "geekName": "陈小明",
            "encryptGeekId": "eg-abc",
            "positionName": "后端工程师",
            "salary": "25-40k",
            "expectLocationName": "上海",
            "geekWorkYear": "5-10年",
            "geekDegree": "本科",
            "geekEdu": {"school": "复旦大学"},
            "geekWork": {"content": "后端开发", "companyName": "某互联网公司"},
            "skills": [{"name": "Python"}, {"name": "Go"}, {"name": "Redis"}],
        }
        out = normalize_candidate_preview(raw, "boss")
        _assert_shape(out)
        assert out["name"] == "陈小明"
        assert out["current_role"] == "后端开发"  # geekWork.content 优先于 positionName
        assert out["current_company"] == "某互联网公司"
        assert out["location"] == "上海"
        assert out["years"] == 5  # "5-10年" → 取最小
        assert out["education"] == "复旦大学 本科"
        assert out["skills"] == ["Python", "Go", "Redis"]
        assert out["platform"] == "boss"
        assert out["platform_id"] == "eg-abc"

    def test_workyear_from_text(self):
        """10年以上 → 10"""
        out = normalize_candidate_preview({"geekWorkYear": "10年以上"}, "boss")
        assert out["years"] == 10

    def test_workyear_missing(self):
        out = normalize_candidate_preview({}, "boss")
        assert out["years"] is None

    def test_skills_as_strings(self):
        raw = {"skillList": ["Java", "Kotlin"]}
        out = normalize_candidate_preview(raw, "boss")
        assert out["skills"] == ["Java", "Kotlin"]

    def test_position_fallback_order(self):
        """geekWork.content 缺失 → positionName 兜底"""
        out = normalize_candidate_preview(
            {"positionName": "产品经理", "geekWork": {}}, "boss",
        )
        assert out["current_role"] == "产品经理"


# ── LinkedIn ─────────────────────────────────────────────────────────────


class TestLinkedIn:

    def test_from_preview_profile_shape(self):
        """LinkedIn 场景：_extract_linkedin_profile_preview 输出已经是本 shape。
        normalize 只需补 platform / platform_id。"""
        preview_input = {
            "name": "Alice Chen",
            "current_role": "Senior ML Engineer",
            "current_company": "Google",
            "location": "SF",
            "years": 7,
            "education": "Stanford MS",
            "skills": ["Python", "PyTorch"],
            "summary": "ML engineer...",
            "public_id": "alice-chen-ml",
        }
        out = normalize_candidate_preview(preview_input, "linkedin")
        _assert_shape(out)
        assert out["platform"] == "linkedin"
        assert out["platform_id"] == "alice-chen-ml"
        assert out["name"] == "Alice Chen"

    def test_from_raw_voyager_profile(self):
        """兜底路径：传入原始 Voyager profile，normalize 也能处理"""
        raw_voyager = {
            "firstName": "Bob",
            "lastName": "Lee",
            "headline": "Staff Eng @ Meta",
            "publicIdentifier": "bob-lee",
            "profileExperience": {
                "elements": [
                    {"title": "Staff Engineer", "companyName": "Meta",
                     "timePeriod": {"startDate": {"year": 2018}}},
                ],
            },
        }
        out = normalize_candidate_preview(raw_voyager, "linkedin")
        _assert_shape(out)
        assert out["name"] == "Bob Lee"
        assert out["current_role"] == "Staff Engineer"
        assert out["platform"] == "linkedin"


# ── Indeed ───────────────────────────────────────────────────────────────


class TestIndeed:

    def test_full_fields(self):
        raw = {
            "name": "Carol Smith",
            "legacyId": "legacy-123",
            "currentTitle": "DevOps Engineer",
            "currentEmployer": "Netflix",
            "location": "Los Gatos, CA",
            "experience": "8 years",
            "education": [
                {"school": "Berkeley", "degree": "BS", "fieldOfStudy": "CS"},
            ],
            "skills": [{"name": "Kubernetes"}, {"name": "Terraform"}],
            "matchHighlights": ["Strong K8s background", "5+ years at scale"],
        }
        out = normalize_candidate_preview(raw, "indeed")
        _assert_shape(out)
        assert out["name"] == "Carol Smith"
        assert out["current_role"] == "DevOps Engineer"
        assert out["current_company"] == "Netflix"
        assert out["years"] == 8
        assert out["education"] == "Berkeley BS CS"
        assert out["skills"] == ["Kubernetes", "Terraform"]
        assert "K8s" in out["summary"]
        assert out["platform"] == "indeed"
        assert out["platform_id"] == "legacy-123"

    def test_experience_as_range(self):
        out = normalize_candidate_preview(
            {"experience": "5-10 years"}, "indeed",
        )
        assert out["years"] == 5

    def test_experience_as_int(self):
        out = normalize_candidate_preview(
            {"experience": 12}, "indeed",
        )
        assert out["years"] == 12

    def test_education_as_string(self):
        out = normalize_candidate_preview(
            {"education": "MIT PhD"}, "indeed",
        )
        assert out["education"] == "MIT PhD"

    def test_education_empty_list(self):
        out = normalize_candidate_preview(
            {"education": []}, "indeed",
        )
        assert out["education"] == ""


# ── Cross-platform shape invariance ──────────────────────────────────────


class TestShapeInvariance:
    """三平台输出 shape 必须一致，cross 模式 agent 才能用同一模板处理"""

    def test_three_platforms_same_keys(self):
        boss = normalize_candidate_preview({"geekName": "A"}, "boss")
        li   = normalize_candidate_preview({"name": "B", "current_role": "X"}, "linkedin")
        ind  = normalize_candidate_preview({"name": "C"}, "indeed")
        assert set(boss.keys()) == set(ind.keys())
        # LinkedIn preview 来自 full profile 会有额外 headline/industry/public_id/profile_url
        # 但核心 10 个字段必须齐全
        for out in (boss, li, ind):
            _assert_shape(out)


# ── Robustness ───────────────────────────────────────────────────────────


class TestCmdLayerAttachesPreview:
    """cmd_get_candidate_detail / cmd_get_candidate 把 _preview 叠加到返回 dict 里"""

    @pytest.mark.asyncio
    async def test_boss_cmd_attaches_preview(self, monkeypatch):
        import commands
        from unittest.mock import AsyncMock, MagicMock
        from datetime import datetime, timezone
        from session_store import SessionEntry, session_store

        # 准备 session
        sid = "sid-boss-prev"
        entry = SessionEntry(
            session_id=sid, browser_id=sid, ws=MagicMock(), pending={},
            connected_at=datetime.now(timezone.utc).isoformat(),
        )
        entry.rate_limiter.wait = AsyncMock(return_value=None)
        session_store._sessions[sid] = entry

        # mock 扩展响应
        raw_candidate = {
            "encryptGeekId": "eg-1",
            "geekName": "小明",
            "positionName": "后端",
            "expectLocationName": "上海",
            "detailSecurityId": "sec-1",
        }
        canned = {"ok": True, "code": 0, "data": raw_candidate}
        monkeypatch.setattr(commands, "send_command_to",
                            AsyncMock(return_value=canned))

        try:
            data = await commands.cmd_get_candidate_detail(
                security_id="sec-in", session_id=sid,
            )
            assert "_preview" in data, "cmd 应在返回里叠加 _preview"
            prev = data["_preview"]
            assert prev["platform"] == "boss"
            assert prev["name"] == "小明"
            assert prev["platform_id"] == "eg-1"
        finally:
            session_store._sessions.pop(sid, None)

    @pytest.mark.asyncio
    async def test_indeed_employer_cmd_attaches_preview(self, monkeypatch):
        import indeed_employer_commands as ie
        from unittest.mock import AsyncMock

        raw = {
            "legacyId": "leg-42",
            "name": "Alice",
            "currentTitle": "SRE",
            "location": "Remote",
        }
        # indeed_employer_commands._send 的响应用 result.result 封装
        canned = {"result": raw}
        monkeypatch.setattr(ie, "send_command_to",
                            AsyncMock(return_value=canned))
        # _send 还要检查 session 存在 → stub
        from session_store import SessionEntry, session_store
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        sid = "sid-ind-emp"
        entry = SessionEntry(
            session_id=sid, browser_id=sid, ws=MagicMock(), pending={},
            connected_at=datetime.now(timezone.utc).isoformat(),
        )
        session_store._sessions[sid] = entry
        try:
            data = await ie.cmd_get_candidate(session_id=sid, legacy_id="leg-42")
            assert "_preview" in data
            assert data["_preview"]["platform"] == "indeed"
            assert data["_preview"]["platform_id"] == "leg-42"
        finally:
            session_store._sessions.pop(sid, None)


class TestRobustness:

    def test_non_dict_input(self):
        out = normalize_candidate_preview(None, "boss")
        _assert_shape(out)
        assert out["name"] == ""
        assert out["platform"] == "boss"

    def test_empty_dict(self):
        out = normalize_candidate_preview({}, "boss")
        _assert_shape(out)
        assert out["name"] == ""
        assert out["years"] is None
        assert out["skills"] == []

    def test_unknown_platform_fallback(self):
        """未知 platform → 兜底返回 shape 但字段大多空"""
        out = normalize_candidate_preview({"name": "X"}, "mystery")
        _assert_shape(out)
        assert out["name"] == "X"
        assert out["platform"] == "mystery"

    def test_platform_case_insensitive(self):
        out = normalize_candidate_preview({"geekName": "Y"}, "BOSS")
        _assert_shape(out)
        assert out["name"] == "Y"
        assert out["platform"] == "boss"
