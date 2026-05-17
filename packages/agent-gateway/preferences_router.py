"""用户职业偏好 REST 端点。"""
from __future__ import annotations

import json
import re

from anthropic import AsyncAnthropic
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import preferences_db
import resume_db

# Boss-style (CNY monthly) — 月薪
_SALARY_OPTIONS_BOSS = ["3k以下", "3-5k", "5-10k", "10-20k", "20-30k", "30-50k", "50k+"]
# LinkedIn / Indeed (USD yearly) — 年薪
_SALARY_OPTIONS_GLOBAL = [
    "<$60k/yr", "$60k-100k/yr", "$100k-150k/yr", "$150k-200k/yr", "$200k+/yr", "Unspecified",
]

_VALID_PLATFORMS = {"boss", "linkedin", "indeed"}


def _platform_of(request: Request) -> str:
    """从 query param 取 platform,默认 'boss'。"""
    p = (request.query_params.get("platform") or "boss").lower().strip()
    return p if p in _VALID_PLATFORMS else "boss"


def _salary_options_for(platform: str) -> list[str]:
    return _SALARY_OPTIONS_BOSS if platform == "boss" else _SALARY_OPTIONS_GLOBAL


async def get_preferences(request: Request) -> JSONResponse:
    user_id = request.path_params["user_id"]
    platform = _platform_of(request)
    try:
        data = await preferences_db.get_user_preferences(user_id, platform=platform)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True, "data": data})


async def save_preferences(request: Request) -> JSONResponse:
    user_id = request.path_params["user_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    # body 里的 platform 优先于 query param(前端一般放 body)
    platform = (body.get("platform") or request.query_params.get("platform") or "boss").lower().strip()
    if platform not in _VALID_PLATFORMS:
        platform = "boss"
    try:
        await preferences_db.save_user_preferences(
            user_id,
            job_role=body.get("job_role", ""),
            city=body.get("city", ""),
            salary_range=body.get("salary_range", ""),
            notes=body.get("notes", ""),
            platform=platform,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True})


async def suggest_preferences(request: Request) -> JSONResponse:
    """GET /user-preferences/{user_id}/suggest?platform=boss|linkedin|indeed
    根据简历用 LLM 推荐求职偏好,按 platform 调整示例和薪资档位。
    """
    user_id = request.path_params["user_id"]
    platform = _platform_of(request)

    try:
        resume = await resume_db.get_resume_by_user(user_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    if not resume:
        return JSONResponse({"ok": False, "error": "未找到简历"}, status_code=404)
    if resume.get("parse_status") != "done":
        return JSONResponse({"ok": False, "error": "简历尚未解析完成，请稍后再试"}, status_code=409)

    def _jf(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v or []

    target_positions = _jf(resume.get("target_positions"))
    target_cities    = _jf(resume.get("target_cities"))
    work_experience  = _jf(resume.get("work_experience"))
    education        = _jf(resume.get("education"))
    skills           = _jf(resume.get("skills"))

    def _list_str(v) -> str:
        if isinstance(v, list):
            return "、".join(str(x) for x in v[:5]) if v else "未填写"
        return str(v) if v else "未填写"

    def _exp_str(exps) -> str:
        if not isinstance(exps, list) or not exps:
            return "无"
        lines = []
        for e in exps[:3]:
            if isinstance(e, dict):
                co = e.get("company") or e.get("公司") or ""
                ti = e.get("title") or e.get("职位") or ""
                lines.append(f"{co} / {ti}".strip(" /"))
            else:
                lines.append(str(e))
        return "；".join(lines)

    resume_summary = (
        f"姓名：{resume.get('name') or '未知'}\n"
        f"工作年限：{resume.get('work_years') or '未知'}\n"
        f"学历：{resume.get('degree') or '未知'}\n"
        f"目标职位：{_list_str(target_positions)}\n"
        f"目标城市：{_list_str(target_cities)}\n"
        f"期望薪资：{resume.get('target_salary_raw') or ''}\n"
        f"技能：{_list_str(skills)}\n"
        f"工作经历：{_exp_str(work_experience)}\n"
        f"教育背景：{_exp_str(education)}\n"
        f"自我评价：{(resume.get('self_evaluation') or '')[:200]}"
    ).strip()

    salary_options = _salary_options_for(platform)

    if platform == "boss":
        role_example = "AI产品经理"
        city_example = "北京"
        city_hint = "中国城市名，如：北京 / 上海 / 深圳"
    else:
        role_example = "Senior ML Engineer"
        city_example = "San Francisco, CA"
        city_hint = (
            "英文城市/地区名，如：San Francisco, CA / New York, NY / Singapore / Remote"
            if platform == "indeed"
            else "英文城市/地区名，如：San Francisco, CA / New York, NY / London, UK / Remote"
        )

    prompt = (
        f"根据以下候选人简历，推荐最合适的 {platform.upper()} 平台求职偏好设置。\n\n"
        f"简历信息：\n{resume_summary}\n\n"
        "请仅返回如下 JSON，不要有任何额外文字：\n"
        "{\n"
        f'  "job_role": "目标职位名称（简洁，如：{role_example}）",\n'
        f'  "city": "{city_hint}",\n'
        f'  "salary_range": "薪资档位，必须是以下之一：{"/".join(salary_options)}",\n'
        '  "notes": "其他偏好（行业、公司规模、远程/到岗等，50字以内）"\n'
        "}"
    )

    try:
        client_kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
        if config.ANTHROPIC_BASE_URL:
            client_kwargs["base_url"] = config.ANTHROPIC_BASE_URL
        client = AsyncAnthropic(**client_kwargs)
        message = await client.messages.create(
            model=config.MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"LLM 未返回有效 JSON：{raw[:200]}")
        suggestion = json.loads(match.group())
        # 校正 salary_range 为合法选项
        if suggestion.get("salary_range") not in salary_options:
            suggestion["salary_range"] = ""
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"LLM 推荐失败: {e}"}, status_code=500)

    return JSONResponse({"ok": True, "suggestion": suggestion})
