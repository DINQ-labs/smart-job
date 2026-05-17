"""
共享 Agent 事件处理生成器。

被 server.py（WebSocket）和 sse_router.py（SSE）共同调用，
消除重复的工具状态追踪、DB 日志、文本累积和错误处理逻辑。

yield 格式：
  普通事件  — dict，由调用方转发给前端
  合成事件  — {"type": "_tool_state", "tool": str|None}
              调用方更新 sess.current_tool 后 continue，不转发前端
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator

import httpx

import config
import db
import redis_client
from agent_loop import run_agent_turn

log = logging.getLogger(__name__)

# 匹配查看职位详情请求中的 encrypt_job_id
_JOB_ID_RE = re.compile(r'encrypt_job_id[：:]\s*([A-Za-z0-9]+)')

# Batch action prefix emitted by the JobListCard UI:
#   __job_action__:{action}:{platform}:{card_id}:{payload}
# view_details      payload = "id1,id2,id3"
# contact_confirmed payload = base64(json.dumps([{job_id, intro}, ...]))
# 兼容旧格式(无 platform, 仅 Boss 时的):
#   __job_action__:{action}:{card_id}:{payload}  → 视为 platform=boss
_JOB_ACTION_RE = re.compile(
    r'^__job_action__:([a-z_]+):(?:([a-z]+):)?([^:]+):(.+)$',
    re.DOTALL,
)

# view_details 按 platform 选工具名 + ID 字段称呼
_DETAIL_TOOL_BY_PLATFORM: dict[str, tuple[str, str]] = {
    "boss":     ("boss_get_job_detail",     "encryptJobId"),
    "linkedin": ("linkedin_get_job_detail", "jobId"),
    "indeed":   ("indeed_get_job_detail",   "jobKey"),
}


def _rewrite_job_action(text: str, language: str) -> tuple[str, str, list[str]]:
    """Rewrite a __job_action__ prefix into a natural-language prompt for Claude.

    Returns (rewritten_text, action, job_ids). action="" means no rewrite.
    """
    if not text:
        return text, "", []
    m = _JOB_ACTION_RE.match(text.strip())
    if not m:
        return text, "", []

    action = m.group(1)
    platform = (m.group(2) or "boss").lower()  # 旧格式无 platform 时默认 boss
    _card_id = m.group(3)
    payload = m.group(4)
    is_en = (language or "").lower().startswith("en")

    if action == "view_details":
        job_ids = [x.strip() for x in payload.split(",") if x.strip()]
        if not job_ids:
            return text, "", []
        tool_name, id_label = _DETAIL_TOOL_BY_PLATFORM.get(
            platform, _DETAIL_TOOL_BY_PLATFORM["boss"],
        )
        n = len(job_ids)
        ids_csv = ", ".join(job_ids)
        # Boss 有本地缓存表（boss_get_cached_job）+ 搜索列表本身带基础字段；
        # LinkedIn / Indeed 没有同级缓存工具，直接走 live detail 即可。
        if platform == "boss":
            if is_en:
                prompt = (
                    f"Please analyze these {n} jobs and give a concise comparison "
                    f"(match tier, pros/cons, who should apply). Follow this plan exactly:\n"
                    f"  1) For EACH encryptJobId, call boss_get_cached_job FIRST — it's free "
                    f"and uses the local cache.\n"
                    f"  2) If cached data has has_detail=true, use it directly; do NOT call "
                    f"boss_get_job_detail.\n"
                    f"  3) Only for jobs where cache is missing or has_detail=false, call "
                    f"boss_get_job_detail. **Call boss_get_job_detail at most 3 times total** "
                    f"to avoid Boss's code=37 rate-limit flag. If more than 3 jobs need live "
                    f"fetching, analyze the first 3 with live data, the remainder from cache/"
                    f"search summary, and tell the user the over-flow was truncated.\n"
                    f"encryptJobIds: {ids_csv}"
                )
            else:
                prompt = (
                    f"请分析这 {n} 个职位并给出简洁对比（匹配度梯队、优缺点、适合什么样的求职者）。"
                    f"严格按以下流程操作：\n"
                    f"  1) 对每个 encryptJobId 先调用 boss_get_cached_job 查本地缓存（免费、不消耗配额）\n"
                    f"  2) 如果缓存里 has_detail=true，直接用缓存数据，**不要**再调 boss_get_job_detail\n"
                    f"  3) 只对缓存里没有详情或 has_detail=false 的职位调用 boss_get_job_detail；"
                    f"**整轮最多调用 3 次 boss_get_job_detail**，避免触发 Boss code=37 风控。"
                    f"如果超过 3 个职位需要 live 拉取，就取前 3 个用 live 数据分析，剩余职位用缓存/"
                    f"搜索结果里的基础字段分析，并告诉用户「已优先分析前 3 个，其余基于列表信息」。\n"
                    f"职位 encryptJobId：{ids_csv}"
                )
        else:
            # LinkedIn / Indeed 现在也有缓存了，镜像 Boss 的 cache-first 流程。
            cache_get_tool = f"{platform}_get_cached_job"
            if is_en:
                prompt = (
                    f"Please analyze these {n} jobs and give a concise comparison "
                    f"(match tier, pros/cons, who should apply). Follow this plan exactly:\n"
                    f"  1) For EACH {id_label}, call {cache_get_tool} FIRST — it's free and "
                    f"uses the local cache.\n"
                    f"  2) If cached data has has_detail=true, use it directly; do NOT call "
                    f"{tool_name}.\n"
                    f"  3) Only for jobs where cache is missing or has_detail=false, call "
                    f"{tool_name}. **Call {tool_name} at most 3 times total** to avoid "
                    f"rate-limit flags. If more than 3 jobs need live fetching, analyze "
                    f"the first 3 with live data and the remainder from cached/search fields, "
                    f"and tell the user the tail was truncated.\n"
                    f"{id_label}s: {ids_csv}"
                )
            else:
                prompt = (
                    f"请分析这 {n} 个职位并给出简洁对比（匹配度梯队、优缺点、适合什么样的求职者）。"
                    f"严格按以下流程操作：\n"
                    f"  1) 对每个 {id_label} 先调用 {cache_get_tool} 查本地缓存（免费、不消耗配额）\n"
                    f"  2) 如果缓存里 has_detail=true，直接用缓存数据，**不要**再调 {tool_name}\n"
                    f"  3) 只对缓存里没有详情或 has_detail=false 的职位调用 {tool_name}；"
                    f"**整轮最多调用 3 次 {tool_name}**，避免触发频次限制。"
                    f"如果超过 3 个职位需要 live 拉取，就取前 3 个用 live 数据分析，剩余职位用缓存/"
                    f"搜索结果里的基础字段分析，并告诉用户「已优先分析前 3 个，其余基于已有信息」。\n"
                    f"职位 {id_label}：{ids_csv}"
                )
        return prompt, action, job_ids

    if action == "contact_confirmed":
        # 仅 Boss 平台走 start_chat + send_message 语义;LinkedIn/Indeed 后续阶段再接
        if platform != "boss":
            log.warning("contact_confirmed received for non-boss platform=%s; ignored", platform)
            return text, "", []
        # payload is base64(json) of [{job_id, intro}, ...]
        import base64 as _b64
        try:
            decoded = _b64.b64decode(payload).decode("utf-8")
            items = json.loads(decoded)
        except Exception as exc:
            log.warning("contact_confirmed payload decode failed: %s", exc)
            return text, "", []
        if not isinstance(items, list) or not items:
            return text, "", []
        rows: list[str] = []
        job_ids: list[str] = []
        for idx, it in enumerate(items, start=1):
            if not isinstance(it, dict):
                continue
            jid = str(it.get("job_id") or "").strip()
            intro = str(it.get("intro") or "").strip()
            if not jid or not intro:
                continue
            job_ids.append(jid)
            intro_escaped = intro.replace('"', '\\"').replace('\n', ' ')
            rows.append(f"{idx}. encrypt_job_id={jid}  开场白=\"{intro_escaped}\"")
        if not rows:
            return text, "", []
        n = len(rows)
        rows_block = "\n".join(rows)
        if is_en:
            prompt = (
                f"Please contact the following {n} jobs in order. For EACH job:\n"
                f"  1) call boss_start_chat(encrypt_job_id=...)\n"
                f"  2) if it succeeds, call boss_send_message(encrypt_job_id=..., content=<the intro>)\n"
                f"Continue to the next job even if one fails. At the end, report a per-job "
                f"success/failure summary.\n\n{rows_block}"
            )
        else:
            prompt = (
                f"请依次联系以下 {n} 个职位。每个职位按以下两步操作：\n"
                f"  1) 调用 boss_start_chat(encrypt_job_id=...) 发起聊天（工具本身会有 3-8 秒随机延迟，避免风控）\n"
                f"  2) 如果第 1 步成功，调用 boss_send_message(encrypt_job_id=..., content=对应开场白) 发送消息\n"
                f"如果某个职位失败，请继续处理下一个，不要中止。全部完成后给出逐条成功/失败的汇总。\n\n"
                f"{rows_block}"
            )
        return prompt, action, job_ids

    return text, "", []

# 双语错误消息
_ERROR_MSGS: dict[str, dict[str, str]] = {
    "payment":    {"zh": "AI 服务额度不足，请检查 API 账户余额",       "en": "AI service quota exceeded, please check your API account balance"},
    "auth":       {"zh": "AI 服务认证失败，请检查 API Key 配置",       "en": "AI service authentication failed, please check your API key configuration"},
    "forbidden":  {"zh": "AI 服务访问被拒绝，请检查 API 权限",         "en": "AI service access denied, please check your API permissions"},
    "rate_limit": {"zh": "AI 服务请求过于频繁，请稍后重试",             "en": "AI service rate limited, please try again later"},
    "overloaded": {"zh": "AI 服务当前繁忙，请稍后重试",                 "en": "AI service is currently busy, please try again later"},
    "connection": {"zh": "AI 服务连接失败，请检查网络",                 "en": "AI service connection failed, please check your network"},
    "timeout":    {"zh": "AI 服务响应超时，请稍后重试",                 "en": "AI service timed out, please try again later"},
    "default":    {"zh": "Agent 内部错误，请稍后重试",                  "en": "Agent internal error, please try again later"},
}


def _err(category: str, language: str) -> str:
    """Return localised error message for given category."""
    lang = "en" if (language or "").startswith("en") else "zh"
    return _ERROR_MSGS.get(category, _ERROR_MSGS["default"])[lang]


async def _save_job_analysis(
    app_user_id: str, encrypt_job_ids: list[str], notes: str, platform: str = "boss"
) -> None:
    """将 agent 分析文字写入 user_job_interests.notes（fire-and-forget）。"""
    if not app_user_id or not encrypt_job_ids or not notes:
        return
    jobs_payload = [{"encrypt_job_id": jid, "notes": notes, "platform": platform} for jid in encrypt_job_ids]
    url = f"{config.BOSS_GATEWAY_URL}/jobs/mark-interested"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"app_user_id": app_user_id, "platform": platform, "jobs": jobs_payload})
    except Exception as exc:
        log.debug("save_job_analysis failed: %s", exc)


async def _try_refresh_session_id(stable_browser_id: str) -> str:
    """通过 stable_browser_id 向 job-api-gateway 查询当前在线 session_id。
    成功返回新 session_id，失败返回空字符串。
    """
    if not stable_browser_id:
        return ""
    url = f"{config.BOSS_GATEWAY_URL}/sessions/by-stable/{stable_browser_id}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        data = resp.json()
        if data.get("ok") and data.get("session_id"):
            return data["session_id"]
    except Exception as exc:
        log.warning("_try_refresh_session_id failed: %s", exc)
    return ""


def _bg(coro):
    """Fire-and-forget：DB 写入失败不影响主流程。"""
    async def _wrap():
        try:
            await coro
        except Exception as exc:
            log.warning("DB log error: %s", exc)
    asyncio.create_task(_wrap())


async def process_agent_turn(
    messages: list[dict],
    user_id: str,
    ext_session_id: str,
    app_user_id: str,
    db_session_id: int | None,
    text: str,
    turn_index: int,
    start_seq: int = 0,
    stable_browser_id: str = "",
    search_session_id: str = "",
    platform: str = "boss",
    user_tier: str = "",
    request_id: str = "",
    current_mode: str = "search",
    role_type: str = "",
    language: str = "",
    last_turn_tool_usage: set[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    共享事件处理生成器。

    - 调用 run_agent_turn() 并迭代原始事件
    - yield {"type": "_tool_state", "tool": name|None} 通知调用方更新 current_tool
    - yield {"type": "_session_refreshed", "new_ext_session_id": ...} 通知调用方更新缓存
    - 累积 assistant 文本，在 message_end 时写入 DB
    - DB fire-and-forget 日志（tool_call / token_usage / tool_result / assistant_text）
    - CancelledError → yield {"type": "aborted"}（DB 记录部分文本），然后 re-raise
    - Exception → yield {"type": "error", "message": ...}
    - 当工具返回 session-not-found 错误时，尝试通过 stable_browser_id 刷新并重试一次
    """
    # 批量动作拦截：把 JobListCard 按钮点击发来的 __job_action__:... 重写成自然语言指令，
    # 让 Claude 按指令链式调用 boss_get_job_detail / boss_start_chat / boss_send_message。
    _job_action = ""
    _action_job_ids: list[str] = []
    if text and text.startswith("__job_action__:"):
        rewritten, _job_action, _action_job_ids = _rewrite_job_action(text, language)
        if _job_action:
            log.info(
                "job_action intercepted action=%s user=%s n=%d",
                _job_action, user_id, len(_action_job_ids),
            )
            text = rewritten

    # 检测是否为"查看职位详情"请求，提取 encrypt_job_id 列表（供 message_end 时保存分析）
    if _job_action == "view_details":
        detail_job_ids = list(_action_job_ids)
    else:
        detail_job_ids = _JOB_ID_RE.findall(text) if text else []

    # Mode tracking: detect switches within this turn
    _active_mode = current_mode
    _mode_switches = 0

    # 外层循环：最多尝试 2 次（首次 + 1 次 session 刷新重试）
    _t0 = time.monotonic()
    for attempt in range(2):
        acc_text = ""
        _need_retry = False

        try:
            async for event in run_agent_turn(
                messages,
                text,
                ext_session_id=ext_session_id,
                app_user_id=app_user_id,
                user_id=user_id,
                search_session_id=search_session_id,
                platform=platform,    # ← cross-platform leak fix(2026-05-10):
                                      # 之前漏传,run_agent_turn 永远 fall back "boss",
                                      # 导致工具列表永远是 boss_*,LLM 在 indeed/linkedin
                                      # cell 也调 boss_check_login,触发 IDENTITY_MISMATCH。
                user_tier=user_tier,
                request_id=request_id,
                current_mode=current_mode,
                role_type=role_type,
                language=language,
                last_turn_tool_usage=last_turn_tool_usage,
            ):
                etype = event.get("type")

                # Track mode changes
                if etype == "mode_detected":
                    new_mode = event.get("mode", _active_mode)
                    if new_mode != _active_mode:
                        _mode_switches += 1
                    _active_mode = new_mode

                # 通知调用方更新 current_tool（合成事件，不转发前端）
                if etype == "tool_call":
                    yield {"type": "_tool_state", "tool": event.get("tool")}
                elif etype in ("tool_result", "message_end", "aborted"):
                    yield {"type": "_tool_state", "tool": None}

                # 累积 assistant 文本
                if etype == "text_delta":
                    acc_text += event.get("delta", "")

                # thinking_done 单条记录完整 reasoning（thinking_delta 不入库，避免行爆）
                if etype == "thinking_done" and db_session_id:
                    _bg(db.log_event(
                        db_session_id, user_id, role_type or "jobseeker", platform or "boss", "thinking",
                        turn_index=turn_index,
                        content=event.get("content", ""),
                        mode=_active_mode,
                    ))

                # DB 记录（fire-and-forget, all events carry mode）
                if db_session_id:
                    if etype == "mode_detected":
                        _bg(db.log_event(
                            db_session_id, user_id, role_type or "jobseeker", platform or "boss", "mode_detected",
                            turn_index=turn_index,
                            content=event.get("mode", ""),
                            mode=_active_mode,
                        ))
                    elif etype == "tool_call":
                        _bg(db.log_event(
                            db_session_id, user_id, role_type or "jobseeker", platform or "boss", "tool_call",
                            turn_index=turn_index,
                            tool_name=event.get("tool"),
                            content=json.dumps(event.get("input", {}), ensure_ascii=False),
                            mode=_active_mode,
                        ))
                    elif etype == "token_usage":
                        _bg(db.log_event(
                            db_session_id, user_id, role_type or "jobseeker", platform or "boss", "token_usage",
                            turn_index=turn_index,
                            input_tokens=event.get("input_tokens"),
                            output_tokens=event.get("output_tokens"),
                            cache_creation_input_tokens=event.get("cache_creation_input_tokens"),
                            cache_read_input_tokens=event.get("cache_read_input_tokens"),
                            cost_usd=event.get("cost_usd"),
                            mode=_active_mode,
                        ))
                    elif etype == "tool_result":
                        tool_name_val = event.get("tool")
                        _JOB_TOOLS = {
                            "boss_search_jobs", "boss_get_recommend_jobs", "boss_rec_job_list",
                            "linkedin_search_jobs", "indeed_search_jobs",
                        }
                        is_job_tool_ok = (
                            tool_name_val in _JOB_TOOLS
                            and event.get("ok")
                            and event.get("data")
                        )
                        tool_content = (
                            json.dumps(event.get("data", {}), ensure_ascii=False)
                            if is_job_tool_ok
                            else event.get("preview", "")
                        )
                        _bg(db.log_event(
                            db_session_id, user_id, role_type or "jobseeker", platform or "boss", "tool_result",
                            turn_index=turn_index,
                            tool_name=tool_name_val,
                            content=tool_content,
                            ok=event.get("ok", True),
                            mode=_active_mode,
                        ))
                        # Phase 1.4 funnel: ok=true 的关键工具触发增长漏斗事件
                        # (per-user-platform 去重,只记首次)。fire-and-forget。
                        # 三平台 × 双角色覆盖:
                        #   first_search:
                        #     jobseeker: boss_search_jobs / boss_get_recommend_jobs / boss_rec_job_list /
                        #                linkedin_search_jobs / indeed_search_jobs
                        #     recruiter: *_search_candidates / linkedin_recruiter_search /
                        #                indeed_employer_search_resumes / indeed_employer_find_applicants /
                        #                boss_rec_geek_list (Boss 推荐人才列表)
                        #   first_apply: linkedin_apply_job / indeed_apply* (Boss 没有独立 apply,
                        #                走 boss_start_chat 算 first_message)
                        #   first_message: *_send_message / *_start_chat / *_contact_candidate /
                        #                  linkedin_reply_to_conversation / linkedin_recruiter_send_inmail
                        if event.get("ok") and tool_name_val:
                            tn = tool_name_val
                            funnel_step = None
                            if tn in _JOB_TOOLS:
                                funnel_step = "first_search"
                            elif tn.endswith("_search_candidates"):
                                funnel_step = "first_search"
                            elif tn in ("linkedin_recruiter_search",
                                        "indeed_employer_search_resumes",
                                        "indeed_employer_find_applicants",
                                        "boss_rec_geek_list"):
                                funnel_step = "first_search"
                            elif tn.endswith("_apply_job") or tn.endswith("_apply"):
                                funnel_step = "first_apply"
                            elif tn.endswith("_send_message") or tn.endswith("_start_chat") \
                                    or tn.endswith("_contact_candidate") \
                                    or tn.endswith("_reply_to_conversation") \
                                    or tn == "linkedin_recruiter_send_inmail":
                                funnel_step = "first_message"
                            if funnel_step:
                                _bg(db.log_funnel_step(
                                    db_session_id, user_id, role_type or "jobseeker", platform, funnel_step,
                                ))
                    elif etype == "message_end":
                        if acc_text:
                            _bg(db.log_event(
                                db_session_id, user_id, role_type or "jobseeker", platform or "boss", "assistant_text",
                                turn_index=turn_index,
                                content=acc_text,
                                mode=_active_mode,
                            ))
                            # 若本轮为职位详情查看，将分析文字写入 user_job_interests.notes
                            if detail_job_ids and app_user_id:
                                _bg(_save_job_analysis(app_user_id, detail_job_ids, acc_text, platform=platform))
                            acc_text = ""
                        # 持久化本轮全部新消息（fire-and-forget）
                        _bg(db.append_messages(db_session_id, messages, start_seq))
                        # Update session mode info
                        _bg(db.update_session_mode(db_session_id, _active_mode, _mode_switches))
                        # Phase 7: persist to Redis for cross-worker recovery
                        # role + platform 现在在 Redis 复合 key 里,不再写入 meta
                        _bg(redis_client.save_session(
                            user_id,
                            role_type or "jobseeker",
                            platform or "boss",
                            messages,
                            {
                                "current_mode": _active_mode,
                                "user_tier": user_tier,
                                "ext_session_id": ext_session_id,
                                "app_user_id": app_user_id,
                                "stable_browser_id": stable_browser_id,
                                "search_session_id": search_session_id,
                                "turn_index": str(turn_index),
                                "db_session_id": str(db_session_id) if db_session_id else "",
                            },
                        ))
                        # 记录整轮耗时
                        _turn_ms = round((time.monotonic() - _t0) * 1000, 1)
                        if db_session_id:
                            _bg(db.log_event(
                                db_session_id, user_id, role_type or "jobseeker", platform or "boss", "turn_complete",
                                turn_index=turn_index,
                                duration_ms=_turn_ms,
                                mode=_active_mode,
                            ))

                # 检测 session-not-found 错误：首次尝试 + 有 stable_browser_id 时才重试
                if (
                    attempt == 0
                    and etype == "tool_result"
                    and not event.get("ok", True)
                    and stable_browser_id
                    and "不存在或已断开" in (event.get("preview") or "")
                ):
                    new_sid = await _try_refresh_session_id(stable_browser_id)
                    if new_sid:
                        ext_session_id = new_sid
                        _need_retry = True
                        del messages[start_seq:]   # 重置 messages 到本轮开始前的状态
                        log.info(
                            "Session refreshed for user=%s new_sid=%s, retrying turn",
                            user_id, new_sid[:16],
                        )
                        yield {"type": "_session_refreshed", "new_ext_session_id": new_sid}
                        break   # 中断内层 for，触发外层重试

                yield event

        except asyncio.CancelledError:
            yield {"type": "_tool_state", "tool": None}
            if db_session_id:
                if acc_text:
                    _bg(db.log_event(
                        db_session_id, user_id, role_type or "jobseeker", platform or "boss", "assistant_text",
                        turn_index=turn_index,
                        content=acc_text + " [已中断]",
                    ))
                _turn_ms = round((time.monotonic() - _t0) * 1000, 1)
                _bg(db.log_event(
                    db_session_id, user_id, role_type or "jobseeker", platform or "boss", "aborted",
                    turn_index=turn_index,
                    duration_ms=_turn_ms,
                ))
            yield {"type": "aborted"}
            raise

        except Exception as exc:
            yield {"type": "_tool_state", "tool": None}
            # 解包 ExceptionGroup（anyio task group 包装）以提取根因
            root_exc = exc
            if isinstance(exc, BaseExceptionGroup):
                flat = list(exc.exceptions)
                while flat:
                    e = flat.pop(0)
                    if isinstance(e, BaseExceptionGroup):
                        flat.extend(e.exceptions)
                    else:
                        root_exc = e
                        break
            # 细分 LLM API 错误类型，给用户更明确的提示
            root_str = str(root_exc)
            root_type = type(root_exc).__name__
            error_category = "unknown"
            msg = ""

            from anthropic import APIStatusError, APIConnectionError, APITimeoutError
            if isinstance(root_exc, APIStatusError):
                code = root_exc.status_code
                if code == 402:
                    error_category = "payment"
                    msg = _err("payment", language)
                    log.error("LLM API payment required (402) for user=%s: %s", user_id, root_str[:300])
                elif code == 401:
                    error_category = "auth"
                    msg = _err("auth", language)
                    log.error("LLM API auth error (401) for user=%s: %s", user_id, root_str[:300])
                elif code == 403:
                    error_category = "forbidden"
                    msg = _err("forbidden", language)
                    log.error("LLM API forbidden (403) for user=%s: %s", user_id, root_str[:300])
                elif code == 429:
                    error_category = "rate_limit"
                    msg = _err("rate_limit", language)
                    log.warning("LLM API rate-limited (429) for user=%s: %s", user_id, root_str[:200])
                elif code in (500, 502, 503, 529):
                    error_category = "overloaded"
                    msg = _err("overloaded", language)
                    log.warning("LLM API server error (%d) for user=%s: %s", code, user_id, root_str[:200])
                else:
                    error_category = f"api_{code}"
                    log.exception("LLM API error (%d) for user=%s", code, user_id)
            elif isinstance(root_exc, APIConnectionError):
                error_category = "connection"
                msg = _err("connection", language)
                log.error("LLM API connection error for user=%s: %s", user_id, root_str[:300])
            elif isinstance(root_exc, APITimeoutError):
                error_category = "timeout"
                msg = _err("timeout", language)
                log.warning("LLM API timeout for user=%s: %s", user_id, root_str[:200])
            elif "Overloaded" in root_str or "overloaded" in root_str:
                error_category = "overloaded"
                msg = _err("overloaded", language)
                log.warning("LLM API overloaded for user=%s: %s", user_id, root_str[:200])
            elif "rate_limit" in root_str or root_type == "RateLimitError":
                error_category = "rate_limit"
                msg = _err("rate_limit", language)
                log.warning("LLM API rate-limited for user=%s: %s", user_id, root_str[:200])

            if not msg:
                log.exception("Agent turn error for user=%s", user_id)
                msg = str(root_exc) if config.DEBUG_MODE else _err("default", language)
            _turn_ms = round((time.monotonic() - _t0) * 1000, 1)
            if db_session_id:
                _bg(db.log_event(
                    db_session_id, user_id, role_type or "jobseeker", platform or "boss", "error",
                    turn_index=turn_index,
                    tool_name=error_category,
                    content=f"[{error_category}] {root_exc}",
                    duration_ms=_turn_ms,
                ))
            yield {"type": "error", "message": msg, "category": error_category}
            return   # 通用异常不重试

        if not _need_retry:
            break   # 正常完成，无需重试
