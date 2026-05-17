"""
mcp_tools_linkedin.py — LinkedIn MCP tool 定义。
"""
from __future__ import annotations

import logging

from fastmcp import Context

import db
from server_helpers import (
    _ok, _err, _get_agent_id, _resolve_and_bind, _current_user_id,
    _run_boss_tool, _run_site_tool, _parse_json_arg,
)
from session_store import session_store
import linkedin_commands as li_cmd
from commands import (
    cmd_get_clickables,
    cmd_get_dom_snapshot,
    cmd_click_by_idx,
    cmd_click_by_text,
    cmd_wait_for_element,
    cmd_navigate_to,
)

log = logging.getLogger(__name__)


def _default_li_session() -> str:
    """按当前 caller (DINQ user_id) 定位其本人的 LinkedIn 扩展会话。

    **多租户安全**：严格按 user_id 精确匹配，**不会 fallback 到其他用户的 session**。
    找不到时返回 ""，由调用方报错(并打印 INFO 日志说明根因)。

    优先级:
      1. 若 caller 在 linkedin 平台设了 active session(set_active_session)且仍在线,用它
      2. 否则遍历该 user_id 的已连接会话,返回第一个支持 linkedin 的
    """
    caller_uid = _current_user_id.get()
    if not caller_uid:
        log.info("[li-session] 无 caller_uid:x-user-id header 缺失,可能 DINQ 未登录或 agent-gateway 未透传")
        return ""
    # 优先 active session(E1)
    pinned = session_store.get_active_session(caller_uid, "linkedin")
    if pinned:
        return pinned
    # 诊断遍历 —— 收集失败原因,失败时打印一行结构化日志
    total = 0
    user_match = 0
    user_match_ws_alive = 0
    user_match_supports_li = 0
    sample_other_uids: list[str] = []
    for entry in session_store._sessions.values():
        total += 1
        if entry.user_id != caller_uid:
            if len(sample_other_uids) < 3 and entry.user_id:
                sample_other_uids.append(entry.user_id[:8])
            continue
        user_match += 1
        if entry.ws is None:
            continue
        user_match_ws_alive += 1
        if entry.sites and "linkedin" not in entry.sites:
            continue
        user_match_supports_li += 1
        return entry.session_id
    log.info(
        "[li-session] miss caller_uid=%s sessions_total=%d user_match=%d "
        "user_match_ws_alive=%d user_match_supports_li=%d other_uids=%s",
        caller_uid[:8], total, user_match, user_match_ws_alive,
        user_match_supports_li, sample_other_uids,
    )
    return ""


def _li_session_error_hint() -> str:
    """根据 _default_li_session() 失败的 caller 状态给出更精确的修复提示。"""
    caller_uid = _current_user_id.get()
    if not caller_uid:
        return (
            "未识别到 DINQ 登录身份(请求未携带 x-user-id)。"
            "请确认你已在 dinq.me 登录,刷新页面后重试。"
        )
    # 看下用户的 session 状态
    own_sessions = [e for e in session_store._sessions.values() if e.user_id == caller_uid]
    if not own_sessions:
        return (
            f"未找到属于当前 DINQ 用户(uid={caller_uid[:8]}...)的扩展会话。"
            "请打开浏览器扩展 popup,确认显示「已连接控制台」+「DINQ 账号」一致;"
            "若不一致可点扩展刷新按钮,或重新登录 DINQ 让扩展自动重连网关。"
        )
    alive = [e for e in own_sessions if e.ws is not None]
    if not alive:
        return (
            "扩展会话已注册但 WebSocket 已断开,正在等待自动重连。"
            "若长时间未恢复,请关闭再打开扩展 popup 触发重连。"
        )
    li_capable = [e for e in alive if (not e.sites) or "linkedin" in e.sites]
    if not li_capable:
        return (
            "当前扩展会话未上报 LinkedIn 能力(可能扩展版本过旧)。"
            "请升级 job-seeker-ext 到 1.5.7+(扩展 popup 顶部右键→详情查看版本号)。"
        )
    return "未找到匹配的扩展会话,请确认已登录 DINQ 且浏览器扩展已连接网关。"


def register(mcp):
    """注册所有 LinkedIn MCP tools。"""


    @mcp.tool()
    async def linkedin_check_login(
        ctx: Context, session_id: str = "", force_reset: bool = False,
    ) -> str:
        """检查 LinkedIn 登录状态。返回当前账号信息（memberId, name）或未登录提示。需先安装 linkedin-api-ext 扩展。

        force_reset=True 时清掉扩展侧的 region-blocked 缓存(用户开 VPN 后用),
        通常仅在收到 region_blocked: true 后用户主动点 chip 重试时使用。
        """
        # no_session_hint 传 callable —— 失败时才按当前 session_store 状态算精细化
        # 诊断,区分"无 user_id" / "无 session" / "session 已断" / "不支持 linkedin"
        return await _run_site_tool(
            session_id, _default_li_session,
            lambda sid: li_cmd.cmd_check_login(sid, force_reset=force_reset),
            no_session_hint=_li_session_error_hint,
        )


    @mcp.tool()
    async def linkedin_search_jobs(
        ctx: Context,
        keywords: str,
        geo_location_id: str = "",
        page: int = 1,
        page_size: int = 25,
        session_id: str = "",
    ) -> str:
        """搜索 LinkedIn 职位。

        keywords: 职位关键词（支持中英文）
        geo_location_id: LinkedIn geoUrn 数字 id，空串则不加地域过滤
          常用：'103644278'=美国, '102890883'=中国, '101174742'=加拿大,
               '101452733'=澳大利亚, '90000070'=上海, '90009496'=北京
        page: 页码（1 起），page_size: 每页条数（默认 25，上限 25）
        返回 { jobs: [{ jobId, jobUrn, title, companyName, location, listedAt }], total, tokens_captured }"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_search_jobs,
            keywords, geo_location_id, page, page_size,
        )


    @mcp.tool()
    async def linkedin_get_job_detail(ctx: Context, job_id: str, session_id: str = "") -> str:
        """查看 LinkedIn 职位详情（只读；不会打开 Easy Apply 表单 / 不会触发投递）。

        **这是查看职位详情的唯一入口**。绝对不要用 linkedin_get_apply_form 代替——
        后者只用于用户明确要投递时预览表单结构。

        job_id: 纯数字职位 ID（从 linkedin_search_jobs 结果的 jobId 字段提取）
        返回: {jobId, raw: {...}} 含标题、描述、薪资、公司、地点等完整字段。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_get_job_detail, job_id,
        )


    @mcp.tool()
    async def linkedin_check_applied(ctx: Context, job_id: str, session_id: str = "") -> str:
        """查询某 LinkedIn 职位是否已投递过（批量投递去重的 guard rail）。

        返回 {applied: true | false | null, job_id, note?}
          - applied=true: 已投过，**直接跳过 linkedin_apply_job 调用**
          - applied=false: 未投，可以继续投递流程
          - applied=null: 响应里无法确定，**谨慎处理**（如再追加一次 get_job_detail）

        典型用法（批量投递）：
          for jid in to_apply:
            r = linkedin_check_applied(jid)
            if r.applied: skip
            else: linkedin_apply_job(jid, ...)
        """
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_check_applied, job_id,
        )


    # ── LinkedIn 缓存读工具（镜像 Boss） ───────────────────────────────────

    def _li_compact_job(j: dict) -> dict:
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
    async def linkedin_list_cached_jobs(
        ctx: Context,
        keyword: str = "",
        city_code: str = "",
        has_detail: bool = False,
        fresh_within_days: int = 0,
        include_expired: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """LinkedIn 列出本地已缓存的职位（之前搜索 / 查详情过的）。免费,不调 live API。

        **批量"分析职位"前应先调本工具查缓存**，避免触发 LinkedIn 查询频次限制。
        参数:
          keyword: 关键字过滤（title / company 模糊匹配）
          city_code: 地域过滤（LinkedIn geoLocationId，如 '103644278' 美国）
          has_detail: true=只返回已抓取详情的职位（直接可用），false=全部
          fresh_within_days: 只返回最近 N 天内抓取的条目（0=不过滤，默认）
          include_expired: 是否包含已过期岗位（默认 false 自动剔除）
          limit: 每页数量（上限 100）
        返回 {jobs: [{external_id=jobId, title, company, city, has_detail, ...}], total}
        """
        try:
            jobs = await db.list_cached_jobs(
                platform="linkedin",
                keyword=keyword or None,
                city_code=city_code or None,
                has_detail=has_detail if has_detail else None,
                fresh_within_days=fresh_within_days if fresh_within_days > 0 else None,
                include_expired=include_expired,
                limit=min(limit, 100),
                offset=offset,
            )
            compact = [_li_compact_job(j) for j in jobs]
            return _ok({"jobs": compact, "total": len(compact)})
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def linkedin_get_cached_job(ctx: Context, job_id: str, session_id: str = "") -> str:
        """LinkedIn 读取单个缓存职位（含 description 若已抓详情）。免费,不调 live。

        **优先于 linkedin_get_job_detail 使用**：命中即可免 live 请求。
        job_id: LinkedIn 数字 jobId。缓存未命中返回错误。
        返回: {external_id, title, company, city, has_detail, description, raw_list, raw_detail, fetched_at}
        """
        try:
            job = await db.get_cached_job("linkedin", str(job_id))
            if not job:
                return _err(
                    f"缓存中未找到 LinkedIn 职位 {job_id}，"
                    "请先调用 linkedin_search_jobs 或 linkedin_get_job_detail 拉取。"
                )
            return _ok(job)
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def linkedin_apply_job(ctx: Context, job_id: str, profile_data: str = "",
                                 session_id: str = "") -> str:
        """LinkedIn Easy Apply 自动投递（支持多步表单自动填写）。
        job_id: 纯数字职位 ID。
        profile_data: JSON 字符串，含 email/phone/firstName/lastName/city 等用户资料。留空则不预填。
        返回 {ok, status, steps}。若 status='unresolved_fields'，需调用 linkedin_fill_fields 补填后继续。"""
        try:
            pd = _parse_json_arg(profile_data, "profile_data", {})
        except ValueError as e:
            return _err(str(e))
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_apply, job_id, pd,
        )


    @mcp.tool()
    async def linkedin_get_apply_form(ctx: Context, job_id: str, session_id: str = "") -> str:
        """预览 LinkedIn Easy Apply 表单结构（用户明确要投递前的准备步骤，不自动填写）。

        **重要**：这不是"查看职位详情"的工具。查看职位详情请用 linkedin_get_job_detail。
        本工具会打开 Easy Apply 浮层（耗时 10-25s），只在用户已明确要投递该职位、
        需要预览表单字段时使用。

        返回 {fields: [{selector, label, type, required, options}], buttons: {next, submit, review}}。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_get_apply_form, job_id,
        )


    @mcp.tool()
    async def linkedin_fill_fields(ctx: Context, actions: str, session_id: str = "") -> str:
        """按指令填写 LinkedIn Easy Apply 表单字段。
        actions: JSON 数组 [{selector, value, type}]。用于处理 linkedin_apply_job 返回的 unresolved_fields。"""
        try:
            acts = _parse_json_arg(actions, "actions", [])
        except ValueError as e:
            return _err(str(e))
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_fill_fields, acts,
        )


    @mcp.tool()
    async def linkedin_search_candidates(
        ctx: Context,
        keywords: str,
        start: int = 0,
        count: int = 10,
        session_id: str = "",
    ) -> str:
        """搜索 LinkedIn 用户 Profile 列表（按关键词）。

        keywords: 技能/职位关键词；
        start: 分页偏移，首页 0；
        count: 每页数量（默认 10，上限 25）。
        返回 Profile 列表含 memberUrn、name、publicId、trackingId（用于后续 connect/send_message）。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_search_candidates,
            keywords, start, count,
        )


    @mcp.tool()
    async def linkedin_get_profile(ctx: Context, public_id: str, session_id: str = "") -> str:
        """获取 LinkedIn 用户完整 Profile。

        public_id 来源(必须从已有上下文取,不要瞎编):
          - 上一次 linkedin_search_candidates 返回的 people[i].publicId
          - 用户消息里粘贴的 LinkedIn URL `/in/{publicId}/` 的 publicId 段
          - 之前对话里出现过的 publicId

        例如用户说 "查看 #4 Zerui Wang",从 search 结果 people[3].publicId
        取(注意 0-indexed)。绝对不要传空字符串或 LinkedIn URL 全文。
        """
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_get_profile, public_id,
        )


    @mcp.tool()
    async def linkedin_preview_profile(
        ctx: Context, public_id: str, session_id: str = "",
    ) -> str:
        """LinkedIn 候选人紧凑 profile（Recruiter 批量初筛场景用）。

        和 linkedin_get_profile 调同一后端 API，但只返回核心字段，体积约 10% —
        Recruiter 一次性看 10-20 个候选人时显著省 token / 不爆 context。

        返回字段（E2 跨平台统一 shape）：
          {name, headline, current_role, current_company, location, industry,
           years, education, skills[], summary, public_id, profile_url, positions[]}

        适用时机：
          - 批量评估多个候选人时先 preview → 选中感兴趣的再 linkedin_get_profile 看全貌
          - Recruiter mode 的候选人评估流程

        不适用：需要完整工作经历 / 所有技能 / 推荐信等深度内容时 → 用 linkedin_get_profile。
        """
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_preview_profile, public_id,
        )


    @mcp.tool()
    async def linkedin_connect(
        ctx: Context,
        member_urn: str,
        message: str = "",
        session_id: str = "",
    ) -> str:
        """LinkedIn 发送好友邀请（connect request）。

        ⚠️ 必须先调 linkedin_search_candidates 或 linkedin_search_people 捕获 trackingId（扩展端自动从 tokenStore 取用）。
        member_urn: 目标用户的 memberUrn（从搜索结果提取）；
        message: 邀请附言，留空则发默认邀请。
        返回 {raw}。典型用于招聘 outreach 或建立 1st-degree 连接后再发消息。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_connect, member_urn, message,
        )


    @mcp.tool()
    async def linkedin_get_connection_degree(
        ctx: Context,
        member_urn: str = "",
        public_id: str = "",
        session_id: str = "",
    ) -> str:
        """LinkedIn 查询与目标用户的连接程度（1st / 2nd / 3rd / None）。

        用途：决定用"普通消息"（1st 度好友）还是"InMail"（2nd+ 度，需付费）发消息。
        member_urn 或 public_id 至少传一个。传 member_urn 时扩展从 tokenStore 反查 publicId（
        需先调 linkedin_search_candidates）；传 public_id 则直接使用。
        返回 {degree: int, connectionType: str, raw}。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_get_connection_degree,
            member_urn, public_id,
        )


    @mcp.tool()
    async def linkedin_send_message(
        ctx: Context,
        member_id: str,
        text: str,
        session_id: str = "",
    ) -> str:
        """向 LinkedIn 用户**发起新会话** / 发 InMail（区别于 reply_to_conversation）。
        member_id: 目标用户的数字 memberId（从搜索结果提取）；text: 消息内容。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_send_message, member_id, text,
        )


    @mcp.tool()
    async def linkedin_request_compose(
        ctx: Context,
        member_urn: str,
        target_name: str = "",
        connection_degree: int = 0,
        draft_text: str = "",
    ) -> str:
        """**触发前端 compose modal 弹出**。本工具不调任何 LinkedIn API,只是给
        agent-gateway 一个信号 → 前端拿到 compose_request SSE 事件,弹
        LinkedinComposeModal,把 draft_text 预填进文本框。

        典型流程(spec 4.3 / 5.3 "消息 #N"chip 触发后):
          1. 用户点 "消息招聘经理 #N" / "消息 #N" / "Msg recruiter #N"
          2. agent 从搜索结果反查 member_urn
          3. agent 调 linkedin_get_connection_degree → 拿到 connection_degree
          4. agent 起草本次消息(融合简历 / JD / 候选人 profile)
          5. agent 调本工具 linkedin_request_compose(...)  ← 触发前端 modal
          6. agent **本轮停下**,不要再调 send_message / connect
          7. 用户编辑 + 点确认发送 → 前端回流 __linkedin_compose_send__:{json}
          8. agent 解析回流消息,按 connection_degree 调 send_message(=1) 或
             connect(>=2) 完成发送

        参数:
          member_urn: 目标用户 memberUrn(必填)
          target_name: 显示名(modal 标题用,可选)
          connection_degree: 连接度 1=已连(DM)/2/3=未连(connection request 300 char)/0=未知
          draft_text: AI 起草的初始消息文本

        返回 {ok: true, signaled: true}。本工具是 no-op 信号工具,真正的发送在
        compose 完成后由 send_message / connect 执行。
        """
        # No-op:agent_loop.py 监听本工具调用,在工具结果之外额外 emit 一个
        # compose_request SSE 事件给前端。这里直接返回 ok 让 LLM 知道 modal
        # 已弹出,可以停下等用户确认。
        return _ok({
            "signaled": True,
            "member_urn": member_urn,
            "connection_degree": connection_degree,
            "note": "Frontend compose modal opened; wait for user to confirm via __linkedin_compose_send__ message.",
        })


    # ── LinkedIn Messaging（新 Messenger 框架：读列表/回复/管理） ────────────────


    @mcp.tool()
    async def linkedin_list_mailboxes(
        ctx: Context, session_id: str = "",
    ) -> str:
        """LinkedIn 列出所有 mailbox：普通（primary）/ Recruiter / Page admin。

        用途：读消息前必须先知道 mailbox_urn。对 Recruiter 用户尤其重要
             （普通 mailbox 和 Recruiter mailbox 的消息是分开的）。
        返回 {mailboxes: [{kind, headline, unread_count_total}], total, raw}。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_list_mailboxes,
        )


    @mcp.tool()
    async def linkedin_list_conversations(
        ctx: Context,
        mailbox_urn: str = "",
        sync_token: str = "",
        count: int = 20,
        session_id: str = "",
    ) -> str:
        """LinkedIn 读消息会话列表（Messenger GraphQL）。

        **普通用户查消息直接调本工具即可**，不需要先调 linkedin_list_mailboxes。

        mailbox_urn: 留空 → 自动使用当前登录用户的 primary 收件箱。
                     **只在用户是 Recruiter 或 Page 管理员、需要切换 mailbox 时才显式传入**
                     （从 linkedin_list_mailboxes 的返回里取）。
        sync_token: 增量同步 token，首次传 ''，下次传上次响应的 new_sync_token 实现增量拉取。
        返回 {conversations: [{conversation_urn, last_activity_ms, unread_count,
                              participants_urns, title}], total, new_sync_token,
              deleted_urns, raw}。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_list_conversations,
            mailbox_urn, sync_token, count,
        )


    @mcp.tool()
    async def linkedin_get_conversation_messages(
        ctx: Context,
        conversation_urn: str,
        count: int = 20,
        session_id: str = "",
    ) -> str:
        """LinkedIn 读取某会话的历史消息内容。

        conversation_urn: 从 linkedin_list_conversations 返回的 conversation_urn。
        返回 {messages: [{message_urn, sender_urn, text, delivered_at}], total, raw}。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_get_conversation_messages,
            conversation_urn, count,
        )


    # ── 2026-04-29 抓包对齐 Boss 求职 Step 2B(查看最近消息子流程) ──────────


    @mcp.tool()
    async def linkedin_list_inbox_counts(
        ctx: Context,
        mailbox_urn: str = "",
        session_id: str = "",
    ) -> str:
        """LinkedIn 主收件箱分类未读计数(对齐 Boss geek_message_center_summary)。

        返回 counts dict,key 为 category 枚举:
          - PRIMARY_INBOX:主收件箱
          - SECONDARY_INBOX:Other 标签(广告 / spam)
          - JOB:求职相关(招聘官 / 申请回执)

        mailbox_urn: 留空 → 自动用本人 primary mailbox(linkedin_check_login 时已落 store)。
        """
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_list_inbox_counts,
            mailbox_urn,
        )


    @mcp.tool()
    async def linkedin_list_conversations_filtered(
        ctx: Context,
        categories: str = "PRIMARY_INBOX",
        count: int = 20,
        read: str = "",
        first_degree_connections: str = "",
        next_cursor: str = "",
        mailbox_urn: str = "",
        session_id: str = "",
    ) -> str:
        """LinkedIn 按标签筛选会话(对齐 Boss geek_filter_by_label,支持精细化过滤)。

        ⭐ 求职 Step 2B "查看最近消息" 子流程主路径。

        过滤维度映射 Boss label_id 0-5:
          - categories="PRIMARY_INBOX"                          → 主收件箱(默认,Boss label_id=0 全部)
          - categories="PRIMARY_INBOX", read="false"            → 仅未读(Boss label_id=1 新招呼)
          - categories="PRIMARY_INBOX", first_degree_connections="true"  → 1 度好友(Boss label_id=2 仅沟通)
          - categories="PRIMARY_INBOX,JOB"                      → 求职相关(LinkedIn 自动归类的招聘官消息)
          - categories="SECONDARY_INBOX"                         → Other 标签(广告 / 不重要)

        参数:
          categories: 逗号分隔多 category。值: PRIMARY_INBOX / SECONDARY_INBOX / JOB
          count: 每页 20(LinkedIn 最大约 20)
          read: '' / 'true' / 'false'
          first_degree_connections: '' / 'true' / 'false'
          next_cursor: 翻页 cursor,从上次返回 next_cursor 取(首页留空)
          mailbox_urn: 留空 → 自动用本人 primary mailbox

        返回 {conversations[], total, hasMore, next_cursor, mailbox_urn, raw}。
        每条 conversation 含: conversation_urn, conversation_url, title, headline,
        last_activity_ms, last_read_ms, unread_count, read, group_chat,
        categories[], participants_urns[]。
        """
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_list_conversations_filtered,
            mailbox_urn, categories, count, read, first_degree_connections, next_cursor,
        )


    @mcp.tool()
    async def linkedin_precheck_compose(
        ctx: Context,
        recipient_urn: str,
        conversation_urn: str = "",
        type: str = "REPLY",
        session_id: str = "",
    ) -> str:
        """LinkedIn 发消息前的预检(LinkedIn 独有 — Boss 没等价)。

        判断能否向 recipient_urn 发消息:是否被拉黑 / 是否需要 InMail / 是否
        触发 trust intervention。建议在打开前端 LinkedinComposeModal 前调一次。

        参数:
          recipient_urn: 接收方 urn:li:fsd_profile:ACoA... 形态(从 search_people /
                         list_conversations_filtered 的 participants_urns 取)
          conversation_urn: 已有会话 URN(REPLY 类型必传;NEW 类型留空)
          type: 'NEW'(新发起) / 'REPLY'(回复已有会话) / 'INMAIL'

        返回:
          can_send: bool — 综合判断能否发
          blocked: bool — 对方拉黑了你
          trust_intervention: bool — LinkedIn 风控需用户在网页上人工干预
          show_subject: bool — InMail 需要主题字段
          invitation_text / header_text / footer_text: LinkedIn 给的引导文案(可空)
        """
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_precheck_compose,
            recipient_urn, conversation_urn, type,
        )


    @mcp.tool()
    async def linkedin_reply_to_conversation(
        ctx: Context,
        conversation_urn: str,
        text: str,
        mailbox_urn: str = "",
        origin_token: str = "",
        session_id: str = "",
    ) -> str:
        """LinkedIn 在**已有会话**里回复消息（对标 reply 按钮）。

        ⚠️ 区别于 linkedin_send_message：后者是"发起新 InMail"，本工具是"在现有对话里回复"。
        conversation_urn: 从 linkedin_list_conversations 返回取。
        text: 回复内容。
        mailbox_urn: 留空 → 自动使用当前登录用户的 primary 收件箱；
                     仅 Recruiter / Page 管理员回复时才显式传入。
        origin_token: 幂等键，留空扩展自动生成 UUID（重试时传同一 token 避免重发）。
        返回 {message_urn, conversation_urn, raw}。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_reply_to_conversation,
            conversation_urn, text, mailbox_urn, origin_token,
        )


    @mcp.tool()
    async def linkedin_mark_messages_seen(
        ctx: Context,
        until_ms: int = 0,
        session_id: str = "",
    ) -> str:
        """LinkedIn 标记所有消息为已读（清除消息红点）。

        until_ms: 时间戳上限（毫秒），只标该时间之前；0 表示当前时间。
        用途：agent 读完用户消息 + 回复后，避免用户仍然看到红点。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_mark_messages_seen, until_ms,
        )


    @mcp.tool()
    async def linkedin_get_user_presence(
        ctx: Context,
        profile_urns: str,
        session_id: str = "",
    ) -> str:
        """LinkedIn 查询多个用户的在线状态。

        profile_urns: JSON 字符串，形如 '["urn:li:fsd_profile:XXX", ...]'。
        用途：发消息前判断对方是否在线/上次活跃时间。
        返回 {statuses: {urn: {available, last_active_at, instantly_reachable}}, raw}。"""
        try:
            urns = _parse_json_arg(profile_urns, "profile_urns", [])
        except ValueError as e:
            return _err(str(e))
        if not isinstance(urns, list) or not urns:
            return _err("profile_urns 必须为非空数组")
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_get_user_presence, urns,
        )


    @mcp.tool()
    async def linkedin_get_my_email_handles(
        ctx: Context,
        primary_only: bool = True,
        session_id: str = "",
    ) -> str:
        """LinkedIn 获取当前登录用户的邮箱列表（驱动 apply 自动填表）。

        对标 indeed_get_resume_section 的角色。
        返回 {emails: [{email, is_primary, is_verified}], total, raw}。"""
        return await _run_site_tool(
            session_id, _default_li_session, li_cmd.cmd_get_my_email_handles, primary_only,
        )


    # ── LinkedIn Recruiter (Talent Solutions) MCP Tools ──────────────────────────

    @mcp.tool()
    async def linkedin_recruiter_list_projects(
        ctx: Context, session_id: str = "", app_user_id: str = "",
    ) -> str:
        """列出 LinkedIn Recruiter 所有招聘项目。返回 projectUrn 供后续搜索使用。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, li_cmd.cmd_recruiter_list_projects,
            site="linkedin", pass_agent_id=False,
        )

    @mcp.tool()
    async def linkedin_recruiter_search(
        ctx: Context,
        project_urn: str,
        keywords: str = "",
        titles: str = "",
        start: int = 0,
        count: int = 25,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """LinkedIn Recruiter 搜索候选人（返回完整资料：姓名、职位、教育、联系方式）。

        参数:
          project_urn: 招聘项目 URN（从 linkedin_recruiter_list_projects 获取）
          keywords: 搜索关键词（如 "equity trader"）
          titles: 职位名称过滤，逗号分隔（可选）
          start: 分页起始位置（默认 0）
          count: 每页数量（默认 25）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, li_cmd.cmd_recruiter_search,
            site="linkedin", pass_agent_id=False,
            project_urn=project_urn, keywords=keywords,
            titles=titles, start=start, count=count,
        )

    @mcp.tool()
    async def linkedin_recruiter_get_profile(
        ctx: Context,
        profile_urn: str,
        project_urn: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取 LinkedIn Recruiter 候选人详细资料（工作经历、教育、联系方式等）。

        参数:
          profile_urn: 候选人 profile URN（从搜索结果获取）
          project_urn: 招聘项目 URN（可选，提供上下文）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, li_cmd.cmd_recruiter_get_profile,
            site="linkedin", pass_agent_id=False,
            profile_urn=profile_urn, project_urn=project_urn,
        )

    @mcp.tool()
    async def linkedin_recruiter_send_inmail(
        ctx: Context,
        recipient_profile_urn: str,
        subject: str,
        body: str,
        hiring_project_urn: str = "",
        sourcing_channel_urn: str = "",
        signature: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """通过 LinkedIn Recruiter 发送 InMail 消息。

        参数:
          recipient_profile_urn: 收件人 profile URN（从搜索结果获取）
          subject: 邮件主题
          body: 邮件正文
          hiring_project_urn: 招聘项目 URN（可选）
          sourcing_channel_urn: 寻源渠道 URN（可选）
          signature: 签名（可选）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, li_cmd.cmd_recruiter_send_inmail,
            site="linkedin", pass_agent_id=False,
            recipient_profile_urn=recipient_profile_urn,
            subject=subject, body=body,
            hiring_project_urn=hiring_project_urn,
            sourcing_channel_urn=sourcing_channel_urn,
            signature=signature,
        )

    @mcp.tool()
    async def linkedin_recruiter_add_to_project(
        ctx: Context,
        candidate_urn: str,
        hiring_project_urn: str,
        sourcing_channel_urn: str,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """将候选人添加到 LinkedIn Recruiter 招聘项目。

        参数:
          candidate_urn: 候选人 URN
          hiring_project_urn: 招聘项目 URN
          sourcing_channel_urn: 寻源渠道 URN
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, li_cmd.cmd_recruiter_add_to_project,
            site="linkedin", pass_agent_id=False,
            candidate_urn=candidate_urn,
            hiring_project_urn=hiring_project_urn,
            sourcing_channel_urn=sourcing_channel_urn,
        )

    @mcp.tool()
    async def linkedin_recruiter_search_facets(
        ctx: Context,
        project_urn: str,
        facet_types: str = "",
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """获取 LinkedIn Recruiter 搜索的可用筛选项（职位、经验、地区、公司、技能等）。

        参数:
          project_urn: 招聘项目 URN
          facet_types: 筛选类型，逗号分隔（默认全部）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, li_cmd.cmd_recruiter_search_facets,
            site="linkedin", pass_agent_id=False,
            project_urn=project_urn, facet_types=facet_types,
        )


    # ── DOM 视觉 + 点击 + 导航（v1.6 新增）─────────────────────────────────

    @mcp.tool()
    async def linkedin_get_clickables(
        ctx: Context, root_selector: str = "body",
        include_hidden: bool = False, max_items: int = 200,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """回传 LinkedIn Worker Tab 当前页所有可点击元素（idx + selector + text + rect）。
        snapshot_id 5s 内可用 linkedin_click_by_idx 点击。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_clickables, site="linkedin",
            root_selector=root_selector, include_hidden=include_hidden,
            max_items=max_items,
        )

    @mcp.tool()
    async def linkedin_get_dom_snapshot(
        ctx: Context, root_selector: str = "body",
        max_depth: int = 6, max_nodes: int = 500, include_text: bool = True,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """回传 LinkedIn Worker Tab 完整 DOM 树（截断）。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_dom_snapshot, site="linkedin",
            root_selector=root_selector, max_depth=max_depth,
            max_nodes=max_nodes, include_text=include_text,
        )

    @mcp.tool()
    async def linkedin_click_by_idx(
        ctx: Context, snapshot_id: str, idx: int,
        timeout_ms: int = 5000, fallback_text: bool = True,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """用 linkedin_get_clickables 拿到的 snapshot_id + idx 点击元素。
        selector 失效时自动 text fallback。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_click_by_idx, site="linkedin",
            snapshot_id=snapshot_id, idx=idx,
            timeout_ms=timeout_ms, fallback_text=fallback_text,
        )

    @mcp.tool()
    async def linkedin_click_by_text(
        ctx: Context, text: str, tag: str = "", exact: bool = False,
        root_selector: str = "body", timeout_ms: int = 5000, nth: int = 0,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """按页面可见文本点击。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_click_by_text, site="linkedin",
            text=text, tag=tag, exact=exact,
            root_selector=root_selector, timeout_ms=timeout_ms, nth=nth,
        )

    @mcp.tool()
    async def linkedin_wait_for(
        ctx: Context, selector: str, timeout_ms: int = 10000,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """等元素出现。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_wait_for_element, site="linkedin",
            selector=selector, timeout_ms=timeout_ms,
        )

    @mcp.tool()
    async def linkedin_navigate_to(
        ctx: Context, url: str, wait_for_selector: str = "",
        timeout_ms: int = 15000,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """导航 LinkedIn Worker Tab 到指定 URL（host 必须 *.linkedin.com）。"""
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_navigate_to, site="linkedin",
            url=url, wait_for_selector=wait_for_selector,
            timeout_ms=timeout_ms,
        )


