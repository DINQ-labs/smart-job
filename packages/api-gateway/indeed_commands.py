"""
indeed_commands.py: Indeed 命令层 — 通过 Extension 传输：
  send_command_to() → job-seeker-ext Chrome 扩展

不修改任何 boss_* / linkedin_* 命令或现有会话管理逻辑。
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
    """Indeed 平台命令发送 —— 委托给 platform_command_base。"""
    return await send_platform_command(
        session_id, method, path, body,
        tool_name=tool_name, platform_label="Indeed",
    )


async def cmd_check_login(session_id: str) -> dict:
    """检查 Indeed 登录状态，返回 {logged_in, email, userId}。

    登录成功时在 session 上绑定 site_users['indeed']=userId；
    明确返回 logged_in=false 时清理该绑定（避免脏读已失效的 session）。
    """
    data = await _send(
        session_id, "GET", "indeed/check_login",
        tool_name="indeed_check_login",
    )
    try:
        if isinstance(data, dict):
            if data.get("logged_in") is True:
                user_id = str(data.get("userId") or "").strip()
                if user_id:
                    session_store.set_site_user(session_id, "indeed", user_id)
            elif data.get("logged_in") is False:
                # 明确登出：清理 site_user 绑定，让 find_by_site 不再匹配
                session_store.set_site_user(session_id, "indeed", "")
            # 其他情况（None / 缺失 / 格式错误）保留现有绑定不动
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass
    return data


async def _cache_indeed_job_list(
    data: dict, keywords: str, location: str, page: int, country: str,
) -> None:
    """Fire-and-forget: 把 Indeed search 结果写入 cached_jobs (+ cached_searches)。"""
    try:
        jobs = (data or {}).get("jobs") or []
        if not isinstance(jobs, list) or not jobs:
            return
        job_ids: list[str] = []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            jk = str(j.get("jobKey") or "").strip()
            if not jk:
                continue
            try:
                await db.upsert_job(
                    "indeed", jk,
                    title=j.get("title") or None,
                    company=j.get("company") or None,
                    city=j.get("location") or None,
                    country=country or None,
                    salary=j.get("salary") or None,
                    raw_list=j,
                )
                job_ids.append(jk)
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
        if keywords and job_ids:
            try:
                # city_code 位用 country+location 合成 key,保证 (keyword,location,page) 去重语义
                city_key = (location or country or "").strip() or None
                await db.upsert_search(
                    "indeed", keywords, city_key, page,
                    job_ids=job_ids,
                    total_count=(data or {}).get("total"),
                )
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
    except Exception as exc:
        log.debug("cache indeed job list failed: %s", exc)


def _extract_indeed_detail_fields(raw: dict) -> tuple[str | None, str | None]:
    """从 Indeed viewjob / job detail 响应里挖 description + address。

    Indeed 的 job detail 结构随 API 版本变化，这里容错多条路径取第一个非空。
    """
    if not isinstance(raw, dict):
        return None, None
    description = None
    address = None
    # 路径 A: body.jobInfoWrapperModel.jobInfoModel.jobDescriptionSectionModel.jobDescriptionText
    body = raw.get("body") or raw
    if isinstance(body, dict):
        wrapper = (body.get("jobInfoWrapperModel") or {})
        model = (wrapper.get("jobInfoModel") or {}) if isinstance(wrapper, dict) else {}
        jd = (model.get("jobDescriptionSectionModel") or {}) if isinstance(model, dict) else {}
        if isinstance(jd, dict):
            description = description or jd.get("jobDescriptionText") or jd.get("descriptionHtml")
        loc = (model.get("jobLocationModel") or {}) if isinstance(model, dict) else {}
        if isinstance(loc, dict):
            address = address or loc.get("formattedAddress") or loc.get("jobLocation")
    # 路径 B: 顶层 description / formattedLocation 兜底
    description = description or raw.get("description") or raw.get("jobDescription")
    address = address or raw.get("formattedLocation") or raw.get("location")
    return description, address


async def _cache_indeed_job_detail(data: dict, job_key: str) -> None:
    """Fire-and-forget: 把 Indeed detail 写入 cached_job_details。"""
    try:
        raw = (data or {}).get("raw") or data or {}
        description, address = _extract_indeed_detail_fields(raw)
        await db.upsert_job_detail(
            "indeed", str(job_key),
            description=description,
            address=address,
            raw_detail=data,
        )
    except Exception as exc:
        log.debug("cache indeed job detail failed: %s", exc)


async def cmd_search_jobs(
    session_id: str,
    keywords: str,
    location: str = "",
    page: int = 1,
    country: str = "US",
    page_size: int = 10,
    # ── 筛选器参数（全部可选）──
    job_type: str = "",      # fulltime / parttime / contract / internship / temporary
    fromage: str = "",       # "1" / "3" / "7" / "14"  (days since posted)
    radius: int = 0,         # miles: 0 / 5 / 10 / 15 / 25 / 50 / 100
    salary_min: int = 0,     # 年薪下限(美区 USD,其他区本币)
    experience: str = "",    # entry_level / mid_level / senior_level
    sort: str = "",          # "" (relevance) / "date"
) -> dict:
    """搜索 Indeed 职位列表。country: 地区码（SG/US/UK 等）决定使用的地区子域。
    成功结果异步写入 cached_jobs 以支持 cache-first 批量分析。

    扩展 handler 约定参数名为 `query` + `start`(offset),gateway 在此做适配:
      - keywords → query
      - (page - 1) * page_size → start
    筛选器透传给扩展,由扩展拼到 /jobs?q=&l=&jt=&fromage=&radius=&sr=&explvl=&sort=...
    """
    if not keywords:
        raise RuntimeError("keywords 必填")
    start = max(0, (page - 1)) * page_size
    body: dict = {
        "query": keywords,
        "location": location,
        "start": start,
        "country": country,
    }
    if job_type:   body["job_type"] = job_type
    if fromage:    body["fromage"] = fromage
    if radius:     body["radius"] = radius
    if salary_min: body["salary_min"] = salary_min
    if experience: body["experience"] = experience
    if sort:       body["sort"] = sort
    data = await _send(
        session_id, "POST", "indeed/search_jobs", body,
        tool_name="indeed_search_jobs",
    )
    asyncio.ensure_future(_cache_indeed_job_list(
        data if isinstance(data, dict) else {},
        keywords, location, page, country,
    ))
    return data


async def cmd_get_job_detail(session_id: str, job_id: str, country: str = "US") -> dict:
    """获取 Indeed 职位详情。扩展侧参数名为 job_key（gateway 这里做适配）。
    成功结果异步写入 cached_job_details，后续 indeed_get_cached_job 可读取。"""
    if not job_id:
        raise RuntimeError("job_id 必填")
    data = await _send(
        session_id, "GET", "indeed/get_job_detail",
        {"job_key": job_id, "country": country},
        tool_name="indeed_get_job_detail",
    )
    asyncio.ensure_future(_cache_indeed_job_detail(
        data if isinstance(data, dict) else {}, job_id,
    ))
    return data


async def cmd_apply(
    session_id: str,
    job_id: str,
    resume_data: dict | None = None,
    country: str = "US",
) -> dict:
    """向 Indeed 职位投递申请（旧版，仅点击按钮）。"""
    body = {"jobId": job_id, "resumeData": resume_data or {}, "country": country}
    return await _send(
        session_id, "POST", "indeed/apply", body,
        tool_name="indeed_apply",
    )


async def cmd_apply_full(
    session_id: str,
    job_id: str,
    profile_data: dict,
    country: str = "US",
) -> dict:
    """Indeed Apply 完整投递（多步表单自动填写）。"""
    body = {"jobId": job_id, "profileData": profile_data, "country": country}
    return await _send(
        session_id, "POST", "indeed/apply", body,
        tool_name="indeed_apply_job",
    )


async def cmd_get_apply_form(session_id: str, job_id: str, country: str = "US") -> dict:
    """获取 Indeed 申请表单结构（不填写）。"""
    return await _send(
        session_id, "GET", "indeed/get_apply_form",
        {"jobId": job_id, "country": country},
        tool_name="indeed_get_apply_form",
    )


async def cmd_fill_fields(session_id: str, actions: list) -> dict:
    """按指令填写 Indeed 表单字段（处理 unresolved_fields）。"""
    return await _send(
        session_id, "POST", "indeed/fill_fields",
        {"actions": actions},
        tool_name="indeed_fill_fields",
    )


async def cmd_prepare_apply(
    session_id: str,
    jk: str,
    job_country: str = "US",
    tk: str = "",
    vjtk: str = "",
    ctk: str = "",
) -> dict:
    """Indeed 投递前置：为 jk 生成 smartapply URL 和 iaUid。

    返回: {raw, ia_uid, apply_url}。iaUid 为 check_applied 所需。
    """
    body = {"jk": jk, "job_country": job_country, "tk": tk, "vjtk": vjtk, "ctk": ctk}
    return await _send(
        session_id, "POST", "indeed/prepare_apply", body,
        tool_name="indeed_prepare_apply",
    )


async def cmd_check_applied(
    session_id: str,
    jk: str,
    ia_uid: str,
    job_country: str = "US",
) -> dict:
    """Indeed 查询某 jk 是否已投递。批量投递前去重用。

    返回: {raw, applied: bool, applied_ms}。
    """
    body = {"jk": jk, "ia_uid": ia_uid, "job_country": job_country}
    return await _send(
        session_id, "POST", "indeed/check_applied", body,
        tool_name="indeed_check_applied",
    )


async def cmd_search_job_alert(
    session_id: str,
    keywords: str,
    location: str = "",
    country: str = "US",
    locale: str = "en_US",
) -> dict:
    """Indeed 查询某搜索是否已订阅邮件 alert。

    返回: {raw, subscription_state, subscribed: bool}。
    """
    body = {"keywords": keywords, "location": location, "country": country, "locale": locale}
    return await _send(
        session_id, "POST", "indeed/search_job_alert", body,
        tool_name="indeed_search_job_alert",
    )


async def cmd_create_job_alert(
    session_id: str,
    keywords: str,
    location: str,
    email: str,
    country: str = "US",
    locale: str = "en_US",
    search_params: str = "",
    client_tk: str = "",
) -> dict:
    """Indeed 订阅邮件 job alert。

    返回: {raw, subscription_id, subscription_state}。
    """
    body = {
        "keywords": keywords,
        "location": location,
        "email": email,
        "country": country,
        "locale": locale,
        "search_params": search_params,
        "client_tk": client_tk,
    }
    return await _send(
        session_id, "POST", "indeed/create_job_alert", body,
        tool_name="indeed_create_job_alert",
    )


async def cmd_autocomplete(
    session_id: str,
    type_: str,
    query: str,
    country: str = "US",
    language: str = "en",
    location: str = "",
) -> dict:
    """Indeed 输入建议。type: 'what' / 'location' / 'cmp-what-with-top-companies'。

    location 仅对 'cmp-what-with-top-companies' 生效（用作 where 过滤）。
    返回: {raw, suggestions: [str]}。
    """
    body = {
        "type": type_, "query": query,
        "country": country, "language": language,
        "location": location,
    }
    return await _send(
        session_id, "POST", "indeed/autocomplete", body,
        tool_name="indeed_autocomplete",
    )


async def cmd_unread_messages(session_id: str) -> dict:
    """Indeed 未读会话数（消息红点）。

    返回: {raw, unread_count: int}。
    """
    return await _send(
        session_id, "POST", "indeed/unread_messages", None,
        tool_name="indeed_unread_messages",
    )


# ──────────────────────────────────────────────────────────────────────────
# P0 批次 #2 — 职位 state（save/unsave/dislike/undislike）+ 公司 + 偏好
# ──────────────────────────────────────────────────────────────────────────


async def cmd_save_job(session_id: str, jk: str, country: str = "US") -> dict:
    """Indeed 保存职位。扩展自动从 cookie 读 csrf。"""
    body = {"jk": jk, "country": country}
    return await _send(
        session_id, "POST", "indeed/save_job", body,
        tool_name="indeed_save_job",
    )


async def cmd_unsave_job(session_id: str, jk: str, country: str = "US") -> dict:
    """Indeed 取消保存职位。"""
    body = {"jk": jk, "country": country}
    return await _send(
        session_id, "POST", "indeed/unsave_job", body,
        tool_name="indeed_unsave_job",
    )


async def cmd_dislike_job(session_id: str, jk: str, country: str = "US") -> dict:
    """Indeed 隐藏/不喜欢职位（搜索结果中过滤）。"""
    body = {"jk": jk, "country": country}
    return await _send(
        session_id, "POST", "indeed/dislike_job", body,
        tool_name="indeed_dislike_job",
    )


async def cmd_undislike_job(session_id: str, jk: str, country: str = "US") -> dict:
    """Indeed 撤销隐藏职位。"""
    body = {"jk": jk, "country": country}
    return await _send(
        session_id, "POST", "indeed/undislike_job", body,
        tool_name="indeed_undislike_job",
    )


async def cmd_search_companies(
    session_id: str,
    query: str,
    caret: int = -1,
    country: str = "US",
) -> dict:
    """Indeed 公司名输入补全。返回 {raw, suggestions}。"""
    body = {"query": query, "caret": caret, "country": country}
    return await _send(
        session_id, "POST", "indeed/search_companies", body,
        tool_name="indeed_search_companies",
    )


async def cmd_get_company_jobs(
    session_id: str,
    job_key: str,
    country: str = "US",
    logged_in: bool = True,
) -> dict:
    """Indeed 公司页上下文下的职位详情（精简版 viewjob）。返回 {raw, job}。"""
    body = {"jobKey": job_key, "country": country, "loggedIn": logged_in}
    return await _send(
        session_id, "POST", "indeed/get_company_jobs", body,
        tool_name="indeed_get_company_jobs",
    )


async def cmd_follow_company_check(
    session_id: str,
    company_name: str,
    company_id: str,
    country: str = "US",
) -> dict:
    """Indeed 检查是否已关注某公司。返回 {raw, alert_code, followed: bool}。"""
    body = {
        "company_name": company_name,
        "company_id": company_id,
        "country": country,
    }
    return await _send(
        session_id, "POST", "indeed/follow_company_check", body,
        tool_name="indeed_follow_company_check",
    )


async def cmd_list_conversations(
    session_id: str,
    country: str = "US",
    locale: str = "",
    folder: str = "inbox",
    last: int = 30,
    cursor: str = "",
) -> dict:
    """Indeed 列出用户会话（消息中心）。folder: inbox/archive/spam。
    返回: {raw, conversations: [...], total, page_info}。"""
    body = {
        "country": country, "locale": locale,
        "folder": folder, "last": last, "cursor": cursor,
    }
    return await _send(
        session_id, "POST", "indeed/list_conversations", body,
        tool_name="indeed_list_conversations",
    )


async def cmd_get_online_status(
    session_id: str,
    country: str = "US",
    locale: str = "",
) -> dict:
    """Indeed 查询用户在线状态偏好。返回 {raw, is_enabled: bool}。"""
    body = {"country": country, "locale": locale}
    return await _send(
        session_id, "POST", "indeed/get_online_status", body,
        tool_name="indeed_get_online_status",
    )


async def cmd_get_preferences(session_id: str, country: str = "US") -> dict:
    """Indeed 读取用户求职偏好（最低薪资、地点、通勤、工作时间等 JSSD 数据）。

    返回: {raw, minimum_pay, relocation, maximum_commute, locations,
           positive_attributes, negative_attributes}。
    """
    body = {"country": country}
    return await _send(
        session_id, "POST", "indeed/get_preferences", body,
        tool_name="indeed_get_preferences",
    )


async def cmd_get_resume_section(session_id: str, country: str = "US") -> dict:
    """Indeed 读取当前登录用户的简历（GraphQL ResumeSection）。

    返回: {raw, account_key, contact:{first_name,last_name,phone_number,location},
           resumes: [...], has_resume: bool}。
    用途：在 indeed/apply 前取本人资料，避免反复询问用户。
    """
    body = {"country": country}
    return await _send(
        session_id, "POST", "indeed/get_resume_section", body,
        tool_name="indeed_get_resume_section",
    )


async def cmd_get_competitor_jobs(
    session_id: str,
    job_key: str,
    limit: int = 15,
    country: str = "US",
) -> dict:
    """Indeed 获取同岗位/同公司的竞品公司职位推荐（SERP 右侧卡片数据源）。

    返回: {raw, jobs: [...], tracking_key}。
    """
    body = {"job_key": job_key, "limit": limit, "country": country}
    return await _send(
        session_id, "POST", "indeed/get_competitor_jobs", body,
        tool_name="indeed_get_competitor_jobs",
    )


async def cmd_get_new_jobs_count(
    session_id: str,
    location: str,
    keywords: str = "",
    fromage: str = "last",
    country: str = "US",
) -> dict:
    """Indeed 查询 (q, l) 组合在时间窗内的新增职位数（用于新职位提醒策略）。

    参数: fromage 'last'(自上次浏览) / '1' / '3' / '7' / '14'。
    返回: {raw, new_count, total_count, query_key}。
    """
    body = {
        "location": location, "keywords": keywords,
        "fromage": fromage, "country": country,
    }
    return await _send(
        session_id, "POST", "indeed/get_new_jobs_count", body,
        tool_name="indeed_get_new_jobs_count",
    )


# ──────────────────────────────────────────────────────────────────────────
# My Jobs 系列（已申请 / 面试 / 状态更新）+ 邮件偏好 + 未读 offer
# ──────────────────────────────────────────────────────────────────────────


async def cmd_list_applied_jobs(
    session_id: str,
    start_ms: int = 0,
    from_param: str = "app-tracker",
) -> dict:
    """Indeed 列出用户已申请职位（My Jobs → Applied）。

    参数: start_ms 过滤起始时间戳（毫秒），0 不限。
    返回: {raw, jobs:[{job_key,app_tk,job_title,job_url,location,company,...}], total}。
    """
    body = {"start_ms": start_ms, "from_param": from_param}
    return await _send(
        session_id, "POST", "indeed/list_applied_jobs", body,
        tool_name="indeed_list_applied_jobs",
    )


async def cmd_list_interviews(
    session_id: str,
    statuses: list | None = None,
    formats: list | None = None,
    start_ms: int = 0,
) -> dict:
    """Indeed 列出用户面试（可按状态/形式过滤）。

    默认 statuses=JS_CONFIRM,JS_CANCEL,EMP_CANCEL,EMP_INVITE
    默认 formats=IN_PERSON,PHONE,THIRD_PARTY_VIDEO,INDEED_VIDEO
    返回: {raw, interviews: [...], total}。
    """
    body = {
        "statuses": statuses or [],
        "formats": formats or [],
        "start_ms": start_ms,
    }
    return await _send(
        session_id, "POST", "indeed/list_interviews", body,
        tool_name="indeed_list_interviews",
    )


async def cmd_update_job_app_status(
    session_id: str,
    jk: str,
    state_payload: dict,
    cause: str = "api update",
) -> dict:
    """Indeed 更新已申请职位状态（标记 NOT_INTERESTED / OFFER 等）。

    state_payload 需含完整的 statuses/prevAppStatusState/newAppStatusState 等字段
    （从 list_applied_jobs 的 statuses 节点得到当前状态，再改动目标字段即可）。
    CSRF 由扩展从 indeedcsrftoken cookie 自动读取。
    返回: {raw, jk, success: bool}。
    """
    body = {"jk": jk, "state_payload": state_payload, "cause": cause}
    return await _send(
        session_id, "POST", "indeed/update_job_app_status", body,
        tool_name="indeed_update_job_app_status",
    )


async def cmd_get_notifications_count(
    session_id: str,
    country: str = "US",
) -> dict:
    """Indeed 全站通知红点（右上角小铃铛，区别于消息红点）。

    返回: {raw, authenticated, new_count, should_show_new}。
    """
    body = {"country": country}
    return await _send(
        session_id, "POST", "indeed/get_notifications_count", body,
        tool_name="indeed_get_notifications_count",
    )


async def cmd_get_email_preferences(session_id: str) -> dict:
    """Indeed 查询邮件订阅偏好（account_updates / recruiter_invites / major_js 等）。

    返回: {raw, preferences:[{category, is_enabled}], email_channel_opted_out}。
    """
    return await _send(
        session_id, "POST", "indeed/get_email_preferences", None,
        tool_name="indeed_get_email_preferences",
    )


async def cmd_get_unread_offers_count(session_id: str) -> dict:
    """Indeed 未读招聘方 offer 数（不同于消息 unread_messages）。

    返回: {raw, unread_offers_count: int}。
    """
    return await _send(
        session_id, "POST", "indeed/get_unread_offers_count", None,
        tool_name="indeed_get_unread_offers_count",
    )
