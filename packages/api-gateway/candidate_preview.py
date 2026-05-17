"""E2-3 跨平台候选人 preview 统一 shape。

Boss / LinkedIn / Indeed Employer 三平台返回的候选人原始字段各不相同：
  - Boss: geekName / positionName / salary / city / expectLocationName /
          geekWorkYear / geekDegree / geekEdu.school / geekWork
  - LinkedIn: firstName / lastName / headline / positions / educations / skills
  - Indeed: name / currentTitle / currentEmployer / location / experience / skills

Agent 在 cross 模式下批量处理候选人时，如果每平台都要学不同字段名，
prompt 会被各种分支污染。本模块提供单一 normalize 函数，把三方原始字段都
映射成同一 shape，cross 模式 agent 可以用同一模板：

  name / current_role / current_company / location / years / education /
  skills / summary / platform / platform_id

调用方：cmd_get_candidate_detail (Boss) / cmd_preview_profile (LinkedIn) /
cmd_get_candidate (Indeed Employer) 在返回值上**附加** `_preview` 字段
（不破坏原有 shape，Agent 可选择用 _preview 或 raw）。
"""
from __future__ import annotations

from typing import Any


def _s(v: Any) -> str:
    """安全字符串化，None 返回空串。"""
    if v is None:
        return ""
    return str(v).strip()


def _parse_boss_year(raw: Any) -> int | None:
    """Boss 的 workYear 常是 "5-10年" 或 "10年以上" 这种文字，抽出最小数字。"""
    if raw is None:
        return None
    s = str(raw)
    import re
    m = re.search(r"\d+", s)
    if m:
        try:
            return int(m.group())
        except ValueError:
            return None
    return None


def _normalize_boss(raw: dict) -> dict:
    """Boss 候选人原始对象 → 统一 shape。"""
    name = _s(raw.get("name") or raw.get("geekName"))
    # current_role 跨平台语义 = "当前在做的工作"。Boss 里 geekWork.content
    # 是当前岗位，positionName 是期望求职岗位——前者更贴近"current"语义，优先。
    current_role = _s(
        (raw.get("geekWork") or {}).get("content")
        or (raw.get("geekWork") or {}).get("name")
        or (raw.get("middleContent") or {}).get("content")
        or raw.get("positionName")
        or ""
    )
    current_company = _s(
        (raw.get("geekWork") or {}).get("companyName")
        or raw.get("companyName")
    )
    location = _s(raw.get("expectLocationName") or raw.get("city"))
    years = _parse_boss_year(raw.get("geekWorkYear") or raw.get("workYear"))
    edu_obj = raw.get("geekEdu") or {}
    education_parts = [
        _s(edu_obj.get("school") or edu_obj.get("name")),
        _s(raw.get("geekDegree") or raw.get("degreeName") or raw.get("degree")),
    ]
    education = " ".join(p for p in education_parts if p).strip()
    skills_raw = raw.get("skills") or raw.get("skillList") or []
    skills: list[str] = []
    if isinstance(skills_raw, list):
        for s in skills_raw[:8]:
            if isinstance(s, dict):
                n = _s(s.get("name") or s.get("skillName"))
                if n:
                    skills.append(n)
            elif isinstance(s, str):
                skills.append(s)

    return {
        "name": name,
        "current_role": current_role,
        "current_company": current_company,
        "location": location,
        "years": years,
        "education": education,
        "skills": skills,
        "summary": _s(raw.get("geekSummary") or raw.get("profile")),
        "platform": "boss",
        "platform_id": _s(raw.get("encryptGeekId") or raw.get("encrypt_geek_id")),
    }


def _normalize_linkedin(raw: dict) -> dict:
    """LinkedIn preview_profile 已经是本 shape，只加 platform 字段。

    兼容两种入参：已经 normalize 过的 preview dict，或者原始 Voyager profile。
    """
    # preview dict（cmd_preview_profile 的输出）已有目标字段
    if "current_role" in raw and "skills" in raw:
        out = dict(raw)
        out.setdefault("platform", "linkedin")
        out.setdefault("platform_id", _s(raw.get("public_id")))
        return out
    # 兜底：fallback 到原始 Voyager profile 抽字段（极少见路径）
    from linkedin_commands import _extract_linkedin_profile_preview
    preview = _extract_linkedin_profile_preview(raw)
    preview["platform"] = "linkedin"
    preview["platform_id"] = _s(preview.get("public_id"))
    return preview


def _parse_indeed_years(raw: Any) -> int | None:
    """Indeed experience 字段格式多样：'5-10 years' / '10+ years' / 纯数字 / 结构化 dict"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, dict):
        for key in ("years", "totalYears", "minimum", "min"):
            v = raw.get(key)
            if isinstance(v, (int, float)):
                return int(v)
    s = str(raw)
    import re
    m = re.search(r"\d+", s)
    if m:
        try:
            return int(m.group())
        except ValueError:
            return None
    return None


def _normalize_indeed(raw: dict) -> dict:
    """Indeed Employer get_candidate / search_candidates 返回对象 → 统一 shape。"""
    name = _s(raw.get("name") or raw.get("candidateName"))
    current_role = _s(raw.get("currentTitle") or raw.get("jobTitle"))
    current_company = _s(raw.get("currentEmployer") or raw.get("employer"))
    location = _s(raw.get("location"))
    years = _parse_indeed_years(raw.get("experience") or raw.get("yearsOfExperience"))

    # education 可能是 list of dicts
    edu_raw = raw.get("education") or raw.get("educations") or []
    education = ""
    if isinstance(edu_raw, list) and edu_raw:
        top = edu_raw[0] if isinstance(edu_raw[0], dict) else {}
        school = _s(top.get("school") or top.get("institution"))
        degree = _s(top.get("degree") or top.get("degreeName"))
        field = _s(top.get("fieldOfStudy") or top.get("major"))
        education = " ".join(p for p in (school, degree, field) if p).strip()
    elif isinstance(edu_raw, str):
        education = _s(edu_raw)

    skills_raw = raw.get("skills") or []
    skills: list[str] = []
    if isinstance(skills_raw, list):
        for s in skills_raw[:8]:
            if isinstance(s, dict):
                n = _s(s.get("name"))
                if n:
                    skills.append(n)
            elif isinstance(s, str):
                skills.append(s)

    # matchHighlights 常是 Indeed search 结果里的亮点段，作为 summary 替身
    highlights = raw.get("matchHighlights") or []
    summary = ""
    if isinstance(highlights, list) and highlights:
        parts = [_s(h if isinstance(h, str) else h.get("text") or h.get("snippet"))
                 for h in highlights[:3]]
        summary = "；".join(p for p in parts if p)

    return {
        "name": name,
        "current_role": current_role,
        "current_company": current_company,
        "location": location,
        "years": years,
        "education": education,
        "skills": skills,
        "summary": summary,
        "platform": "indeed",
        "platform_id": _s(raw.get("legacyId") or raw.get("legacy_id")),
    }


_NORMALIZERS = {
    "boss":     _normalize_boss,
    "linkedin": _normalize_linkedin,
    "indeed":   _normalize_indeed,
}


def normalize_candidate_preview(raw: Any, platform: str) -> dict:
    """跨平台候选人数据 → 统一 shape 的单一入口。

    Args:
      raw: 各平台原始候选人 dict（如 Boss geek/info 返回、LinkedIn preview、
           Indeed employer get_candidate 返回）
      platform: 'boss' / 'linkedin' / 'indeed'（大小写不敏感）

    Returns:
      {name, current_role, current_company, location, years, education,
       skills, summary, platform, platform_id}。任何字段缺失返回 "" / [] / None。

      非 dict 输入或未知 platform → 返回 {platform, ...空字段}。
    """
    if not isinstance(raw, dict):
        return {
            "name": "", "current_role": "", "current_company": "",
            "location": "", "years": None, "education": "",
            "skills": [], "summary": "",
            "platform": (platform or "").lower(), "platform_id": "",
        }
    fn = _NORMALIZERS.get((platform or "").lower())
    if not fn:
        return {
            "name": _s(raw.get("name")),
            "current_role": "", "current_company": "", "location": "",
            "years": None, "education": "", "skills": [], "summary": "",
            "platform": (platform or "").lower(), "platform_id": "",
        }
    return fn(raw)
