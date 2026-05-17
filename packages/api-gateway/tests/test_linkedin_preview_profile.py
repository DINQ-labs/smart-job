"""E2-1 LinkedIn preview_profile —— 字段提取契约。

_extract_linkedin_profile_preview() 从 Voyager /identity/dash/profiles 返回的
profile dict 里抽 Recruiter 初筛需要的紧凑字段。本文件用仿真 Voyager 响应
覆盖常见字段组合 + 异常兜底。
"""
from __future__ import annotations

import pytest

import server  # noqa: F401  conftest bootstrap
import linkedin_commands as li_cmd
from linkedin_commands import _extract_linkedin_profile_preview


def _sample_profile_full() -> dict:
    """典型 Voyager profile 载荷（含完整字段）。"""
    return {
        "firstName": "Alice",
        "lastName": "Chen",
        "headline": "Senior ML Engineer @ Google",
        "publicIdentifier": "alice-chen-ml",
        "industryName": "Computer Software",
        "geoLocation": {
            "geo": {"defaultLocalizedName": "San Francisco Bay Area"},
        },
        "summary": "ML engineer with 8 years experience in NLP and recommendation systems.",
        "profileExperience": {
            "elements": [
                {
                    "title": "Senior ML Engineer",
                    "companyName": "Google",
                    "timePeriod": {"startDate": {"year": 2020}},
                },
                {
                    "title": "ML Engineer",
                    "company": {"name": "Meta"},
                    "timePeriod": {
                        "startDate": {"year": 2017},
                        "endDate": {"year": 2020},
                    },
                },
            ],
        },
        "profileEducation": {
            "elements": [
                {
                    "schoolName": "Stanford University",
                    "degreeName": "MS",
                    "fieldOfStudy": "Computer Science",
                },
            ],
        },
        "profileSkills": {
            "elements": [
                {"name": "Python"},
                {"name": "PyTorch"},
                {"name": "NLP"},
                {"name": "TensorFlow"},
                {"name": "Distributed Systems"},
            ],
        },
    }


class TestExtractFull:

    def test_core_identity_fields(self):
        out = _extract_linkedin_profile_preview(_sample_profile_full())
        assert out["name"] == "Alice Chen"
        assert out["headline"] == "Senior ML Engineer @ Google"
        assert out["public_id"] == "alice-chen-ml"
        assert out["profile_url"] == "https://www.linkedin.com/in/alice-chen-ml"
        assert out["industry"] == "Computer Software"

    def test_location_from_geoLocation_nested(self):
        out = _extract_linkedin_profile_preview(_sample_profile_full())
        assert out["location"] == "San Francisco Bay Area"

    def test_current_role_from_first_position(self):
        out = _extract_linkedin_profile_preview(_sample_profile_full())
        assert out["current_role"] == "Senior ML Engineer"
        assert out["current_company"] == "Google"

    def test_years_estimated_from_earliest_start(self):
        """profile 里最早一段是 Meta 2017 起 → 至今 ~7-8 年"""
        from datetime import datetime, timezone
        out = _extract_linkedin_profile_preview(_sample_profile_full())
        expected = datetime.now(timezone.utc).year - 2017
        assert out["years"] == expected

    def test_education_joined(self):
        out = _extract_linkedin_profile_preview(_sample_profile_full())
        assert out["education"] == "Stanford University MS Computer Science"

    def test_skills_extracted_and_capped(self):
        out = _extract_linkedin_profile_preview(_sample_profile_full())
        assert out["skills"] == ["Python", "PyTorch", "NLP", "TensorFlow", "Distributed Systems"]
        assert len(out["skills"]) <= 8  # _MAX_SKILLS

    def test_summary_preserved(self):
        out = _extract_linkedin_profile_preview(_sample_profile_full())
        assert "NLP" in out["summary"]

    def test_positions_capped_to_3(self):
        profile = _sample_profile_full()
        profile["profileExperience"]["elements"].extend([
            {"title": "Intern", "companyName": "X"},
            {"title": "Junior", "companyName": "Y"},
            {"title": "Student", "companyName": "Z"},
        ])
        out = _extract_linkedin_profile_preview(profile)
        assert len(out["positions"]) == 3


class TestExtractFallbacks:
    """缺字段 / 字段格式异常时不应 crash"""

    def test_empty_profile(self):
        out = _extract_linkedin_profile_preview({})
        assert out["name"] == ""
        assert out["current_role"] == ""
        assert out["skills"] == []
        assert out["years"] is None

    def test_non_dict_input(self):
        assert _extract_linkedin_profile_preview(None) == {}
        assert _extract_linkedin_profile_preview("") == {}
        assert _extract_linkedin_profile_preview([]) == {}

    def test_name_falls_back_to_publicid_when_missing_names(self):
        out = _extract_linkedin_profile_preview({"publicIdentifier": "no-name-user"})
        assert out["name"] == "no-name-user"

    def test_long_summary_truncated(self):
        long_summary = "x" * 500
        out = _extract_linkedin_profile_preview({
            "firstName": "A", "lastName": "B", "summary": long_summary,
        })
        assert len(out["summary"]) <= 250  # max 200 + 省略号
        assert out["summary"].endswith("…")

    def test_location_fallback_to_locationName(self):
        out = _extract_linkedin_profile_preview({
            "firstName": "A", "lastName": "B", "locationName": "Shanghai",
        })
        assert out["location"] == "Shanghai"

    def test_skills_as_plain_strings(self):
        """有些载荷 skills 是字符串数组而非对象数组"""
        out = _extract_linkedin_profile_preview({
            "firstName": "A", "lastName": "B",
            "profileSkills": {"elements": ["Rust", "Go", "C++"]},
        })
        assert out["skills"] == ["Rust", "Go", "C++"]

    def test_positions_via_positionsView(self):
        """旧式载荷用 positionsView 而非 profileExperience"""
        out = _extract_linkedin_profile_preview({
            "firstName": "A", "lastName": "B",
            "positionsView": {
                "elements": [
                    {"title": "Engineer", "companyName": "TestCo"},
                ],
            },
        })
        assert out["current_role"] == "Engineer"
        assert out["current_company"] == "TestCo"

    def test_years_none_when_no_dates(self):
        """职业经历无 startDate 时年资保守返回 None，不瞎猜"""
        out = _extract_linkedin_profile_preview({
            "firstName": "A", "lastName": "B",
            "profileExperience": {
                "elements": [{"title": "X", "companyName": "Y"}],
            },
        })
        assert out["years"] is None


class TestCmdPreviewProfile:
    """cmd_preview_profile 调 cmd_get_profile 后做字段提取"""

    @pytest.mark.asyncio
    async def test_unwraps_profile_and_extracts(self, monkeypatch):
        """模拟 cmd_get_profile 返回 {profile: {...}} 的壳"""
        async def fake_get_profile(session_id, public_id):
            assert session_id == "sid-1"
            assert public_id == "alice-chen-ml"
            return {"profile": _sample_profile_full(), "tokens_captured": ["profileId"]}
        monkeypatch.setattr(li_cmd, "cmd_get_profile", fake_get_profile)

        out = await li_cmd.cmd_preview_profile("sid-1", "alice-chen-ml")
        assert out["name"] == "Alice Chen"
        assert "Google" in out["current_company"]
        # token_captured 不应出现在 preview（紧凑就是紧凑）
        assert "tokens_captured" not in out

    @pytest.mark.asyncio
    async def test_returns_error_when_profile_missing(self, monkeypatch):
        async def fake_get_profile(session_id, public_id):
            return {"tokens_captured": []}  # 没 profile key
        monkeypatch.setattr(li_cmd, "cmd_get_profile", fake_get_profile)

        out = await li_cmd.cmd_preview_profile("sid-1", "who")
        assert out["error"] == "profile_not_found"
        assert out["public_id"] == "who"
