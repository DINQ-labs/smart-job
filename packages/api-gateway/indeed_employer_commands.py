"""
indeed_employer_commands.py: Indeed 雇主端命令层 — 简历搜索与下载。

通过 Extension 传输：send_command_to() → job-seeker-ext Chrome 扩展
"""
from __future__ import annotations

from typing import Any

from candidate_preview import normalize_candidate_preview
from ext_client import send_command_to
from session_store import session_store


async def _send(
    session_id: str,
    method: str,
    path: str,
    body: Any = None,
    tool_name: str = "",
    agent_id: str = "",
) -> Any:
    if session_store.get(session_id) is not None:
        return await send_command_to(
            session_id, method, path, body,
            tool_name=tool_name, agent_id=agent_id,
        )
    raise RuntimeError(
        f"Indeed employer session {session_id[:16]} 不存在。请确认扩展已连接并登录 employers.indeed.com。"
    )


def _unwrap(result: Any) -> dict:
    """从扩展返回格式中提取 result 字段。"""
    if isinstance(result, dict):
        return result.get("result", result)
    return result


async def cmd_check_login(session_id: str, agent_id: str = "") -> dict:
    result = await _send(
        session_id, "GET", "indeed_employer/check_login",
        tool_name="indeed_employer_check_login", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_list_jobs(session_id: str, limit: int = 20, agent_id: str = "") -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/list_jobs",
        {"limit": limit},
        tool_name="indeed_employer_list_jobs", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_search_candidates(
    session_id: str,
    employer_job_id: str,
    dispositions: str = "NEW,PENDING,REVIEWED,PHONE_SCREENED,INTERVIEWED,OFFER_MADE",
    sort_by: str = "APPLY_DATE",
    limit: int = 20,
    cursor: str | None = None,
    agent_id: str = "",
) -> dict:
    body = {
        "employer_job_id": employer_job_id,
        "dispositions": dispositions,
        "sort_by": sort_by,
        "limit": limit,
    }
    if cursor:
        body["cursor"] = cursor
    result = await _send(
        session_id, "POST", "indeed_employer/search_candidates", body,
        tool_name="indeed_employer_search_candidates", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_candidate(
    session_id: str,
    submission_uuid: str = "",
    legacy_id: str = "",
    agent_id: str = "",
) -> dict:
    body: dict[str, Any] = {}
    if submission_uuid:
        body["submission_uuid"] = submission_uuid
    if legacy_id:
        body["legacy_id"] = legacy_id
    result = await _send(
        session_id, "POST", "indeed_employer/get_candidate", body,
        tool_name="indeed_employer_get_candidate", agent_id=agent_id,
    )
    data = _unwrap(result)
    # E2-3 跨平台统一 preview shape
    if isinstance(data, dict):
        data["_preview"] = normalize_candidate_preview(data, "indeed")
    return data


async def cmd_download_resume(
    session_id: str,
    legacy_id: str,
    candidate_name: str = "",
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/download_resume",
        {"legacy_id": legacy_id, "candidate_name": candidate_name},
        tool_name="indeed_employer_download_resume", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_update_candidate_status(
    session_id: str,
    legacy_id: str,
    job_id: str,
    milestone_id: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/update_candidate_status",
        {"legacy_id": legacy_id, "job_id": job_id, "milestone_id": milestone_id},
        tool_name="indeed_employer_update_candidate_status", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_conversations(
    session_id: str,
    candidate_key: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_conversations",
        {"candidate_key": candidate_key},
        tool_name="indeed_employer_get_conversations", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_screening_summary(
    session_id: str,
    submission_uuid: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_screening_summary",
        {"submission_uuid": submission_uuid},
        tool_name="indeed_employer_get_screening_summary", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_interviews(
    session_id: str,
    submission_uuid: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_interviews",
        {"submission_uuid": submission_uuid},
        tool_name="indeed_employer_get_interviews", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_match_details(
    session_id: str,
    legacy_id: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_match_details",
        {"legacy_id": legacy_id},
        tool_name="indeed_employer_get_match_details", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_set_candidate_feedback(
    session_id: str,
    legacy_id: str,
    sentiment: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/set_candidate_feedback",
        {"legacy_id": legacy_id, "sentiment": sentiment},
        tool_name="indeed_employer_set_candidate_feedback", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_screening_answers(
    session_id: str,
    candidate_id: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_screening_answers",
        {"candidate_id": candidate_id},
        tool_name="indeed_employer_get_screening_answers", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_mark_candidate_viewed(
    session_id: str,
    submission_uuid: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/mark_candidate_viewed",
        {"submission_uuid": submission_uuid},
        tool_name="indeed_employer_mark_candidate_viewed", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_send_message(
    session_id: str,
    candidate_key: str,
    message_body: str,
    conversation_id: str = "",
    agg_job_key: str = "",
    agent_id: str = "",
) -> dict:
    body: dict[str, Any] = {
        "candidate_key": candidate_key,
        "message_body": message_body,
    }
    if conversation_id:
        body["conversation_id"] = conversation_id
    if agg_job_key:
        body["agg_job_key"] = agg_job_key
    result = await _send(
        session_id, "POST", "indeed_employer/send_message", body,
        tool_name="indeed_employer_send_message", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_conversation_messages(
    session_id: str,
    conversation_id: str,
    limit: int = 50,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_conversation_messages",
        {"conversation_id": conversation_id, "limit": limit},
        tool_name="indeed_employer_get_conversation_messages", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_message_templates(
    session_id: str,
    limit: int = 20,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_message_templates",
        {"limit": limit},
        tool_name="indeed_employer_get_message_templates", agent_id=agent_id,
    )
    return _unwrap(result)


# ── Indeed Resume Search ────────────────────────────────────────────────────


async def cmd_search_resumes(
    session_id: str,
    query: str,
    location: str = "",
    employer_job_id: str = "",
    offset: int = 0,
    filters: list | None = None,
    agent_id: str = "",
) -> dict:
    body: dict[str, Any] = {
        "query": query,
        "location": location,
        "offset": offset,
    }
    if employer_job_id:
        body["employer_job_id"] = employer_job_id
    if filters:
        body["filters"] = filters
    result = await _send(
        session_id, "POST", "indeed_employer/search_resumes", body,
        tool_name="indeed_employer_search_resumes", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_talent_engagement(
    session_id: str,
    candidate_id: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_talent_engagement",
        {"candidate_id": candidate_id},
        tool_name="indeed_employer_get_talent_engagement", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_log_candidate_seen(
    session_id: str,
    candidate_ids: list,
    rcp_request_id: str,
    surface: str = "sourcing-search",
    grps: list | None = None,
    agent_id: str = "",
) -> dict:
    """反作弊曝光埋点(spec Phase 1)。建议由 agent_loop 在 search_resumes 返回
    后自动 fire-and-forget 调用。"""
    body: dict[str, Any] = {
        "candidate_ids": candidate_ids,
        "rcp_request_id": rcp_request_id,
        "surface": surface,
        "grps": grps or [],
    }
    result = await _send(
        session_id, "POST", "indeed_employer/log_candidate_seen", body,
        tool_name="indeed_employer_log_candidate_seen", agent_id=agent_id,
    )
    return _unwrap(result)


# ── Spec 5.3 / 5.4 / 5.5 候选人评审 + 申请人筛选 + 消息(7 命令)──


async def cmd_get_match_profile(
    session_id: str,
    job_id: str,
    agent_id: str = "",
) -> dict:
    """spec 5.3 「分析候选人」匹配可解释性。"""
    result = await _send(
        session_id, "POST", "indeed_employer/get_match_profile",
        {"job_id": job_id},
        tool_name="indeed_employer_get_match_profile", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_candidate_submission(
    session_id: str,
    submission_id: str,
    first: int = 1,
    agent_id: str = "",
) -> dict:
    """spec 5.3 候选人 submission 完整详情(含 resume URL)。"""
    result = await _send(
        session_id, "POST", "indeed_employer/get_candidate_submission",
        {"submission_id": submission_id, "first": first},
        tool_name="indeed_employer_get_candidate_submission", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_find_applicants(
    session_id: str,
    employer_job_id: str,
    dispositions: list | None = None,
    sort_by: str = "APPLY_DATE",
    sort_order: str = "DESCENDING",
    created_after_ms: int = 0,
    limit: int = 20,
    agent_id: str = "",
) -> dict:
    """spec 5.4 申请人列表(per-job RCP 匹配)。"""
    body: dict[str, Any] = {
        "employer_job_id": employer_job_id,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "created_after_ms": created_after_ms,
        "limit": limit,
    }
    if dispositions:
        body["dispositions"] = dispositions
    result = await _send(
        session_id, "POST", "indeed_employer/find_applicants", body,
        tool_name="indeed_employer_find_applicants", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_applicant_filters(
    session_id: str,
    employer_job_id: str,
    agent_id: str = "",
) -> dict:
    """spec 5.4 申请人 facet。"""
    result = await _send(
        session_id, "POST", "indeed_employer/get_applicant_filters",
        {"employer_job_id": employer_job_id},
        tool_name="indeed_employer_get_applicant_filters", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_risk_assessment(
    session_id: str,
    contexts: list,
    agent_id: str = "",
) -> dict:
    """spec 5.4 「自动标记可疑申请」真信号。"""
    result = await _send(
        session_id, "POST", "indeed_employer/get_risk_assessment",
        {"contexts": contexts},
        tool_name="indeed_employer_get_risk_assessment", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_list_conversations_v2(
    session_id: str,
    since_ms: int = 0,
    until_ms: int = 0,
    employer_job_id: str = "",
    limit: int = 20,
    agent_id: str = "",
) -> dict:
    """spec 5.5 招聘端会话列表(支持时间窗口过滤)。"""
    body: dict[str, Any] = {
        "since_ms": since_ms,
        "until_ms": until_ms,
        "employer_job_id": employer_job_id,
        "limit": limit,
    }
    result = await _send(
        session_id, "POST", "indeed_employer/list_conversations_v2", body,
        tool_name="indeed_employer_list_conversations_v2", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_conversation_thread(
    session_id: str,
    conversation_id: str,
    agent_id: str = "",
) -> dict:
    """spec 5.5 单 conversation 完整 thread。"""
    result = await _send(
        session_id, "POST", "indeed_employer/get_conversation_thread",
        {"conversation_id": conversation_id},
        tool_name="indeed_employer_get_conversation_thread", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_search_autocomplete(
    session_id: str,
    query: str,
    type: str = "keyword",
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/search_autocomplete",
        {"query": query, "type": type},
        tool_name="indeed_employer_search_autocomplete", agent_id=agent_id,
    )
    return _unwrap(result)


# ── Indeed Job Posting ──────────────────────────────────────────────────────


async def cmd_list_draft_jobs(
    session_id: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "GET", "indeed_employer/list_draft_jobs",
        tool_name="indeed_employer_list_draft_jobs", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_job_form(
    session_id: str,
    draft_job_id: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/get_job_form",
        {"draft_job_id": draft_job_id},
        tool_name="indeed_employer_get_job_form", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_update_job_form(
    session_id: str,
    form_id: str,
    patch: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/update_job_form",
        {"form_id": form_id, "patch": patch},
        tool_name="indeed_employer_update_job_form", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_publish_job(
    session_id: str,
    form_id: str,
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/publish_job",
        {"form_id": form_id},
        tool_name="indeed_employer_publish_job", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_optimize_job_description(
    session_id: str,
    draft_job_id: str,
    title: str,
    language: str = "en",
    country: str = "US",
    agent_id: str = "",
) -> dict:
    result = await _send(
        session_id, "POST", "indeed_employer/optimize_job_description",
        {"draft_job_id": draft_job_id, "title": title, "language": language, "country": country},
        tool_name="indeed_employer_optimize_job_description", agent_id=agent_id,
    )
    return _unwrap(result)
