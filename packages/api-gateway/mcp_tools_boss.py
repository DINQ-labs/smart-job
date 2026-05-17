"""
mcp_tools_boss.py — Boss 直聘 MCP tool 定义。
"""
from __future__ import annotations

import db
from fastmcp import Context

from server_helpers import (
    _ok, _err, _ext_connected, _get_agent_id, _get_caller_user_id,
    _no_ext_msg, _resolve_and_bind, _run_boss_tool,
)
from commands import (
    cmd_get_clickables,
    cmd_get_dom_snapshot,
    cmd_click_by_idx,
    cmd_click_by_text,
    cmd_wait_for_element,
    cmd_navigate_to,
    cmd_check_login,
    cmd_login,
    cmd_capture_qr,
    cmd_generate_qrcode,
    cmd_wait_for_login,
    cmd_init_session,
    cmd_search_jobs,
    cmd_get_job_detail,
    cmd_start_chat,
    cmd_send_message,
    cmd_get_chat_history,
    cmd_get_session_status,
    cmd_get_tokens,
    cmd_logout,
    cmd_list_sessions,
    cmd_list_agents,
    cmd_search_candidates,
    cmd_boss_auto_suggest,
    cmd_get_candidate_detail,
    cmd_contact_candidate,
    cmd_boss_get_geek_info,
    cmd_boss_enter_session,
    cmd_boss_get_chat_history,
    cmd_resume_preview_check,
    cmd_resume_download,
    cmd_filter_by_label,
    cmd_accept_exchange,
    cmd_geek_mark_job_interest,
    cmd_boss_refresh_my_jobs,
    cmd_boss_list_my_jobs,
    cmd_boss_chatted_jobs,
    cmd_boss_rec_job_list,
    cmd_boss_get_geek_list,
    cmd_boss_rec_geek_list,
    cmd_boss_mark_geek_interest,
    cmd_boss_list_geek_interests,
    cmd_boss_contact_list,
    cmd_boss_view_geek_info,
    cmd_get_quota_status,
    cmd_set_proxy,
    cmd_get_recommend_jobs,
    cmd_get_job_card,
    cmd_get_job_history,
    cmd_get_resume_baseinfo,
    cmd_get_resume_expect,
    cmd_get_resume_status,
    cmd_get_deliver_list,
    cmd_get_interview_data,
    cmd_get_friend_list,
    cmd_get_geek_job,
    cmd_geek_filter_by_label,
    cmd_recruiter_chat_list,
    cmd_geek_get_boss_data,
    cmd_get_ws_endpoints,
    cmd_msg_history_pull,
    cmd_save_job_interests,
    cmd_list_job_interests,
    cmd_update_job_interest_status,
    cmd_get_quick_replies,
    cmd_exchange_request,
    cmd_boss_check_reply_block,
)
from session_store import session_store
from agent_tracker import agent_tracker
from quota_tracker import quota_tracker, QuotaExceededError


def register(mcp):
    """注册所有 Boss 直聘 MCP tools 到给定的 FastMCP 实例。"""


    @mcp.tool()
    async def boss_check_login(ctx: Context, session_id: str = "", app_user_id: str = "",
                               cookie_id: str = "") -> str:
        """
        检查 Boss直聘 登录状态（不改变任何状态）。
        返回 {logged_in: bool, userId, name}。
        建议每次操作前先调用确认登录状态；未登录时调用 boss_login()。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        aid = _get_agent_id(ctx)
        try:
            sid = await _resolve_and_bind(aid, session_id, app_user_id)
        except RuntimeError as e:
            return _err(str(e))
        try:
            result = await cmd_check_login(sid, agent_id=aid, cookie_id=cookie_id)
            # 登录态确认后同步账号名到 agent 绑定
            if isinstance(result, dict) and result.get("logged_in") and aid:
                account_name = result.get("name", "")
                if account_name:
                    agent_tracker.set_bound_account(aid, account_name)
                    entry = session_store.get(sid)
                    if entry and not entry.account_name:
                        entry.account_name = account_name
            return _ok(result)
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_login(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        打开 Boss直聘 登录页，等待页面加载后自动截取二维码，返回可点击的扫码链接。
        扫码后调用 boss_wait_for_login() 等待登录完成。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_login,
        )


    @mcp.tool()
    async def boss_capture_qr(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        截取当前登录页的二维码图片，返回文件路径。
        需先调用 boss_login() 打开登录页。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        aid = _get_agent_id(ctx)
        try:
            sid = await _resolve_and_bind(aid, session_id, app_user_id)
        except RuntimeError as e:
            return _err(str(e))
        try:
            qr_path = await cmd_capture_qr(sid, agent_id=aid)
            login_url = f"http://127.0.0.1:{GATEWAY_PORT}/login"
            if qr_path:
                return _ok({"ok": True, "qr_image_path": qr_path,
                            "login_url": login_url,
                            "hint": f"请在浏览器打开: {login_url} 扫码，或直接查看文件: {qr_path}"})
            return _ok({"ok": False, "qr_image_path": None,
                        "login_url": login_url,
                        "hint": f"截图失败，请在浏览器打开登录页: {login_url} 直接扫码"})
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_generate_qrcode(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        通过 Boss直聘 API 直接生成登录二维码（无需打开登录页）。
        返回 { qr_id, rand_key, secret_key, short_rand_key, qrcode_dataurl }。
        二维码图片同时保存到 /tmp/boss_login_qr.png 供 /qr 端点访问。
        可配合 boss_wait_for_login(use_api_qr=true) 使用，实现纯 API 登录流程。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_generate_qrcode,
        )


    @mcp.tool()
    async def boss_wait_for_login(ctx: Context, timeout_sec: int = 120, use_api_qr: bool = False, session_id: str = "", app_user_id: str = "") -> str:
        """
        等待用户完成扫码登录（轮询，最长等待 timeout_sec 秒）。
        QR 码约 90 秒过期时自动刷新。

        参数:
          timeout_sec: 最长等待秒数（默认 120）
          use_api_qr: True=用 API 直接刷新二维码（无需打开登录页）；False=导航登录页截图刷新（默认）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        aid = _get_agent_id(ctx)
        try:
            sid = await _resolve_and_bind(aid, session_id, app_user_id)
        except RuntimeError as e:
            return _err(str(e))
        try:
            result = await cmd_wait_for_login(timeout=timeout_sec, session_id=sid, agent_id=aid, use_api_qr=use_api_qr)
            # 登录成功后更新绑定账号名
            if isinstance(result, dict) and result.get("logged_in") and aid:
                account_name = result.get("name", "")
                if account_name:
                    agent_tracker.set_bound_account(aid, account_name)
                    entry = session_store.get(sid)
                    if entry:
                        entry.account_name = account_name
            return _ok(result)
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_init_session(ctx: Context, session_id: str = "", app_user_id: str = "",
                                cookie_id: str = "") -> str:
        """
        初始化会话令牌（获取 wt2）。bosszp-cli 模式：强制重新从 DB 加载 Cookie。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_init_session, cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_search_jobs(
        ctx: Context,
        keyword: str,
        city: int = 101010100,
        page: int = 1,
        session_id: str = "",
        app_user_id: str = "",
        cookie_id: str = "",
        search_session_id: str = "",
    ) -> str:
        """
        搜索 Boss直聘 职位列表。
        返回 jobList（encryptJobId、职位名、公司名、薪资等），自动存储 listSecurityId。

        参数:
          keyword: 关键词，如 "产品经理"、"Python工程师"
          city: 城市代码，北京=101010100，上海=101020100，深圳=101280600
          page: 页码（从 1 开始）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
          search_session_id: 搜索会话ID，用于标记本次搜索的缓存结果
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_search_jobs,
            keyword=keyword, city=city, page=page, cookie_id=cookie_id,
            search_session_id=search_session_id,
        )


    @mcp.tool()
    async def boss_get_job_detail(ctx: Context, encrypt_job_id: str, security_id: str = "",
                                  session_id: str = "", app_user_id: str = "",
                                  cookie_id: str = "") -> str:
        """
        获取职位详情（自动使用已存储的 listSecurityId）。
        需先调用 boss_search_jobs。返回职位描述、公司信息，并存储 detailSecurityId。
        bosszp-cli 模式：直接传入 security_id + cookie_id，无需 encrypt_job_id。

        参数:
          encrypt_job_id: 职位加密ID（来自搜索结果的 encryptJobId，扩展模式使用）
          security_id: 职位 securityId（bosszp-cli 模式使用）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_job_detail,
            encrypt_job_id=encrypt_job_id, security_id=security_id, cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_start_chat(ctx: Context, encrypt_job_id: str = "", security_id: str = "",
                              session_id: str = "", app_user_id: str = "",
                              cookie_id: str = "") -> str:
        """
        向 Boss 发起聊天（打招呼）。自动执行 detail→friend/add→进入会话链路。
        注意：有 3-8 秒随机延迟以避免风控。
        bosszp-cli 模式：传入 security_id + cookie_id。

        参数:
          encrypt_job_id: 职位加密ID（来自搜索结果，扩展模式使用）
          security_id: 职位 securityId（bosszp-cli 模式使用）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_start_chat,
            encrypt_job_id=encrypt_job_id, security_id=security_id, cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_send_message(
        ctx: Context,
        content: str,
        encrypt_job_id: str = "",
        encrypt_uid: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        向 Boss 发送消息（求职者侧）。**两种调用方式**(2026-04-28 抓包反推):

        1. **search_jobs → start_chat 完整链已建立**:传 encrypt_job_id + content,
           扩展用 tokenStore 里的 chatSecurityId 直接发送
        2. **从消息列表(friendList)进入的会话**: 传 encrypt_uid=encryptFriendId +
           content,扩展从 tokenStore 反查 (encrypt_job_id, chatSecurityId);**扩展
           会自动 enterSession 激活会话**(Boss 要求 sendMsg 前必须 enter,旧路径
           漏调导致 code 121 "请求不合法")

        前置:用户从消息列表进入时,需先调过 boss_geek_filter_by_label(stage 2 批量
        把每个 friend 的 chatSecurityId 写入 tokenStore)。

        参数:
          content: 消息文本内容(必填)
          encrypt_job_id: 职位加密ID(方式 1 用)
          encrypt_uid: encryptFriendId / encryptBossId(方式 2 用)
          session_id: 指定扩展会话ID(多会话时必填)
          app_user_id: 外部业务用户ID(pool 模式)
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_send_message,
            encrypt_job_id=encrypt_job_id, content=content, encrypt_uid=encrypt_uid,
        )


    @mcp.tool()
    async def boss_get_chat_history(
        ctx: Context,
        encrypt_job_id: str = "",
        max_msg_id: str = "",
        encrypt_uid: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        拉取与 Boss 的聊天历史。

        **两种调用方式**:

        1. **完整链已建立**(已调过 boss_start_chat,本会话内): 传 encrypt_job_id +
           security_id(security_id 可由后端自动从 tokenStore 注入,留空即可)。

        2. **只拿到 encryptFriendId**(从 boss_geek_filter_by_label 的 friendList
           取的"查看最近消息"入口): **传 encrypt_uid=encryptFriendId,encrypt_job_id
           和 security_id 都留空**。后端会按 encrypt_uid 反查 tokenStore 里捕获过
           的 (encryptJobId, chatSecurityId);反查失败返回 chat_token_not_captured,
           按 hint 引导用户在 Boss 浏览器里打开对话页让扩展 intercept 后再调一次。

        参数:
          encrypt_job_id: 职位加密ID(方式 1 必填,方式 2 留空)
          max_msg_id: 分页游标,首次为空,后续传上一次返回的 lastId
          encrypt_uid: encryptFriendId / encryptBossId(方式 2 必填)
          session_id: 指定扩展会话ID(多会话时必填)
          app_user_id: 外部业务用户ID(pool 模式)
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_chat_history,
            encrypt_job_id=encrypt_job_id, max_msg_id=max_msg_id, encrypt_uid=encrypt_uid,
        )


    @mcp.tool()
    async def boss_get_session_status(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        查询当前 session 状态：扩展连接状态、登录状态、令牌缓存数量。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_session_status,
        )


    @mcp.tool()
    async def boss_get_tokens(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        获取扩展内完整令牌快照（session 信息 + 所有职位令牌链），用于调试。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_tokens,
        )


    @mcp.tool()
    async def boss_logout(ctx: Context, session_id: str = "", app_user_id: str = "",
                          cookie_id: str = "", open_login_page: bool = True) -> str:
        """
        退出 Boss直聘 账号（清除 cookies + 重置令牌）。
        退出后需重新调用 boss_login 扫码登录。
        bosszp-cli 模式：从 client 池中移除指定 cookie_id。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
          open_login_page: 默认 True，扩展清完 cookie 后自动打开 Boss 登录页，
                           便于用户立即扫码重登；批量脚本场景可传 False 禁用。
        """
        aid = _get_agent_id(ctx)
        try:
            sid = await _resolve_and_bind(aid, session_id, app_user_id)
        except RuntimeError as e:
            return _err(str(e))
        try:
            result = await cmd_logout(sid, agent_id=aid, cookie_id=cookie_id,
                                       open_login_page=open_login_page)
            # 退出后清空 agent 绑定的账号名
            if aid:
                agent_tracker.set_bound_account(aid, "")
            entry = session_store.get(sid)
            if entry:
                entry.account_name = ""
            return _ok(result)
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_list_sessions(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        列出当前用户已连接的扩展会话。
        返回 session_id、账号名、连接时间等信息。
        """
        try:
            uid = _get_caller_user_id(ctx)
            return _ok(await cmd_list_sessions(user_id=uid))
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_list_agents(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        列出当前用户已连接的 AI Agent（MCP 客户端）及其绑定的扩展会话。
        """
        try:
            uid = _get_caller_user_id(ctx)
            return _ok(await cmd_list_agents(user_id=uid))
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def set_active_session(
        ctx: Context,
        session_id: str,
        platform: str = "boss",
    ) -> str:
        """
        设定"当前平台优先使用哪个扩展会话"。适用场景：同一 DINQ 账号下有多个
        浏览器扩展同时连接（Chrome + Edge、多开浏览器等），默认遇到多会话会
        报错要求显式传 session_id；调用本工具后，后续该平台的工具会自动用它。

        参数:
          session_id: 目标扩展会话 ID（可从 boss_list_sessions 的返回里挑）
          platform:   "boss" | "linkedin" | "indeed"，默认 boss

        传 session_id="" 表示清除该平台的亲和偏好，回到默认"自动选第一个"。
        """
        try:
            uid = _get_caller_user_id(ctx)
            if not uid:
                return _err("无法识别当前用户身份，请先登录 DINQ")
            platform = (platform or "boss").strip().lower()
            if not session_id:
                session_store.clear_active_session(uid, platform)
                return _ok({"cleared": True, "platform": platform})
            session_store.set_active_session(uid, platform, session_id)
            return _ok({
                "platform": platform,
                "session_id": session_id,
                "hint": f"后续 {platform} 工具调用将默认落到 {session_id[:16]}",
            })
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def get_active_session(
        ctx: Context,
        platform: str = "",
    ) -> str:
        """
        查询当前账号在某平台（或所有平台）下设定的 active session。

        参数:
          platform: 空字符串返回全部平台的映射；指定单一平台只返回该平台
        """
        try:
            uid = _get_caller_user_id(ctx)
            if not uid:
                return _err("无法识别当前用户身份，请先登录 DINQ")
            if platform:
                sid = session_store.get_active_session(uid, platform.strip().lower())
                return _ok({"platform": platform, "session_id": sid or ""})
            return _ok({"active_sessions": session_store.list_active_sessions(uid)})
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_search_candidates(
        ctx: Context,
        encrypt_job_id: str,
        keywords: str = "",
        city: int = -1,
        page: int = 1,
        gender: int = -1,
        experience: str = "-1,-1",
        salary: str = "-1,-1",
        age: str = "-1,-1",
        degree: str = "201,201",
        activeness: int = 0,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        招聘方搜索候选人（Boss 视角）。使用 /wapi/zpitem/web/boss/search/geeks.json。
        返回候选人列表，每项包含 encryptGeekId 和 securityId，
        可直接传入 boss_geek_info 获取详情并进入聊天。

        参数:
          encrypt_job_id: 招聘方自己的职位加密 ID（必填，可从 Boss 职位管理页面获取）
          keywords: 搜索关键词（空=不限，如 "产品经理"、"Python工程师"）
          city: 城市代码（-1=全国，101010100=北京，101020100=上海，101280600=深圳）
          page: 页码（从 1 开始，每页约 15 条）
          gender: 性别（-1=不限，1=男，2=女）
          experience: 工作年限范围 "min,max"（-1,-1=不限，如 "1,3" 表示 1-3 年）
          salary: 薪资范围 "min,max"（-1,-1=不限，单位 K，如 "10,30"）
          age: 年龄范围 "min,max"（-1,-1=不限）
          degree: 学历代码（201,201=不限，101=大专，201=本科，301=硕士，401=博士）
          activeness: 活跃度过滤（0=不限，1=3天内活跃，2=7天内，3=30天内）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        filters = {
            "gender": gender, "experience": experience, "salary": salary,
            "age": age, "degree": degree, "activeness": activeness,
        }
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_search_candidates,
            encrypt_job_id=encrypt_job_id, keywords=keywords, city=city, page=page,
            filters=filters,
        )


    @mcp.tool()
    async def boss_auto_suggest(
        ctx: Context,
        query: str,
        encrypt_job_id: str,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        招聘方搜索关键词自动补全（输入提示）。
        在调用 boss_search_candidates 前可先用此接口获取标准关键词。

        参数:
          query: 输入的关键词前缀（如 "产品"）
          encrypt_job_id: 招聘方职位加密 ID
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_auto_suggest,
            query=query, encrypt_job_id=encrypt_job_id,
        )


    @mcp.tool()
    async def boss_get_candidate_detail(ctx: Context, security_id: str, encrypt_uid: str = "",
                                        session_id: str = "", app_user_id: str = "") -> str:
        """
        获取候选人详情（搜索页视角）。使用 /wapi/zpitem/web/boss/search/geek/info。

        仅需 security_id（来自 boss_search_candidates 结果），无需 uid。
        返回候选人基本信息 + 三个令牌（encryptGeekId / encryptExpectId / detailSecurityId），
        这些令牌会自动缓存，供 boss_contact_candidate / boss_boss_enter 直接使用。

        调用顺序：boss_search_candidates → boss_get_candidate_detail → boss_contact_candidate

        参数:
          security_id: 候选人 securityId（来自 boss_search_candidates 结果，必填）
          encrypt_uid: 候选人加密 UID（可选，用于 tokenStore 回退查询）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）

        返回: { encryptGeekId, encryptExpectId, encryptJobId, detailSecurityId, name, degree, ... }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_candidate_detail,
            security_id=security_id, encrypt_uid=encrypt_uid,
        )


    @mcp.tool()
    async def boss_contact_candidate(
        ctx: Context,
        encrypt_uid: str,
        security_id: str = "",
        job_id: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        主动沟通候选人（搜索页流程）。受每日配额限制（默认 20 次/天）。

        自动完成令牌链补全：
          若 tokenStore 中无 detailSecurityId，则自动调用 boss_get_candidate_detail 获取，
          再调用 bossEnter 建立聊天会话。

        调用顺序（推荐）：
          boss_search_candidates → boss_get_candidate_detail → boss_contact_candidate

        或简化（自动补全）：
          boss_search_candidates → boss_contact_candidate(encrypt_uid, security_id)

        参数:
          encrypt_uid: 候选人加密 UID（来自 boss_search_candidates 结果）
          security_id: 搜索页 securityId（可选，有则跳过 tokenStore 查询）
          job_id: 招聘方职位加密 ID（可选，从 tokenStore 自动取）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        aid = _get_agent_id(ctx)
        try:
            sid = await _resolve_and_bind(aid, session_id, app_user_id)
        except RuntimeError as e:
            return _err(str(e))
        try:
            return _ok(await cmd_contact_candidate(encrypt_uid, job_id,
                                                   security_id=security_id,
                                                   session_id=sid, agent_id=aid))
        except QuotaExceededError as e:
            return _ok(e.to_dict())
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_geek_info(ctx: Context, uid: str, security_id: str,
                             session_id: str = "", app_user_id: str = "") -> str:
        """
        获取候选人信息（招聘官视角）。自动存储令牌供后续 boss_boss_enter 使用。

        参数:
          uid: 候选人明文 uid
          security_id: 来自招聘官聊天历史消息的 securityId
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_get_geek_info,
            uid=uid, security_id=security_id,
        )


    @mcp.tool()
    async def boss_boss_enter(ctx: Context, encrypt_uid: str, encrypt_job_id: str,
                              security_id: str = "", encrypt_expect_id: str = "",
                              session_id: str = "", app_user_id: str = "") -> str:
        """
        进入招聘官聊天会话（bossEnter）。如已调用 boss_geek_info，security_id 和
        encrypt_expect_id 可省略（从令牌缓存中自动获取）。

        参数:
          encrypt_uid: 候选人加密 uid（来自 boss_geek_info 响应）
          encrypt_job_id: 招聘官职位加密 ID
          security_id: 来自 boss_geek_info 响应的新 securityId（可省略）
          encrypt_expect_id: 候选人期望加密 ID（可省略）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_enter_session,
            encrypt_uid=encrypt_uid, encrypt_job_id=encrypt_job_id,
            security_id=security_id, encrypt_expect_id=encrypt_expect_id,
        )


    @mcp.tool()
    async def boss_boss_chat_history(ctx: Context, uid: str = "", encrypt_uid: str = "",
                                     max_msg_id: str = "0", count: int = 20, page: int = 1,
                                     session_id: str = "", app_user_id: str = "") -> str:
        """
        拉取招聘官侧聊天历史（boss/historyMsg）。uid 和 encrypt_uid 二选一。
        消息中的 securityId 可用于后续 boss_geek_info 调用。

        参数:
          uid: 候选人明文 uid
          encrypt_uid: 候选人加密 uid（如已调用 boss_geek_info 可替代 uid）
          max_msg_id: 分页游标（首次传 "0"）
          count: 每页消息数（默认 20）
          page: 页码（默认 1）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_get_chat_history,
            uid=uid, encrypt_uid=encrypt_uid, max_msg_id=max_msg_id,
            count=count, page=page,
        )


    @mcp.tool()
    async def boss_resume_preview_check(ctx: Context, encrypt_uid: str, authority_id: str = "",
                                        session_id: str = "", app_user_id: str = "") -> str:
        """
        简历预览权限检查。authority_id 来源尚不确认（需继续抓包）。
        检查通过后自动存储 encryptAuthorityId 供 boss_resume_download 使用。

        参数:
          encrypt_uid: 候选人加密 uid
          authority_id: 简历权限 ID（来源待确认）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_resume_preview_check,
            encrypt_uid=encrypt_uid, authority_id=authority_id,
        )


    @mcp.tool()
    async def boss_resume_download(ctx: Context, encrypt_uid: str, authority_id: str = "",
                                   timestamp: int = 0,
                                   session_id: str = "", app_user_id: str = "") -> str:
        """
        下载候选人简历 PDF，返回 base64_pdf 字段。需先调用 boss_resume_preview_check。

        参数:
          encrypt_uid: 候选人加密 uid
          authority_id: 简历权限 ID（可省略，从令牌缓存获取）
          timestamp: 时间戳（0 表示使用当前时间）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_resume_download,
            encrypt_uid=encrypt_uid, authority_id=authority_id, timestamp=timestamp,
        )


    @mcp.tool()
    async def boss_filter_by_label(ctx: Context, label_id: int, encrypt_job_id: str,
                                   sort: str = "",
                                   session_id: str = "", app_user_id: str = "") -> str:
        """
        **per-job 维度**：在某职位下按标签筛选候选人，返回 friendId/encryptFriendId
        列表。要求 encrypt_job_id 必填非空（招聘官自己的职位 ID）。

        ⚠️ 与 boss_recruiter_chat_list 的区别：
          - 本工具：encrypt_job_id 必填，结果是该职位下打过标签的候选人
          - boss_recruiter_chat_list：不绑定职位，是 Boss "消息"页全局聊天列表

        参数:
          label_id: 标签 ID（如 1=待筛选，2=合适，3=不合适等，由招聘方在 Boss UI 自定义）
          encrypt_job_id: 招聘官自己的职位加密 ID（必填）
          sort: 排序方式（空字符串表示默认）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_filter_by_label,
            label_id=label_id, encrypt_job_id=encrypt_job_id, sort=sort,
        )


    @mcp.tool()
    async def boss_recruiter_chat_list(
        ctx: Context,
        label_id: int = 0,
        sort: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        招聘方"消息"页全局聊天列表（POST filterByLabel + encJobId=""，不绑定职位）。

        labelId 取值：
          0 = 全部（含系统通知）
          1 = 默认聊天 tab（多数账号 100+ 条）
          2-11 = 用户在 Boss UI 自定义的标签（多数账号 0 条）

        返回 result[] 字段稀疏（friendId/encryptFriendId/updateTime/waterLevel +
        labelId=0 偶有 name），详情通过 boss_view_geek_detail / boss_geek_info /
        boss_boss_enter 在用户点开具体对话时再拉。

        ⚠️ 区分：
          - boss_filter_by_label：per-job 候选人标签筛（要 encrypt_job_id）
          - boss_list_interacted_geeks：per-job"看过我的/沟通过的/待反馈"
          - 本工具：global 聊天列表（chat 页 tab 切换）

        缓存优先：用户**重复**问"看消息 / 我的对话" → 先调
        boss_list_cached_recruiter_chats(label_id, fresh_within_minutes=10)。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_recruiter_chat_list,
            label_id=label_id, sort=sort,
        )


    @mcp.tool()
    async def boss_accept_exchange(ctx: Context, message_id: str, security_id: str,
                                   session_id: str = "", app_user_id: str = "") -> str:
        """
        接受候选人的联系方式交换请求（索要简历/微信/手机号）。

        参数:
          message_id: 交换请求消息 ID
          security_id: 当前聊天 securityId
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_accept_exchange,
            message_id=message_id, security_id=security_id,
        )


    @mcp.tool()
    async def boss_get_quick_replies(
        ctx: Context, session_id: str = "", app_user_id: str = "",
    ) -> str:
        """
        Boss 招聘方拉取自定义的快捷回复短语模板列表（聊天框右侧"常用语"）。

        用途：agent 给候选人回消息时，先调此工具看 HR 已有话术，
              然后从中挑选/微调成最贴合上下文的回复，避免凭空生成与 HR 风格不符。

        返回 {replies: [str], total: int, raw}
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_quick_replies,
        )


    @mcp.tool()
    async def boss_exchange_request(
        ctx: Context, security_id: str, type: int = 4,
        session_id: str = "", app_user_id: str = "",
    ) -> str:
        """
        Boss 招聘方主动向候选人发起交换电话/微信请求
        （区别于 boss_accept_exchange 被动接受候选人请求）。

        参数:
          security_id: 当前聊天的 securityId（来自 boss_boss_enter 响应或 chat 上下文）
          type: 4=微信，3=电话（默认 4 微信）

        扩展端会先调 /exchange/test 预检，避免重复发起触发风控。
        若 alert_type ≠ 0 表示被拦截（如已发送过 / 配额耗尽），blocked=true 返回。

        返回 {blocked: bool, alert_type: int, type, success: bool, raw}
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_exchange_request,
            security_id=security_id, type=type,
        )


    @mcp.tool()
    async def boss_check_reply_block(
        ctx: Context,
        encrypt_jid: str,
        encrypt_exp_id: str,
        security_id: str,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        Boss 招聘方批量联系候选人前的屏蔽预检。在 boss_contact_candidate / boss_start_chat
        之前对每个候选人调用一次，过滤掉已屏蔽你的人，避免浪费配额和触发风控。

        参数:
          encrypt_jid:    招聘方自己的 encryptJobId（从 boss_list_my_jobs / geek info 返回）
          encrypt_exp_id: 候选人 encryptExpId（来自推荐 / 搜索 / 互动列表）
          security_id:    聊天级 securityId（来自 boss_boss_enter / chat/geek/info）

        返回 {blocked: bool, reply_block, hunter_call_chat_limit, raw}
        blocked=True 表示对方已屏蔽你（拒绝该职位方向）→ **跳过该候选人**。

        典型用法（批量联系流程）:
          1. boss_rec_geek_list / boss_search_candidates 拿到候选人列表
          2. 对每人调 boss_check_reply_block 过滤 blocked=True 的
          3. 剩下的再调 boss_contact_candidate
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_check_reply_block,
            encrypt_jid=encrypt_jid, encrypt_exp_id=encrypt_exp_id,
            security_id=security_id,
        )


    @mcp.tool()
    async def boss_list_interacted_geeks(
        ctx: Context,
        tag: int = 2,
        geek_apply_status: int = -1,
        chat_status: int = -1,
        jobid: str = "-1",
        page: int = 1,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        获取与我互动的候选人列表（看过我的简历 / 沟通过 / 待反馈）。
        候选人资料自动写入数据库。每项含 securityId，可直接传给 boss_get_candidate_detail。

        tag 取值：
          2 = 看过我的（默认）
          4 = 沟通过的
          8 = 待反馈的

        参数:
          tag: 候选人分类标签（见上）
          geek_apply_status: -1=全部投递状态
          chat_status: -1=全部沟通状态
          jobid: 按职位过滤（-1=全部，或传 encryptJobId）
          page: 页码（从 1 开始）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）

        返回: { geeks: [{name, securityId, encryptGeekId, encryptJobId, expectId, salary, ...}], hasMore, totalCount }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_get_geek_list,
            tag=tag, geek_apply_status=geek_apply_status,
            chat_status=chat_status, jobid=jobid, page=page,
        )


    @mcp.tool()
    async def boss_rec_geek_list(
        ctx: Context,
        job_id: str,
        page: int = 1,
        filters: dict | None = None,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        获取招聘方职位的推荐牛人列表（/wapi/zpjob/rec/geek/list）。
        候选人资料自动写入 cached_geeks 数据库。

        参数:
          job_id: 招聘方职位 ID（必填）
          page: 页码（从 1 开始）
          filters: 筛选条件 dict，支持 age/degree/experience/activation/recentNotView/gender/keyword1
          session_id: 指定扩展会话ID
          app_user_id: 外部业务用户ID（pool 模式）

        返回: { geeks: [{name, encryptGeekId, securityId, salary, ...}], hasMore, totalCount }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_rec_geek_list,
            job_id=job_id, page=page, filters=filters,
        )


    @mcp.tool()
    async def boss_mark_geek_interest(
        ctx: Context,
        encrypt_geek_id: str,
        encrypt_job_id: str = "",
        interested: bool = True,
        status: str = "new",
        match_score: int | None = None,
        notes: str = "",
        geek_name: str = "",
        salary: str = "",
        city: str = "",
        degree: str = "",
        work_year: str = "",
        search_security_id: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        标记招聘者对候选人的兴趣（写入 recruiter_geek_interests 表）。

        参数:
          encrypt_geek_id: 候选人加密 ID（必填，来自搜索/推荐结果）
          encrypt_job_id: 关联职位加密 ID（可选，为空=不限职位）
          interested: True=感兴趣，False=不感兴趣
          status: new | viewed | contacted | rejected
          match_score: 匹配分数（0-100，可选）
          notes: 备注
          geek_name/salary/city/degree/work_year: 候选人基本信息快照
          search_security_id: 搜索页 securityId（可选）
          session_id: 指定扩展会话ID
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_mark_geek_interest,
            encrypt_geek_id=encrypt_geek_id,
            encrypt_job_id=encrypt_job_id,
            interested=interested, status=status,
            match_score=match_score, notes=notes,
            geek_name=geek_name, salary=salary, city=city,
            degree=degree, work_year=work_year,
            search_security_id=search_security_id,
        )


    @mcp.tool()
    async def boss_list_geek_interests(
        ctx: Context,
        encrypt_job_id: str = "",
        interested_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        获取招聘者标注的候选人兴趣列表（来自 recruiter_geek_interests 表）。

        参数:
          encrypt_job_id: 按职位过滤（空=返回所有职位）
          interested_only: True=只返回标注为感兴趣的
          limit: 每页返回数量（默认 50）
          offset: 跳过前 N 条（分页用）
          session_id: 指定扩展会话ID
          app_user_id: 外部业务用户ID（pool 模式）

        返回: { interests: [{encrypt_geek_id, geek_name, salary, status, interested, ...}] }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_list_geek_interests,
            encrypt_job_id=encrypt_job_id,
            interested_only=interested_only,
            limit=limit, offset=offset,
        )


    @mcp.tool()
    async def boss_contact_list(
        ctx: Context,
        page: int = 1,
        source: int = 2,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        获取联系人列表（沟通中的候选人，另一视图）。
        候选人资料自动写入数据库。

        参数:
          page: 页码（从 1 开始）
          source: 来源标识（默认 2）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）

        返回: { geeks: [{name, securityId, encryptGeekId, encryptJobId, expectId, isFriend, ...}], hasMore, totalCount }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_contact_list,
            page=page, source=source,
        )


    @mcp.tool()
    async def boss_view_geek_detail(
        ctx: Context,
        encrypt_jid: str,
        expect_id: str,
        security_id: str,
        lid: str = "",
        entrance: int = 2,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        查看互动候选人详情（/wapi/zpjob/view/geek/info/v2）。
        参数来自 boss_list_interacted_geeks 或 boss_contact_list 返回的 geekCard：

        参数:
          encrypt_jid: geekCard.encryptJobId（招聘方职位加密 ID）
          expect_id: geekCard.expectId（候选人期望 ID，数字字符串）
          security_id: geekCard.securityId（用于本接口鉴权）
          lid: geekCard.lid（可选）
          entrance: 入口类型（默认 2）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_view_geek_info,
            encrypt_jid=encrypt_jid, expect_id=expect_id,
            security_id=security_id, lid=lid, entrance=entrance,
        )


    @mcp.tool()
    async def boss_refresh_my_jobs(ctx: Context, type: int = 0, search_str: str = "",
                                   session_id: str = "", app_user_id: str = "") -> str:
        """
        获取招聘方自己发布的全部职位，自动翻页并写入本地缓存（recruiter_jobs 表）。

        每次搜索候选人前请先调用此接口获取 encryptJobId，再将其传给 boss_search_candidates。

        参数:
          type: 0=全部（默认） 5=草稿/待审 6=已过期
          search_str: 按职位名称筛选（空=不过滤）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）

        返回: { jobs: [{encryptJobId, jobName, city, salaryDesc, experienceName, ...}], total }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_refresh_my_jobs,
            type=type, search_str=search_str,
        )


    @mcp.tool()
    async def boss_list_my_jobs(ctx: Context, keyword: str = "", job_status: int = -1,
                                session_id: str = "", app_user_id: str = "") -> str:
        """
        从本地缓存读取招聘方已发布职位列表。若缓存为空则自动从 Boss直聘 拉取一次。

        用于在调用 boss_search_candidates 之前选择目标职位的 encryptJobId。

        参数:
          keyword: 按职位名称模糊过滤（空=不过滤）
          job_status: -1=全部（默认） 0=上线中 1=下线
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）

        返回: { jobs: [{encryptJobId, jobName, city, salaryDesc, experienceName, degreeName, ...}], total }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_list_my_jobs,
            keyword=keyword,
            job_status=job_status if job_status >= 0 else None,
        )


    @mcp.tool()
    async def boss_chatted_jobs(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        获取有活跃沟通记录的职位列表。快速定位哪些职位有候选人互动，用于优先跟进。

        返回: { jobs: [{encryptJobId, jobName, salaryDesc, address}], total }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_chatted_jobs,
        )


    @mcp.tool()
    async def boss_rec_job_list(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        获取招聘方简化在招职位列表（recJobList）。

        返回 { onlineJobList: [{encryptJobId, jobName, salaryDesc, positionCode}] }
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_boss_rec_job_list,
        )


    @mcp.tool()
    async def boss_save_job_interests(
        ctx: Context,
        encrypt_job_ids: list[str],
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        将选中的职位保存到用户职位兴趣列表（持久化到数据库）。
        搜索结果展示后、用户选择"查看详情"或"打招呼"时调用，用于记录感兴趣的职位。

        参数:
          encrypt_job_ids: 职位加密ID列表（来自搜索结果的 encryptJobId）
          session_id: 指定扩展会话ID
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_save_job_interests,
            encrypt_job_ids=encrypt_job_ids,
        )


    @mcp.tool()
    async def boss_list_job_interests(
        ctx: Context,
        status: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        查询用户的职位兴趣列表（来自历史搜索中用户选择的职位）。
        status 可选: new/viewed/applied/rejected，空=全部。

        参数:
          status: 筛选状态（new=新增/viewed=已查看/applied=已投递/rejected=不感兴趣）
          session_id: 指定扩展会话ID
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_list_job_interests,
            status=status,
        )


    @mcp.tool()
    async def boss_update_job_interest_status(
        ctx: Context,
        encrypt_job_id: str,
        status: str,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        更新职位兴趣状态（new → viewed → applied / rejected）。
        投递后调用以标记为 applied；不感兴趣时标记为 rejected。

        参数:
          encrypt_job_id: 职位加密ID
          status: 新状态（new/viewed/applied/rejected）
          session_id: 指定扩展会话ID
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_update_job_interest_status,
            encrypt_job_id=encrypt_job_id, status=status,
        )


    @mcp.tool()
    async def boss_get_quota_status(ctx: Context, session_id: str = "", app_user_id: str = "") -> str:
        """
        查询当前会话的每日配额状态。
        返回 candidate_contact（主动沟通候选人）和 job_application（投递工作）的已用/剩余/上限。
        配额在北京时间每日零点自动重置。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_quota_status,
            pass_agent_id=False,
        )


    @mcp.tool()
    async def boss_set_quota_limit(ctx: Context, quota_type: str, limit: int) -> str:
        """
        动态调整某类操作的每日配额上限（管理功能，重启后恢复环境变量设置）。
        可通过环境变量永久配置：BOSS_LIMIT_CANDIDATE_CONTACT、BOSS_LIMIT_JOB_APPLICATION。

        参数:
          quota_type: 配额类型，可选 candidate_contact 或 job_application
          limit: 新上限（0 表示禁止，正整数表示每日最大次数）
        """
        try:
            quota_tracker.set_limit(quota_type, limit)
            return _ok({"ok": True, "quota_type": quota_type, "new_limit": limit})
        except ValueError as e:
            return _err(str(e))
        except Exception as e:
            return _err(str(e))


    # ── 求职者全链路：职位发现 + 个人中心 + Cookie 导出 ──────────────────────────


    @mcp.tool()
    async def boss_get_recommend_jobs(ctx: Context, page: int = 1, session_id: str = "",
                                      app_user_id: str = "", cookie_id: str = "") -> str:
        """
        获取 Boss直聘 个性化推荐职位列表。

        参数:
          page: 页码（从 1 开始）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_recommend_jobs,
            page=page, cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_job_card(ctx: Context, security_id: str, lid: str = "",
                                session_id: str = "", app_user_id: str = "",
                                cookie_id: str = "") -> str:
        """
        获取职位卡片（轻量详情，不触发完整 token 链）。

        参数:
          security_id: 职位 securityId（来自搜索结果）
          lid: 可选的 lid 参数
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_job_card,
            security_id=security_id, lid=lid, cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_job_history(ctx: Context, page: int = 1, session_id: str = "",
                                   app_user_id: str = "", cookie_id: str = "") -> str:
        """
        获取最近浏览过的职位历史列表。

        参数:
          page: 页码（从 1 开始）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_job_history,
            page=page, cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_resume_baseinfo(ctx: Context, session_id: str = "", app_user_id: str = "",
                                       cookie_id: str = "") -> str:
        """
        获取当前登录用户的简历基本信息（姓名、年龄、学历等）。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_resume_baseinfo,
            cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_resume_expect(ctx: Context, session_id: str = "", app_user_id: str = "",
                                     cookie_id: str = "") -> str:
        """
        获取当前登录用户的求职期望（目标职位、城市、薪资期望等）。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_resume_expect,
            cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_resume_status(ctx: Context, session_id: str = "", app_user_id: str = "",
                                     cookie_id: str = "") -> str:
        """
        获取简历投递状态汇总（投递数、被看次数等）。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_resume_status,
            cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_deliver_list(ctx: Context, page: int = 1, session_id: str = "",
                                    app_user_id: str = "", cookie_id: str = "") -> str:
        """
        查看已投递职位列表（求职者个人中心），包含职位名、公司名、投递状态等。

        参数:
          page: 页码（从 1 开始）
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_deliver_list,
            page=page, cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_interview_data(ctx: Context, session_id: str = "", app_user_id: str = "",
                                      cookie_id: str = "") -> str:
        """
        获取面试邀请数据（求职者个人中心）。

        参数:
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_interview_data,
            cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_get_friend_list(ctx: Context, session_id: str = "", app_user_id: str = "",
                                   cookie_id: str = "") -> str:
        """
        获取已沟通 Boss 列表（聊天好友列表），包含 bossId、bossSec 等信息。
        旧接口（getGeekFriendList.json）；Boss 前端已切到 boss_geek_filter_by_label，
        新代码请优先用后者，本工具保留兼容。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_friend_list,
            cookie_id=cookie_id,
        )


    @mcp.tool()
    async def boss_geek_filter_by_label(
        ctx: Context,
        label_id: int = 0,
        encrypt_system_id: str = "",
        name: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        求职者侧消息中心列表（按 tab 筛选）。这是 Boss 前端"消息"页的主接口。

        labelId 取值：0=全部 / 1=新招呼 / 2=仅沟通 / 3=有交换 / 4=有面试 / 5=不感兴趣 / -1=系统侧默认。

        返回 zpData.friendList[] 含 friendId / encryptFriendId（即 bossId）/ name /
        brandName / jobName / positionName / bossTitle / updateTime / waterLevel；
        encryptFriendId 可作 boss_geek_get_boss_data 的 boss_id 入参。

        **重要约束**: 本接口**不返回 securityId / chatSecurityId**,而下游
        boss_geek_get_boss_data 和 boss_get_chat_history 都强制要求 security_id 入参
        (agent_loop.py _TOOLS_REQUIRE_SECURITY_ID pre-flight 校验拦截空串)。

        **可行路径**: 引导用户先在 Boss 浏览器(zhipin.com/web/geek/chat)打开和该
        boss 的聊天页面,触发 ext interceptor 抓 friend/add 响应进 tokenStore;
        之后下游工具的 security_id 可被 ext 端按 boss_id auto-resolve。具体提示
        文案见 modes/search.py Step 2B 段。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_geek_filter_by_label,
            label_id=label_id, encrypt_system_id=encrypt_system_id, name=name,
        )


    @mcp.tool()
    async def boss_geek_get_boss_data(
        ctx: Context,
        boss_id: str,
        security_id: str = "",
        boss_source: int = 0,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        求职者进入聊天前拉 boss 元信息 + 关联职位（含 encryptJobId、salary range、
        bothTalked、isBlacked、isTop 等）。**"查看 #N 详情" 的主路径**(返回数据
        最完整),常配合 boss_geek_filter_by_label 使用。

        **两种调用方式**(2026-04-28 抓包反推后新增反查路径):

        1. **完整链已建立**(同会话内有 chatSecurityId):传 boss_id + security_id
        2. **从 friendList 进入**(只有 encryptFriendId): **传 boss_id=encryptFriendId,
           security_id 留空**。前置条件是先调 boss_geek_filter_by_label —— 它会在
           stage 2 (POST getGeekFriendList) 批量把每个 friend 的 chatSecurityId
           写进 ext tokenStore;本工具调用时由 ext 反查 lookupChatTokenByBoss 拿到。

        参数:
          boss_id: encryptBossId / encryptFriendId(必填)
          security_id: chatSecurityId(方式 2 留空让 ext 反查)
          boss_source: 抓包中固定 0
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_geek_get_boss_data,
            boss_id=boss_id, security_id=security_id, boss_source=boss_source,
        )


    @mcp.tool()
    async def boss_get_ws_endpoints(
        ctx: Context, session_id: str = "", app_user_id: str = "",
    ) -> str:
        """
        获取 Boss 实时消息 WebSocket 服务器列表（如 ws.zhipin.com / ws6.zhipin.com）。
        用于实时推送 / 离线补拉时的握手准备。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_ws_endpoints,
        )


    @mcp.tool()
    async def boss_msg_history_pull(
        ctx: Context,
        type: int = 0,
        last_id: int = 0,
        secret_id: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        离线消息增量补拉（WS 重连后或定时轮询用）。
        type 抓包中固定 0；last_id 是上次拉到的最大 messageId（首次给 0）；
        secret_id 来自 WS 握手或上一次 pull 的响应。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_msg_history_pull,
            type=type, last_id=last_id, secret_id=secret_id,
        )


    @mcp.tool()
    async def boss_get_geek_job(ctx: Context, security_id: str, session_id: str = "",
                                app_user_id: str = "", cookie_id: str = "") -> str:
        """
        获取互动职位信息（geekGetJob），用于查询与某个 Boss 的互动职位上下文。

        参数:
          security_id: 职位 securityId
          session_id: 指定扩展会话ID（多会话时必填）
          app_user_id: 外部业务用户ID（pool 模式）
          cookie_id: httpx 模式：account_cookies 表中的记录 ID（bosszp-cli 专用）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_geek_job,
            security_id=security_id, cookie_id=cookie_id,
        )



    # ── 职位缓存 MCP 工具 ────────────────────────────────────────────────────────


    def _compact_job(j: dict) -> dict:
        return {
            "external_id": j.get("external_id"),
            "title": j.get("title"),
            "company": j.get("company"),
            "city": j.get("city"),
            "salary": j.get("salary"),
            "job_type": j.get("job_type"),
            "experience": j.get("experience"),
            "education": j.get("education"),
            "hr_name": j.get("hr_name"),
            "hr_title": j.get("hr_title"),
            "has_detail": j.get("has_detail", False),
            "fetched_at": j.get("fetched_at"),
        }


    @mcp.tool()
    async def boss_list_cached_jobs(
        ctx: Context,
        keyword: str = "",
        city_code: str = "",
        has_detail: bool = False,
        fresh_within_days: int = 0,
        include_expired: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """
        从本地缓存检索已抓取的职位列表（无需调用 Boss API）。
        适用场景：重新审视之前搜索的职位、比较多个职位、容灾兜底。
        返回紧凑格式（职位名、公司、薪资、城市、学历、是否有详情）。

        参数:
          keyword: 关键字过滤（职位名或公司名，模糊匹配）
          city_code: 城市代码过滤（如 101010100 代表北京）
          has_detail: true=只返回已拉取详情的职位，false=全部
          fresh_within_days: 只返回最近 N 天内抓取的条目（0=不过滤，默认）。
                             用于避免返回 30+ 天前的过时快照。
          include_expired: 是否包含已过招聘截止日期的职位（默认 false 自动剔除）
          limit: 每页数量（最大 100）
          offset: 分页偏移
        """
        try:
            jobs = await db.list_cached_jobs(
                keyword=keyword or None,
                city_code=city_code or None,
                has_detail=has_detail if has_detail else None,
                fresh_within_days=fresh_within_days if fresh_within_days > 0 else None,
                include_expired=include_expired,
                limit=min(limit, 100),
                offset=offset,
            )
            compact = [_compact_job(j) for j in jobs]
            return _ok({"jobs": compact, "total": len(compact)})
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_get_cached_job(ctx: Context, encrypt_job_id: str,
                                  session_id: str = "", app_user_id: str = "") -> str:
        """
        从本地缓存获取单个职位的完整信息（含岗位描述、地址，若已抓取详情）。
        适用场景：重新查看某职位详情而无需消耗 API 配额。

        参数:
          encrypt_job_id: 职位加密 ID（encryptJobId）
          session_id: 扩展会话ID（可选，与其他工具保持一致）
          app_user_id: 外部业务用户ID（可选，与其他工具保持一致）
        """
        try:
            job = await db.get_cached_job("boss", encrypt_job_id)
            if not job:
                return _err(f"缓存中未找到职位 {encrypt_job_id}，请先调用 boss_get_job_detail 获取")
            return _ok(job)
        except Exception as e:
            return _err(str(e))


    # ── 聊天 / 候选人 列表缓存 MCP 工具 ─────────────────────────────────────────


    def _compact_chat(c: dict) -> dict:
        ft = c.get("fetched_at")
        return {
            "encrypt_friend_id": c.get("encrypt_friend_id"),
            "name": c.get("name"),
            "brand_name": c.get("brand_name"),
            "job_name": c.get("job_name"),
            "position_name": c.get("position_name"),
            "boss_title": c.get("boss_title"),
            "job_city": c.get("job_city"),
            "update_time": c.get("update_time"),
            "labels": c.get("label_set") or [],
            # token chain（merge 自旧接口）：下游 boss_geek_get_boss_data /
            # boss_get_chat_history 直接可用，不用再额外拉
            "encrypt_job_id": c.get("encrypt_job_id"),
            "chat_security_id": c.get("chat_security_id"),
            "last_msg": c.get("last_msg"),
            "last_msg_ts": c.get("last_msg_ts"),
            "fetched_at": ft,
        }


    def _compact_geek(g: dict) -> dict:
        return {
            "encrypt_geek_id": g.get("encrypt_geek_id"),
            "name": g.get("name"),
            "city": g.get("city"),
            "work_year": g.get("work_year"),
            "salary": g.get("salary"),
            "current_work": g.get("current_work"),
            "school": g.get("school"),
            "degree_name": g.get("degree_name"),
            "active_desc": g.get("active_desc"),
            "apply_status": g.get("apply_status"),
            "source_job_id": g.get("source_job_id"),
            "fetched_at": g.get("fetched_at"),
        }


    @mcp.tool()
    async def boss_list_cached_chats(
        ctx: Context,
        label_id: int = 0,
        fresh_within_minutes: int = 10,
        search: str = "",
        limit: int = 20,
        offset: int = 0,
        account_name: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        从本地缓存读求职者消息中心列表（无需调用 Boss API）。
        优先于 boss_geek_filter_by_label 用于"再看一下我的对话"这类**重复**查询。
        若用户表达"有新消息吗 / refresh / 最新"等"我要最新"语义，请改用
        boss_geek_filter_by_label 实时拉取。

        参数:
          label_id: 0=不过滤 / 1=新招呼 / 2=仅沟通 / 3=有交换 / 4=有面试 / 5=不感兴趣
                    （cached_chats.label_set 包含该 label 的好友才会返回）
          fresh_within_minutes: 只返回最近 N 分钟内抓取的条目（默认 10）。
                                聊天状态变化快，超过这个值就该重拉。
          search: 模糊匹配 name / brand_name / job_name / position_name
          limit / offset: 分页，limit 上限 50
          account_name: 显式指定账号；为空则自动用当前 session 绑定的账号
          session_id / app_user_id: 与其他工具一致，留空自动解析
        """
        from session_store import session_store
        try:
            account = account_name
            if not account:
                aid = _get_agent_id(ctx)
                try:
                    sid = await _resolve_and_bind(aid, session_id, app_user_id, site="boss")
                    entry = session_store.get(sid)
                    account = entry.account_name if entry else ""
                except RuntimeError:
                    account = ""
            chats = await db.list_cached_chats(
                account_name=account or None,
                label_id=label_id if label_id and label_id > 0 else None,
                search=search or None,
                fresh_within_minutes=fresh_within_minutes if fresh_within_minutes > 0 else None,
                limit=min(limit, 50),
                offset=offset,
            )
            return _ok({"chats": [_compact_chat(c) for c in chats], "total": len(chats)})
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_list_cached_geeks(
        ctx: Context,
        search: str = "",
        source_job_id: str = "",
        fresh_within_days: int = 1,
        limit: int = 20,
        offset: int = 0,
        account_name: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        从本地缓存读招聘方候选人列表（cached_geeks 由 boss_list_interacted_geeks /
        boss_contact_list / boss_search_candidates 等接口写入）。
        优先于上述实时接口用于"再看一下候选人"这类**重复**查询。"刚刚有人投了
        我吗 / 最新候选人"等语义请改用 boss_list_interacted_geeks 实时。

        参数:
          search: 模糊匹配 name / current_work / school
          source_job_id: 限定来源职位（encryptJobId）
          fresh_within_days: 只返回最近 N 天内抓取的（默认 1）
          limit / offset: 分页，limit 上限 50
          account_name: 显式指定账号；为空则自动用当前 session 绑定的账号
          session_id / app_user_id: 与其他工具一致，留空自动解析
        """
        from session_store import session_store
        try:
            account = account_name
            if not account:
                aid = _get_agent_id(ctx)
                try:
                    sid = await _resolve_and_bind(aid, session_id, app_user_id, site="boss")
                    entry = session_store.get(sid)
                    account = entry.account_name if entry else ""
                except RuntimeError:
                    account = ""
            geeks = await db.list_cached_geeks(
                account_name=account or None,
                source_job_id=source_job_id or None,
                search=search or None,
                fresh_within_days=fresh_within_days if fresh_within_days > 0 else None,
                limit=min(limit, 50),
                offset=offset,
            )
            return _ok({"geeks": [_compact_geek(g) for g in geeks], "total": len(geeks)})
        except Exception as e:
            return _err(str(e))


    def _compact_recruiter_chat(c: dict) -> dict:
        return {
            "encrypt_friend_id": c.get("encrypt_friend_id"),
            "friend_id": c.get("friend_id"),
            "name": c.get("name"),
            "update_time": c.get("update_time"),
            "water_level": c.get("water_level"),
            "labels": c.get("label_set") or [],
            "fetched_at": c.get("fetched_at"),
        }


    @mcp.tool()
    async def boss_list_cached_recruiter_chats(
        ctx: Context,
        label_id: int = 0,
        fresh_within_minutes: int = 10,
        search: str = "",
        limit: int = 20,
        offset: int = 0,
        account_name: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        从本地缓存读招聘方全局聊天列表（cached_recruiter_chats，由
        boss_recruiter_chat_list 写入）。优先于实时接口用于"再看一下消息"
        类**重复**查询。"有新消息吗 / 最新 / refresh" → 跳过缓存。

        参数:
          label_id: 0=不过滤 / 1=默认聊天 tab / 2-11=用户在 Boss UI 自定义标签
          fresh_within_minutes: 只返回最近 N 分钟内抓取的（默认 10）
          search: 模糊匹配 name（招聘方 result[] 只填 name，无其它字段）
          limit / offset: 分页，limit 上限 50
          account_name: 显式指定账号；为空则自动用当前 session 绑定的账号
          session_id / app_user_id: 与其他工具一致，留空自动解析
        """
        from session_store import session_store
        try:
            account = account_name
            if not account:
                aid = _get_agent_id(ctx)
                try:
                    sid = await _resolve_and_bind(aid, session_id, app_user_id, site="boss")
                    entry = session_store.get(sid)
                    account = entry.account_name if entry else ""
                except RuntimeError:
                    account = ""
            chats = await db.list_cached_recruiter_chats(
                account_name=account or None,
                label_id=label_id if label_id and label_id > 0 else None,
                search=search or None,
                fresh_within_minutes=fresh_within_minutes if fresh_within_minutes > 0 else None,
                limit=min(limit, 50),
                offset=offset,
            )
            return _ok({
                "chats": [_compact_recruiter_chat(c) for c in chats],
                "total": len(chats),
            })
        except Exception as e:
            return _err(str(e))


    @mcp.tool()
    async def boss_list_chat_records(
        ctx: Context,
        keyword: str = "",
        account_name: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """
        查询打招呼记录（本地缓存，无需 API 配额）。
        适用场景：查看已联系过的职位、避免重复打招呼、统计活跃度。

        参数:
          keyword: 按职位名称或公司名模糊搜索
          account_name: 按打招呼账号过滤
          limit: 返回条数（默认 50）
          offset: 分页偏移
        """
        try:
            records = await db.list_chat_records(
                keyword=keyword or None,
                account_name=account_name or None,
                limit=limit,
                offset=offset,
            )
            return _ok({"records": records, "total": len(records)})
        except Exception as e:
            return _err(str(e))


    # ── LinkedIn MCP Tools ───────────────────────────────────────────────────────

    import linkedin_commands as li_cmd  # noqa: E402




    @mcp.tool()
    async def boss_geek_mark_job_interest(
        ctx: Context,
        job_id: str,
        collect: bool = True,
        security_id: str = "",
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        收藏或取消收藏职位（求职者视角，调用 Boss 真实接口）。
        ⚠️  必须先调用 boss_search_jobs 搜索过该职位（5分钟内），才能收藏。
        listSecurityId 是短期令牌，超过5分钟即失效，需重新搜索。

        参数:
          job_id:       职位加密 ID（encryptJobId，来自 boss_search_jobs 搜索结果）
          collect:      True=收藏/感兴趣，False=取消收藏/不感兴趣（默认 True）
          security_id:  listSecurityId（通常留空，自动从本次搜索结果取）
          session_id:   指定扩展会话 ID（多会话时必填）
          app_user_id:  外部业务用户 ID
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_geek_mark_job_interest,
            job_id=job_id, collect=collect, security_id=security_id,
        )


    # ── DOM 视觉 + 点击 + 导航（v1.6 新增）─────────────────────────────────

    @mcp.tool()
    async def boss_get_clickables(
        ctx: Context,
        root_selector: str = "body",
        include_hidden: bool = False,
        max_items: int = 200,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        回传 Boss Worker Tab 当前页面所有可点击元素列表（含 idx + selector + text + rect）。
        返回的 snapshot_id 5s 内可用 boss_click_by_idx 点击。

        典型流程：boss_get_clickables → 看 text 字段决定要点哪个 → boss_click_by_idx({snapshot_id, idx})。

        参数:
          root_selector: CSS selector，默认 'body'（全页）
          include_hidden: 是否含隐藏元素（默认 false）
          max_items: 最多返回多少项（默认 200）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_clickables, site="boss",
            root_selector=root_selector, include_hidden=include_hidden,
            max_items=max_items,
        )

    @mcp.tool()
    async def boss_get_dom_snapshot(
        ctx: Context,
        root_selector: str = "body",
        max_depth: int = 6,
        max_nodes: int = 500,
        include_text: bool = True,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        回传 Boss Worker Tab 完整 DOM 树（截断）。比 boss_get_clickables 详细，
        但 token 消耗大；只在需要看页面结构层级时用。

        参数:
          root_selector: 子树根，默认 body
          max_depth: 最大递归深度（默认 6）
          max_nodes: 最大节点数（默认 500）
          include_text: 是否含直接子文本（默认 true）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_get_dom_snapshot, site="boss",
            root_selector=root_selector, max_depth=max_depth,
            max_nodes=max_nodes, include_text=include_text,
        )

    @mcp.tool()
    async def boss_click_by_idx(
        ctx: Context,
        snapshot_id: str,
        idx: int,
        timeout_ms: int = 5000,
        fallback_text: bool = True,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        用 boss_get_clickables 拿到的 snapshot_id + idx 点击元素。
        selector 失效时自动用快照里的 text 走 fallback；返回里 clicked_via 字段
        指示走的是 'selector' 还是 'text_fallback'。

        snapshot 5s 后过期，过期时返回 error: 'snapshot expired'，
        重新调用 boss_get_clickables 即可。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_click_by_idx, site="boss",
            snapshot_id=snapshot_id, idx=idx,
            timeout_ms=timeout_ms, fallback_text=fallback_text,
        )

    @mcp.tool()
    async def boss_click_by_text(
        ctx: Context,
        text: str,
        tag: str = "",
        exact: bool = False,
        root_selector: str = "body",
        timeout_ms: int = 5000,
        nth: int = 0,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        按页面可见文本点击。多匹配时用 nth 选第几个；selector 漂移场景的稳定备选。

        参数:
          text: 要匹配的文本（先 exact 后 contains）
          tag: 限定 tag（如 'button'）
          exact: 默认 false（contains 匹配）；true 时严格相等
          nth: 0-based 索引，默认 0（第一个匹配）
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_click_by_text, site="boss",
            text=text, tag=tag, exact=exact,
            root_selector=root_selector, timeout_ms=timeout_ms, nth=nth,
        )

    @mcp.tool()
    async def boss_wait_for(
        ctx: Context,
        selector: str,
        timeout_ms: int = 10000,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        等元素出现。click 后等新页面 / 新弹窗渲染好再继续操作。
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_wait_for_element, site="boss",
            selector=selector, timeout_ms=timeout_ms,
        )

    @mcp.tool()
    async def boss_navigate_to(
        ctx: Context,
        url: str,
        wait_for_selector: str = "",
        timeout_ms: int = 15000,
        session_id: str = "",
        app_user_id: str = "",
    ) -> str:
        """
        导航 Boss Worker Tab 到指定 URL。host 必须在 zhipin.com 域内，
        跨站 URL 被扩展拒绝。可选 wait_for_selector 等导航后元素出现。

        典型用法：
          boss_navigate_to(url="https://www.zhipin.com/web/chat/index",
                           wait_for_selector=".chat-list")
        """
        return await _run_boss_tool(
            ctx, session_id, app_user_id, cmd_navigate_to, site="boss",
            url=url, wait_for_selector=wait_for_selector,
            timeout_ms=timeout_ms,
        )

