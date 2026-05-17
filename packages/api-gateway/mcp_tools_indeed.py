"""
mcp_tools_indeed.py — Indeed (求职 + 雇主端) MCP tool 定义。
"""
from __future__ import annotations

from fastmcp import Context

import db
from server_helpers import (
    _ok, _err, _get_agent_id, _resolve_and_bind, _current_user_id,
    _run_boss_tool, _run_site_tool, _parse_json_arg,
)
from session_store import session_store
import indeed_commands as ind_cmd
import indeed_employer_commands as ine_cmd
from commands import (
    cmd_get_clickables,
    cmd_get_dom_snapshot,
    cmd_click_by_idx,
    cmd_click_by_text,
    cmd_wait_for_element,
    cmd_navigate_to,
)

# 兼容旧别名：文件内历史代码使用 `in_cmd.*` 调用。
in_cmd = ind_cmd


def _default_indeed_session() -> str:
    """按当前 caller (DINQ user_id) 定位其本人的 Indeed 扩展会话。

    **多租户安全**：严格按 user_id 精确匹配，**不会 fallback 到其他用户的 session**。
    找不到时返回 ""，由调用方报错。

    优先级：
      1. 若 caller 在 indeed 平台设了 active session（set_active_session）且仍在线，用它
      2. 否则遍历该 user_id 的已连接会话，返回第一个支持 indeed 的

    失败场景（返回空串）：
      1. 请求未携带 x-user-id header（DINQ 未登录或 agent-gateway 未透传）
      2. 有 caller_uid 但该用户的扩展会话未连接 / 未上报 indeed 能力
    """
    caller_uid = _current_user_id.get()
    if not caller_uid:
        return ""
    # 优先 active session（E1）
    pinned = session_store.get_active_session(caller_uid, "indeed")
    if pinned:
        return pinned
    for entry in session_store._sessions.values():
        if entry.ws is None:
            continue
        if entry.user_id != caller_uid:
            continue
        # sites 为空视为支持全部（兼容旧扩展）
        if entry.sites and "indeed" not in entry.sites:
            continue
        return entry.session_id
    return ""


def register(mcp):
    """注册所有 Indeed MCP tools（含 indeed_employer）。"""

    _INDEED_NO_SESS = "没有找到匹配的 Indeed 扩展会话（请先登录 DINQ，并确认浏览器 job-api-ext 已连接网关）。"

    @mcp.tool()
    async def indeed_check_login(ctx: Context, session_id: str = "") -> str:
        """检查 Indeed 登录状态。返回 {logged_in: bool, email, userId}。通过扩展检测 PPID cookie。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_check_login,
            no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_search_jobs(
        ctx: Context,
        keywords: str,
        location: str = "",
        page: int = 1,
        country: str = "US",
        # ── 筛选器（可选）──
        job_type: str = "",
        fromage: str = "",
        radius: int = 0,
        salary_min: int = 0,
        experience: str = "",
        sort: str = "",
        session_id: str = "",
    ) -> str:
        """搜索 Indeed 职位。

        基础参数:
          keywords: 职位关键词(必填,英文)
          location: 城市/地区(英文,如 'San Francisco, CA' / 'Remote')
          country:  US / SG / UK / CA / IN / AU 等,决定地区子域
          page:     页码(1 起,每页 10 条)

        筛选器(全可选,与 Indeed 网页搜索筛选 chip 对应):
          job_type:   fulltime / parttime / contract / internship / temporary
          fromage:    '1' / '3' / '7' / '14' —— 最近 N 天发布的职位
          radius:     miles 半径 (0 / 5 / 10 / 15 / 25 / 50 / 100)
          salary_min: 年薪下限(美区 USD)
          experience: entry_level / mid_level / senior_level
          sort:       空串=按相关度(默认), 'date'=按发布时间倒序

        返回职位列表含 jobKey、title、company、location、salary。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_search_jobs,
            keywords, location, page, country,
            no_session_hint=_INDEED_NO_SESS,
            job_type=job_type, fromage=fromage, radius=radius,
            salary_min=salary_min, experience=experience, sort=sort,
        )


    @mcp.tool()
    async def indeed_get_job_detail(
        ctx: Context, job_id: str, country: str = "US", session_id: str = "",
    ) -> str:
        """查看 Indeed 职位详情（只读；不会打开申请表单 / 不会触发投递）。

        **这是查看职位详情的唯一入口**。绝对不要用 indeed_get_apply_form 代替——
        后者只用于用户明确要投递时预览表单结构。

        job_id: 职位 ID（从 indeed_search_jobs 结果的 jobId 字段获取）
        country: 地区码决定地区子域（默认 US）"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_job_detail,
            job_id, country, no_session_hint=_INDEED_NO_SESS,
        )


    # ── Indeed 缓存读工具（镜像 Boss） ─────────────────────────────────────

    def _indeed_compact_job(j: dict) -> dict:
        return {
            "external_id": j.get("external_id"),
            "title": j.get("title"),
            "company": j.get("company"),
            "city": j.get("city"),
            "country": j.get("country"),
            "has_detail": j.get("has_detail", False),
            "fetched_at": j.get("fetched_at"),
        }


    @mcp.tool()
    async def indeed_list_cached_jobs(
        ctx: Context,
        keyword: str = "",
        city_code: str = "",
        has_detail: bool = False,
        fresh_within_days: int = 0,
        include_expired: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """Indeed 列出本地已缓存的职位（之前搜索 / 查详情过的）。免费,不调 live API。

        **批量"分析职位"前应先调本工具查缓存**，避免触发 Indeed viewjob 频次限制。
        参数:
          keyword: 关键字过滤（title / company 模糊匹配）
          city_code: 地域过滤（Indeed 的 location 字符串，如 "San Francisco, CA"）
          has_detail: true=只返回已抓取详情的职位，false=全部
          fresh_within_days: 只返回最近 N 天内抓取的条目（0=不过滤，默认）
          include_expired: 是否包含已过期岗位（默认 false 自动剔除）
          limit: 每页数量（上限 100）
        返回 {jobs: [{external_id=jobKey, title, company, city, has_detail, ...}], total}
        """
        try:
            jobs = await db.list_cached_jobs(
                platform="indeed",
                keyword=keyword or None,
                city_code=city_code or None,
                has_detail=has_detail if has_detail else None,
                fresh_within_days=fresh_within_days if fresh_within_days > 0 else None,
                include_expired=include_expired,
                limit=min(limit, 100),
                offset=offset,
            )
            compact = [_indeed_compact_job(j) for j in jobs]
            return _ok({"jobs": compact, "total": len(compact)})
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def indeed_get_cached_job(ctx: Context, job_key: str, session_id: str = "") -> str:
        """Indeed 读取单个缓存职位（含 description 若已抓详情）。免费,不调 live。

        **优先于 indeed_get_job_detail 使用**：命中即可免 live 请求。
        job_key: Indeed 职位 jobKey。缓存未命中返回错误。
        返回: {external_id, title, company, city, has_detail, description, raw_list, raw_detail, fetched_at}
        """
        try:
            job = await db.get_cached_job("indeed", str(job_key))
            if not job:
                return _err(
                    f"缓存中未找到 Indeed 职位 {job_key}，"
                    "请先调用 indeed_search_jobs 或 indeed_get_job_detail 拉取。"
                )
            return _ok(job)
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def indeed_apply(
        ctx: Context, job_id: str, country: str = "US", session_id: str = "",
    ) -> str:
        """向 Indeed 职位投递申请（旧版，仅点击按钮）。job_id: 职位 ID。country: 地区码。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_apply,
            job_id, None, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_apply_job(ctx: Context, job_id: str, profile_data: str = "",
                               country: str = "US", session_id: str = "") -> str:
        """Indeed 自动投递（支持多步表单自动填写）。
        job_id: 职位 ID。
        profile_data: JSON 字符串，含 email/phone/firstName/lastName 等用户资料。留空则不预填。
        country: 地区码决定地区子域（默认 US）。
        返回 {ok, status, steps}。若 status='unresolved_fields'，需调用 indeed_fill_fields 补填。"""
        try:
            pd = _parse_json_arg(profile_data, "profile_data", {})
        except ValueError as e:
            return _err(str(e))
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_apply_full,
            job_id, pd, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_apply_form(
        ctx: Context, job_id: str, country: str = "US", session_id: str = "",
    ) -> str:
        """预览 Indeed 职位申请表单结构（用户明确要投递前的准备步骤，不自动填写）。

        **重要**：这不是"查看职位详情"的工具。查看职位详情请用 indeed_get_job_detail。
        本工具会打开申请浮层，只在用户已明确要投递 + 需要预览表单字段时使用。

        country: 地区码
        返回 {fields: [{selector, label, type, required, options}], buttons: {...}}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_apply_form,
            job_id, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_fill_fields(ctx: Context, actions: str, session_id: str = "") -> str:
        """按指令填写 Indeed 申请表单字段。
        actions: JSON 数组 [{selector, value, type}]。用于处理 indeed_apply_job 返回的 unresolved_fields。"""
        try:
            acts = _parse_json_arg(actions, "actions", [])
        except ValueError as e:
            return _err(str(e))
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_fill_fields, acts,
            no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_prepare_apply(
        ctx: Context,
        jk: str,
        job_country: str = "US",
        tk: str = "",
        vjtk: str = "",
        ctk: str = "",
        session_id: str = "",
    ) -> str:
        """Indeed 投递前置：为 jk 生成 smartapply URL 和 iaUid。
        返回 {ia_uid, apply_url, raw}。iaUid 是 indeed_check_applied 的必需入参。
        Agent 在批量投递场景应先调此 tool 再调 indeed_check_applied 去重。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_prepare_apply,
            jk, job_country, tk, vjtk, ctk, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_check_applied(
        ctx: Context,
        jk: str,
        ia_uid: str,
        job_country: str = "US",
        session_id: str = "",
    ) -> str:
        """Indeed 查询某 jk 是否已投递过（批量投递去重）。
        ia_uid: 从 indeed_prepare_apply 的响应 ia_uid 字段获取。
        返回 {applied: bool, applied_ms, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_check_applied,
            jk, ia_uid, job_country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_search_job_alert(
        ctx: Context,
        keywords: str,
        location: str = "",
        country: str = "US",
        locale: str = "en_US",
        session_id: str = "",
    ) -> str:
        """Indeed 查询某 q+l 搜索是否已订阅邮件 alert。
        返回 {subscribed: bool, subscription_state, raw}。
        状态为 NOT_FOUND 表示未订阅，可继续调 indeed_create_job_alert。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_search_job_alert,
            keywords, location, country, locale, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_create_job_alert(
        ctx: Context,
        keywords: str,
        location: str,
        email: str,
        country: str = "US",
        locale: str = "en_US",
        search_params: str = "",
        client_tk: str = "",
        session_id: str = "",
    ) -> str:
        """Indeed 订阅邮件 job alert（免费用户长期自动化的 穷人 Cron）。
        email: 登录邮箱；country/locale: SG/en_SG 或 US/en_US 等。
        返回 {subscription_id, subscription_state, raw}。state=ACTIVE 表示订阅成功。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_create_job_alert,
            keywords, location, email, country, locale, search_params, client_tk,
            no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_autocomplete(
        ctx: Context,
        type: str,
        query: str,
        country: str = "US",
        language: str = "en",
        location: str = "",
        session_id: str = "",
    ) -> str:
        """Indeed 输入建议（补全用户正在输入的词），**不返回职位列表**。

        **用户想搜职位 → 用 `indeed_search_jobs`**，不要先调本工具再转发结果。
        本工具只在用户**明确要求词条联想**、或前端搜索框需要下拉提示时才用。

        type:
          - 'what'（关键词）
          - 'location'（地点）
          - 'cmp-what-with-top-companies'（关键词 + 顶级公司融合补全，SERP 主搜索框用）
        location 仅对 'cmp-what-with-top-companies' 生效（作为 where 过滤）。
        返回 {suggestions: [str], raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_autocomplete,
            type, query, country, language, location,
            no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_unread_messages(
        ctx: Context,
        session_id: str = "",
    ) -> str:
        """Indeed 未读会话数（消息红点）。
        返回 {unread_count: int, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_unread_messages,
            no_session_hint=_INDEED_NO_SESS,
        )


    # ── 职位 state（save/unsave/dislike/undislike） + 公司 + 偏好 ──────────────

    @mcp.tool()
    async def indeed_save_job(
        ctx: Context, jk: str, country: str = "US", session_id: str = "",
    ) -> str:
        """Indeed 保存职位到 My Jobs。country: 用户所在地区码（SG/US/UK 等），决定 RPC 的地区子域。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_save_job,
            jk, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_unsave_job(
        ctx: Context, jk: str, country: str = "US", session_id: str = "",
    ) -> str:
        """Indeed 取消保存职位。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_unsave_job,
            jk, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_dislike_job(
        ctx: Context, jk: str, country: str = "US", session_id: str = "",
    ) -> str:
        """Indeed 隐藏/不喜欢职位（搜索结果中过滤）。常用于批量清理不相关的推荐。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_dislike_job,
            jk, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_undislike_job(
        ctx: Context, jk: str, country: str = "US", session_id: str = "",
    ) -> str:
        """Indeed 撤销隐藏职位。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_undislike_job,
            jk, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_search_companies(
        ctx: Context,
        query: str,
        caret: int = -1,
        country: str = "US",
        session_id: str = "",
    ) -> str:
        """Indeed 公司名补全（用于查找 Apple / Meta 等公司）。
        返回 {suggestions: [...], raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_search_companies,
            query, caret, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_company_jobs(
        ctx: Context,
        job_key: str,
        country: str = "US",
        logged_in: bool = True,
        session_id: str = "",
    ) -> str:
        """Indeed 公司页上下文的职位详情（/cmp/-/rpc/fetch-jobs，结构比 viewjob 精简）。
        返回 {job: {key,title,description,indeedApply,location}, raw}。"""
        try:
            sid = session_id or _default_indeed_session()
            if not sid:
                return _err("没有找到匹配的 Indeed 扩展会话（请先登录 DINQ，并确认浏览器 job-api-ext 已连接网关）。")
            return _ok(await in_cmd.cmd_get_company_jobs(sid, job_key, country, logged_in))
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def indeed_follow_company_check(
        ctx: Context,
        company_name: str,
        company_id: str,
        country: str = "US",
        session_id: str = "",
    ) -> str:
        """Indeed 检查是否已关注某公司（返回 alertCode；非 null 表示已订阅）。
        company_name: 公司显示名（如 'Ministry of Education Singapore'）；
        company_id: 公司 ID（cid，从 search_companies 或公司页 URL 获取）。"""
        try:
            sid = session_id or _default_indeed_session()
            if not sid:
                return _err("没有找到匹配的 Indeed 扩展会话（请先登录 DINQ，并确认浏览器 job-api-ext 已连接网关）。")
            return _ok(await in_cmd.cmd_follow_company_check(sid, company_name, company_id, country))
        except Exception as e:
            return _err(str(e))


    # NOTE: indeed_get_company_jobs / indeed_follow_company_check 的 sid 解析/提示文案
    # 与 _run_site_tool 模板一致，但它们还接 logged_in/country 等较独特的位置参数，
    # 保留原样以免触及现有测试心智。后续要迁可用 _run_site_tool + cmd_kwargs。

    @mcp.tool()
    async def indeed_list_conversations(
        ctx: Context,
        country: str = "US",
        locale: str = "",
        folder: str = "inbox",
        last: int = 30,
        cursor: str = "",
        session_id: str = "",
    ) -> str:
        """Indeed 列出用户会话（消息中心）。
        folder: 'inbox'（默认）/'archive'/'spam'；last: 每页条数；cursor: 翻页用。
        返回 {conversations: [{id,last_message.preview,participants,unread,job,...}], total, page_info, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_list_conversations,
            country, locale, folder, last, cursor, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_online_status(
        ctx: Context,
        country: str = "US",
        locale: str = "",
        session_id: str = "",
    ) -> str:
        """Indeed 查询用户在线状态偏好（对招聘方是否可见在线）。
        返回 {is_enabled: bool, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_online_status,
            country, locale, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_preferences(
        ctx: Context,
        country: str = "US",
        session_id: str = "",
    ) -> str:
        """Indeed 读取用户求职偏好（最低薪资/地点/通勤/工作时间等 JSSD 数据）。
        Agent 启动 Indeed 对话时建议先调此工具，把偏好注入上下文，后续搜索按用户偏好过滤。
        返回 {minimum_pay, relocation, maximum_commute, locations,
              positive_attributes, negative_attributes, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_preferences,
            country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_resume_section(
        ctx: Context,
        country: str = "US",
        session_id: str = "",
    ) -> str:
        """Indeed 读取当前登录用户的简历数据（本人资料，不是候选人）。

        用途：在 indeed_apply 之前调用，把返回字段灌给 profileData，
        避免反复询问用户姓名/电话/地址。
        返回 {account_key, contact:{first_name,last_name,phone_number,location},
              resumes:[...], has_resume: bool, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_resume_section,
            country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_competitor_jobs(
        ctx: Context,
        job_key: str,
        limit: int = 15,
        country: str = "US",
        session_id: str = "",
    ) -> str:
        """Indeed 获取同岗位竞品公司的职位推荐（SERP 右侧"Jobs at similar companies"数据源）。

        用途：用户查看某个职位详情后，推荐类似公司的类似岗位，扩大选择面。
        返回 {jobs: [...], tracking_key, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_competitor_jobs,
            job_key, limit, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_new_jobs_count(
        ctx: Context,
        location: str,
        keywords: str = "",
        fromage: str = "last",
        country: str = "US",
        session_id: str = "",
    ) -> str:
        """⚠️ 这**不是**职位搜索工具。仅用于"X 个新职位"提醒策略（告诉用户自上次起有多少新增）。

        **用户想搜工作 / 看职位 → 请用 `indeed_search_jobs`，不要调本工具。**

        本工具的场景很窄：用户问"自从上次搜 xxx 以来有多少新职位"时才用。
        location 必填（Indeed API 硬要求；如果用户说"不限地区"请直接用 indeed_search_jobs）。

        参数: fromage 'last'(自上次)/'1'/'3'/'7'/'14' 天内。
        返回 {new_count, total_count, query_key, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_new_jobs_count,
            location, keywords, fromage, country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_list_applied_jobs(
        ctx: Context,
        start_ms: int = 0,
        session_id: str = "",
    ) -> str:
        """Indeed 列出用户已申请的所有职位（My Jobs → Applied tab）。

        用途：回答"我最近申请了哪些？"、投递前去重、进度跟踪。
        参数: start_ms 过滤起始时间戳（毫秒），0 不限时间。
        返回 {jobs: [{job_key, job_title, company, location, job_url,
                     app_tk, withdrawn, expired, statuses, ...}], total, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_list_applied_jobs,
            start_ms, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_list_interviews(
        ctx: Context,
        start_ms: int = 0,
        session_id: str = "",
    ) -> str:
        """Indeed 列出用户面试（邀请/确认/取消全状态 + IN_PERSON/PHONE/VIDEO 全形式）。

        用途：面试提醒 / 准备日程规划。
        返回 {interviews: [...], total, raw}。空数组表示暂无面试。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_list_interviews,
            None, None, start_ms, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_update_job_app_status(
        ctx: Context,
        jk: str,
        state_payload: str,
        cause: str = "api update",
        session_id: str = "",
    ) -> str:
        """Indeed 更新已申请职位状态（手动标记 NOT_INTERESTED / OFFER / INTERVIEWING 等）。

        用法：先调 indeed_list_applied_jobs 拿到目标 job 的 statuses 结构，
              修改其中 userJobStatus.status 或 selfReportedStatus.status，
              把完整结构 + prev*/new* 字段传给 state_payload（JSON 字符串）。
        state_payload 示例（JSON 字符串）:
          {"statuses":{"userJobStatus":{"status":"POST_APPLY","timestamp":1776667760016},
                       "selfReportedStatus":{"status":"NOT_INTERESTED","timestamp":1776667805444}},
           "prevAppStatusState":"APPLIED","prevAppStatusSource":"CANDIDATE",
           "prevUserJobStatusState":"POST_APPLY",
           "newAppStatusState":"NOT_INTERESTED","newAppStatusSource":"SELF_REPORTED"}
        CSRF 由扩展自动从 cookie 读取。
        返回 {jk, success: bool, raw}。"""
        try:
            parsed = _parse_json_arg(state_payload, "state_payload", {})
        except ValueError as e:
            return _err(str(e))
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_update_job_app_status,
            jk, parsed, cause, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_notifications_count(
        ctx: Context,
        country: str = "US",
        session_id: str = "",
    ) -> str:
        """Indeed 全站通知红点（右上角小铃铛，区别于消息红点 unread_messages）。

        返回 {new_count: int, authenticated: bool, should_show_new: bool, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_notifications_count,
            country, no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_email_preferences(
        ctx: Context,
        session_id: str = "",
    ) -> str:
        """Indeed 查询用户邮件订阅偏好（account_updates / recruiter_invites / major_js 等 category）。

        返回 {preferences: [{category, is_enabled}], email_channel_opted_out, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_email_preferences,
            no_session_hint=_INDEED_NO_SESS,
        )


    @mcp.tool()
    async def indeed_get_unread_offers_count(
        ctx: Context,
        session_id: str = "",
    ) -> str:
        """Indeed 未读招聘方 offer 数（招聘方主动推职位，不同于消息 unread_messages）。

        返回 {unread_offers_count: int, raw}。"""
        return await _run_site_tool(
            session_id, _default_indeed_session, in_cmd.cmd_get_unread_offers_count,
            no_session_hint=_INDEED_NO_SESS,
        )


    # ── Indeed Employer MCP Tools ────────────────────────────────────────────────

    import indeed_employer_commands as ine_cmd  # noqa: E402


    @mcp.tool()
    async def indeed_employer_check_login(
        ctx: Context, session_id: str = "", app_user_id: str = "",
    ) -> str:
        """检查 Indeed 雇主账号登录状态。需要浏览器已登录 employers.indeed.com。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_check_login, site="indeed",
        )


    @mcp.tool()
    async def indeed_employer_list_jobs(
        ctx: Context, limit: int = 20,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """列出当前 Indeed 雇主账号下的职位。返回 employerJobId 供后续搜索候选人。

        参数:
          limit: 最多返回职位数（默认 20）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_list_jobs,
            site="indeed", limit=limit,
        )


    @mcp.tool()
    async def indeed_employer_search_candidates(
        ctx: Context,
        employer_job_id: str,
        dispositions: str = "NEW,PENDING,REVIEWED,PHONE_SCREENED,INTERVIEWED,OFFER_MADE",
        sort_by: str = "APPLY_DATE",
        limit: int = 20,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """搜索某个 Indeed 职位的候选人列表。

        参数:
          employer_job_id: 职位 ID（从 indeed_employer_list_jobs 获取）
          dispositions: 逗号分隔的状态过滤（NEW, PENDING, REVIEWED, PHONE_SCREENED, INTERVIEWED, OFFER_MADE）
          sort_by: 排序方式（APPLY_DATE, MATCH_SCORE）
          limit: 每页数量（默认 20）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_search_candidates,
            site="indeed", employer_job_id=employer_job_id,
            dispositions=dispositions, sort_by=sort_by, limit=limit,
        )


    @mcp.tool()
    async def indeed_employer_get_candidate(
        ctx: Context,
        legacy_id: str = "",
        submission_uuid: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取 Indeed 候选人详情，包括简历附件信息。

        参数:
          legacy_id: 候选人 legacy ID（从搜索结果获取）
          submission_uuid: 候选人提交 UUID（可替代 legacy_id）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_candidate,
            site="indeed", submission_uuid=submission_uuid, legacy_id=legacy_id,
        )


    @mcp.tool()
    async def indeed_employer_download_resume(
        ctx: Context,
        legacy_id: str,
        candidate_name: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """下载 Indeed 候选人简历 PDF。文件会自动保存到服务器，返回保存确认。

        参数:
          legacy_id: 候选人 legacy ID
          candidate_name: 候选人姓名（可选，用于文件命名）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_download_resume,
            site="indeed", legacy_id=legacy_id, candidate_name=candidate_name,
        )


    @mcp.tool()
    async def indeed_employer_update_candidate_status(
        ctx: Context,
        legacy_id: str,
        job_id: str,
        milestone_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """移动候选人到不同招聘阶段。

        参数:
          legacy_id: 候选人 legacy ID（从搜索结果获取）
          job_id: 职位 jobDataId（从 indeed_employer_list_jobs 返回的 jobDataId 字段获取）
          milestone_id: 目标阶段（NEW, REVIEWED, PHONE_SCREENED, INTERVIEWED, OFFER_MADE, HIRED, REJECTED）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_update_candidate_status,
            site="indeed", legacy_id=legacy_id, job_id=job_id,
            milestone_id=milestone_id,
        )


    @mcp.tool()
    async def indeed_employer_get_conversations(
        ctx: Context,
        candidate_key: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取与某候选人的消息记录列表。

        参数:
          candidate_key: 候选人 legacyId（从搜索结果获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_conversations,
            site="indeed", candidate_key=candidate_key,
        )


    @mcp.tool()
    async def indeed_employer_get_screening_summary(
        ctx: Context,
        submission_uuid: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取候选人的 AI 面试状态、身份验证结果和智能筛选问答摘要。

        参数:
          submission_uuid: 候选人提交 UUID（从搜索结果的 submissionUuid 获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_screening_summary,
            site="indeed", submission_uuid=submission_uuid,
        )


    @mcp.tool()
    async def indeed_employer_get_interviews(
        ctx: Context,
        submission_uuid: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """查看候选人的面试安排。

        参数:
          submission_uuid: 候选人提交 UUID（从搜索结果的 submissionUuid 获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_interviews,
            site="indeed", submission_uuid=submission_uuid,
        )


    @mcp.tool()
    async def indeed_employer_get_match_details(
        ctx: Context,
        legacy_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取候选人与职位的详细匹配分析（比搜索结果的 matchHighlights 更详细）。

        参数:
          legacy_id: 候选人 legacy ID（从搜索结果获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_match_details,
            site="indeed", legacy_id=legacy_id,
        )


    @mcp.tool()
    async def indeed_employer_set_candidate_feedback(
        ctx: Context,
        legacy_id: str,
        sentiment: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """给候选人打兴趣标签。用于快速筛选：感兴趣/不感兴趣/待定。

        参数:
          legacy_id: 候选人 legacy ID（从搜索结果获取）
          sentiment: 兴趣标签（YES=感兴趣, NO=不感兴趣, MAYBE=待定）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_set_candidate_feedback,
            site="indeed", legacy_id=legacy_id, sentiment=sentiment,
        )


    @mcp.tool()
    async def indeed_employer_get_screening_answers(
        ctx: Context,
        candidate_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取候选人的筛选问答（雇主设定的教育、语言、经验等筛选问题及候选人回答）。

        参数:
          candidate_id: 候选人 legacyId（从搜索结果获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_screening_answers,
            site="indeed", candidate_id=candidate_id,
        )


    @mcp.tool()
    async def indeed_employer_mark_candidate_viewed(
        ctx: Context,
        submission_uuid: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """标记候选人为已查看（已读回执）。

        参数:
          submission_uuid: 候选人提交 UUID（从搜索结果的 submissionUuid 获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_mark_candidate_viewed,
            site="indeed", submission_uuid=submission_uuid,
        )


    @mcp.tool()
    async def indeed_employer_send_message(
        ctx: Context,
        candidate_key: str,
        message_body: str,
        conversation_id: str = "",
        agg_job_key: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """发送消息给候选人。自动查找已有会话，无会话时创建新会话。

        参数:
          candidate_key: 候选人 legacyId（从搜索结果获取）
          message_body: 消息内容（纯文本）
          conversation_id: 已有会话 ID（可选，跳过查找直接回复）
          agg_job_key: 职位聚合 key（可选，仅首次发消息且无已有会话时需要）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_send_message,
            site="indeed",
            candidate_key=candidate_key, message_body=message_body,
            conversation_id=conversation_id, agg_job_key=agg_job_key,
        )


    @mcp.tool()
    async def indeed_request_compose(
        ctx: Context,
        candidate_key: str,
        target_name: str = "",
        draft_text: str = "",
        intent: str = "message",
    ) -> str:
        """**触发前端 IndeedComposeModal 弹出**。本工具不调任何 Indeed API,
        只是给 agent-gateway 一个信号 → 前端拿到 compose_request SSE 事件,
        弹 IndeedComposeModal,把 draft_text 预填进文本框。

        典型流程(spec 5.3 / 5.4 "发送消息 #N"或"发送面试邀请 #N"chip 触发后):
          1. 用户点 "发送消息 #N" / "发送面试邀请 #N" / "Msg #N" / "Send invite #N"
          2. agent 从搜索 / 申请人结果反查 candidate_key + 名字
          3. agent 起草本次消息(融合候选人简历 / JD 要求;Indeed 无字数限制)
          4. agent 调本工具 indeed_request_compose(...)  ← 触发前端 modal
          5. agent **本轮停下**,不要再调 indeed_employer_send_message
          6. 用户编辑 + 点确认发送 → 前端回流 __indeed_compose_send__:{json}
          7. agent 解析回流消息,调 indeed_employer_send_message 完成发送

        参数:
          candidate_key: 候选人 / 申请人 legacyId(必填)
          target_name: 显示名字(modal 标题用)
          draft_text: AI 起草的初始消息文本
          intent: "message"(普通消息) | "interview_invite"(面试邀请,影响 modal 文案)

        返回 {ok: true, signaled: true}。本工具是 no-op 信号工具,真正的发送
        在 compose 完成后由 indeed_employer_send_message 执行。
        """
        return _ok({
            "signaled": True,
            "candidate_key": candidate_key,
            "intent": intent,
            "note": "Frontend compose modal opened; wait for user to confirm via __indeed_compose_send__ message.",
        })


    @mcp.tool()
    async def indeed_employer_get_conversation_messages(
        ctx: Context,
        conversation_id: str,
        limit: int = 50,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取与候选人的完整消息历史。

        参数:
          conversation_id: 会话 ID（从 indeed_employer_get_conversations 获取）
          limit: 最多返回消息数（默认 50）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_conversation_messages,
            site="indeed", conversation_id=conversation_id, limit=limit,
        )


    @mcp.tool()
    async def indeed_employer_get_message_templates(
        ctx: Context,
        limit: int = 20,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取已保存的消息模板列表。可用于快速回复候选人。

        参数:
          limit: 最多返回模板数（默认 20）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_message_templates,
            site="indeed", limit=limit,
        )


    # ── Indeed Resume Search MCP Tools ──────────────────────────────────────────


    @mcp.tool()
    async def indeed_employer_search_resumes(
        ctx: Context,
        query: str,
        location: str = "",
        employer_job_id: str = "",
        offset: int = 0,
        filters: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """在 Indeed 简历库中按关键词搜索候选人(主动寻源,非已申请的候选人)。
        基于 Smart Sourcing GraphQL(SmartSourcingResultsBFF)。

        参数:
          query: 搜索关键词(如 "equity research", "python developer")
          location: 地点过滤(如 "Remote", "United States", "New York, NY")
          employer_job_id: 关联的职位 ID(可选,用于匹配度排序)
          offset: 分页偏移量(默认 0)
          filters: refinement 筛选 JSON 字符串,格式
            `[{"refinementId": "wa", "values": ["US_ELIGIBLE"]},
              {"refinementId": "availability", "values": ["now"]},
              {"refinementId": "yoe", "values": ["1-11"]},
              {"refinementId": "dt", "values": ["ba"]}]`
            常用 refinementId:
              - `wa`(工作权力):US_ELIGIBLE / WA_UNKNOWN
              - `availability`(到岗):now
              - `yoe`(经验,月数):1-11 / 12-24 / 121
              - `dt`(学历):ba(本科)等
              - `mil`(退伍军人):1
              - `jtid`(历史岗位 ID):numeric

        返回 {rcpRequestId, searchSessionId, totalCount, hasNextPage,
              candidates: [{accountKey, name, location, education, experience,
                           skills, credentials, highlights, ...}],
              refinements: [{key, label, items: [{label, value, count}]}]}

        **重要**:rcpRequestId 是反作弊埋点必填的会话 token —— 调用方应在结果
        返回后立刻 indeed_employer_log_candidate_seen 给 candidate_ids 打曝光,
        否则 Indeed 会限流 / 降低后续搜索质量。建议由 agent_loop 自动 fire,
        不需要 LLM 主动调。
        """
        import json as _json
        filt = []
        if filters:
            try:
                filt = _json.loads(filters)
            except Exception:
                filt = []
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_search_resumes,
            site="indeed", query=query, location=location,
            employer_job_id=employer_job_id, offset=offset, filters=filt,
        )


    @mcp.tool()
    async def indeed_employer_get_talent_engagement(
        ctx: Context,
        candidate_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """查看与某候选人的互动历史（是否已联系过、回复状态等）。

        参数:
          candidate_id: 候选人 accountKey（从 search_resumes 结果获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_talent_engagement,
            site="indeed", candidate_id=candidate_id,
        )


    @mcp.tool()
    async def indeed_employer_log_candidate_seen(
        ctx: Context,
        candidate_ids: str,
        rcp_request_id: str,
        surface: str = "sourcing-search",
        grps: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """**反作弊曝光埋点** —— Smart Sourcing 搜索 + 申请人列表加载后必调,
        标记看到的候选人为"真人浏览"。不调会让 Indeed 限流后续搜索 / 降低匹配质量。

        **典型使用模式 = agent_loop 自动 fire**(不让 LLM 主动决策):
        - 在 `indeed_employer_search_resumes` 返回后,自动 fire,surface=`sourcing-search`
        - 在 `indeed_employer_find_applicants` 返回后,自动 fire,surface=`candidate-list-page`
        两条路径都从工具返回值提取 rcpRequestId + 候选人 ID,fire-and-forget。
        - LLM **不应主动调用本工具** —— 重复调或漏调都不正确。

        参数:
          candidate_ids: JSON 数组字符串,候选人 accountKey / submission legacy_id 列表
          rcp_request_id: 取自最近一次 search_resumes / find_applicants 返回的 rcpRequestId
          surface: "sourcing-search"(简历库主页) | "candidate-list-page"(申请人列表) |
                   "candidate-detail"(详情页);**手动调用**时按场景选对应值
          grps: 可选 JSON 数组字符串,A/B 实验分组
        """
        import json as _json
        try:
            ids = _json.loads(candidate_ids) if isinstance(candidate_ids, str) else candidate_ids
        except Exception:
            return _err("candidate_ids 必须是合法 JSON 数组字符串")
        try:
            grps_list = _json.loads(grps) if grps else []
        except Exception:
            grps_list = []
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_log_candidate_seen,
            site="indeed", candidate_ids=ids, rcp_request_id=rcp_request_id,
            surface=surface, grps=grps_list,
        )


    # ── Spec 5.3 / 5.4 / 5.5 候选人评审 + 申请人筛选 + 消息(7 工具)──


    @mcp.tool()
    async def indeed_employer_get_match_profile(
        ctx: Context,
        job_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """spec 5.3 「分析候选人」匹配可解释性。

        参数:
          job_id: EmployerJob IRI(从 list_jobs 返回的 employerJob.id)
                  或 legacyId(短数字格式,部分场景接受)

        返回 {job_id, fit_qualities: [{id, raw_value, state}]} 含 AI 评估的
        候选人对该岗位各维度的契合度(state 为 STRONG/PARTIAL/MISSING 等)。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_match_profile,
            site="indeed", job_id=job_id,
        )


    @mcp.tool()
    async def indeed_employer_get_candidate_submission(
        ctx: Context,
        submission_id: str,
        first: int = 1,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """spec 5.3 候选人 submission 完整详情(含简历 URL + 岗位关联 + milestone)。
        比 get_candidate 字段更全,前端「查看简历」按钮取这里的 public_url 跳转。

        参数:
          submission_id: candidateSubmission IRI(从 find_applicants 或
                         search_candidates 返回的 submission_id 字段)
          first: 返回的 submission 数(通常传 1)

        返回 {submissions: [{id, legacy_id, submission_uuid, created,
              candidate_name, candidate_phone, milestone, feedback,
              job: {id, title, location, public_url}}]}
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_candidate_submission,
            site="indeed", submission_id=submission_id, first=first,
        )


    @mcp.tool()
    async def indeed_employer_find_applicants(
        ctx: Context,
        employer_job_id: str,
        dispositions: str = "",
        sort_by: str = "APPLY_DATE",
        sort_order: str = "DESCENDING",
        created_after_ms: int = 0,
        limit: int = 20,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """spec 5.4 申请人列表(per-job RCP 匹配,支持按匹配度排序)。
        与 search_resumes 的区别:search_resumes 是简历库主动搜人,本接口是
        "我已发布岗位的申请人列表"。返回的 rcpRequestId 用于后续 log_candidate_seen
        曝光埋点(agent_loop 自动 fire)。

        参数:
          employer_job_id: EmployerJob IRI(从 list_jobs 返回的 employerJob.id)
          dispositions: JSON 数组字符串,disposition 过滤,默认全 6 类:
            `["NEW","PENDING","PHONE_SCREENED","INTERVIEWED","OFFER_MADE","REVIEWED"]`
            spec 5.4 用 NEW + PENDING 即可。
          sort_by: APPLY_DATE / MATCH_SCORE。spec 5.4 "按匹配度从高到低" 用 MATCH_SCORE
          sort_order: DESCENDING / ASCENDING
          created_after_ms: 起始时间戳(ms),0 = 不限
          limit: 默认 20

        返回 {rcpRequestId, searchSessionId, totalCount, applicants: [
              {match_id, submission_id, legacy_id, candidate_name, milestone, created}]}
        """
        import json as _json
        disp_list = []
        if dispositions:
            try:
                disp_list = _json.loads(dispositions)
            except Exception:
                disp_list = []
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_find_applicants,
            site="indeed", employer_job_id=employer_job_id,
            dispositions=disp_list or None,
            sort_by=sort_by, sort_order=sort_order,
            created_after_ms=created_after_ms, limit=limit,
        )


    @mcp.tool()
    async def indeed_employer_get_applicant_filters(
        ctx: Context,
        employer_job_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """spec 5.4 申请人 facet — locations/sentiments/milestones 的 count 分布,
        以及 shortlist_count / undecided_count。用于自动标记前的辅助统计。

        参数:
          employer_job_id: EmployerJob IRI

        返回 {locations: [{value, count}], sentiments: [...],
              milestones: [...], shortlist_count: N, undecided_count: M}
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_applicant_filters,
            site="indeed", employer_job_id=employer_job_id,
        )


    @mcp.tool()
    async def indeed_employer_get_risk_assessment(
        ctx: Context,
        contexts: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """spec 5.4 **自动标记可疑申请**的真信号(替代 LLM 启发式判断)。
        Indeed 官方风险评估 API,返回 action=BLOCK/ALLOW + reason。

        参数:
          contexts: JSON 数组字符串,每项形如
            `[{"type": "CANDIDATE_SUBMISSION", "id": "<submission_iri>"}]`
            支持其他 RiskContext type(如 "ADVERTISER_SEND_MESSAGE")。

        返回 {action: "BLOCK"|"ALLOW"|...,reason: 原因,limit_info: {limit, remaining}}
        action="BLOCK" + reason 是 spec 5.4 "可疑申请" 的标注依据。
        """
        import json as _json
        try:
            ctx_list = _json.loads(contexts)
        except Exception:
            return _err("contexts 必须是合法 JSON 数组字符串")
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_risk_assessment,
            site="indeed", contexts=ctx_list,
        )


    @mcp.tool()
    async def indeed_employer_list_conversations_v2(
        ctx: Context,
        since_ms: int = 0,
        until_ms: int = 0,
        employer_job_id: str = "",
        limit: int = 20,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """spec 5.5 招聘端会话列表 v2(支持 24h/3d/7d 时间窗口过滤)。
        现有 indeed_employer_get_conversations 不支持时间过滤;本工具走
        FindConversations GraphQL,支持 minDateTime/maxDateTime 服务端过滤,
        减少传输量。

        参数:
          since_ms: 起始时间戳(ms),0=不限。spec 5.5 "最近 24 小时" 传 now-86400000
          until_ms: 结束时间戳(ms),0=不限
          employer_job_id: 过滤特定岗位的会话(可选)
          limit: 默认 20

        返回 {conversations: [{conversation_id, created, candidate_key, job_key,
              last_message_ts, last_message_preview, last_event_role, participants}]}
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_list_conversations_v2,
            site="indeed", since_ms=since_ms, until_ms=until_ms,
            employer_job_id=employer_job_id, limit=limit,
        )


    @mcp.tool()
    async def indeed_employer_get_conversation_thread(
        ctx: Context,
        conversation_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """spec 5.5 单 conversation 完整 thread —— 取 last_event + participants
        + scope(candidate_key/job_key/advertiser_key)。"回复 #N" 取上下文用。

        参数:
          conversation_id: 从 list_conversations_v2 返回的 conversation_id

        返回 {conversation_id, created, context, candidate_key, job_key,
              last_event: {author_role, type, preview, published_at},
              participants: [{name, role, access_level}]}
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_conversation_thread,
            site="indeed", conversation_id=conversation_id,
        )


    @mcp.tool()
    async def indeed_employer_search_autocomplete(
        ctx: Context,
        query: str,
        type: str = "keyword",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """搜索关键词或地点的自动补全建议。用于优化简历搜索关键词。

        参数:
          query: 输入的关键词片段
          type: 补全类型（keyword=职位/技能关键词, location=地点）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_search_autocomplete,
            site="indeed", query=query, type=type,
        )


    # ── Indeed Job Posting MCP Tools ────────────────────────────────────────────


    @mcp.tool()
    async def indeed_employer_list_draft_jobs(
        ctx: Context,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """列出所有草稿岗位。返回 draftJobId 和 formId 用于后续编辑和发布。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_list_draft_jobs,
            site="indeed",
        )


    @mcp.tool()
    async def indeed_employer_get_job_form(
        ctx: Context,
        draft_job_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """加载岗位发布表单当前状态，查看已填写的字段。

        参数:
          draft_job_id: 草稿岗位 ID（从 list_draft_jobs 获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_get_job_form,
            site="indeed", draft_job_id=draft_job_id,
        )


    @mcp.tool()
    async def indeed_employer_update_job_form(
        ctx: Context,
        form_id: str,
        patch: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """更新岗位发布表单字段。

        参数:
          form_id: 表单 ID（从 get_job_form 返回的 formId 获取）
          patch: JSON 字符串，包含要更新的字段。支持的字段:
            - title: {"title": "岗位标题"}
            - description: {"description": "岗位描述"}
            - jobLocation: {"location": "城市", "roleLocationType": "REMOTE_WORK_FROM_HOME"}
            - pay: {"minimumMinor": 7000000, "maximumMinor": 15000000, "period": "YEAR", "type": "RANGE"}
            - jobType: {"types": ["CF3CP"]}
            - benefits: {"benefits": ["YDH5H"], "benefitsOptOut": false}
            - hiresNeeded: {"hiresNeeded": 1}
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_update_job_form,
            site="indeed", form_id=form_id, patch=patch,
        )


    @mcp.tool()
    async def indeed_employer_publish_job(
        ctx: Context,
        form_id: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """发布岗位（将草稿变为正式发布状态）。

        参数:
          form_id: 表单 ID（从 get_job_form 返回的 formId 获取）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_publish_job,
            site="indeed", form_id=form_id,
        )


    @mcp.tool()
    async def indeed_employer_optimize_job_description(
        ctx: Context,
        draft_job_id: str,
        title: str,
        language: str = "en",
        country: str = "US",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取 AI 优化的岗位描述建议。

        参数:
          draft_job_id: 草稿岗位 ID
          title: 岗位标题（用于生成上下文相关的描述）
          language: 语言（默认 en）
          country: 国家（默认 US）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, ine_cmd.cmd_optimize_job_description,
            site="indeed", draft_job_id=draft_job_id, title=title,
            language=language, country=country,
        )


    # ── DOM 视觉 + 点击 + 导航（v1.6 新增）─────────────────────────────────

    @mcp.tool()
    async def indeed_get_clickables(
        ctx: Context, root_selector: str = "body",
        include_hidden: bool = False, max_items: int = 200,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """回传 Indeed Worker Tab 当前页所有可点击元素（idx + selector + text + rect）。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_clickables, site="indeed",
            root_selector=root_selector, include_hidden=include_hidden,
            max_items=max_items,
        )

    @mcp.tool()
    async def indeed_get_dom_snapshot(
        ctx: Context, root_selector: str = "body",
        max_depth: int = 6, max_nodes: int = 500, include_text: bool = True,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """回传 Indeed Worker Tab 完整 DOM 树（截断）。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_dom_snapshot, site="indeed",
            root_selector=root_selector, max_depth=max_depth,
            max_nodes=max_nodes, include_text=include_text,
        )

    @mcp.tool()
    async def indeed_click_by_idx(
        ctx: Context, snapshot_id: str, idx: int,
        timeout_ms: int = 5000, fallback_text: bool = True,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """用 indeed_get_clickables 拿到的 snapshot_id + idx 点击。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_click_by_idx, site="indeed",
            snapshot_id=snapshot_id, idx=idx,
            timeout_ms=timeout_ms, fallback_text=fallback_text,
        )

    @mcp.tool()
    async def indeed_click_by_text(
        ctx: Context, text: str, tag: str = "", exact: bool = False,
        root_selector: str = "body", timeout_ms: int = 5000, nth: int = 0,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """按页面可见文本点击。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_click_by_text, site="indeed",
            text=text, tag=tag, exact=exact,
            root_selector=root_selector, timeout_ms=timeout_ms, nth=nth,
        )

    @mcp.tool()
    async def indeed_wait_for(
        ctx: Context, selector: str, timeout_ms: int = 10000,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """等元素出现。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_wait_for_element, site="indeed",
            selector=selector, timeout_ms=timeout_ms,
        )

    @mcp.tool()
    async def indeed_navigate_to(
        ctx: Context, url: str, wait_for_selector: str = "",
        timeout_ms: int = 15000,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """导航 Indeed Worker Tab 到指定 URL（host 必须 *.indeed.com）。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_navigate_to, site="indeed",
            url=url, wait_for_selector=wait_for_selector,
            timeout_ms=timeout_ms,
        )


