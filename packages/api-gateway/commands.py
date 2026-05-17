"""
commands.py: 高层命令实现，支持多会话（session_id 参数）。
每个命令通过 session_store.resolve_session() 获取独立的 job_store 和 rate_limiter。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
import time as _time
from typing import Any

log = logging.getLogger(__name__)

import db
import admin_broadcaster as ab
from candidate_preview import normalize_candidate_preview
from ext_client import send_command_to
from job_context import JobContextStore
from geek_context import GeekContextStore
from platform_command_base import unwrap_ext_envelope
from session_store import session_store
from agent_tracker import agent_tracker
from quota_tracker import quota_tracker, QuotaExceededError

GATEWAY_PORT = int(os.environ.get("BOSS_GATEWAY_PORT", "8767"))
GATEWAY_PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", f"http://127.0.0.1:{GATEWAY_PORT}")


def _qr_file_path(session_id: str = "") -> str:
    name = f"boss_login_qr_{session_id}.png" if session_id else "boss_login_qr.png"
    return os.path.join(tempfile.gettempdir(), name)


def _extract_job_list(raw: Any) -> list[dict]:
    if not isinstance(raw, dict):
        return []
    zp = raw.get("zpData") or {}
    return zp.get("jobList") or []


def _extract_zp_security_id(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    return (raw.get("zpData") or {}).get("securityId", "")


def _extract_boss_id(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    zp = raw.get("zpData") or {}
    return zp.get("encryptBossId") or zp.get("bossId") or ""


_unwrap = unwrap_ext_envelope


async def cmd_check_login(session_id: str | None = None, agent_id: str = "",
                          cookie_id: str = "", force: bool = False) -> dict:
    """检查当前登录状态，返回 {logged_in, userId, name}。"""
    try:
        result = await send_command_to(session_id, "GET", "boss/check_login",
                                       {"cookie_id": cookie_id},
                                       timeout_ms=10000, tool_name="boss_check_login",
                                       agent_id=agent_id)
        data = _unwrap(result)
        # 登录成功时更新会话的 account_name / app_user_id
        if isinstance(data, dict) and data.get("logged_in"):
            try:
                entry = session_store.resolve_session(session_id)
                entry.account_name = data.get("name", "")
                entry.app_user_id = data.get("userId", "")
                await db.upsert_session(
                    entry.session_id,
                    account_name=entry.account_name,
                    app_user_id=entry.app_user_id,
                )
                await db.log_session_event(entry.session_id, agent_id or None, "login_success",
                                           f"userId={entry.app_user_id}")
                await ab.admin_broadcaster.broadcast({
                    "event": "session_login",
                    "session_id": entry.session_id,
                    "account_name": entry.account_name,
                    "user_id": entry.user_id,
                    "app_user_id": entry.app_user_id,
                })
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
        return data
    except Exception as e:
        return {"logged_in": False, "error": str(e)}


async def _capture_qr(session_id: str | None = None, agent_id: str = "") -> str | None:
    """调用扩展截取 QR 码，保存为 PNG 文件，返回路径。"""
    try:
        actual_sid = session_store.resolve_session(session_id).session_id
    except Exception:
        actual_sid = session_id or ""
    try:
        qr = await send_command_to(session_id, "POST", "boss/capture_qrcode", {},
                                   timeout_ms=15000, tool_name="boss_capture_qr",
                                   agent_id=agent_id)
        dataurl = _unwrap(qr).get("qrcode_dataurl") if isinstance(qr, dict) else None
        if dataurl and "base64," in dataurl:
            b64 = dataurl.split("base64,", 1)[1]
            img = base64.b64decode(b64)
            path = _qr_file_path(actual_sid)
            with open(path, "wb") as f:
                f.write(img)
            print(f"[gateway] QR码已保存: {path}", flush=True)
            return path
    except Exception as e:
        print(f"[gateway] QR码截图失败（请在浏览器中直接扫码）: {e}", flush=True)
    return None


async def cmd_login(session_id: str | None = None, agent_id: str = "") -> dict:
    """导航登录页，等待加载，自动截取 QR 码，保存并返回可点击的 HTTP 链接。"""
    try:
        actual_sid = session_store.resolve_session(session_id).session_id
    except Exception:
        actual_sid = session_id or ""
    print(f"[cmd_login] 调用方 session_id={session_id!r} → resolved={actual_sid[:16] if actual_sid else '-'} agent={agent_id or '-'}", flush=True)
    login_url = f"{GATEWAY_PUBLIC_URL}/login/{actual_sid}" if actual_sid else f"{GATEWAY_PUBLIC_URL}/login"
    try:
        result = await send_command_to(session_id, "POST", "boss/login_with_qr", {},
                                       timeout_ms=20000, tool_name="boss_login",
                                       agent_id=agent_id)
    except Exception as e:
        print(f"[cmd_login] send_command_to 失败: {e}", flush=True)
        return {"ok": False, "login_url": login_url,
                "error": f"登录页打开失败: {e}",
                "message": f"请手动打开登录页: {login_url}"}

    print(f"[cmd_login] 扩展响应 ok={result.get('ok') if isinstance(result, dict) else '?'} keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}", flush=True)
    qr_path = None
    unwrapped = _unwrap(result)
    dataurl = unwrapped.get("qrcode_dataurl") if isinstance(unwrapped, dict) else None
    print(f"[cmd_login] unwrapped keys={list(unwrapped.keys()) if isinstance(unwrapped, dict) else type(unwrapped).__name__} dataurl={'有(' + str(len(dataurl)) + 'chars)' if dataurl else '无'}", flush=True)
    if dataurl and "base64," in dataurl:
        b64 = dataurl.split("base64,", 1)[1]
        img = base64.b64decode(b64)
        qr_path = _qr_file_path(actual_sid)
        with open(qr_path, "wb") as f:
            f.write(img)
        print(f"[cmd_login] QR码已保存: {qr_path} size={len(img)}bytes tab_state={unwrapped.get('tab_state','?')} source={unwrapped.get('source','?')}", flush=True)
    else:
        print(f"[cmd_login] 扩展未返回 qrcode_dataurl，/login 页面可能无 QR 图 unwrapped={str(unwrapped)[:200]}", flush=True)

    return {
        "ok": True,
        "login_url": login_url,
        "qr_image_path": qr_path,
        "message": f"二维码已就绪，请点击链接扫码登录: {login_url}",
    }


async def cmd_capture_qr(session_id: str | None = None, agent_id: str = "") -> str | None:
    return await _capture_qr(session_id, agent_id)


async def cmd_generate_qrcode(session_id: str | None = None, agent_id: str = "") -> dict:
    """通过 API 直接生成二维码（无需打开登录页），返回 qr_id 及图片 data URL。
    同时将图片保存到会话专属路径，供 /qr/{session_id} 端点访问。"""
    try:
        actual_sid = session_store.resolve_session(session_id).session_id
    except Exception:
        actual_sid = session_id or ""
    result = await send_command_to(session_id, "POST", "boss/generate_qrcode", {},
                                   timeout_ms=15000, tool_name="boss_generate_qrcode",
                                   agent_id=agent_id)
    data = _unwrap(result)
    # 保存图片供 /qr/{session_id} 端点
    dataurl = data.get("qrcode_dataurl") if isinstance(data, dict) else None
    if dataurl and "base64," in dataurl:
        b64 = dataurl.split("base64,", 1)[1]
        img = base64.b64decode(b64)
        qr_path = _qr_file_path(actual_sid)
        with open(qr_path, "wb") as f:
            f.write(img)
        print(f"[gateway] QR码(API)已保存: {qr_path}", flush=True)
    return data


async def cmd_wait_for_login(
    poll_interval: float = 3.0,
    timeout: float = 120.0,
    session_id: str | None = None,
    agent_id: str = "",
    use_api_qr: bool = False,
) -> dict:
    """轮询等待用户完成扫码登录，QR 码约 90 秒过期时自动刷新。
    use_api_qr=True：用 boss/generate_qrcode API 刷新（无需登录页截图）。
    use_api_qr=False（默认）：用 cmd_login（导航登录页 + 截图）刷新。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    QR_TTL = 90.0
    last_qr = loop.time()

    while loop.time() < deadline:
        status = await cmd_check_login(session_id, agent_id, force=True)
        if status.get("logged_in"):
            return status

        if loop.time() - last_qr >= QR_TTL:
            print("[gateway] QR码可能已过期，正在自动刷新...", flush=True)
            try:
                if use_api_qr:
                    refresh = await cmd_generate_qrcode(session_id, agent_id)
                    last_qr = loop.time()
                    print(f"[gateway] QR码(API)已刷新: qr_id={refresh.get('qr_id')}", flush=True)
                else:
                    refresh = await cmd_login(session_id, agent_id)
                    last_qr = loop.time()
                    print(f"[gateway] QR码已刷新: {refresh.get('qr_image_path')}", flush=True)
            except Exception as e:
                print(f"[gateway] 刷新QR码失败: {e}", flush=True)

        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(poll_interval, remaining))

    return {"logged_in": False, "message": f"等待登录超时（{timeout:.0f}秒）"}


async def cmd_init_session(session_id: str | None = None, agent_id: str = "",
                           cookie_id: str = "") -> dict:
    """初始化 session（获取 wt2 和用户信息）。"""
    result = await send_command_to(session_id, "GET", "boss/init_session",
                                   {"cookie_id": cookie_id},
                                   timeout_ms=15000, tool_name="boss_init_session",
                                   agent_id=agent_id)
    return _unwrap(result)


async def _cache_job_list(data: dict, keyword: str, city: int, page: int, search_session_id: str = "") -> None:
    """Fire-and-forget: parse search result and upsert jobs + search record into cache."""
    try:
        raw = data.get("raw") or {}
        zp_data = raw.get("zpData") or {}
        job_list = zp_data.get("jobList") or []
        if not isinstance(job_list, list) or not job_list:
            return

        city_code = str(city) if city else ""
        job_ids: list[str] = []
        _type_map = {4: "实习", 2: "兼职"}

        for j in job_list:
            if not isinstance(j, dict):
                continue
            ext_id = j.get("encryptJobId", "")
            if not ext_id:
                continue
            city_parts = [j.get("cityName", ""), j.get("areaDistrict", "")]
            city_str = " ".join(p for p in city_parts if p) or None
            jt_raw = j.get("jobType")
            job_type_str = _type_map.get(jt_raw, "全职")
            job_cc = str(j.get("cityCode", city_code)) if j.get("cityCode") else city_code
            try:
                await db.upsert_job(
                    "boss", ext_id,
                    title=j.get("jobName"),
                    company=j.get("brandName"),
                    city=city_str,
                    city_code=job_cc or None,
                    salary=j.get("salaryDesc"),
                    job_type=job_type_str,
                    experience=j.get("jobExperience"),
                    education=j.get("jobDegree"),
                    tags=j.get("jobLabels"),
                    skills=j.get("skills"),
                    hr_name=j.get("bossName"),
                    hr_title=j.get("bossTitle"),
                    encrypt_boss_id=j.get("encryptBossId"),
                    list_security_id=j.get("securityId"),
                    raw_list=j,
                    search_session_id=search_session_id or None,
                )
                job_ids.append(ext_id)
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass

        if keyword and job_ids:
            total = zp_data.get("totalCount")
            try:
                await db.upsert_search(
                    "boss", keyword, city_code or None, page,
                    job_ids=job_ids,
                    total_count=int(total) if total is not None else None,
                )
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


async def _cache_chat_list(data: dict | None, account_name: str, label_id: int) -> None:
    """Fire-and-forget：把 friendList[] 写到 cached_chats 并合并 label_set。

    兼容两种条目 schema：
      - 新接口 geekFilterByLabel：encryptFriendId / friendId / jobName / bossTitle / ...
      - 旧接口 getGeekFriendList：encryptUid / uid / title (== 招聘官职位) / ...
        （cmd_get_friend_list 把 zpData.result 改名为 friendList，但条目内部字段没动）
    """
    try:
        if not isinstance(data, dict):
            return
        raw = data.get("raw") or {}
        zp = raw.get("zpData") or {}
        friends = zp.get("friendList") or []
        if not isinstance(friends, list) or not friends:
            return
        for f in friends:
            if not isinstance(f, dict):
                continue
            # 新接口用 encryptFriendId；旧接口用 encryptUid（== encryptBossId）
            efid = f.get("encryptFriendId") or f.get("encryptUid") or f.get("encryptBossId") or ""
            if not efid:
                continue
            try:
                await db.upsert_chat(
                    "boss", account_name or "", efid,
                    friend_id=str(f.get("friendId") or f.get("uid") or ""),
                    name=f.get("name"),
                    brand_name=f.get("brandName"),
                    # 旧接口的 title == 招聘官当前职位；新接口的 jobName 优先
                    job_name=f.get("jobName") or f.get("title"),
                    position_name=f.get("positionName"),
                    boss_title=f.get("bossTitle"),
                    job_city=f.get("jobCity"),
                    update_time=f.get("updateTime") or f.get("lastTS"),
                    water_level=f.get("waterLevel"),
                    label_id=int(label_id) if label_id is not None else None,
                    chat_security_id=f.get("chatSecurityId") or f.get("securityId"),
                    encrypt_job_id=f.get("encryptJobId"),
                    last_msg=f.get("lastMsg"),
                    last_msg_ts=f.get("lastTS"),
                    raw_card=f,
                )
            except Exception as e:
                log.debug("_cache_chat_list upsert %s: %s", efid, e)
    except Exception as e:
        log.debug("_cache_chat_list: %s", e)


async def _cache_recruiter_chat_list(data: dict | None, account_name: str, label_id: int) -> None:
    """Fire-and-forget：把招聘方 result[] 写到 cached_recruiter_chats。

    招聘方 result[] 字段稀疏（friendId/encryptFriendId/updateTime/waterLevel +
    labelId=0 偶有 name），name 留 None 让后续 boss_view_geek_detail 等
    机会主义补；upsert_recruiter_chat 内部 COALESCE 不会覆盖已有值。
    """
    try:
        if not isinstance(data, dict):
            return
        raw = data.get("raw") or {}
        zp = raw.get("zpData") or {}
        rows = zp.get("result") or []
        if not isinstance(rows, list) or not rows:
            return
        for r in rows:
            if not isinstance(r, dict):
                continue
            efid = r.get("encryptFriendId") or ""
            if not efid:
                continue
            try:
                # name 显式 None 而不是 "" —— COALESCE 才能跳过覆盖
                nm = r.get("name")
                await db.upsert_recruiter_chat(
                    "boss", account_name or "", efid,
                    friend_id=str(r.get("friendId") or ""),
                    friend_source=r.get("friendSource"),
                    name=nm if nm else None,
                    update_time=r.get("updateTime"),
                    water_level=r.get("waterLevel"),
                    label_id=int(label_id) if label_id is not None else None,
                    raw_card=r,
                )
            except Exception as e:
                log.debug("_cache_recruiter_chat_list upsert %s: %s", efid, e)
    except Exception as e:
        log.debug("_cache_recruiter_chat_list: %s", e)


async def _save_jobs_to_user_interests(
    app_user_id: str, job_list: list[dict], conversation_id: str
) -> None:
    """Fire-and-forget: save all search results to user_job_interests table."""
    for job in job_list:
        encrypt_job_id = job.get("encryptJobId", "")
        if not encrypt_job_id:
            continue
        try:
            await db.upsert_user_job_interest(
                app_user_id,
                encrypt_job_id,
                job_name=job.get("jobName", ""),
                company_name=job.get("brandName", ""),
                salary_desc=job.get("salaryDesc", ""),
                city=job.get("cityName", ""),
                list_security_id=job.get("securityId", ""),
                lid=job.get("lid", ""),
                conversation_id=conversation_id,
            )
        except Exception as e:
            log.debug("_save_jobs_to_user_interests: %s %s", encrypt_job_id, e)


async def _cache_job_detail(data: dict, encrypt_job_id: str) -> None:
    """Fire-and-forget: parse detail result and upsert job detail into cache."""
    try:
        raw = data.get("raw") or {}
        zp_data = raw.get("zpData") or {}
        job_info = zp_data.get("jobInfo") or {}
        if not isinstance(job_info, dict):
            return

        ext_id = (
            job_info.get("encryptId") or        # detail API uses encryptId
            job_info.get("encryptJobId") or      # fallback
            encrypt_job_id                        # last resort from caller
        )
        if not ext_id:
            return

        lng = job_info.get("longitude")
        lat = job_info.get("latitude")
        await db.upsert_job_detail(
            "boss", ext_id,
            description=job_info.get("postDescription"),
            address=job_info.get("address"),
            longitude=float(lng) if lng is not None else None,
            latitude=float(lat) if lat is not None else None,
            detail_security_id=job_info.get("securityId"),
            raw_detail=data,
        )
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


async def cmd_search_jobs(
    keyword: str,
    city: int = 101010100,
    page: int = 1,
    extra: dict | None = None,
    cookie_id: str = "",
    session_id: str | None = None,
    agent_id: str = "",
    search_session_id: str = "",
    app_user_id: str = "",
) -> dict:
    """搜索职位，同步 listSecurityId 到对应会话的 job_store。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "POST", "boss/search_jobs",
        {"keyword": keyword, "city": city, "page": page, "extra": extra or {},
         "cookie_id": cookie_id},
        timeout_ms=20000, tool_name="boss_search_jobs", agent_id=agent_id,
    )
    data = _unwrap(result)
    raw = data.get("raw") if isinstance(data, dict) else None
    if raw:
        job_list = _extract_job_list(raw)
        if job_list:
            entry.job_store.upsert_from_job_list(job_list)
            uid = app_user_id or entry.app_user_id or ""
            if uid:
                asyncio.ensure_future(
                    _save_jobs_to_user_interests(uid, job_list, search_session_id)
                )
    asyncio.ensure_future(_cache_job_list(data, keyword, city, page, search_session_id))
    return data


async def cmd_get_job_detail(
    encrypt_job_id: str,
    security_id: str = "",
    cookie_id: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取职位详情，同步 detailSecurityId 到对应会话的 job_store。
    code 37（环境异常）时自动等待 20s 并重试一次；仍失败则抛错。
    返回值顶层含 boss_code 字段，方便 agent 快速判断是否成功。
    """
    entry = session_store.resolve_session(session_id)

    # 未传 security_id 时自动从 job_store 取 listSecurityId + lid
    job_ctx = entry.job_store.get(encrypt_job_id)
    if not security_id:
        security_id = (job_ctx.list_security_id if job_ctx else None) or ""
    lid = (job_ctx.lid if job_ctx else "") or ""

    for attempt in range(2):
        await entry.rate_limiter.wait("detail")
        result = await send_command_to(
            entry.session_id, "POST", "boss/get_job_detail",
            {"encrypt_job_id": encrypt_job_id, "security_id": security_id,
             "lid": lid, "cookie_id": cookie_id},
            timeout_ms=20000, tool_name="boss_get_job_detail", agent_id=agent_id,
        )
        data = _unwrap(result)
        raw = data.get("raw") if isinstance(data, dict) else None
        boss_code = raw.get("code", 0) if isinstance(raw, dict) else 0

        if boss_code == 37:
            if attempt == 0:
                log.warning(
                    "cmd_get_job_detail: code 37（环境异常）job=%s，等待 20s 后重试",
                    encrypt_job_id,
                )
                await asyncio.sleep(20)
                continue
            # 重试后仍 37 → 抛 RiskControlSignal,TaskRunner 看到信号会再 等 30s 重试,
            # LLM 路径(非 task)由 agent_loop 截 → 转 action_buttons 提示用户
            from job_common.risk_signals import RiskControlSignal
            raise RiskControlSignal(
                "boss:code_37",
                original_text=(
                    "Boss直聘 检测到环境异常（code 37），当前会话被限速。"
                    "建议优先使用 boss_get_cached_job 查看已缓存的职位详情。"
                ),
                platform="boss",
            )

        # 成功：同步令牌 + 缓存
        if raw:
            sid = _extract_zp_security_id(raw)
            boss_id = _extract_boss_id(raw)
            if sid:
                entry.job_store.update_detail(encrypt_job_id, sid, boss_id)
        asyncio.ensure_future(_cache_job_detail(data, encrypt_job_id))
        if isinstance(data, dict):
            data["boss_code"] = boss_code
        return data

    # unreachable
    raise RuntimeError("cmd_get_job_detail: unexpected exit")


async def cmd_start_chat(
    encrypt_job_id: str,
    security_id: str = "",
    cookie_id: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """发起聊天：detail→friend/add→session/enter 链路。

    Boss 常见返回：
      - friend_add.code == 0            → 打招呼已送达（Boss 会发系统默认 greeting）
      - friend_add.code != 0            → 打招呼失败，整体失败（raise）
      - session_enter.code == 121（请求不合法）→ 对方尚未接受打招呼，无法发自定义消息；
        这不算 start_chat 失败，但会影响后续 boss_send_message；通过 boss_code 透传给 agent
    """
    entry = session_store.resolve_session(session_id)
    quota_tracker.check_or_raise(entry.session_id, "job_application")
    await entry.rate_limiter.wait("chat")
    result = await send_command_to(
        entry.session_id, "POST", "boss/start_chat",
        {"encrypt_job_id": encrypt_job_id, "security_id": security_id,
         "cookie_id": cookie_id},
        timeout_ms=30000, tool_name="boss_start_chat", agent_id=agent_id,
    )
    data = _unwrap(result)
    chat_security_id = ""
    friend_code = 0
    session_code = 0
    if isinstance(data, dict):
        raw = data.get("raw") or {}
        raw_friend = raw.get("friend_add") or {}
        raw_enter = raw.get("session_enter") or {}
        friend_code = raw_friend.get("code", 0) if isinstance(raw_friend, dict) else 0
        session_code = raw_enter.get("code", 0) if isinstance(raw_enter, dict) else 0

        if friend_code != 0:
            msg = raw_friend.get("message") or f"Boss friend_add failed (code {friend_code})"
            raise RuntimeError(
                f"Boss 打招呼失败：{msg}（code {friend_code}）。职位: {encrypt_job_id}"
            )

        sid = _extract_zp_security_id(raw_friend)
        if sid:
            chat_security_id = sid
            entry.job_store.update_chat(encrypt_job_id, sid)
        quota_tracker.increment(entry.session_id, "job_application")

        # 把内层状态透传给 agent
        data["boss_code"] = friend_code
        data["session_enter_code"] = session_code
        if session_code != 0:
            data["note"] = (
                "默认招呼语已送达，但对方尚未接受好友申请（session_enter "
                f"code={session_code}），此时无法通过 boss_send_message 发送自定义内容。"
                "建议等待对方回复后再尝试发送。"
            )
    asyncio.ensure_future(_save_chat_record(
        encrypt_job_id, entry, agent_id, chat_security_id, data
    ))
    return data


async def _save_chat_record(
    encrypt_job_id: str,
    entry: Any,
    agent_id: str,
    chat_security_id: str,
    raw_result: dict,
) -> None:
    """Fire-and-forget: 从缓存读取职位信息后写打招呼记录。"""
    try:
        cached = await db.get_cached_job("boss", encrypt_job_id)
        await db.upsert_chat_record(
            "boss", encrypt_job_id,
            title=cached.get("title") if cached else None,
            company=cached.get("company") if cached else None,
            salary=cached.get("salary") if cached else None,
            city=cached.get("city") if cached else None,
            hr_name=cached.get("hr_name") if cached else None,
            hr_title=cached.get("hr_title") if cached else None,
            encrypt_boss_id=cached.get("encrypt_boss_id") if cached else None,
            chat_security_id=chat_security_id or None,
            account_name=entry.account_name or None,
            app_user_id=entry.app_user_id or None,
            session_id=entry.session_id,
            agent_id=agent_id or None,
            status="sent",
            raw_result=raw_result,
        )
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


async def cmd_send_message(
    encrypt_job_id: str = "",
    content: str = "",
    security_id: str = "",
    encrypt_uid: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """发送消息，使用 chatSecurityId。

    **2026-04-28 新增 encrypt_uid 反查路径**:从 friendList 进入的会话(用户点
    "查看最近消息"分支),LLM 只有 encryptFriendId,可以传 encrypt_uid 让扩展
    从 tokenStore 反查 (encrypt_job_id, chatSecurityId)。扩展同时会自动 enterSession
    激活会话(Boss 要求 sendMsg 前必须 enter)。

    Boss 内层 raw.code 才是真实状态：
      - code == 0   消息送达
      - code == 121 请求不合法（通常是对方未接受好友申请前不允许发自定义内容,
        现在已通过 ext-side 自动 enterSession 大幅减少出现频率）
      - 其他非零    其他错误
    任何非 0 都 raise，避免 MCP 工具层误报 success。
    """
    if not content:
        raise ValueError("content 必填")
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("chat")
    result = await send_command_to(
        entry.session_id, "POST", "boss/send_message",
        {
            "encrypt_job_id": encrypt_job_id,
            "content": content,
            "security_id": security_id,
            "encrypt_uid": encrypt_uid,
        },
        timeout_ms=20000, tool_name="boss_send_message", agent_id=agent_id,
    )
    data = _unwrap(result)
    raw = data.get("raw") if isinstance(data, dict) else None
    boss_code = raw.get("code", 0) if isinstance(raw, dict) else 0
    if boss_code != 0:
        msg = raw.get("message") if isinstance(raw, dict) else ""
        msg = msg or f"Boss send_message failed (code {boss_code})"
        if boss_code == 121:
            raise RuntimeError(
                f"Boss 发送自定义消息被拒绝：{msg}。通常是对方尚未接受好友申请"
                "（先通过 boss_start_chat 的默认打招呼等待对方回复，再发自定义内容）。"
                f"职位: {encrypt_job_id}"
            )
        raise RuntimeError(f"{msg}（Boss code {boss_code}）。职位: {encrypt_job_id}")
    if isinstance(data, dict):
        data["boss_code"] = boss_code
    return data


async def cmd_get_chat_history(
    encrypt_job_id: str = "",
    max_msg_id: str = "",
    security_id: str = "",
    encrypt_uid: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """拉取聊天历史。

    **2026-04-28 新增 encrypt_uid 反查路径**:从 boss_geek_filter_by_label 拿
    到的 friendList 项里**只有** encryptFriendId(== encryptBossId),没有
    encryptJobId 也没有 chatSecurityId。LLM 这时如果硬调,会卡在"没法拿到
    encrypt_job_id"。

    新行为:当 encrypt_job_id / security_id **都为空但 encrypt_uid 非空**时,
    先调扩展 `boss/lookup_chat_token(encrypt_uid)` 从 tokenStore 反查捕获过的
    (encryptJobId, chatSecurityId);找到 → 用反查值;找不到 → 返回结构化错误
    `chat_token_not_captured`,引导前端 / agent 让用户去 Boss 浏览器打开和该
    boss 的对话页(扩展 intercept friend/add 后才填 tokenStore)。
    """
    entry = session_store.resolve_session(session_id)

    # 反查路径:LLM 只拿到 encryptFriendId 时,从扩展 tokenStore 拉双键
    if encrypt_uid and not encrypt_job_id and not security_id:
        lookup_raw = await send_command_to(
            entry.session_id, "GET", "boss/lookup_chat_token",
            {"encrypt_uid": encrypt_uid},
            timeout_ms=5000, tool_name="boss_get_chat_history", agent_id=agent_id,
        )
        lookup = _unwrap(lookup_raw) or {}
        if not lookup.get("found"):
            return {
                "ok": False,
                "error": "chat_token_not_captured",
                "hint": (
                    "尚未在 Boss 浏览器里打开过和该 boss 的对话页 —— 请引导用户:"
                    "(1) 打开 Boss直聘 → 消息页;"
                    "(2) 点击该 boss 的对话(扩展会自动 intercept friend/add 响应);"
                    "(3) 回到这里点'我已打开-重新查看'按钮,本工具会自动反查 token。"
                ),
            }
        encrypt_job_id = lookup.get("encrypt_job_id") or ""
        security_id = lookup.get("chat_security_id") or ""

    if not encrypt_job_id:
        return {
            "ok": False,
            "error": "missing_encrypt_job_id",
            "hint": "encrypt_job_id 必填(或传 encrypt_uid 让后端反查)",
        }

    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "POST", "boss/get_chat_history",
        {"encrypt_job_id": encrypt_job_id, "max_msg_id": max_msg_id, "security_id": security_id},
        timeout_ms=20000, tool_name="boss_get_chat_history", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_session_status(session_id: str | None = None, agent_id: str = "") -> dict:
    """查询当前 session 状态（登录状态、token 情况）。"""
    try:
        ext_raw = await send_command_to(session_id, "GET", "boss/get_session_status", {},
                                        timeout_ms=15000, tool_name="boss_get_session_status",
                                        agent_id=agent_id)
        ext_status = _unwrap(ext_raw)
    except Exception as e:
        ext_status = {"error": str(e)}

    try:
        entry = session_store.resolve_session(session_id)
        job_count = len(entry.job_store.list_all())
        jobs = entry.job_store.list_all()[:10]
    except Exception:
        job_count = 0
        jobs = []

    return {
        "extension_connected": not bool(ext_status.get("error")),
        "extension_status": ext_status,
        "job_store_count": job_count,
        "jobs": jobs,
    }


async def cmd_get_tokens(session_id: str | None = None, agent_id: str = "") -> dict:
    """获取扩展内完整令牌状态。"""
    result = await send_command_to(session_id, "GET", "boss/tokens", {},
                                   timeout_ms=10000, tool_name="boss_get_tokens",
                                   agent_id=agent_id)
    return _unwrap(result)


async def cmd_logout(session_id: str | None = None, agent_id: str = "",
                     cookie_id: str = "", open_login_page: bool = True) -> dict:
    """退出登录：清除扩展 cookies → 重置 job_store。

    open_login_page: 默认 True，扩展清完 cookie 后顺便打开 Boss 登录页，
    方便用户立刻扫码重登（UX 对齐 popup 的"刷新登录状态"按钮）。
    批量脚本场景可传 False 禁用。
    """
    entry = session_store.resolve_session(session_id)
    try:
        raw = await send_command_to(
            entry.session_id, "POST", "boss/logout",
            {"cookie_id": cookie_id, "open_login_page": open_login_page},
            timeout_ms=15000, tool_name="boss_logout", agent_id=agent_id,
        )
        result = _unwrap(raw)
    except Exception as e:
        result = {"ok": False, "error": str(e)}

    # 重置该会话的 job_store 和配额计数（无论扩展侧是否成功都要重置）
    entry.job_store = JobContextStore()
    entry.account_name = ""
    entry.app_user_id = ""
    quota_tracker.reset_session(entry.session_id)

    # 写 DB 事件
    try:
        await db.upsert_session(entry.session_id, account_name="", app_user_id="")
        await db.log_session_event(entry.session_id, agent_id or None, "logout", None)
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass

    await ab.admin_broadcaster.broadcast({
        "event": "session_logout",
        "session_id": entry.session_id,
    })
    return result


async def cmd_set_proxy(proxy_url: str = "", session_id: str | None = None, agent_id: str = "") -> dict:
    """为指定会话设置或清除代理，并通知扩展立即生效。"""
    from proxy_pool import proxy_pool
    entry = session_store.resolve_session(session_id)
    raw = await send_command_to(
        entry.session_id, "POST", "boss/set_proxy", {"proxy_url": proxy_url},
        timeout_ms=10000, tool_name="boss_set_proxy", agent_id=agent_id,
    )
    data = _unwrap(raw)
    # 更新内存 + DB + 代理池
    entry.proxy_url = proxy_url
    proxy_pool.override(entry.session_id, proxy_url)
    try:
        await db.upsert_session(entry.session_id, proxy_url=proxy_url)
        await db.log_session_event(entry.session_id, agent_id or None, "proxy_changed",
                                   proxy_url if proxy_url else "cleared")
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass
    await ab.admin_broadcaster.broadcast({
        "event": "session_proxy_changed",
        "session_id": entry.session_id,
        "proxy_url": proxy_url,
    })
    return data


async def cmd_list_sessions(user_id: str = "") -> dict:
    """列出扩展会话（可按 dinQ user_id 过滤）。"""
    all_sessions = session_store.list_all()
    if user_id:
        return {"sessions": [s for s in all_sessions if s["user_id"] == user_id]}
    return {"sessions": all_sessions}


async def cmd_list_agents(user_id: str = "") -> dict:
    """列出已连接的 agent（可按 dinQ user_id 过滤）。"""
    all_agents = agent_tracker.list_all()
    if user_id:
        return {"agents": [a for a in all_agents if a.get("user_id") == user_id]}
    return {"agents": all_agents}


# ── 候选人缓存 helpers（fire-and-forget）──────────────────────────────────────

async def _cache_geek_list(
    data: dict,
    encrypt_job_id: str,
    keywords: str,
    city: int,
    page: int,
    account_name: str = "",
) -> None:
    """将搜索结果候选人资料快照 + 搜索结果页写入 PostgreSQL（忽略错误）。
    data 是扩展返回的处理后数据：{geeks:[{encryptGeekId,securityId,name,...}], raw:...}
    """
    try:
        # 优先使用扩展已标准化的 data.geeks 列表
        geeks_norm: list[dict] = data.get("geeks") or []
        # 同时尝试从 raw 补充原始字段
        raw_zp = (data.get("raw") or {}).get("zpData") or {}
        raw_list: list[dict] = raw_zp.get("geekList") or raw_zp.get("geeks") or []

        # 建立 eid → raw_geekCard 快速查找
        raw_by_eid: dict[str, dict] = {}
        for g in raw_list:
            gc = g.get("geekCard") or g
            eid = gc.get("encryptGeekId") or gc.get("encGeekId") or ""
            if eid:
                raw_by_eid[eid] = gc

        if not geeks_norm:
            return

        city_code = str(city) if city and city != -1 else ""
        geek_ids: list[str] = []

        for g in geeks_norm:
            eid = g.get("encryptGeekId") or ""
            if not eid:
                continue
            geek_ids.append(eid)
            raw_gc = raw_by_eid.get(eid, {})
            try:
                await db.upsert_geek(
                    "boss", eid,
                    uid=str(g.get("geekId") or ""),
                    name=g.get("name") or raw_gc.get("geekName") or raw_gc.get("name"),
                    city=g.get("city"),
                    work_year=g.get("workYear"),
                    salary=g.get("salary"),
                    current_work=g.get("currentWork"),
                    school=g.get("school"),
                    degree_name=g.get("degree"),
                    active_desc=g.get("activeDesc"),
                    apply_status=g.get("applyStatus"),
                    source_job_id=encrypt_job_id,
                    account_name=account_name,
                    raw_card=raw_gc or None,
                )
                if g.get("securityId"):
                    await db.upsert_geek_detail(
                        "boss", eid,
                        search_security_id=g["securityId"],
                        encrypt_expect_id=g.get("encryptExpectId"),
                    )
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass

        if geek_ids:
            try:
                await db.upsert_geek_search(
                    "boss", encrypt_job_id, keywords or "", city_code, page,
                    geek_ids=geek_ids,
                    has_more=bool(data.get("hasMore")),
                    total_count=data.get("totalCount"),
                )
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


async def _cache_geek_detail(
    data: dict,
    encrypt_geek_id: str,
    account_name: str = "",
    app_user_id: str = "",
    encrypt_job_id: str = "",
) -> None:
    """将候选人详情写入 PostgreSQL。
    支持两种来源：
    - search/geek/info（新）：data.{encryptGeekId, encryptExpectId, detailSecurityId, name, degree, ...}
    - chat/geek/info（旧）：data.raw.zpData.data.{encryptUid, geekName, securityId, ...}
    """
    try:
        eid = data.get("encryptGeekId") or encrypt_geek_id
        if not eid:
            return
        # search/geek/info 格式（新路径）
        if data.get("detailSecurityId") or data.get("name"):
            await db.upsert_geek(
                "boss", eid,
                name=data.get("name"),
                degree_name=data.get("degree"),
                active_desc=data.get("activeDesc"),
                account_name=account_name,
            )
            if data.get("detailSecurityId") or data.get("encryptExpectId"):
                await db.upsert_geek_detail(
                    "boss", eid,
                    detail_security_id=data.get("detailSecurityId"),
                    encrypt_expect_id=data.get("encryptExpectId"),
                    raw_detail=data.get("raw"),
                )
            return
        # chat/geek/info 格式（旧路径兼容）
        raw = data.get("raw") or {}
        info = (raw.get("zpData") or {}).get("data") or {}
        if not info:
            return
        eid2 = info.get("encryptUid") or eid
        gw = info.get("geekWork") or {}
        ge = info.get("geekEdu") or {}
        await db.upsert_geek(
            "boss", eid2,
            uid=str(info.get("uid") or ""),
            name=info.get("geekName") or info.get("name"),
            degree_name=info.get("highestDegreeName") or info.get("degreeName"),
            active_desc=info.get("activeDesc"),
            current_work=gw.get("name") if isinstance(gw, dict) else None,
            school=ge.get("name") if isinstance(ge, dict) else None,
            account_name=account_name,
        )
        if info.get("securityId") or info.get("encryptExpectId"):
            await db.upsert_geek_detail(
                "boss", eid2,
                detail_security_id=info.get("securityId"),
                encrypt_expect_id=info.get("encryptExpectId"),
                raw_detail=data,
            )
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


async def _cache_boss_contact(
    data: dict,
    encrypt_geek_id: str,
    encrypt_job_id: str,
    account_name: str = "",
    app_user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
) -> None:
    """将招聘官进入聊天的动作写入 boss_contact_records（忽略错误）。"""
    try:
        await db.upsert_boss_contact(
            "boss", encrypt_geek_id,
            encrypt_job_id=encrypt_job_id,
            account_name=account_name,
            user_id=app_user_id,
            session_id=session_id,
            agent_id=agent_id,
            status="entered",
            raw_result=data,
        )
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass


# ── 候选人功能（招聘方） ─────────────────────────────────────────────────────

async def cmd_search_candidates(
    encrypt_job_id: str,
    keywords: str = "",
    city: int = -1,
    page: int = 1,
    filters: dict | None = None,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """
    招聘方搜索候选人。使用 /wapi/zpitem/web/boss/search/geeks.json。
    返回 geeks 列表，每项含 encryptGeekId + securityId，可直接用于 boss_geek_info。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "GET", "boss/search_candidates",
        {"encrypt_job_id": encrypt_job_id, "keywords": keywords,
         "city": city, "page": page, "filters": filters or {}},
        timeout_ms=20000, tool_name="boss_search_candidates", agent_id=agent_id,
    )
    data = _unwrap(result)
    # 内存缓存：批量写入 geek_store
    geeks = data.get("geeks") if isinstance(data, dict) else None
    if geeks:
        entry.geek_store.upsert_from_search_list(geeks, source_encrypt_job_id=encrypt_job_id)
    # 持久化缓存（fire-and-forget）
    asyncio.ensure_future(_cache_geek_list(
        data, encrypt_job_id, keywords, city, page, entry.account_name
    ))
    # 返回 geeks 字段以便 Agent 直接读取（兼容扩展侧字段名）
    return data


async def cmd_boss_auto_suggest(
    query: str,
    encrypt_job_id: str,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """招聘方搜索关键词自动补全。返回 suggestions 列表。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "GET", "boss/auto_suggest",
        {"query": query, "encrypt_job_id": encrypt_job_id},
        timeout_ms=10000, tool_name="boss_auto_suggest", agent_id=agent_id,
    )
    return _unwrap(result)


# ── 招聘方职位管理 ───────────────────────────────────────────────────────────

async def _cache_recruiter_jobs(
    jobs: list[dict],
    account_name: str,
    app_user_id: str,
    platform: str = "boss",
) -> None:
    """Fire-and-forget: 批量写入 recruiter_jobs 表。

    platform 参数便于 LinkedIn / Indeed 雇主端复用同一缓存表 —— user_id 列是 TEXT，
    三平台的 app_user_id 格式（数字 / URN 后缀 / hex）都能存。
    """
    for j in jobs:
        try:
            await db.upsert_recruiter_job(
                platform, j["encryptJobId"],
                account_name=account_name, user_id=app_user_id,
                job_name=j.get("jobName", ""), city=j.get("city", ""),
                city_code=j.get("cityCode", ""), area_code=j.get("areaCode", ""),
                salary_desc=j.get("salaryDesc", ""),
                low_salary=j.get("lowSalary", 0), high_salary=j.get("highSalary", 0),
                salary_month=j.get("salaryMonth", 12),
                experience_name=j.get("experienceName", ""),
                degree_name=j.get("degreeName", ""),
                job_type_name=j.get("jobTypeName", ""),
                job_status=j.get("jobStatus", 0),
                job_audit_status=j.get("jobAuditStatus", 0),
                view_count=j.get("viewCount", 0), concat_count=j.get("concatCount", 0),
                add_time=j.get("addTime", 0), update_time=j.get("updateTime", 0),
                raw=j,
            )
        except Exception as e:
            print(f"[db] upsert_recruiter_job failed: {e}", flush=True)


async def cmd_boss_refresh_my_jobs(
    type: int = 0,
    search_str: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取招聘方自己发布的全部职位（自动翻页），写入 recruiter_jobs 缓存表。
    type: 0=全部 5=草稿/待审 6=已过期。返回 {jobs, total} 供 Agent 选择 encryptJobId。"""
    entry = session_store.resolve_session(session_id)
    all_jobs: list[dict] = []
    page = 1
    while True:
        await entry.rate_limiter.wait("search")
        result = await send_command_to(
            entry.session_id, "GET", "boss/my_job_list",
            {"page": page, "type": type, "search_str": search_str},
            timeout_ms=15000, tool_name="boss_refresh_my_jobs", agent_id=agent_id,
        )
        data = _unwrap(result)
        batch = data.get("jobs") or []
        all_jobs.extend(batch)
        if not data.get("hasMore") or not batch:
            break
        page += 1
    asyncio.ensure_future(_cache_recruiter_jobs(
        all_jobs, entry.account_name, entry.app_user_id, platform="boss",
    ))
    return {"jobs": all_jobs, "total": len(all_jobs)}


async def cmd_boss_list_my_jobs(
    keyword: str = "",
    job_status: int | None = None,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """从缓存表读取招聘方已发布职位。若缓存为空，自动调用 cmd_boss_refresh_my_jobs 抓取一次。
    返回 jobs 列表，每项含 encryptJobId / jobName / city / salaryDesc 等，可直接用于 boss_search_candidates。"""
    entry = session_store.resolve_session(session_id)
    # 强隔离：优先按 Boss userId (entry.app_user_id) 过滤。account_name 仅在 app_user_id 未知时兜底。
    jobs = await db.list_recruiter_jobs(
        platform="boss",
        app_user_id=entry.app_user_id or None,
        account_name=(entry.account_name or None) if not entry.app_user_id else None,
        job_status=job_status,
        keyword=keyword or None,
    )
    if not jobs:
        # 缓存为空 → 先拉一次
        refresh = await cmd_boss_refresh_my_jobs(session_id=session_id, agent_id=agent_id)
        jobs = refresh.get("jobs", [])
    return {"jobs": jobs, "total": len(jobs)}


async def cmd_boss_chatted_jobs(
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取有沟通记录的职位列表。返回 {jobs, total}。"""
    entry = session_store.resolve_session(session_id)
    result = await send_command_to(
        entry.session_id, "GET", "boss/chatted_jobs", {},
        timeout_ms=10000, tool_name="boss_chatted_jobs", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_boss_rec_job_list(
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取招聘方简化职位列表（/wapi/zpjob/job/recJobList），适用于快速选择当前在招职位。"""
    entry = session_store.resolve_session(session_id)
    result = await send_command_to(
        entry.session_id, "GET", "boss/rec_job_list", {},
        timeout_ms=10000, tool_name="boss_rec_job_list", agent_id=agent_id,
    )
    data = _unwrap(result)
    return {"onlineJobList": data.get("onlineJobList", []), "raw": data.get("raw")}


async def cmd_get_candidate_detail(
    security_id: str,
    encrypt_uid: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取候选人详情（搜索页视角）。
    使用 /wapi/zpitem/web/boss/search/geek/info?securityId=...
    返回 encryptGeekId、encryptExpectId、encryptJobId、detailSecurityId，
    自动写入 geek_store 令牌链供 boss_contact_candidate / boss_boss_enter 使用。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/get_candidate_detail",
        {"security_id": security_id, "encrypt_uid": encrypt_uid},
        timeout_ms=20000, tool_name="boss_get_candidate_detail", agent_id=agent_id,
    )
    data = _unwrap(result)
    # 更新内存令牌链
    eid = data.get("encryptGeekId") or encrypt_uid
    if eid:
        entry.geek_store.update_from_geek_info(eid, {
            "securityId":      data.get("detailSecurityId", ""),
            "encryptExpectId": data.get("encryptExpectId", ""),
            "encryptUid":      eid,
        })
    # 持久化（fire-and-forget）
    asyncio.ensure_future(_cache_geek_detail(
        data, eid, entry.account_name, entry.app_user_id,
        data.get("encryptJobId", "")
    ))
    # E2-3 跨平台统一 preview shape（cross 模式 agent 可用同一模板处理候选人）
    if isinstance(data, dict):
        data["_preview"] = normalize_candidate_preview(data, "boss")
    return data


async def cmd_contact_candidate(
    encrypt_uid: str,
    job_id: str = "",
    security_id: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """主动沟通候选人（搜索页流程，消耗 candidate_contact 配额）。
    自动补全令牌链：security_id（search 级）→ geek/info → bossEnter。
    流程：若无 detail token 则先调 boss/get_candidate_detail，再调 boss/boss_enter。
    """
    entry = session_store.resolve_session(session_id)
    quota_tracker.check_or_raise(entry.session_id, "candidate_contact")
    await entry.rate_limiter.wait("chat")
    result = await send_command_to(
        entry.session_id, "POST", "boss/contact_candidate",
        {"encrypt_uid": encrypt_uid, "job_id": job_id, "security_id": security_id},
        timeout_ms=30000, tool_name="boss_contact_candidate", agent_id=agent_id,
    )
    data = _unwrap(result)
    quota_tracker.increment(entry.session_id, "candidate_contact")
    # 持久化联系记录
    eid = data.get("encryptGeekId") or encrypt_uid
    if eid:
        entry.geek_store.mark_contacted(eid, contact_job_id=job_id or data.get("encryptJobId", ""))
        asyncio.ensure_future(_cache_boss_contact(
            data, eid,
            encrypt_job_id=job_id or data.get("encryptJobId", ""),
            account_name=entry.account_name, app_user_id=entry.app_user_id,
            session_id=entry.session_id, agent_id=agent_id,
        ))
    return data


# ── 招聘官功能（已有 API 数据支撑）──────────────────────────────────────────

async def cmd_boss_get_geek_info(uid: str, security_id: str,
                                 session_id: str | None = None, agent_id: str = "") -> dict:
    """获取候选人信息（聊天页版）。自动更新 geek_store 令牌链供后续 bossEnter 使用。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(entry.session_id, "GET", "boss/geek_info",
        {"uid": uid, "security_id": security_id},
        timeout_ms=15000, tool_name="boss_geek_info", agent_id=agent_id)
    data = _unwrap(result)
    # 内存缓存：更新 detail_security_id + encrypt_expect_id
    raw = data.get("raw") if isinstance(data, dict) else None
    encrypt_uid = (raw or {}).get("zpData", {}).get("data", {}).get("encryptUid", "") if raw else ""
    if encrypt_uid:
        entry.geek_store.update_from_geek_info(encrypt_uid, data)
    # 持久化缓存（fire-and-forget）
    asyncio.ensure_future(_cache_geek_detail(data, encrypt_uid or uid, entry.account_name))
    return data


async def cmd_boss_enter_session(encrypt_uid: str, encrypt_job_id: str,
                                 security_id: str = "", encrypt_expect_id: str = "",
                                 session_id: str | None = None, agent_id: str = "") -> dict:
    """进入招聘官聊天会话。成功后标记 geek_store 并写入 boss_contact_records。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("chat")
    result = await send_command_to(entry.session_id, "POST", "boss/boss_enter",
        {"encrypt_uid": encrypt_uid, "encrypt_job_id": encrypt_job_id,
         "security_id": security_id, "encrypt_expect_id": encrypt_expect_id},
        timeout_ms=20000, tool_name="boss_boss_enter", agent_id=agent_id)
    data = _unwrap(result)
    # 内存缓存：标记已接触
    entry.geek_store.mark_contacted(encrypt_uid, contact_job_id=encrypt_job_id)
    # 持久化缓存（fire-and-forget）
    ctx = entry.geek_store.get(encrypt_uid)
    asyncio.ensure_future(_cache_boss_contact(
        data, encrypt_uid, encrypt_job_id,
        account_name=entry.account_name,
        app_user_id=entry.app_user_id,
        session_id=entry.session_id,
        agent_id=agent_id,
    ))
    return data


async def cmd_boss_get_chat_history(uid: str = "", encrypt_uid: str = "",
                                    max_msg_id: str = "0", count: int = 20, page: int = 1,
                                    session_id: str | None = None, agent_id: str = "") -> dict:
    """拉取招聘官侧聊天历史。uid 和 encrypt_uid 二选一。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(entry.session_id, "GET", "boss/boss_history",
        {"uid": uid, "encrypt_uid": encrypt_uid, "max_msg_id": max_msg_id,
         "count": count, "page": page},
        timeout_ms=15000, tool_name="boss_boss_history", agent_id=agent_id)
    return _unwrap(result)


async def cmd_resume_preview_check(encrypt_uid: str, authority_id: str = "",
                                   session_id: str | None = None, agent_id: str = "") -> dict:
    """简历预览权限检查。成功后将 authority_id 写入 geek_store。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(entry.session_id, "GET", "boss/resume_preview",
        {"encrypt_uid": encrypt_uid, "authority_id": authority_id},
        timeout_ms=15000, tool_name="boss_resume_preview", agent_id=agent_id)
    data = _unwrap(result)
    # 内存缓存：更新 authority_id
    raw = data.get("raw") if isinstance(data, dict) else None
    auth_id = ((raw or {}).get("zpData") or {}).get("encryptAuthorityId", "") or authority_id
    if auth_id:
        entry.geek_store.update_from_resume_preview(encrypt_uid, auth_id)
        asyncio.ensure_future(db.upsert_geek_detail("boss", encrypt_uid, authority_id=auth_id))
    return data


async def cmd_resume_download(encrypt_uid: str, authority_id: str = "", timestamp: int = 0,
                              session_id: str | None = None, agent_id: str = "") -> dict:
    """下载简历 PDF，返回 base64_pdf 字段。需先调用 cmd_resume_preview_check。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(entry.session_id, "GET", "boss/resume_download",
        {"encrypt_uid": encrypt_uid, "authority_id": authority_id, "timestamp": timestamp},
        timeout_ms=30000, tool_name="boss_resume_download", agent_id=agent_id)
    return _unwrap(result)


async def cmd_filter_by_label(label_id: int, encrypt_job_id: str, sort: str = "",
                              session_id: str | None = None, agent_id: str = "") -> dict:
    """按标签筛选候选人，返回 friendId/encryptFriendId 列表。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(entry.session_id, "POST", "boss/filter_label",
        {"label_id": label_id, "encrypt_job_id": encrypt_job_id, "sort": sort},
        timeout_ms=15000, tool_name="boss_filter_by_label", agent_id=agent_id)
    return _unwrap(result)


async def cmd_geek_mark_job_interest(
    job_id: str,
    collect: bool = True,
    security_id: str = "",
    expect_id: str = "",
    tag: int = 4,
    lid: str = "",
    session_id: str | None = None,
    agent_id: str = "",
    app_user_id: str = "",
) -> dict:
    """收藏或取消收藏职位（求职者视角，调用 Boss 真实 API）。
    collect=True 表示收藏/感兴趣（flag=1），collect=False 表示取消收藏/不感兴趣（flag=0）。
    security_id 可留空，自动从 job_store 取 listSecurityId（需先调用 boss_search_jobs）。
    """
    entry = session_store.resolve_session(session_id)
    # 未传 security_id 时自动从 job_store 取 listSecurityId + lid
    if not security_id:
        job_ctx = entry.job_store.get(job_id)
        if not job_ctx or not job_ctx.list_security_id:
            raise ValueError(
                f"job_id={job_id} 不在当前会话的 job_store 中（未搜索过该职位）。"
                "请先调用 boss_search_jobs 搜索职位，再调用 boss_geek_mark_job_interest。"
            )
        security_id = job_ctx.list_security_id
        if not lid:
            lid = job_ctx.lid or ""
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "POST", "geek/mark_job_interest",
        {"job_id": job_id, "security_id": security_id, "flag": 1 if collect else 0,
         "expect_id": expect_id, "tag": tag, "lid": lid},
        timeout_ms=15000, tool_name="boss_geek_mark_job_interest", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_accept_exchange(message_id: str, security_id: str,
                              session_id: str | None = None, agent_id: str = "") -> dict:
    """接受候选人的联系方式交换请求（索要简历/微信/手机号）。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("chat")
    result = await send_command_to(entry.session_id, "POST", "boss/accept_exchange",
        {"message_id": message_id, "security_id": security_id},
        timeout_ms=15000, tool_name="boss_accept_exchange", agent_id=agent_id)
    return _unwrap(result)


async def cmd_get_quick_replies(session_id: str | None = None, agent_id: str = "") -> dict:
    """招聘方拉取自定义的快捷回复短语模板列表。

    返回: {raw, replies: [str], total: int}。replies 用于 agent 在回复候选人时
    选择/参考 HR 自己沉淀的话术，而不是凭空生成。
    """
    entry = session_store.resolve_session(session_id)
    result = await send_command_to(entry.session_id, "GET", "boss/get_quick_replies",
        None, timeout_ms=10000,
        tool_name="boss_get_quick_replies", agent_id=agent_id)
    return _unwrap(result)


async def cmd_exchange_request(security_id: str, type_: int = 4,
                                session_id: str | None = None, agent_id: str = "") -> dict:
    """招聘方主动向候选人发起交换电话/微信请求（区别于 accept_exchange 被动接受）。

    type: 4=微信，3=电话（按抓包推测，后续可扩展更多类型）
    扩展端会先调 /exchange/test 预检，避免重复发起被风控拦截。

    返回: {raw, blocked: bool, alert_type: int, type, success: bool}
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("chat")
    result = await send_command_to(entry.session_id, "POST", "boss/exchange_request",
        {"security_id": security_id, "type": type_},
        timeout_ms=15000, tool_name="boss_exchange_request", agent_id=agent_id)
    return _unwrap(result)


async def cmd_boss_check_reply_block(
    encrypt_jid: str,
    encrypt_exp_id: str,
    security_id: str,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """招聘方批量联系候选人前的屏蔽预检。

    返回 {blocked: bool, reply_block, hunter_call_chat_limit, raw}。
    blocked=True 说明候选人已屏蔽你，**跳过该人**，不要再 contact / send_message。
    典型用法：boss_rec_geek_list / boss_search_candidates → 对每人调 check_reply_block
             → 过滤掉 blocked=True → 剩下的再 boss_contact_candidate。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "POST", "boss/check_reply_block",
        {"encrypt_jid": encrypt_jid, "encrypt_exp_id": encrypt_exp_id, "security_id": security_id},
        timeout_ms=10000, tool_name="boss_check_reply_block", agent_id=agent_id,
    )
    return _unwrap(result)


# ── 招聘方：互动候选人列表 ────────────────────────────────────────────────────

async def _cache_interaction_geeks(
    geeks: list[dict],
    account_name: str = "",
    source_tag: int = 0,
) -> None:
    """Fire-and-forget：将互动列表候选人写入 cached_geeks + cached_geek_details。"""
    for g in geeks:
        eid = g.get("encryptGeekId") or ""
        uid = str(g.get("geekId") or "")
        sid = g.get("securityId") or ""
        # 用 encryptGeekId 优先；无则用 securityId 作为临时 key
        db_key = eid or sid
        if not db_key:
            continue
        try:
            await db.upsert_geek(
                "boss", db_key,
                uid=uid,
                name=g.get("name"),
                city=g.get("city"),
                work_year=g.get("workYear"),
                salary=g.get("salary"),
                current_work=g.get("currentWork"),
                school=g.get("school"),
                degree_name=g.get("degree"),
                active_desc=g.get("activeDesc"),
                apply_status=g.get("applyStatus"),
                account_name=account_name,
            )
        except Exception as _e:
            log.debug("silently swallowed: %s", _e)
            pass
        if sid:
            try:
                await db.upsert_geek_detail(
                    "boss", db_key,
                    search_security_id=sid,
                )
            except Exception as _e:
                log.debug("silently swallowed: %s", _e)
                pass


async def cmd_boss_get_geek_list(
    tag: int = 2,
    geek_apply_status: int = -1,
    chat_status: int = -1,
    jobid: str = "-1",
    page: int = 1,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取互动候选人列表（看过我/沟通过/待反馈）。
    tag: 2=看过我的, 4=沟通过的, 8=待反馈的。status 自动与 tag 保持一致。
    返回 geeks 列表，每项含 securityId，可用于 boss_get_candidate_detail 获取令牌链后主动沟通。
    候选人资料自动写入 cached_geeks 数据库。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "GET", "boss/geek_list",
        {"tag": tag, "status": tag, "geek_apply_status": geek_apply_status,
         "chat_status": chat_status, "jobid": jobid, "page": page},
        timeout_ms=20000, tool_name="boss_list_interacted_geeks", agent_id=agent_id,
    )
    data = _unwrap(result)
    geeks = data.get("geeks") if isinstance(data, dict) else None
    if geeks:
        # geek_store 只接受有 encryptGeekId 的条目；无则跳过（DB 由 _cache_interaction_geeks 保障）
        store_geeks = [g for g in geeks if g.get("encryptGeekId")]
        if store_geeks:
            entry.geek_store.upsert_from_search_list(store_geeks, source_encrypt_job_id="")
        asyncio.ensure_future(_cache_interaction_geeks(geeks, entry.account_name, tag))
    return data


async def _batch_upsert_match_scores(
    geeks: list[dict],
    app_user_id: str,
    encrypt_job_id: str,
) -> None:
    """F4 fire-and-forget：把扩展端从推荐响应抽出的 matchScore 批量持久化到
    recruiter_geek_interests。只动 match_score 列，其余字段（interested / status
    / notes 等）由 upsert_recruiter_geek_interest 保留原值（见 db.py 1004）。
    """
    if not app_user_id or not encrypt_job_id:
        return
    for g in geeks:
        score = g.get("matchScore")
        geek_id = g.get("encryptGeekId")
        if geek_id and score is not None:
            try:
                await db.upsert_recruiter_geek_interest(
                    app_user_id=app_user_id,
                    platform="boss",
                    encrypt_geek_id=geek_id,
                    encrypt_job_id=encrypt_job_id,
                    match_score=int(score),
                )
            except Exception as e:
                log.warning("[rec_geek_list] match_score 写入失败 geek=%s: %s", geek_id, e)


async def cmd_boss_rec_geek_list(
    job_id: str,
    page: int = 1,
    filters: dict | None = None,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取招聘方职位的推荐牛人列表（/wapi/zpjob/rec/geek/list）。
    filters 支持: age, degree, experience, activation, recentNotView, gender, keyword1。
    候选人资料自动写入 cached_geeks 数据库，securityId 写入 cached_geek_details。
    如扩展端在响应里抽到 matchScore，自动 upsert 到 recruiter_geek_interests。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "GET", "boss/rec_geek_list",
        {"job_id": job_id, "page": page, "filters": filters or {}},
        timeout_ms=20000, tool_name="boss_rec_geek_list", agent_id=agent_id,
    )
    data = _unwrap(result)
    geeks = data.get("geeks") if isinstance(data, dict) else None
    if geeks:
        store_geeks = [g for g in geeks if g.get("encryptGeekId")]
        if store_geeks:
            entry.geek_store.upsert_from_search_list(store_geeks, source_encrypt_job_id=job_id)
        asyncio.ensure_future(_cache_geek_list(data, job_id, "", -1, page, entry.account_name))
        asyncio.ensure_future(
            _batch_upsert_match_scores(geeks, entry.app_user_id, job_id)
        )
    return data


async def cmd_boss_mark_geek_interest(
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
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """标记招聘者对候选人的兴趣（写入 recruiter_geek_interests 表）。
    status: new | viewed | contacted | rejected。interested=True 表示感兴趣。
    """
    entry = session_store.resolve_session(session_id)
    await db.upsert_recruiter_geek_interest(
        app_user_id=entry.app_user_id,
        platform="boss",
        encrypt_geek_id=encrypt_geek_id,
        encrypt_job_id=encrypt_job_id,
        geek_name=geek_name,
        salary=salary,
        city=city,
        degree=degree,
        work_year=work_year,
        search_security_id=search_security_id,
        status=status,
        interested=interested,
        match_score=match_score,
        notes=notes,
    )
    return {"ok": True, "encrypt_geek_id": encrypt_geek_id, "interested": interested}


async def cmd_boss_list_geek_interests(
    encrypt_job_id: str = "",
    interested_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取招聘者标注的候选人兴趣列表。可按职位过滤，可只看感兴趣的。"""
    entry = session_store.resolve_session(session_id)
    rows = await db.get_recruiter_geek_interests(
        app_user_id=entry.app_user_id,
        encrypt_job_id=encrypt_job_id or None,
        interested_only=interested_only,
        limit=limit,
        offset=offset,
    )
    return {"interests": rows}


async def cmd_boss_contact_list(
    filter_json: dict | None = None,
    page: int = 1,
    source: int = 2,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取联系人列表（沟通中的候选人）。
    filter_json: 筛选条件，如 {"geek-apply-status": -1, "chat-status": -1}。
    候选人资料自动写入 cached_geeks 数据库。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "GET", "boss/contact_list",
        {"filter": filter_json or {}, "page": page, "source": source},
        timeout_ms=20000, tool_name="boss_contact_list", agent_id=agent_id,
    )
    data = _unwrap(result)
    geeks = data.get("geeks") if isinstance(data, dict) else None
    if geeks:
        store_geeks = [g for g in geeks if g.get("encryptGeekId")]
        if store_geeks:
            entry.geek_store.upsert_from_search_list(store_geeks, source_encrypt_job_id="")
        asyncio.ensure_future(_cache_interaction_geeks(geeks, entry.account_name))
    return data


async def cmd_boss_view_geek_info(
    encrypt_jid: str,
    expect_id: str,
    security_id: str,
    lid: str = "",
    entrance: int = 2,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """查看互动候选人详情（/wapi/zpjob/view/geek/info/v2）。
    所需参数来自 boss_list_interacted_geeks 或 boss_contact_list 返回的 geekCard：
      encrypt_jid = geekCard.encryptJobId
      expect_id   = geekCard.expectId
      security_id = geekCard.securityId
      lid         = geekCard.lid（可选）
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/view_geek_info",
        {"encrypt_jid": encrypt_jid, "expect_id": expect_id,
         "security_id": security_id, "lid": lid, "entrance": entrance},
        timeout_ms=20000, tool_name="boss_view_geek_info", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_recommend_jobs(page: int = 1, cookie_id: str = "",
                                 session_id: str | None = None, agent_id: str = "") -> dict:
    """获取个性化推荐职位列表。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "POST", "boss/get_recommend_jobs",
        {"page": page, "cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_recommend_jobs", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_job_card(security_id: str, lid: str = "", cookie_id: str = "",
                           session_id: str | None = None, agent_id: str = "") -> dict:
    """获取职位卡片（轻量详情，不触发完整 token 链）。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "POST", "boss/get_job_card",
        {"security_id": security_id, "lid": lid, "cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_job_card", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_job_history(page: int = 1, cookie_id: str = "",
                              session_id: str | None = None, agent_id: str = "") -> dict:
    """获取最近浏览历史职位列表。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "POST", "boss/get_job_history",
        {"page": page, "cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_job_history", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_resume_baseinfo(session_id: str | None = None, agent_id: str = "",
                                   cookie_id: str = "") -> dict:
    """获取简历基本信息（姓名、年龄、学历等）。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/get_resume_baseinfo", {"cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_resume_baseinfo", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_resume_expect(session_id: str | None = None, agent_id: str = "",
                                cookie_id: str = "") -> dict:
    """获取求职期望（职位、城市、薪资期望等）。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/get_resume_expect", {"cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_resume_expect", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_resume_status(session_id: str | None = None, agent_id: str = "",
                                cookie_id: str = "") -> dict:
    """获取简历投递状态汇总。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/get_resume_status", {"cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_resume_status", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_deliver_list(page: int = 1, cookie_id: str = "",
                               session_id: str | None = None, agent_id: str = "") -> dict:
    """获取已投递职位列表。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("search")
    result = await send_command_to(
        entry.session_id, "POST", "boss/get_deliver_list",
        {"page": page, "cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_deliver_list", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_interview_data(session_id: str | None = None, agent_id: str = "",
                                  cookie_id: str = "") -> dict:
    """获取面试邀请数据。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/get_interview_data", {"cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_interview_data", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_friend_list(session_id: str | None = None, agent_id: str = "",
                              cookie_id: str = "") -> dict:
    """获取已沟通 Boss 好友列表。

    旧接口 getGeekFriendList.json 返回 zpData.result[]；新接口 geekFilterByLabel
    返回 zpData.friendList[]。Prompt（modes/base.py）只教模型读 friendList[]，
    所以这里把 result 归一到 friendList，避免回退路径被模型误判为"空"。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/get_friend_list", {"cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_friend_list", agent_id=agent_id,
    )
    data = _unwrap(result)
    if isinstance(data, dict):
        raw = data.get("raw")
        if isinstance(raw, dict):
            zp = raw.get("zpData")
            if isinstance(zp, dict) and "friendList" not in zp \
                    and isinstance(zp.get("result"), list):
                zp["friendList"] = zp.pop("result")
    # 旧接口没有 label 概念，按"全部"（label_id=0）入库
    asyncio.ensure_future(_cache_chat_list(data, entry.account_name, 0))
    return data


# ── 求职者消息中心（2026-04 抓包反推） ──────────────────────────────────────


def _merge_friend_security_tokens(new_data: dict | None, old_data: dict | None) -> None:
    """把旧接口 zpData.result[] 的 securityId / encryptJobId / lastMsg 等
    merge 进新接口 zpData.friendList[]，原地修改 new_data。

    Boss 新接口 geekFilterByLabel 故意精简（不返回 securityId / encryptJobId /
    lastMsg），这些 token 只能从旧接口 getGeekFriendList.json 拿。
    用 encryptFriendId == encryptUid 做 join key（抓包验证 encryptUid==encryptBossId==encryptFriendId）。

    只补缺失字段，不覆盖（防御 schema 漂移）。
    """
    if not isinstance(new_data, dict) or not isinstance(old_data, dict):
        return
    new_zp = (new_data.get("raw") or {}).get("zpData") or {}
    old_zp = (old_data.get("raw") or {}).get("zpData") or {}
    new_friends = new_zp.get("friendList") or []
    old_results = old_zp.get("result") or []
    if not isinstance(new_friends, list) or not isinstance(old_results, list):
        return
    # 按 encryptUid（== encryptFriendId） 建索引；fallback 用 encryptBossId
    old_index: dict[str, dict] = {}
    for r in old_results:
        if not isinstance(r, dict):
            continue
        key = r.get("encryptUid") or r.get("encryptBossId")
        if key:
            old_index[str(key)] = r
    if not old_index:
        return
    for f in new_friends:
        if not isinstance(f, dict):
            continue
        efid = f.get("encryptFriendId")
        if not efid:
            continue
        old = old_index.get(str(efid))
        if not old:
            continue
        # 仅补缺失字段。chatSecurityId 在旧接口里叫 securityId（聊天会话级，
        # 不是搜索 listSecurityId），名字差异由这里统一。
        if not f.get("chatSecurityId") and old.get("securityId"):
            f["chatSecurityId"] = old["securityId"]
        for k_new, k_old in (
            ("encryptJobId", "encryptJobId"),
            ("lastMsg",      "lastMsg"),
            ("lastTime",     "lastTime"),
            ("lastTS",       "lastTS"),
            ("unreadMsgCount", "unreadMsgCount"),
            ("isTop",        "isTop"),
            ("title",        "title"),  # 招聘官职位名 fallback
        ):
            if f.get(k_new) in (None, "", 0) and old.get(k_old) not in (None, ""):
                f[k_new] = old[k_old]


async def cmd_geek_filter_by_label(
    label_id: int = 0,
    encrypt_system_id: str = "",
    name: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """求职者侧消息中心列表（按 tab 筛选）。

    新接口 geekFilterByLabel 字段富但缺 securityId/encryptJobId/lastMsg；旧接口
    getGeekFriendList.json 字段恰好相反。这里并发调两个接口并 merge，让 agent
    一次拿到富信息 + 完整 token chain，下游 boss_geek_get_boss_data /
    boss_get_chat_history 直接可用。
    """
    entry = session_store.resolve_session(session_id)
    # 一次 wait 覆盖整对调用 —— 两个不同 endpoint 并发不会触发 Boss 风控
    # （Boss UI 自己也是同时调 geekFilterByLabel + getGeekFriendList）
    await entry.rate_limiter.wait("detail")
    new_call = send_command_to(
        entry.session_id, "GET", "boss/geek_filter_by_label",
        {"label_id": label_id, "encrypt_system_id": encrypt_system_id, "name": name},
        timeout_ms=15000, tool_name="boss_geek_filter_by_label", agent_id=agent_id,
    )
    old_call = send_command_to(
        entry.session_id, "GET", "boss/get_friend_list", {"cookie_id": ""},
        timeout_ms=15000, tool_name="boss_geek_filter_by_label_merge", agent_id=agent_id,
    )
    new_result, old_result = await asyncio.gather(
        new_call, old_call, return_exceptions=True,
    )
    # 主路径：新接口必须成功
    if isinstance(new_result, BaseException):
        raise new_result
    data = _unwrap(new_result)
    # 旧接口 best-effort —— 失败不阻塞主路径，只是 chat token chain 会缺
    # _merge_friend_security_tokens 直接读 zpData.result[]（不归一为 friendList），
    # 与 cmd_get_friend_list 单独调用时的 result→friendList 归一无关
    if isinstance(old_result, BaseException):
        log.debug("[merge] old getGeekFriendList failed (best-effort): %s", old_result)
    else:
        _merge_friend_security_tokens(data, _unwrap(old_result))
    asyncio.ensure_future(_cache_chat_list(data, entry.account_name, int(label_id)))
    return data


async def cmd_recruiter_chat_list(
    label_id: int = 0,
    sort: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """招聘方全局聊天列表（POST filterByLabel, encJobId=""）。

    与 cmd_filter_by_label（per-job 候选人筛选）严格区分：本工具走的是
    Boss UI 的 "消息" 页 tab 切换，不绑定职位。返回 zpData.result[] 稀疏行。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "POST", "boss/recruiter_chat_list",
        {"label_id": label_id, "sort": sort},
        timeout_ms=15000, tool_name="boss_recruiter_chat_list", agent_id=agent_id,
    )
    data = _unwrap(result)
    asyncio.ensure_future(
        _cache_recruiter_chat_list(data, entry.account_name, int(label_id))
    )
    return data


async def cmd_geek_get_boss_data(
    boss_id: str,
    security_id: str = "",
    boss_source: int = 0,
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """求职者进入聊天前拉 boss 元信息 + 关联职位。

    **2026-04-28 新增 security_id 反查路径**:LLM 从 friendList(由
    boss_geek_filter_by_label 返回)只拿到 encryptFriendId 时,可只传
    boss_id=encryptFriendId,**security_id 留空**。此时:

    - ext-side 的 boss/geek_get_boss_data handler 自动调
      tokenStore.lookupChatTokenByBoss(boss_id) 反查 chatSecurityId
    - geek_filter_by_label 已经在 stage 2 把所有 friend 的 chatSecurityId
      批量写入了 tokenStore,所以反查命中率很高
    - 反查失败时 ext 抛 'chat_token_not_captured' 错误,前端按 hint 引导
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "GET", "boss/geek_get_boss_data",
        {"boss_id": boss_id, "security_id": security_id, "boss_source": boss_source},
        timeout_ms=15000, tool_name="boss_geek_get_boss_data", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_ws_endpoints(
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """获取 Boss WebSocket 服务器列表。"""
    entry = session_store.resolve_session(session_id)
    result = await send_command_to(
        entry.session_id, "GET", "boss/get_ws_endpoints", {},
        timeout_ms=10000, tool_name="boss_get_ws_endpoints", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_msg_history_pull(
    type: int = 0,
    last_id: int = 0,
    secret_id: str = "",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """离线消息增量补拉。"""
    entry = session_store.resolve_session(session_id)
    result = await send_command_to(
        entry.session_id, "GET", "boss/msg_history_pull",
        {"type": type, "last_id": last_id, "secret_id": secret_id},
        timeout_ms=15000, tool_name="boss_msg_history_pull", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_geek_job(security_id: str, cookie_id: str = "",
                           session_id: str | None = None, agent_id: str = "") -> dict:
    """获取互动职位（geekGetJob）。"""
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait("detail")
    result = await send_command_to(
        entry.session_id, "POST", "boss/get_geek_job",
        {"security_id": security_id, "cookie_id": cookie_id},
        timeout_ms=15000, tool_name="boss_get_geek_job", agent_id=agent_id,
    )
    return _unwrap(result)



async def cmd_save_job_interests(
    encrypt_job_ids: list[str],
    session_id: str | None = None,
    agent_id: str = "",
    app_user_id: str = "",
) -> dict:
    """将指定职位保存到用户职位兴趣列表，用于后续跟踪查看/投递状态。"""
    entry = session_store.resolve_session(session_id)
    uid = app_user_id or entry.app_user_id or entry.session_id
    saved = []
    for jid in encrypt_job_ids:
        ctx = entry.job_store.get(jid)
        fields: dict = {}
        if ctx:
            fields = {
                "job_name": ctx.job_name,
                "company_name": ctx.company_name,
                "list_security_id": ctx.list_security_id,
                "lid": ctx.lid,
            }
        await db.upsert_user_job_interest(uid, jid, **fields)
        saved.append(jid)
    return {"saved": len(saved), "encrypt_job_ids": saved, "app_user_id": uid}


async def cmd_list_job_interests(
    status: str = "",
    session_id: str | None = None,
    agent_id: str = "",
    app_user_id: str = "",
) -> dict:
    """查询当前用户的职位兴趣列表（status: new/viewed/applied/rejected，空=全部）。"""
    entry = session_store.resolve_session(session_id)
    uid = app_user_id or entry.app_user_id or entry.session_id
    rows = await db.list_user_job_interests(uid, status=status)
    return {"count": len(rows), "jobs": rows, "app_user_id": uid}


async def cmd_update_job_interest_status(
    encrypt_job_id: str,
    status: str,
    session_id: str | None = None,
    agent_id: str = "",
    app_user_id: str = "",
) -> dict:
    """更新职位兴趣状态（new → viewed → applied / rejected）。"""
    entry = session_store.resolve_session(session_id)
    uid = app_user_id or entry.app_user_id or entry.session_id
    await db.update_user_job_interest_status(uid, encrypt_job_id, status)
    return {"ok": True, "encrypt_job_id": encrypt_job_id, "status": status}


async def cmd_dynamic_dispatch(
    *,
    ext_path: str,
    method: str = "GET",
    cmd_kwargs: dict[str, Any] | None = None,
    tool_name: str = "",
    timeout_ms: int = 20000,
    rate_bucket: str = "default",
    session_id: str | None = None,
    agent_id: str = "",
) -> dict:
    """通用动态命令分发（Phase 2）。

    所有从 yaml 注册的 dynamic MCP tool 都走这一条路：解析会话 → 限速 →
    转发到扩展。模板渲染（{{body.X}} / {{tokens.*.*}}）由扩展端
    request-builder.js 完成；gateway 这边只把 agent 入参 dict 当做 body
    透传。这样单一真相源在扩展，gateway 升级 yaml schema 时不用改代码。

    rate_bucket: 默认 "default"（0.5–1.5s 抖动）。如果未来需要按命令类型选
    chat/search/detail，加 yaml.metadata.rate_bucket 字段后透传即可。
    """
    entry = session_store.resolve_session(session_id)
    await entry.rate_limiter.wait(rate_bucket)
    body = dict(cmd_kwargs or {})
    result = await send_command_to(
        entry.session_id, method, ext_path, body,
        timeout_ms=timeout_ms,
        tool_name=tool_name or ext_path,
        agent_id=agent_id,
    )
    return _unwrap(result)


# ── DOM 视觉 + 点击 + 导航（v1.6 新增）──────────────────────────────────────
#
# 6 个通用 dispatcher：参数 site 决定路径前缀（boss/linkedin/indeed），
# 各家 mcp_tools_*.py 用偏函数包装出 18 个 @mcp.tool。


async def cmd_get_clickables(
    *, site: str,
    root_selector: str = "body", include_hidden: bool = False, max_items: int = 200,
    session_id: str | None = None, agent_id: str = "",
) -> dict:
    """让扩展回传 Worker Tab 所有可点击元素列表，含 idx + selector + text + rect。
    返回 snapshot_id 5s 内可用 cmd_click_by_idx 点击。"""
    result = await send_command_to(
        session_id, "POST", f"{site}/get_clickables",
        {"rootSelector": root_selector, "includeHidden": include_hidden,
         "maxItems": max_items},
        timeout_ms=20000, tool_name=f"{site}_get_clickables", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_dom_snapshot(
    *, site: str,
    root_selector: str = "body", max_depth: int = 6, max_nodes: int = 500,
    include_text: bool = True,
    session_id: str | None = None, agent_id: str = "",
) -> dict:
    """让扩展回传 Worker Tab 完整 DOM 树（截断）。token 比 clickables 大。"""
    result = await send_command_to(
        session_id, "POST", f"{site}/get_dom_snapshot",
        {"rootSelector": root_selector, "maxDepth": max_depth,
         "maxNodes": max_nodes, "includeText": include_text},
        timeout_ms=20000, tool_name=f"{site}_get_dom_snapshot", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_click_by_idx(
    *, site: str,
    snapshot_id: str, idx: int, timeout_ms: int = 5000, fallback_text: bool = True,
    session_id: str | None = None, agent_id: str = "",
) -> dict:
    """用 cmd_get_clickables 拿到的 snapshot_id + idx 点击元素。
    selector 失效时自动用快照里的 text 走 fallback。"""
    result = await send_command_to(
        session_id, "POST", f"{site}/click_by_idx",
        {"snapshot_id": snapshot_id, "idx": idx,
         "timeout_ms": timeout_ms, "fallback_text": fallback_text},
        timeout_ms=max(15000, timeout_ms + 5000),
        tool_name=f"{site}_click_by_idx", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_click_by_text(
    *, site: str,
    text: str, tag: str = "", exact: bool = False,
    root_selector: str = "body", timeout_ms: int = 5000, nth: int = 0,
    session_id: str | None = None, agent_id: str = "",
) -> dict:
    """按页面可见文本点击。多匹配时用 nth 选第几个。selector 漂移场景的稳定备选。"""
    result = await send_command_to(
        session_id, "POST", f"{site}/click_by_text",
        {"text": text, "tag": tag, "exact": exact,
         "root_selector": root_selector, "timeout_ms": timeout_ms, "nth": nth},
        timeout_ms=max(15000, timeout_ms + 5000),
        tool_name=f"{site}_click_by_text", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_wait_for_element(
    *, site: str,
    selector: str, timeout_ms: int = 10000,
    session_id: str | None = None, agent_id: str = "",
) -> dict:
    """等元素出现。click 后等新页面 / 弹窗渲染好再继续。"""
    result = await send_command_to(
        session_id, "POST", f"{site}/wait_for",
        {"selector": selector, "timeout_ms": timeout_ms},
        timeout_ms=max(15000, timeout_ms + 5000),
        tool_name=f"{site}_wait_for", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_navigate_to(
    *, site: str,
    url: str, wait_for_selector: str = "", timeout_ms: int = 15000,
    session_id: str | None = None, agent_id: str = "",
) -> dict:
    """导航 Worker Tab 到指定 URL。host 必须在 site 域内（zhipin / linkedin /
    indeed），跨站 URL 被扩展拒绝。"""
    result = await send_command_to(
        session_id, "POST", f"{site}/navigate_to",
        {"url": url, "wait_for_selector": wait_for_selector,
         "timeout_ms": timeout_ms},
        timeout_ms=max(20000, timeout_ms + 5000),
        tool_name=f"{site}_navigate_to", agent_id=agent_id,
    )
    return _unwrap(result)


async def cmd_get_quota_status(session_id: str | None = None) -> dict:
    """返回当前会话所有配额的今日使用情况（已用/上限/剩余）。"""
    if session_id:
        # 指定了 session_id → 严格查找，找不到报错
        entry = session_store.resolve_session(session_id)
        sid = entry.session_id
    else:
        # 未指定 → 尝试获取默认会话；无扩展连接时用占位 key（返回全零计数）
        try:
            entry = session_store.resolve_session(None)
            sid = entry.session_id
        except Exception:
            sid = "_no_session"
    return quota_tracker.get_all_status(sid)
