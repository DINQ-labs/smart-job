"""
LinkedIn 命令实现 — 通过 Extension 传输：
  send_command_to() → job-seeker-ext Chrome 扩展

不修改任何 boss_* 命令或现有会话管理逻辑。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import db
from ext_client import send_command_to
from platform_command_base import send_platform_command, unwrap_ext_envelope
from session_store import session_store

log = logging.getLogger(__name__)

_unwrap = unwrap_ext_envelope


async def _send(
    session_id: str,
    method: str,
    path: str,
    body: Any = None,
    tool_name: str = "",
) -> Any:
    """LinkedIn 平台命令发送 —— 委托给 platform_command_base。"""
    return await send_platform_command(
        session_id, method, path, body,
        tool_name=tool_name, platform_label="LinkedIn",
    )


async def cmd_check_login(session_id: str, force_reset: bool = False) -> dict:
    """检查 LinkedIn 登录状态，返回 {logged_in, memberId, name, region_blocked?}。

    登录成功时在 session 上绑定 site_users['linkedin']=memberId；
    明确返回 logged_in=false 时清理该绑定（避免脏读已失效的 session）。

    force_reset=True 时把 force_reset 字段透传给扩展,扩展会在执行检测前清掉
    region-blocked 缓存(用户开 VPN 后用)。
    """
    body = {"force_reset": True} if force_reset else None
    data = await _send(
        session_id, "GET", "linkedin/check_login",
        body=body,
        tool_name="linkedin_check_login",
    )
    try:
        if isinstance(data, dict):
            if data.get("logged_in") is True:
                member_id = str(data.get("memberId") or "").strip()
                if member_id:
                    session_store.set_site_user(session_id, "linkedin", member_id)
            elif data.get("logged_in") is False:
                # 明确登出：清理 site_user 绑定，让 find_by_site 不再匹配
                session_store.set_site_user(session_id, "linkedin", "")
            # 其他情况（None / 缺失 / 格式错误）保留现有绑定不动
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass
    return data


async def _cache_linkedin_job_list(
    data: dict, keywords: str, geo_id: str, page: int,
) -> None:
    """Fire-and-forget: 把 LinkedIn search 结果写入 cached_jobs (+ cached_searches)。"""
    try:
        jobs = (data or {}).get("jobs") or []
        if not isinstance(jobs, list) or not jobs:
            return
        job_ids: list[str] = []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            jid = str(j.get("jobId") or "").strip()
            if not jid:
                continue
            try:
                await db.upsert_job(
                    "linkedin", jid,
                    title=j.get("title") or None,
                    company=j.get("companyName") or None,
                    city=j.get("location") or None,    # flat "City, Country"
                    city_code=(geo_id or None),
                    raw_list=j,
                )
                job_ids.append(jid)
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
        if keywords and job_ids:
            try:
                await db.upsert_search(
                    "linkedin", keywords, geo_id or None, page,
                    job_ids=job_ids,
                    total_count=(data or {}).get("total"),
                )
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
    except Exception as exc:
        log.debug("cache linkedin job list failed: %s", exc)


def _extract_linkedin_detail_fields(raw: dict) -> tuple[str | None, str | None]:
    """从 voyager /jobs/jobPostings/{id} 响应里挖 description / formatted location。

    LinkedIn voyager 返回 included[] 有 com.linkedin.voyager.jobs.JobPosting 节点，
    也可能直接在 data 下。容错 4 条路径，取第一个非空。
    """
    if not isinstance(raw, dict):
        return None, None
    description = None
    location = None
    # 路径 A: data 下直接是 JobPosting
    d = raw.get("data") or {}
    if isinstance(d, dict):
        desc_obj = d.get("description") or {}
        if isinstance(desc_obj, dict) and desc_obj.get("text"):
            description = desc_obj.get("text")
        location = location or d.get("formattedLocation") or None
    # 路径 B: included 列表里挑 JobPosting
    included = raw.get("included") or []
    if isinstance(included, list):
        for it in included:
            if not isinstance(it, dict):
                continue
            t = it.get("$type") or it.get("@type") or ""
            if "JobPosting" not in t and "jobs.JobPosting" not in t:
                continue
            if description is None:
                desc_obj = it.get("description") or {}
                if isinstance(desc_obj, dict):
                    description = desc_obj.get("text")
            if location is None:
                location = it.get("formattedLocation") or None
            if description and location:
                break
    return description, location


async def _cache_linkedin_job_detail(data: dict, job_id: str) -> None:
    """Fire-and-forget: 把 LinkedIn detail 写入 cached_job_details。"""
    try:
        raw = (data or {}).get("raw") or {}
        description, address = _extract_linkedin_detail_fields(raw)
        await db.upsert_job_detail(
            "linkedin", str(job_id),
            description=description,
            address=address,
            raw_detail=data,
        )
    except Exception as exc:
        log.debug("cache linkedin job detail failed: %s", exc)


async def cmd_search_jobs(
    session_id: str,
    keywords: str,
    geo_location_id: str = "",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    """搜索 LinkedIn 职位列表。

    geo_location_id 是 LinkedIn 的 geoUrn 数字 id（如 '103644278' 美国、
    '102890883' 中国），为空则不加地域过滤。
    成功结果异步写入 cached_jobs 以支持 cache-first 批量分析。
    """
    body = {
        "keywords": keywords,
        "geoLocationId": geo_location_id,
        "page": page,
        "pageSize": page_size,
    }
    data = await _send(
        session_id, "POST", "linkedin/search_jobs", body,
        tool_name="linkedin_search_jobs",
    )
    asyncio.ensure_future(_cache_linkedin_job_list(
        data if isinstance(data, dict) else {},
        keywords, geo_location_id, page,
    ))
    return data


async def cmd_get_job_detail(session_id: str, job_id: str) -> dict:
    """查看 LinkedIn 职位详情（voyager/api/jobs/jobPostings/{jobId}）。

    纯只读；不会触发 Easy Apply 表单 / 不投递。
    成功结果异步写入 cached_job_details，后续 linkedin_get_cached_job 可读取。
    返回 {jobId, raw: {...LinkedIn 原始职位元数据...}}。
    """
    body = {"jobId": job_id}
    data = await _send(
        session_id, "POST", "linkedin/get_job_detail", body,
        tool_name="linkedin_get_job_detail",
    )
    asyncio.ensure_future(_cache_linkedin_job_detail(
        data if isinstance(data, dict) else {}, job_id,
    ))
    return data


async def cmd_apply(session_id: str, job_id: str, profile_data: dict) -> dict:
    """LinkedIn Apply 完整投递（多步表单自动填写）。"""
    body = {"jobId": job_id, "profileData": profile_data}
    return await _send(
        session_id, "POST", "linkedin/apply", body,
        tool_name="linkedin_apply_job",
    )


# ── E2-2: 投递去重 ─────────────────────────────────────────────────────────
# Voyager /jobs/jobPostings/{jobId} 响应里通常带 applied 状态，字段位置因
# LinkedIn 内部模型版本不同有多种。防御性扫描一组常见路径，命中返回 bool,
# 都缺失返回 None（调用方决定是否保守当作未投递）。

def _extract_applied_flag(raw: Any) -> bool | None:
    """从 LinkedIn 职位详情响应提取 applied 状态。返回 True/False/None。"""
    if not isinstance(raw, dict):
        return None
    # 常见字段路径，按命中概率排序
    candidates = [
        raw.get("applied"),
        (raw.get("applyingInfo") or {}).get("applied"),
        (raw.get("jobApplyingInfo") or {}).get("applied"),
        (raw.get("applicantsInfo") or {}).get("isApplied"),
        (raw.get("applicantStatus") or {}).get("applied"),
        raw.get("isApplied"),
    ]
    for v in candidates:
        if isinstance(v, bool):
            return v
    # applyingInfo.appliedAt 存在（时间戳）→ 视为已投
    applying = raw.get("applyingInfo") or {}
    if applying.get("appliedAt") or applying.get("appliedTimestamp"):
        return True
    return None


async def cmd_check_applied(session_id: str, job_id: str) -> dict:
    """E2-2: 查询某 LinkedIn 职位是否已投递（批量投递去重）。

    复用 cmd_get_job_detail 的网络调用（后者会把结果写入 cached_job_details，
    下一次 linkedin_get_cached_job 免费读）。返回：
      {applied: bool | None, job_id, note?}
    applied=None 表示响应里无法确定投递状态（字段缺失），Agent 应谨慎处理。
    """
    data = await cmd_get_job_detail(session_id, job_id)
    raw = data.get("raw") if isinstance(data, dict) else None
    applied = _extract_applied_flag(raw)
    out: dict[str, Any] = {"applied": applied, "job_id": job_id}
    if applied is None:
        out["note"] = "无法从响应确定投递状态；建议默认为未投递，但投递前再次确认"
    return out


async def cmd_get_apply_form(session_id: str, job_id: str) -> dict:
    """获取 LinkedIn Easy Apply 表单结构（不填写）。"""
    return await _send(
        session_id, "GET", "linkedin/get_apply_form",
        {"jobId": job_id},
        tool_name="linkedin_get_apply_form",
    )


async def cmd_fill_fields(session_id: str, actions: list) -> dict:
    """按指令填写 LinkedIn 表单字段（处理 unresolved_fields）。"""
    return await _send(
        session_id, "POST", "linkedin/fill_fields",
        {"actions": actions},
        tool_name="linkedin_fill_fields",
    )


async def cmd_search_candidates(
    session_id: str,
    keywords: str,
    start: int = 0,
    count: int = 10,
) -> dict:
    """搜索 LinkedIn 用户 Profile 列表（对标"搜人"能力）。

    扩展侧路径 linkedin/search_people；gateway 保留 cmd_search_candidates 名字以
    保持 MCP tool 外部引用稳定（tool 名仍为 linkedin_search_candidates）。
    """
    body = {"keywords": keywords, "start": start, "count": count}
    return await _send(
        session_id, "POST", "linkedin/search_people", body,
        tool_name="linkedin_search_candidates",
    )


async def cmd_get_profile(session_id: str, public_id: str) -> dict:
    """获取 LinkedIn 用户 Profile（通过公开 ID）。"""
    return await _send(
        session_id, "GET", "linkedin/get_profile",
        {"publicId": public_id},
        tool_name="linkedin_get_profile",
    )


# ── E2-1: 紧凑版 profile preview ─────────────────────────────────────────
# Voyager /identity/dash/profiles 返回的 profile 对象通常 5-10KB，Recruiter
# 批量看 20 个候选人会把 context 拉爆。本函数只提取 Recruiter 初筛需要的字段,
# 返回 ~200 bytes / 人，让 LLM 能在一次 turn 里对 10-20 个候选人同时做对比。

_MAX_SKILLS = 8
_MAX_POSITIONS = 3
_MAX_SUMMARY_CHARS = 200


def _extract_linkedin_profile_preview(profile: dict) -> dict:
    """从 Voyager `/identity/dash/profiles` 的 elements[0] 提取紧凑字段。

    字段名遵循 E2-3 跨平台统一 shape：
      {name, current_role, current_company, location, years, education, skills, summary, profile_url, ...}
    任何字段缺失返回空值（"" / [] / None），由调用方决定展示。
    """
    if not isinstance(profile, dict):
        return {}

    first = str(profile.get("firstName") or "").strip()
    last = str(profile.get("lastName") or "").strip()
    name = f"{first} {last}".strip() or str(profile.get("publicIdentifier") or "").strip()

    # 地理位置：geoLocation.geo.defaultLocalizedName 优先；locationName 兜底
    geo = profile.get("geoLocation") or {}
    loc_raw = (
        (geo.get("geo") or {}).get("defaultLocalizedName")
        or profile.get("geoLocationName")
        or profile.get("locationName")
        or ""
    )

    # 职业经历：profileExperience / *profileExperience / positionsView.elements
    positions_raw = (
        (profile.get("profileExperience") or {}).get("elements")
        or (profile.get("positionsView") or {}).get("elements")
        or profile.get("*profileExperience")  # embedded model pattern
        or []
    )
    if not isinstance(positions_raw, list):
        positions_raw = []

    positions = []
    for p in positions_raw[:_MAX_POSITIONS]:
        if not isinstance(p, dict):
            continue
        positions.append({
            "title": p.get("title") or p.get("jobTitle") or "",
            "company": (
                p.get("companyName")
                or (p.get("company") or {}).get("name")
                or ""
            ),
            "start": (p.get("timePeriod") or {}).get("startDate") or p.get("startDate") or {},
            "end": (p.get("timePeriod") or {}).get("endDate") or p.get("endDate") or {},
        })

    # 当前岗位取第一条（LinkedIn 惯例按时间倒序）
    current_role = positions[0]["title"] if positions else ""
    current_company = positions[0]["company"] if positions else ""

    # 年资估算：首岗 startDate.year 到现在
    years_of_experience: int | None = None
    if positions:
        try:
            first_start = positions[-1].get("start") or {}  # 职业生涯最早一段
            start_year = int(first_start.get("year")) if first_start.get("year") else None
            if start_year:
                from datetime import datetime, timezone
                years_of_experience = max(0, datetime.now(timezone.utc).year - start_year)
        except (TypeError, ValueError):
            pass

    # 教育：取首条
    edu_raw = (
        (profile.get("profileEducation") or {}).get("elements")
        or (profile.get("educationView") or {}).get("elements")
        or []
    )
    education = ""
    if isinstance(edu_raw, list) and edu_raw:
        top = edu_raw[0] if isinstance(edu_raw[0], dict) else {}
        school = top.get("schoolName") or (top.get("school") or {}).get("name") or ""
        degree = top.get("degreeName") or top.get("degree") or ""
        field = top.get("fieldOfStudy") or ""
        education = " ".join(x for x in (school, degree, field) if x).strip()

    # 技能
    skills_raw = (
        (profile.get("profileSkills") or {}).get("elements")
        or (profile.get("skills") or {}).get("elements")
        or []
    )
    skills: list[str] = []
    if isinstance(skills_raw, list):
        for s in skills_raw[:_MAX_SKILLS]:
            if isinstance(s, dict):
                n = s.get("name") or (s.get("skill") or {}).get("name") or ""
                if n:
                    skills.append(n)
            elif isinstance(s, str):
                skills.append(s)

    summary = str(profile.get("summary") or "").strip().replace("\n", " ")
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS].rstrip() + "…"

    public_id = profile.get("publicIdentifier") or ""
    return {
        "name": name,
        "headline": str(profile.get("headline") or "").strip(),
        "current_role": current_role,
        "current_company": current_company,
        "location": loc_raw,
        "industry": profile.get("industryName") or "",
        "years": years_of_experience,
        "education": education,
        "skills": skills,
        "summary": summary,
        "public_id": public_id,
        "profile_url": f"https://www.linkedin.com/in/{public_id}" if public_id else "",
        "positions": positions,  # 前 3 段完整经历（title + company + 日期）
        # E2-3 跨平台统一 shape 标识
        "platform": "linkedin",
        "platform_id": public_id,
    }


async def cmd_preview_profile(session_id: str, public_id: str) -> dict:
    """E2-1: 紧凑版 LinkedIn profile。复用 cmd_get_profile 的网络调用 + 扩展端
    token 捕获，仅在网关侧做字段裁剪，避免 Recruiter 批量看候选人时爆 context。

    返回字段遵循跨平台统一 shape（E2-3）：
      {name, current_role, current_company, location, years, education,
       skills, summary, public_id, profile_url, positions[]}
    """
    full = await cmd_get_profile(session_id, public_id)
    # cmd_get_profile 返回 {profile: {...}, tokens_captured: [...]}
    profile = (full or {}).get("profile") if isinstance(full, dict) else None
    if not profile:
        return {"error": "profile_not_found", "public_id": public_id}
    return _extract_linkedin_profile_preview(profile)


async def cmd_connect(
    session_id: str,
    member_urn: str,
    message: str = "",
) -> dict:
    """LinkedIn 发送好友邀请（connect request）。

    trackingId 由扩展从 tokenStore 自动查找（需先调 cmd_search_candidates 捕获）。
    返回: {raw}。
    """
    body = {"member_urn": member_urn, "message": message}
    return await _send(
        session_id, "POST", "linkedin/connect", body,
        tool_name="linkedin_connect",
    )


async def cmd_get_connection_degree(
    session_id: str,
    member_urn: str = "",
    public_id: str = "",
) -> dict:
    """LinkedIn 查询与目标用户的连接程度（1st/2nd/3rd/None）。

    member_urn 或 public_id 至少传一个（member_urn 时扩展从 tokenStore 反查 publicId）。
    返回: {raw, degree, connectionType}。
    """
    body = {"member_urn": member_urn, "public_id": public_id}
    return await _send(
        session_id, "GET", "linkedin/get_connection_degree", body,
        tool_name="linkedin_get_connection_degree",
    )


async def cmd_send_message(
    session_id: str, member_id: str, text: str, subject: str = "",
) -> dict:
    """向 LinkedIn 用户发送消息/InMail(发起新会话)。

    gateway 用 member_id/text 是为了对齐其它平台 cmd 命名风格;
    扩展 handler 需要 member_urn/body/subject(LinkedIn 协议字段名),这里转换。
    """
    body = {
        "member_urn": member_id,
        "body": text,
        "subject": subject,
    }
    return await _send(
        session_id, "POST", "linkedin/send_message", body,
        tool_name="linkedin_send_message",
    )


# ── LinkedIn Messaging (Messenger 框架，新版) ───────────────────────────────


async def cmd_list_conversations(
    session_id: str,
    mailbox_urn: str = "",
    sync_token: str = "",
    count: int = 20,
) -> dict:
    """LinkedIn 读消息会话列表（Messenger GraphQL）。

    mailbox_urn: 留空 → 扩展自动用当前登录用户的 primary mailbox（urn:li:fsd_profile:{memberId}）。
                 只有需要切换到 Recruiter/Page mailbox 时才显式传入（通过 cmd_list_mailboxes 获取）。
    sync_token: 增量同步 token（首次传 ''；从上次响应 new_sync_token 取）。
    返回: {raw, conversations:[{conversation_urn, last_activity_ms, unread_count,
                               participants_urns, title}], total, new_sync_token,
           deleted_urns}。
    """
    body = {"mailbox_urn": mailbox_urn, "sync_token": sync_token, "count": count}
    return await _send(
        session_id, "POST", "linkedin/list_conversations", body,
        tool_name="linkedin_list_conversations",
    )


async def cmd_get_conversation_messages(
    session_id: str,
    conversation_urn: str,
    count: int = 20,
) -> dict:
    """LinkedIn 读取某会话的历史消息。

    返回: {raw, messages:[{message_urn, sender_urn, text, delivered_at}], total}。
    """
    body = {"conversation_urn": conversation_urn, "count": count}
    return await _send(
        session_id, "POST", "linkedin/get_conversation_messages", body,
        tool_name="linkedin_get_conversation_messages",
    )


async def cmd_list_mailboxes(session_id: str) -> dict:
    """LinkedIn 列出所有 mailbox（普通 / Recruiter / Page admin）。

    返回: {raw, mailboxes:[{kind: 'primary'|'recruiter'|'page',
                          headline, unread_count_total}], total}。
    """
    return await _send(
        session_id, "GET", "linkedin/list_mailboxes", None,
        tool_name="linkedin_list_mailboxes",
    )


# ── 2026-04-29 抓包对齐 Boss 求职 Step 2B(查看最近消息子流程) ──────────────


async def cmd_list_inbox_counts(
    session_id: str,
    mailbox_urn: str = "",
) -> dict:
    """LinkedIn 主收件箱分类未读计数(对齐 Boss geek_message_center_summary)。

    返回 counts: {category: unreadConversationCount},category 枚举:
      - PRIMARY_INBOX   主收件箱
      - SECONDARY_INBOX Other 标签(广告 / 不重要)
      - JOB             求职相关消息(招聘官 / 申请回执)
    """
    body = {"mailbox_urn": mailbox_urn}
    return await _send(
        session_id, "POST", "linkedin/list_inbox_counts", body,
        tool_name="linkedin_list_inbox_counts",
    )


async def cmd_list_conversations_filtered(
    session_id: str,
    mailbox_urn: str = "",
    categories: str = "PRIMARY_INBOX",
    count: int = 20,
    read: str = "",
    first_degree_connections: str = "",
    next_cursor: str = "",
) -> dict:
    """LinkedIn 按筛选条件列会话(对齐 Boss geek_filter_by_label)。

    categories:  逗号分隔, e.g. 'PRIMARY_INBOX' / 'JOB' / 'PRIMARY_INBOX,JOB'
    read:        '' / 'true' / 'false' — 已读 / 未读 / 不过滤
    first_degree_connections: '' / 'true' / 'false' — 仅 1 度好友
    next_cursor: 翻页 cursor(从上次返回 next_cursor 取)

    返回 conversations[] 含 conversation_urn / title / unread_count /
    last_activity_ms / participants_urns / categories[] / read 等。
    """
    body = {
        "mailbox_urn": mailbox_urn,
        "categories": categories,
        "count": count,
        "read": read,
        "first_degree_connections": first_degree_connections,
        "next_cursor": next_cursor,
    }
    return await _send(
        session_id, "POST", "linkedin/list_conversations_filtered", body,
        tool_name="linkedin_list_conversations_filtered",
    )


async def cmd_precheck_compose(
    session_id: str,
    recipient_urn: str,
    conversation_urn: str = "",
    type: str = "REPLY",
) -> dict:
    """LinkedIn 发消息前的预检 — 能否向该 recipient 发?是否被拉黑?是否需要 InMail?

    LinkedIn 独有,Boss 没等价机制(Boss 在 send 时才报错)。
    建议在打开 LinkedinComposeModal 之前调一次,根据 can_send / blocked /
    trust_intervention 决定 modal 提示。

    type: 'NEW' / 'REPLY' / 'INMAIL'
    """
    body = {
        "recipient_urn": recipient_urn,
        "conversation_urn": conversation_urn,
        "type": type,
    }
    return await _send(
        session_id, "POST", "linkedin/precheck_compose", body,
        tool_name="linkedin_precheck_compose",
    )


async def cmd_mark_messages_seen(
    session_id: str,
    until_ms: int = 0,
) -> dict:
    """LinkedIn 标记所有消息为已读（清除消息红点）。

    until_ms: 时间戳上限（毫秒），0 表示当前时间。
    返回: {raw, success: bool}。
    """
    body = {"until_ms": until_ms}
    return await _send(
        session_id, "POST", "linkedin/mark_messages_seen", body,
        tool_name="linkedin_mark_messages_seen",
    )


async def cmd_reply_to_conversation(
    session_id: str,
    conversation_urn: str,
    text: str,
    mailbox_urn: str = "",
    origin_token: str = "",
) -> dict:
    """LinkedIn 在已有会话里回复（区别于 send_message 发起新 InMail）。

    conversation_urn: 目标会话 URN（从 list_conversations 返回取）。
    mailbox_urn: 留空 → 扩展自动用当前登录用户的 primary mailbox；
                 仅在 Recruiter/Page 场景需要显式指定。
    origin_token: 幂等键，留空则扩展自动生成 UUID。
    返回: {raw, message_urn, conversation_urn}。
    """
    body = {
        "conversation_urn": conversation_urn,
        "mailbox_urn": mailbox_urn,
        "text": text,
        "origin_token": origin_token,
    }
    return await _send(
        session_id, "POST", "linkedin/reply_to_conversation", body,
        tool_name="linkedin_reply_to_conversation",
    )


async def cmd_get_user_presence(
    session_id: str,
    profile_urns: list[str],
) -> dict:
    """LinkedIn 查询用户在线状态。profile_urns: fsd_profile URN 数组。

    返回: {raw, statuses: {urn: {available, last_active_at, instantly_reachable}}}。
    """
    body = {"profile_urns": profile_urns}
    return await _send(
        session_id, "POST", "linkedin/get_user_presence", body,
        tool_name="linkedin_get_user_presence",
    )


async def cmd_get_my_email_handles(
    session_id: str,
    primary_only: bool = True,
) -> dict:
    """LinkedIn 获取当前用户邮箱列表（Apply 自动填表用）。

    返回: {raw, emails:[{email, is_primary, is_verified}], total}。
    """
    body = {"primary_only": primary_only}
    return await _send(
        session_id, "POST", "linkedin/get_my_email_handles", body,
        tool_name="linkedin_get_my_email_handles",
    )


# ── LinkedIn Recruiter (Talent Solutions) ───────────────────────────────────


async def cmd_recruiter_search(
    session_id: str,
    project_urn: str,
    keywords: str = "",
    titles: str = "",
    start: int = 0,
    count: int = 25,
) -> dict:
    body: dict[str, Any] = {
        "project_urn": project_urn,
        "keywords": keywords,
        "start": start,
        "count": count,
    }
    if titles:
        body["titles"] = titles
    return await _send(
        session_id, "POST", "linkedin_recruiter/search", body,
        tool_name="linkedin_recruiter_search",
    )


async def cmd_recruiter_get_profile(
    session_id: str,
    profile_urn: str,
    project_urn: str = "",
) -> dict:
    body: dict[str, Any] = {"profile_urn": profile_urn}
    if project_urn:
        body["project_urn"] = project_urn
    return await _send(
        session_id, "POST", "linkedin_recruiter/get_profile", body,
        tool_name="linkedin_recruiter_get_profile",
    )


async def cmd_recruiter_send_inmail(
    session_id: str,
    recipient_profile_urn: str,
    subject: str,
    body: str,
    hiring_project_urn: str = "",
    sourcing_channel_urn: str = "",
    signature: str = "",
) -> dict:
    payload: dict[str, Any] = {
        "recipient_profile_urn": recipient_profile_urn,
        "subject": subject,
        "body": body,
    }
    if hiring_project_urn:
        payload["hiring_project_urn"] = hiring_project_urn
    if sourcing_channel_urn:
        payload["sourcing_channel_urn"] = sourcing_channel_urn
    if signature:
        payload["signature"] = signature
    return await _send(
        session_id, "POST", "linkedin_recruiter/send_inmail", payload,
        tool_name="linkedin_recruiter_send_inmail",
    )


async def cmd_recruiter_add_to_project(
    session_id: str,
    candidate_urn: str,
    hiring_project_urn: str,
    sourcing_channel_urn: str,
) -> dict:
    return await _send(
        session_id, "POST", "linkedin_recruiter/add_to_project",
        {
            "candidate_urn": candidate_urn,
            "hiring_project_urn": hiring_project_urn,
            "sourcing_channel_urn": sourcing_channel_urn,
        },
        tool_name="linkedin_recruiter_add_to_project",
    )


async def cmd_recruiter_list_projects(session_id: str) -> dict:
    return await _send(
        session_id, "GET", "linkedin_recruiter/list_projects",
        tool_name="linkedin_recruiter_list_projects",
    )


async def cmd_recruiter_search_facets(
    session_id: str,
    project_urn: str,
    facet_types: str = "",
) -> dict:
    body: dict[str, Any] = {"project_urn": project_urn}
    if facet_types:
        body["facet_types"] = facet_types
    return await _send(
        session_id, "POST", "linkedin_recruiter/search_facets", body,
        tool_name="linkedin_recruiter_search_facets",
    )
