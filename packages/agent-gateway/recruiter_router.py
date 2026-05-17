"""Recruiter 端点 — 候选人画像 / 招呼模板 / 在招职位。

Phase 1.1 MVP 3.5 新增。jobseeker / recruiter 通用同张 message_templates 表
(role_type 区分),候选人画像 recruiter_preferences 单独建表,在招职位实时拉
平台 MCP 工具。
"""
from __future__ import annotations

import json
import logging

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import recruiter_db

log = logging.getLogger(__name__)

_VALID_PLATFORMS = {"boss", "linkedin", "indeed"}
_VALID_ROLE_TYPES = {"jobseeker", "recruiter"}
_VALID_TEMPLATE_KINDS = {
    # recruiter 侧
    "greeting", "followup", "reject",
    # jobseeker 侧
    "apply_intro",
}
_VALID_DEGREE = {None, "", "大专", "本科", "硕士", "博士"}


def _platform_of(request: Request) -> str:
    p = (request.query_params.get("platform") or "boss").lower().strip()
    return p if p in _VALID_PLATFORMS else "boss"


# ── 候选人画像 ──────────────────────────────────────────────────────────────

async def get_recruiter_preferences(request: Request) -> JSONResponse:
    """GET /recruiter/preferences/{user_id}?platform=X"""
    user_id = request.path_params["user_id"]
    platform = _platform_of(request)
    try:
        data = await recruiter_db.get_preferences(user_id, platform)
        return JSONResponse({"ok": True, "data": data or {}})
    except Exception as e:
        log.exception("get_recruiter_preferences failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


async def save_recruiter_preferences(request: Request) -> JSONResponse:
    """POST /recruiter/preferences/{user_id}  body: {platform, target_role,
    must_skills:[], nice_skills:[], exp_min, exp_max, degree, background_pref, exclude_pref}
    """
    user_id = request.path_params["user_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)

    platform = (body.get("platform") or "boss").lower().strip()
    if platform not in _VALID_PLATFORMS:
        return JSONResponse({"ok": False, "error": "platform 必须为 boss/linkedin/indeed"}, status_code=400)

    must = body.get("must_skills") or []
    nice = body.get("nice_skills") or []
    if not isinstance(must, list) or not isinstance(nice, list):
        return JSONResponse({"ok": False, "error": "must_skills / nice_skills 必须为数组"}, status_code=400)
    # 限制长度避免 DB 滥用
    must = [str(s).strip()[:50] for s in must if s][:30]
    nice = [str(s).strip()[:50] for s in nice if s][:30]

    exp_min = body.get("exp_min")
    exp_max = body.get("exp_max")
    if exp_min is not None and (not isinstance(exp_min, int) or exp_min < 0 or exp_min > 50):
        return JSONResponse({"ok": False, "error": "exp_min 必须为 0-50"}, status_code=400)
    if exp_max is not None and (not isinstance(exp_max, int) or exp_max < 0 or exp_max > 50):
        return JSONResponse({"ok": False, "error": "exp_max 必须为 0-50"}, status_code=400)

    degree = body.get("degree") or None
    if degree not in _VALID_DEGREE:
        return JSONResponse({"ok": False, "error": "degree 必须为 大专/本科/硕士/博士 或空"}, status_code=400)

    try:
        await recruiter_db.save_preferences(
            user_id=user_id,
            platform=platform,
            target_role=(body.get("target_role") or "").strip()[:200],
            must_skills=must,
            nice_skills=nice,
            exp_min=exp_min,
            exp_max=exp_max,
            degree=degree,
            background_pref=(body.get("background_pref") or "").strip()[:500],
            exclude_pref=(body.get("exclude_pref") or "").strip()[:500],
        )
    except Exception as e:
        log.exception("save_recruiter_preferences failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True})


# ── 招呼模板 (jobseeker + recruiter 共用) ────────────────────────────────

async def get_templates(request: Request) -> JSONResponse:
    """GET /recruiter/templates/{user_id}?platform=X&role_type=Y
    返当前 (user_id, role_type, platform) 全部 kind 一次性,前端按 kind 取。
    """
    user_id = request.path_params["user_id"]
    platform = _platform_of(request)
    role_type = (request.query_params.get("role_type") or "recruiter").lower().strip()
    if role_type not in _VALID_ROLE_TYPES:
        return JSONResponse({"ok": False, "error": "role_type 必须为 jobseeker/recruiter"}, status_code=400)
    try:
        rows = await recruiter_db.get_templates(user_id, role_type, platform)
        # 转成 {kind: content} 字典方便前端
        templates = {row["kind"]: row["content"] for row in rows}
        return JSONResponse({"ok": True, "templates": templates})
    except Exception as e:
        log.exception("get_templates failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


async def save_template(request: Request) -> JSONResponse:
    """POST /recruiter/templates/{user_id}
    body: {platform, role_type, kind, content}
    """
    user_id = request.path_params["user_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    platform = (body.get("platform") or "boss").lower().strip()
    role_type = (body.get("role_type") or "").lower().strip()
    kind = (body.get("kind") or "").lower().strip()
    content = (body.get("content") or "").strip()

    if platform not in _VALID_PLATFORMS:
        return JSONResponse({"ok": False, "error": "platform 必须为 boss/linkedin/indeed"}, status_code=400)
    if role_type not in _VALID_ROLE_TYPES:
        return JSONResponse({"ok": False, "error": "role_type 必须为 jobseeker/recruiter"}, status_code=400)
    if kind not in _VALID_TEMPLATE_KINDS:
        return JSONResponse({"ok": False, "error": f"kind 必须为 {_VALID_TEMPLATE_KINDS}"}, status_code=400)
    if len(content) > 2000:
        return JSONResponse({"ok": False, "error": "模板长度不能超过 2000 字"}, status_code=400)

    try:
        await recruiter_db.save_template(user_id, role_type, platform, kind, content)
    except Exception as e:
        log.exception("save_template failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True})


# ── 在招职位 (实时调 MCP 工具) ───────────────────────────────────────────

async def get_my_jobs(request: Request) -> JSONResponse:
    """GET /recruiter/my-jobs?user_id=X&platform=Y

    内部按 platform 分发到对应的 MCP 工具:
      Boss     → boss_my_job_list
      LinkedIn → linkedin_recruiter_list_projects (没 Recruiter Lite seat 时返空 + 提示)
      Indeed   → indeed_employer_list_jobs
    返 {ok, jobs:[{job_id, title, city, salary, status, applicant_count, url, ...}]}
    标准化 shape 供前端 sidepanel 直接消费,不暴露平台原生字段差异。
    """
    user_id = (request.query_params.get("user_id") or "").strip()
    platform = _platform_of(request)
    if not user_id:
        return JSONResponse({"ok": False, "error": "user_id 必填"}, status_code=400)

    try:
        jobs = await _fetch_my_jobs(user_id, platform)
        return JSONResponse({"ok": True, "platform": platform, "jobs": jobs})
    except Exception as e:
        log.exception("get_my_jobs failed: platform=%s user=%s", platform, user_id)
        return JSONResponse({"ok": False, "platform": platform, "error": str(e)}, status_code=503)


async def _fetch_my_jobs(user_id: str, platform: str) -> list[dict]:
    """调底层 MCP 工具(boss/linkedin/indeed),把异质响应标准化为统一 shape。"""
    # 调 job-api-gateway 的 MCP HTTP 接口
    base = getattr(config, "JOB_API_GATEWAY_URL", None) or "http://127.0.0.1:8767"
    if platform == "boss":
        return await _fetch_boss_jobs(user_id, base)
    if platform == "linkedin":
        return await _fetch_linkedin_jobs(user_id, base)
    if platform == "indeed":
        return await _fetch_indeed_jobs(user_id, base)
    return []


async def _post_mcp_tool(base: str, tool_name: str, args: dict) -> dict:
    """通过 MCP HTTP 端点调一次 tool。"""
    url = f"{base.rstrip('/')}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, json=payload, headers={"Accept": "application/json, text/event-stream"})
        r.raise_for_status()
        try:
            data = r.json()
        except Exception:
            data = json.loads(r.text)
        # FastMCP 返 {result: {content: [{type:'text', text: '...'}]}}
        result = data.get("result") or {}
        contents = result.get("content") or []
        if contents and contents[0].get("type") == "text":
            try:
                return json.loads(contents[0].get("text") or "{}")
            except Exception:
                return {}
        return result


async def _fetch_boss_jobs(user_id: str, base: str) -> list[dict]:
    """Boss 端拉自 boss_my_job_list — recruiter 端登录后能看到自己发布的岗位"""
    try:
        data = await _post_mcp_tool(base, "boss_my_job_list", {"app_user_id": user_id})
    except Exception as e:
        log.warning("boss_my_job_list failed: %s", e)
        return []
    if not data.get("ok"):
        return []
    raw = data.get("jobList") or data.get("data", {}).get("jobList") or []
    out = []
    for j in raw:
        if not isinstance(j, dict):
            continue
        out.append({
            "job_id": j.get("encryptJobId") or j.get("jobId") or "",
            "title": j.get("jobName") or j.get("positionName") or "",
            "city": j.get("cityName") or j.get("city") or "",
            "salary": j.get("salaryDesc") or j.get("salary") or "",
            "status": _boss_status(j.get("jobStatusDesc") or j.get("status")),
            "applicant_count": j.get("communicateCnt") or j.get("recruitCnt") or None,
            "url": j.get("jobUrl") or "",
        })
    return out


def _boss_status(s: object) -> str:
    s = str(s or "").lower()
    if any(k in s for k in ["active", "招聘中", "在招", "0"]):
        return "active"
    if "暂停" in s or "pause" in s:
        return "paused"
    if "关闭" in s or "下线" in s or "close" in s:
        return "closed"
    return "active"


async def _fetch_linkedin_jobs(user_id: str, base: str) -> list[dict]:
    """LinkedIn 招聘端 — 没 Recruiter Lite seat 时返空 + 提示用户手动添加。"""
    try:
        data = await _post_mcp_tool(base, "linkedin_recruiter_list_projects", {"app_user_id": user_id})
    except Exception as e:
        log.warning("linkedin_recruiter_list_projects failed: %s", e)
        return []
    if not data.get("ok"):
        return []
    raw = data.get("projects") or data.get("data", {}).get("projects") or []
    out = []
    for j in raw:
        if not isinstance(j, dict):
            continue
        out.append({
            "job_id": j.get("projectUrn") or j.get("project_id") or "",
            "title": j.get("title") or j.get("name") or "",
            "city": j.get("location") or "",
            "salary": "",
            "status": "active",
            "applicant_count": j.get("candidate_count"),
            "url": j.get("url") or "",
        })
    return out


async def _fetch_indeed_jobs(user_id: str, base: str) -> list[dict]:
    """Indeed Employer 端拉自 indeed_employer_list_jobs"""
    try:
        data = await _post_mcp_tool(base, "indeed_employer_list_jobs", {"app_user_id": user_id, "limit": 50})
    except Exception as e:
        log.warning("indeed_employer_list_jobs failed: %s", e)
        return []
    if not data.get("ok"):
        return []
    raw = data.get("jobs") or data.get("data", {}).get("jobs") or []
    out = []
    for j in raw:
        if not isinstance(j, dict):
            continue
        out.append({
            "job_id": j.get("employerJobId") or j.get("jobKey") or j.get("jobId") or "",
            "title": j.get("title") or j.get("jobTitle") or "",
            "city": j.get("location") or j.get("city") or "",
            "salary": j.get("salary") or "",
            "status": _indeed_status(j.get("status")),
            "applicant_count": j.get("applicant_count") or j.get("applicationCount"),
            "url": j.get("url") or "",
        })
    return out


def _indeed_status(s: object) -> str:
    s = str(s or "").lower()
    if "active" in s or "open" in s or "publish" in s:
        return "active"
    if "paus" in s:
        return "paused"
    if "clos" in s or "expir" in s:
        return "closed"
    return "active"
