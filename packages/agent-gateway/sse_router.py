"""
SSE (Server-Sent Events) 端点 — job-agent-gateway (Phase 2: per-platform sessions)

会话主键: (user_id, role, platform) — 每个用户每个 (role, platform) 都有独立 session。
role: 'jobseeker' | 'recruiter' (extension 硬钉)
platform: 'boss' | 'linkedin' | 'indeed' (前端 sub-tab)

端点:
  POST /agent/sse?user_id=&role=&platform=       发送消息,SSE 流式接收
  POST /agent/sse/session?user_id=&role=&platform=  init / resume session
  POST /agent/sse/{user_id}/abort?role=&platform=   中止指定 session(缺省 → 全部)
  DELETE /agent/sse/{user_id}?role=&platform=    释放指定 session(缺省 → 全部)
  GET  /agent/sse/sessions                        列出所有 SSE 会话状态

SSE 事件格式(每条以空行结束):
  event: <type>
  data: <json>

  事件类型:
    connected         — 流式开始,含历史长度
    text_delta        — 流式 token
    thinking_delta    — 流式 reasoning
    tool_call         — 工具调用开始
    tool_result       — 工具执行结果
    action_buttons    — 结构化按钮(身份切换 / 重新检查等)
    job_list_card     — 求职结果富卡片
    candidate_list_card — 候选人结果富卡片
    message_end       — 本轮结束
    error             — 错误
    aborted           — 已中止
    done              — 流式结束(data: [DONE])

客户端使用示例 (fetch API):
  const resp = await fetch(
    '/agent/sse?user_id=xxx&role=jobseeker&platform=boss',
    {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: '帮我搜索产品经理职位' }),
    },
  );
  const reader = resp.body.getReader();
"""

import asyncio
import json
import logging
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

import config
import db
import ext_version_cache
import preferences_db
import redis_client
import resume_db
from agent_events import _bg, process_agent_turn
from session import UserAgentSession, SessionKey, global_turn_semaphore

log = logging.getLogger(__name__)


# ── role / platform 解析:三处来源(URL > header > body)+ 冲突告警 ──
# Phase 2 audit fix: 之前 `or` 链默默 first-wins,如果前端发不一致(比如 ext
# 升级版 URL 带 role=jobseeker 但 body 还残留 role_type=recruiter)会装作没事,
# 但 session 走 URL 路由,prompt 用的可能是 body —— 难以排查。现在显式记日志。

_VALID_ROLES = ("jobseeker", "recruiter")


def _resolve_role_and_platform(request, body, *, user_id: str):
    """三处来源解析 role + platform,冲突时记 warning。

    优先级:URL ?role= / ?platform=  >  header x-user-role  >  body role_type/platform。

    返回 (role, platform, error_response_or_None)。
    error_response 非 None 时,调用方应直接 return。
    """
    # role: 三源
    url_role     = (request.query_params.get("role") or "").strip()
    header_role  = (request.headers.get("x-user-role", "") or "").strip()
    # audit P2 fix:body 接受 `role`(规范)+ `role_type`(兼容);两者都给不一致时也警告
    body_role_canonical = (body.get("role") or "").strip()
    body_role_legacy    = (body.get("role_type") or "").strip()
    body_role           = body_role_canonical or body_role_legacy
    if body_role_canonical and body_role_legacy and body_role_canonical != body_role_legacy:
        log.warning(
            "body 同时含 role 与 role_type 字段且不一致 user_id=%s role=%s role_type=%s — 选用 role(规范)",
            user_id, body_role_canonical, body_role_legacy,
        )
    # platform: 两源
    url_platform   = (request.query_params.get("platform") or "").strip()
    body_platform  = (body.get("platform") or "").strip()
    request_id     = (request.headers.get("x-request-id") or "").strip() or "-"

    # 优先级 + 冲突告警
    role = url_role or header_role or body_role or "jobseeker"
    role_sources = {
        "url":    url_role,
        "header": header_role,
        "body":   body_role,
    }
    role_present = {k: v for k, v in role_sources.items() if v}
    if len(set(role_present.values())) > 1:
        log.warning(
            "role 来源不一致 user_id=%s req_id=%s sources=%s 选用=%s(URL > header > body)",
            user_id, request_id, role_present, role,
        )

    platform = url_platform or body_platform or "boss"
    platform_present = {k: v for k, v in {"url": url_platform, "body": body_platform}.items() if v}
    if len(set(platform_present.values())) > 1:
        log.warning(
            "platform 来源不一致 user_id=%s req_id=%s sources=%s 选用=%s(URL > body)",
            user_id, request_id, platform_present, platform,
        )

    if role not in _VALID_ROLES:
        return None, None, JSONResponse(
            {"error": f"invalid role: {role}"}, status_code=400,
        )
    # Phase 2 manifest:platform 校验从 platforms_config 单一真相读
    try:
        from platforms_config import list_platforms as _list_platforms
        valid_platforms = set(_list_platforms())
    except Exception:
        valid_platforms = {"boss", "linkedin", "indeed"}
    if platform not in valid_platforms:
        return None, None, JSONResponse(
            {"error": f"invalid platform: {platform}"}, status_code=400,
        )

    return role, platform, None


# ── SSE Session Manager (per-platform: keyed by SessionKey 三元组) ────────────

class SseSessionManager:
    """管理所有 SSE 会话(per-platform 模型,Phase 2)。

    主键: SessionKey(user_id, role, platform) 三元组
    一个用户最多有 6 个并发 session(2 role × 3 platform),lazy spawn。
    Idle sweep 30 分钟无活动关闭(history 已持久化,下次 reopen 仍能拿到)。
    Redis 用复合 key 隔离不同 (role, platform) 的会话状态。
    """

    def __init__(self) -> None:
        self._sessions: dict[SessionKey, UserAgentSession] = {}
        self._lock = asyncio.Lock()
        self.turn_semaphore = global_turn_semaphore
        self._sweep_task: asyncio.Task | None = None

    async def get_or_create(
        self, user_id: str, role: str, platform: str,
    ) -> UserAgentSession | None:
        key = SessionKey(user_id, role, platform)
        async with self._lock:
            if key in self._sessions:
                sess = self._sessions[key]
                sess.last_active_at = time.time()
                return sess
            if len(self._sessions) >= config.MAX_SESSIONS:
                return None

            # Phase 7: try to restore from Redis (cross-worker recovery)
            sess = await self._restore_from_redis(key)
            if sess is None:
                sess = UserAgentSession(user_id=user_id, role_type=role, platform=platform)
            else:
                # Restore 后强制覆盖 role/platform(防 redis 数据漂移)
                sess.role_type = role
                sess.platform = platform

            self._sessions[key] = sess
            return sess

    def get(self, user_id: str, role: str, platform: str) -> UserAgentSession | None:
        return self._sessions.get(SessionKey(user_id, role, platform))

    def get_all_for_user(self, user_id: str) -> list[UserAgentSession]:
        """返回该用户的所有(role, platform)session,供广播 abort / 列表查询用。"""
        return [s for k, s in self._sessions.items() if k.user_id == user_id]

    async def remove(self, user_id: str, role: str, platform: str) -> None:
        key = SessionKey(user_id, role, platform)
        async with self._lock:
            self._sessions.pop(key, None)
        # 同步清 Redis(fire-and-forget)
        _bg(redis_client.delete_session(user_id, role, platform))

    # ── Redis persistence (Phase 7) ─────────────────────────────────────────

    @staticmethod
    async def _restore_from_redis(key: SessionKey) -> UserAgentSession | None:
        """Try to restore a (user, role, platform) session from Redis. None if not found."""
        messages, meta = await redis_client.load_session(key.user_id, key.role, key.platform)
        if messages is None:
            return None
        sess = UserAgentSession(
            user_id=key.user_id,
            role_type=key.role,
            platform=key.platform,
            messages=messages,
        )
        if meta:
            sess.current_mode = meta.get("current_mode", "search")
            sess.entry_type = meta.get("entry_type", "")
            sess.user_tier = meta.get("user_tier", "")
            sess.ext_session_id = meta.get("ext_session_id", "")
            sess.app_user_id = meta.get("app_user_id", "")
            sess.stable_browser_id = meta.get("stable_browser_id", "")
            sess.search_session_id = meta.get("search_session_id", "")
            sess.turn_index = int(meta.get("turn_index", 0))
            db_sid = meta.get("db_session_id", "")
            sess.db_session_id = int(db_sid) if db_sid and db_sid != "None" else None
            sess.title_set = meta.get("title_set", "") == "1"
            _tu = meta.get("last_turn_tool_usage", "")
            if _tu:
                try:
                    import json as _json
                    sess.last_turn_tool_usage = set(_json.loads(_tu))
                except Exception:
                    sess.last_turn_tool_usage = set()
        log.info("SSE session restored from Redis: %s messages=%d mode=%s",
                 key, len(messages), sess.current_mode)
        return sess

    @staticmethod
    def session_meta(sess: UserAgentSession) -> dict[str, str]:
        """Extract session metadata dict for Redis persistence."""
        import json as _json
        return {
            "current_mode": sess.current_mode,
            # role + platform 不在 meta 里,因为已在 Redis 复合 key 里
            "entry_type": sess.entry_type,
            "user_tier": sess.user_tier,
            "ext_session_id": sess.ext_session_id,
            "app_user_id": sess.app_user_id,
            "stable_browser_id": sess.stable_browser_id,
            "search_session_id": sess.search_session_id,
            "turn_index": str(sess.turn_index),
            "db_session_id": str(sess.db_session_id) if sess.db_session_id else "",
            "title_set": "1" if sess.title_set else "0",
            "last_turn_tool_usage": _json.dumps(
                sorted(sess.last_turn_tool_usage), ensure_ascii=False,
            ) if sess.last_turn_tool_usage else "",
        }

    def list_all(self) -> list[dict]:
        result = []
        for key, sess in self._sessions.items():
            running = sess.task is not None and not sess.task.done()
            result.append({
                "user_id": key.user_id,
                "role": key.role,
                "platform": key.platform,
                "transport": "sse",
                "history_length": len(sess.messages),
                "turn_count": sum(1 for m in sess.messages if m.get("role") == "user"),
                "running": running,
                "current_tool": sess.current_tool if running else None,
                "last_active_at": sess.last_active_at,
                "current_mode": sess.current_mode,
            })
        return result

    # ── Idle sweep ───────────────────────────────────────────────────────────

    def start_idle_sweep(self) -> None:
        """Start the background idle sweep task. Call from server _startup()."""
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._idle_sweep())

    def stop_idle_sweep(self) -> None:
        if self._sweep_task and not self._sweep_task.done():
            self._sweep_task.cancel()

    async def _idle_sweep(self) -> None:
        """Remove sessions idle longer than SESSION_IDLE_TTL. Runs every 60s."""
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                ttl = config.SESSION_IDLE_TTL
                stale: list[SessionKey] = []
                for key, sess in self._sessions.items():
                    if sess.task and not sess.task.done():
                        continue
                    if now - sess.last_active_at > ttl:
                        stale.append(key)
                for key in stale:
                    sess = self._sessions.get(key)
                    if sess and sess.db_session_id:
                        _bg(db.close_session(sess.db_session_id))
                    _bg(redis_client.delete_session(key.user_id, key.role, key.platform))
                    async with self._lock:
                        self._sessions.pop(key, None)
                if stale:
                    log.info("SSE idle sweep: removed %d stale sessions: %s",
                             len(stale), [str(k) for k in stale[:5]])
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("SSE idle sweep error: %s", exc)


sse_manager = SseSessionManager()


# ── 欢迎消息（init 模式，无 LLM 调用）────────────────────────────────────────────
#
# 这是首次连接 SSE 时直接发的 canned 文案(不走 LLM,~0ms)。**必须**与
# modes/base.py 的 WELCOME_TEMPLATE_RULES 保持一致 —— LLM 路径用 RULES 生成,
# init 路径用本字典,两条路径输出的欢迎文案必须 byte-by-byte 同义,否则用户
# 看到的能力描述会自相矛盾。
#
# 当前产品策略:
# - Boss:找工作 / 招人 二选一(双语)
# - Indeed:**不做投递**(INDEED_ADDON 已声明),求职面只有 search/匹配度/详情;
#   招聘面只有 search resumes/筛选/匹配度/发消息,**不要**写 "Apply for jobs"
#   或 "Post & manage jobs"
# - LinkedIn:找工作 / 找人(不是"招人")—— 同账号既可搜人也可加好友
_WELCOME: dict[str, dict[str, str]] = {
    "boss": {
        "zh": (
            "您好！我是 Boss直聘助手 👋\n\n"
            "我可以帮您在 Boss直聘 上高效地：\n"
            "- **找工作**：智能搜索职位、分析岗位匹配度、一键打招呼、管理消息回复\n"
            "- **招人**：智能搜索候选人、分析人才匹配度、批量打招呼、管理候选人消息\n\n"
            "请问您今天想做什么？"
        ),
        "en": (
            "Hi! I'm your Boss直聘 Assistant 👋\n\n"
            "I can help you on Boss直聘:\n"
            "- **Find jobs** — smart search, fit analysis, one-click greetings, manage replies\n"
            "- **Hire** — find candidates, fit analysis, bulk greetings, manage messages\n\n"
            "What would you like to do today?"
        ),
    },
    "linkedin": {
        "zh": (
            "您好！我是 DINQ LinkedIn 助手 👋\n\n"
            "我可以帮您在 LinkedIn 上高效地：\n"
            "- **找工作**：智能搜索职位、分析岗位匹配度、向招聘经理发送个性化消息、管理消息回复\n"
            "- **找人**：智能搜索候选人、分析匹配度、发送个性化消息、管理消息回复\n\n"
            "请问您今天想做什么？"
        ),
        "en": (
            "Hi! I'm your DINQ LinkedIn Assistant 👋\n\n"
            "I can help you on LinkedIn:\n"
            "- **Find jobs** — smart search, fit analysis, personalized messages to recruiters, manage replies\n"
            "- **Find people** — smart search, fit analysis, personalized messages, manage messages\n\n"
            "What would you like to do today?"
        ),
    },
    "indeed": {
        "zh": (
            "您好！我是 DINQ Indeed 助手，我可以帮您在 Indeed 上高效地找工作或招人。\n\n"
            "找工作：智能搜索职位、分析岗位匹配度、查看职位详情\n\n"
            "招人：智能搜索候选人简历、筛选申请人、分析匹配度、发送消息\n\n"
            "请问您今天想做什么？"
        ),
        "en": (
            "Hi! I'm your DINQ Indeed assistant. I can help you find jobs or hire on Indeed.\n\n"
            "Find jobs: smart search, fit analysis, view job details\n\n"
            "Hire: search resumes, screen applicants, fit analysis, send messages\n\n"
            "What would you like to do today?"
        ),
    },
}

# Keep backwards-compatible names used in older code paths
_WELCOME_ZH = _WELCOME["boss"]["zh"]
_WELCOME_EN = _WELCOME["boss"]["en"]


def _get_welcome(platform: str, lang: str, role: str = "") -> str:
    """Phase 1 改造:role 给定时优先从 (role, platform) cell 读 welcome
    (单一真相,跟 LLM system prompt 同源)。role='' 时降级到旧 _WELCOME 字典。"""
    lang_key = "en" if lang.lower().startswith("en") else "zh"
    if role in ("jobseeker", "recruiter"):
        try:
            from modes.cells import get_cell
            cell = get_cell(role, platform or "boss")
            return cell.welcome_en if lang_key == "en" else cell.welcome_zh
        except Exception:
            pass
    platform_key = platform if platform in _WELCOME else "boss"
    return _WELCOME[platform_key][lang_key]


# ── SSE 格式化 ─────────────────────────────────────────────────────────────────

def _fmt(event_type: str, payload) -> str:
    """格式化单条 SSE 事件。payload 可以是 dict 或字符串。"""
    data = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)
    return f"event: {event_type}\ndata: {data}\n\n"


# ── Agent Turn 生成器 ─────────────────────────────────────────────────────────

async def _sse_turn(request: Request, sess: UserAgentSession, text: str, turn_index: int):
    """
    异步生成器：驱动 process_agent_turn，yield 已格式化的 SSE 字符串。

    - 通过 asyncio.Queue 解耦 producer（process_agent_turn）和 consumer（yield 侧）
    - 每 0.5 s 检查一次客户端是否断开；断开则取消 producer task
    - keep-alive comment 每次超时发送，防止代理断连
    """
    queue: asyncio.Queue = asyncio.Queue()

    # ── producer：将 agent 事件放入 queue ─────────────────────────────────
    async def _producer():
        start_seq = len(sess.messages)
        sess.trim_messages()
        try:
            async for event in process_agent_turn(
                sess.messages, sess.user_id, sess.ext_session_id, sess.app_user_id,
                sess.db_session_id, text, turn_index,
                start_seq=start_seq,
                stable_browser_id=sess.stable_browser_id,
                search_session_id=sess.search_session_id,
                platform=sess.platform,
                user_tier=getattr(sess, "user_tier", ""),
                request_id=getattr(sess, "request_id", ""),
                current_mode=sess.current_mode,
                role_type=sess.role_type,
                language=sess.language,
                last_turn_tool_usage=sess.last_turn_tool_usage,
            ):
                etype = event.get("type")
                if etype == "_tool_state":
                    sess.current_tool = event.get("tool")
                    continue
                if etype == "_session_refreshed":
                    sess.ext_session_id = event.get("new_ext_session_id", sess.ext_session_id)
                    continue
                if etype == "_language_updated":
                    new_lang = event.get("language", "")
                    sess.language = new_lang
                    _bg(preferences_db.save_user_language(sess.user_id, new_lang))
                    continue
                if etype == "mode_detected":
                    sess.current_mode = event.get("mode", sess.current_mode)
                    # fall through: 同时转发给客户端，前端据此渲染 mode chip。
                    # 与旁边的 _tool_state / _session_refreshed 不同：这俩是下划线前缀的内部事件；
                    # mode_detected 没有前缀，是 public 事件，客户端有合法消费需求。
                if etype == "turn_tools":
                    # Phase 2：agent_loop 在 turn 结束时 emit 的工具使用快照。
                    # 存到 sess.last_turn_tool_usage 供下一轮 detect_mode 当 hint。
                    # 不转发给前端（内部事件，客户端无需感知）。
                    sess.last_turn_tool_usage = set(event.get("tools") or [])
                    continue
                await queue.put(event)
        except asyncio.CancelledError:
            pass
        finally:
            await queue.put(None)   # sentinel — 通知 consumer turn 结束

    async def _guarded_producer():
        if sse_manager.turn_semaphore.locked():
            await queue.put({"type": "info", "message": "当前排队中，请稍候…"})
        async with sse_manager.turn_semaphore:
            await _producer()

    producer_task = asyncio.create_task(_guarded_producer())
    sess.task = producer_task

    # ── consumer：从 queue 取事件并 yield ────────────────────────────────
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=6.0)
            except asyncio.TimeoutError:
                # 检查客户端断开
                if await request.is_disconnected():
                    log.info("SSE client disconnected: user=%s, cancelling turn", sess.user_id)
                    producer_task.cancel()
                    return
                # keep-alive SSE comment，防止代理/浏览器因无活动断连
                yield ": keepalive\n\n"
                continue

            if event is None:   # sentinel
                break

            etype = event.get("type", "message")
            yield _fmt(etype, event)

    finally:
        if not producer_task.done():
            producer_task.cancel()
        sess.task = None
        sess.current_tool = None

    yield _fmt("done", "[DONE]")


# ── HTTP 端点 ──────────────────────────────────────────────────────────────────

async def sse_chat(request: Request):
    """
    POST /agent/sse?user_id=<id>&role=<jobseeker|recruiter>&platform=<boss|linkedin|indeed>

    Query params(role + platform 是 per-platform session 主键,前端 ext 硬钉):
      role         必填(default jobseeker if missing) — extension type
      platform     必填(default boss if missing) — 前端 sub-tab

    Body JSON:
      text             string  必填,用户消息
      new_session      bool    可选,true = 清空历史
      ext_session_id   string  可选,绑定扩展 session
      app_user_id      string  可选
      entry_type       string  可选
      search_session_id string 可选,搜索会话 ID

    Response: text/event-stream
    """
    # X-User-ID 由 Go gateway 注入，优先级高于 query param
    user_id = (request.headers.get("x-user-id", "")
               or request.query_params.get("user_id", "")).strip()
    request_id = request.headers.get("x-request-id", "").strip()
    user_tier  = request.headers.get("x-user-tier", "").strip()
    # X-Language from client (browser navigator.language); Accept-Language as fallback
    header_language = (
        request.headers.get("x-language", "")
        or request.headers.get("accept-language", "")
    ).strip()
    if not user_id:
        return JSONResponse({"error": "user_id is required"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)

    init_mode          = bool(body.get("init", False))
    text = (body.get("text") or "").strip()
    if not text and not init_mode:
        return JSONResponse({"error": "text is required"}, status_code=400)

    new_session        = bool(body.get("new_session", False)) or init_mode
    ext_session_id    = (body.get("ext_session_id") or body.get("boss_session_id") or "").strip()
    app_user_id        = (body.get("app_user_id")        or "").strip()
    # role + platform 是 per-platform session 的核心键 — 必填,前端 ext 硬钉
    # 优先级 URL > header > body;冲突会 log warning(audit fix P0 #2)
    role_type, platform, _err = _resolve_role_and_platform(request, body, user_id=user_id)
    if _err is not None:
        return _err
    entry_type         = (body.get("entry_type")          or "").strip()
    stable_browser_id  = (body.get("stable_browser_id")  or
                          request.query_params.get("stable_browser_id", "")).strip()
    search_session_id  = (body.get("search_session_id")   or "").strip()

    sess = await sse_manager.get_or_create(user_id, role_type, platform)
    if sess is None:
        return JSONResponse({"error": "服务繁忙,请稍后再试"}, status_code=503)
    _bg(resume_db.upsert_user(user_id, None))

    # 取消进行中的轮次
    if sess.task and not sess.task.done():
        sess.task.cancel()
        try:
            await sess.task
        except (asyncio.CancelledError, Exception):
            pass

    # 新会话：清历史
    if new_session:
        if sess.db_session_id:
            _bg(db.close_session(sess.db_session_id))
            sess.db_session_id = None
        sess.messages.clear()
        sess.turn_index = 0
        sess.title_set = False
        sess.current_tool = None
        # 审核补丁 #R1：mode 状态也要跟着回到冷启动，否则 detect_mode 的
        # sticky / tool_usage_hint 会带着上个会话的尾巴，第一轮就可能被错误
        # 升级到 evaluate/apply。
        sess.current_mode = "search"
        sess.last_turn_tool_usage = set()

    # 语言偏好：DB 存储优先；新会话或无存储时回退到请求头推断
    if not sess.language:
        stored_lang = await preferences_db.get_user_language(user_id)
        sess.language = stored_lang or header_language

    # 更新 session 元数据
    if ext_session_id:
        sess.ext_session_id = ext_session_id
    if app_user_id:
        sess.app_user_id = app_user_id
    if role_type:
        # Phase 1.4 funnel: 用户首次明确角色(jobseeker/recruiter) → 'role_selected'
        # 只在 role_type 从空变为有值时埋,避免每次都重写
        # NOTE: 不能 gate 在 sess.db_session_id 上(那个在下面 get_or_create 才赋值),
        # log_funnel_step 接受 session_id=None,首轮调用直接传 None 即可。
        _was_empty = not sess.role_type
        sess.role_type = role_type
        if _was_empty and user_id and (platform or sess.platform):
            _bg(db.log_funnel_step(
                None, user_id, sess.role_type, platform or sess.platform, "role_selected",
            ))
    if entry_type:
        sess.entry_type = entry_type
    if stable_browser_id:
        sess.stable_browser_id = stable_browser_id
    if search_session_id:
        sess.search_session_id = search_session_id
    if platform:
        sess.platform = platform
    if user_tier:
        sess.user_tier = user_tier
    if request_id:
        sess.request_id = request_id

    # 建立(或 upsert)DB session — per-platform 三元组唯一
    # 每个 turn 都 upsert 一遍,保证 last_active_at 持续更新(idle sweep 用)
    try:
        _role = sess.role_type or "jobseeker"
        _platform = sess.platform or "boss"
        sess.db_session_id = await db.get_or_create_session_id(
            user_id, role=_role, platform=_platform,
            app_user_id=sess.app_user_id,
            user_tier=sess.user_tier,
        )
        if sess.db_session_id and not sess.title_set:
            sess.title_set = False  # 重新允许写 title(reopen 后)
    except Exception as exc:
        log.warning("DB get_or_create_session_id (SSE) failed: %s", exc)
        sess.db_session_id = None

    sess.last_active_at = time.time()
    sess.turn_index += 1
    turn_index = sess.turn_index

    # DB:写用户消息 & 首条消息标题(init 模式不写用户消息)
    if not init_mode and sess.db_session_id:
        if not sess.title_set:
            _bg(db.update_session_title(sess.db_session_id, text[:60]))
            sess.title_set = True
        _bg(db.log_event(
            sess.db_session_id, user_id,
            sess.role_type or "jobseeker",
            sess.platform or "boss",
            "user_message",
            turn_index=turn_index,
            content=text,
        ))

    log.info("SSE turn start: user=%s request_id=%s tier=%s turn=%d history=%d init=%s text=%r",
             user_id, request_id, user_tier, turn_index, len(sess.messages), init_mode, text[:60])

    async def _stream():
        # Phase 2: 按当前 session role 拉对应 ext kind 的版本(60s per-kind 缓存)
        # role='jobseeker' → ext_kind='jobseeker';role='recruiter' → 'recruiter'
        _ext_kind = sess.role_type if sess.role_type in ("jobseeker", "recruiter") else "jobseeker"
        _ext_info = await ext_version_cache.get_ext_version(kind=_ext_kind)
        # 先推送 connected 事件（携带历史长度，与 WS 协议对齐）
        yield _fmt("connected", {
            "type": "connected",
            "user_id": user_id,
            "history_length": len(sess.messages),
            "db_session_id": sess.db_session_id,
            # ext_connected 粗略判断：有 ext_session_id 视为已连。精确态由前端
            # postMessage probe 二次验证。
            "ext_connected": bool(sess.ext_session_id),
            "ext_session_id": sess.ext_session_id or "",
            "latest_ext_version": _ext_info.get("version", ""),
            "min_ext_version": _ext_info.get("min_compatible", ""),
            "ext_download_url": _ext_info.get("download_url", ""),
            "ext_chrome_store_url": _ext_info.get("chrome_store_url", ""),
        })

        # init 模式：流式推送硬编码欢迎语，不调用 LLM
        if init_mode:
            # Phase 1.4 funnel: 用户首次开会话 → 'welcome' 步埋点
            # (per-user-platform 去重,只记首次)。fire-and-forget。
            if sess.db_session_id and user_id and sess.platform:
                _bg(db.log_funnel_step(
                    sess.db_session_id, user_id, sess.role_type or "jobseeker",
                    sess.platform, "welcome",
                ))
            lang = sess.language or ""
            welcome = _get_welcome(sess.platform or "boss", lang, role=sess.role_type or "")
            # 分块推送，避免单个超大 SSE 事件
            chunk_size = 8
            for i in range(0, len(welcome), chunk_size):
                yield _fmt("text_delta", {"type": "text_delta", "delta": welcome[i:i + chunk_size]})
            yield _fmt("message_end", {"type": "message_end", "stop_reason": "end_turn"})
            yield _fmt("done", "[DONE]")
            return

        async for chunk in _sse_turn(request, sess, text, turn_index):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",          # 禁用 Nginx 缓冲
        },
    )


def _abort_or_delete_key(request: Request) -> tuple[str, str | None, str | None]:
    """从 path/query 解析 (user_id, role, platform);role/platform 缺省为 None(全用户操作)。"""
    user_id = request.path_params.get("user_id", "").strip()
    role = (request.query_params.get("role") or "").strip() or None
    platform = (request.query_params.get("platform") or "").strip() or None
    return user_id, role, platform


async def sse_abort(request: Request):
    """POST /agent/sse/{user_id}/abort?role=&platform= — 中止当前轮次。
    若 role/platform 缺省 → 中止该用户所有活跃 session。
    """
    user_id, role, platform = _abort_or_delete_key(request)
    if role and platform:
        sess = sse_manager.get(user_id, role, platform)
        if sess is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if sess.task and not sess.task.done():
            sess.task.cancel()
            sess.current_tool = None
            return JSONResponse({"ok": True, "aborted": True, "count": 1})
        return JSONResponse({"ok": True, "aborted": False, "reason": "not running"})

    aborted = 0
    for sess in sse_manager.get_all_for_user(user_id):
        if sess.task and not sess.task.done():
            sess.task.cancel()
            sess.current_tool = None
            aborted += 1
    return JSONResponse({"ok": True, "aborted": aborted > 0, "count": aborted})


async def sse_delete(request: Request):
    """DELETE /agent/sse/{user_id}?role=&platform= — 释放该用户的会话内存态(history 仍在 DB)。
    若 role/platform 缺省 → 释放该用户所有 session。
    """
    user_id, role, platform = _abort_or_delete_key(request)
    if role and platform:
        sess = sse_manager.get(user_id, role, platform)
        if sess is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if sess.task and not sess.task.done():
            sess.task.cancel()
        if sess.db_session_id:
            _bg(db.close_session(sess.db_session_id))
        await sse_manager.remove(user_id, role, platform)
        return JSONResponse({"ok": True, "removed": 1})

    sessions = sse_manager.get_all_for_user(user_id)
    if not sessions:
        return JSONResponse({"error": "no sessions for user"}, status_code=404)
    for sess in sessions:
        if sess.task and not sess.task.done():
            sess.task.cancel()
        if sess.db_session_id:
            _bg(db.close_session(sess.db_session_id))
        await sse_manager.remove(sess.user_id, sess.role_type, sess.platform)
    return JSONResponse({"ok": True, "removed": len(sessions)})


async def sse_init_session(request: Request):
    """
    POST /agent/sse/session?user_id=<id>

    Body JSON（均可选）：
      new_session       bool  — true = 强制新会话（清空历史）
      resume_session_id int   — DB session ID，加载历史消息
      ext_session_id   str
      app_user_id       str
      role_type         str
      entry_type        str

    Response：
      { user_id, db_session_id, history_length }
    """
    # X-User-ID 由 Go gateway 注入，优先级高于 query param
    user_id = (request.headers.get("x-user-id", "")
               or request.query_params.get("user_id", "")).strip()
    request_id = request.headers.get("x-request-id", "").strip()
    user_tier  = request.headers.get("x-user-tier", "").strip()
    if not user_id:
        return JSONResponse({"error": "user_id is required"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}

    new_sess_flag     = bool(body.get("new_session", False))
    resume_id         = body.get("resume_session_id") or None
    ext_session_id   = (body.get("ext_session_id")   or "").strip()
    app_user_id       = (body.get("app_user_id")       or "").strip()
    # role + platform: 三处来源解析,冲突会 log warning(audit fix P0 #2)
    role_type, platform, _err = _resolve_role_and_platform(request, body, user_id=user_id)
    if _err is not None:
        return _err
    entry_type        = (body.get("entry_type")         or "").strip()
    stable_browser_id = (body.get("stable_browser_id") or
                         request.query_params.get("stable_browser_id", "")).strip()

    sess = await sse_manager.get_or_create(user_id, role_type, platform)
    if sess is None:
        return JSONResponse({"error": "服务繁忙,请稍后再试"}, status_code=503)
    _bg(resume_db.upsert_user(user_id, {"app_user_id": app_user_id, "ext_session_id": ext_session_id} if (app_user_id or ext_session_id) else None))

    # 更新元数据
    if ext_session_id:
        sess.ext_session_id = ext_session_id
    if app_user_id:
        sess.app_user_id = app_user_id
    if role_type:
        sess.role_type = role_type
    if entry_type:
        sess.entry_type = entry_type
    if stable_browser_id:
        sess.stable_browser_id = stable_browser_id
    if platform:
        sess.platform = platform
    if user_tier:
        sess.user_tier = user_tier
    if request_id:
        sess.request_id = request_id

    if new_sess_flag:
        # 取消进行中的轮次
        if sess.task and not sess.task.done():
            sess.task.cancel()
        # 关闭旧 DB session
        if sess.db_session_id:
            _bg(db.close_session(sess.db_session_id))
            sess.db_session_id = None
        sess.messages.clear()
        sess.turn_index = 0
        sess.title_set = False
        sess.current_tool = None
        # 审核补丁 #R1：mode 状态一并回到冷启动（与上方 _sse_run 的 new_session
        # 路径对称）。
        sess.current_mode = "search"
        sess.last_turn_tool_usage = set()

    elif resume_id is not None:
        try:
            resume_id = int(resume_id)
            # 验证该 session 属于此 user_id
            pool_row = await (await db._get_pool()).fetchrow(
                "SELECT user_id FROM agent_conv_sessions WHERE id=$1", resume_id
            )
            if pool_row is None or pool_row["user_id"] != user_id:
                return JSONResponse({"error": "resume_session_id not found or not owned by user"}, status_code=404)
            # 加载历史消息
            loaded = await db.load_session_messages(resume_id)
            sess.messages = loaded
            sess.db_session_id = resume_id
            # 重新打开 session（清除 ended_at）
            await (await db._get_pool()).execute(
                "UPDATE agent_conv_sessions SET ended_at=NULL WHERE id=$1", resume_id
            )
            sess.turn_index = sum(1 for m in loaded if m.get("role") == "user")
            sess.title_set = True
        except Exception as exc:
            log.warning("resume_session failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    # 若无 DB session,创建/复用 per-platform 三元组
    if sess.db_session_id is None:
        try:
            sess.db_session_id = await db.get_or_create_session_id(
                user_id,
                role=(sess.role_type or "jobseeker"),
                platform=(sess.platform or "boss"),
                app_user_id=sess.app_user_id,
                user_tier=sess.user_tier,
            )
            sess.title_set = False
        except Exception as exc:
            log.warning("DB get_or_create_session_id (sse_init) failed: %s", exc)
            sess.db_session_id = None

    return JSONResponse({
        "user_id": user_id,
        "db_session_id": sess.db_session_id,
        "history_length": len(sess.messages),
    })


async def sse_list_sessions(request: Request):
    """GET /agent/sse/sessions — 列出所有 SSE 会话"""
    return JSONResponse({"sessions": sse_manager.list_all()})
