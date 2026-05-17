"""
Job Agent Gateway — SSE 对话服务（端口 8769）。

每个 user_id 获得独立的 Claude Agent 对话会话（SSE 传输）。
实时流式推送：token delta、工具调用、工具结果。
支持中断（abort）、多轮对话、Redis 跨 worker 恢复。
对话完整记录到 PostgreSQL，供后续管理后台反查。
"""
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime

# audit P2 fix:把 repo-root 加到 sys.path,让 `from job_common.* import ...`
# 在 server 启动时一次到位 —— 不再分散到 tasks/engine.py / mcp_client.py 各自
# `sys.path.insert(0, '../job-api-gateway')`
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import psutil
_PROCESS = psutil.Process()
_START_TIME = time.time()

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

import config
import db
import job_evaluator
import resume_db
from agent_events import _bg, process_agent_turn
from resume_router import upload_resume, get_resume_status, get_resume, delete_resume, retry_resume_parse
from preferences_router import get_preferences, save_preferences, suggest_preferences
from autofill_router import (
    autofill_match, autofill_ocr, init_autofill_db,
    get_profile as autofill_get_profile,
    put_profile as autofill_put_profile,
)
from form_template_router import record_template_handler, upload_capture_handler
from form_template_admin import (
    admin_list_templates, admin_get_template, admin_patch_template_field,
    admin_delete_template, admin_list_captures, admin_get_capture,
    admin_enrich_capture, admin_delete_capture, admin_autofill_stats,
)
import form_template_db
import form_template_enrich
import portal_auth
from recruiter_router import (
    get_recruiter_preferences, save_recruiter_preferences,
    get_templates, save_template,
    get_my_jobs,
)
import recruiter_db
from sse_router import sse_chat, sse_abort, sse_delete, sse_list_sessions, sse_init_session, sse_manager
from voice_router import voice_asr_handler, voice_stream_handler
from capture_router import (
    capture_health, capture_session_start, receive_capture,
    list_capture_sessions, get_capture_session as get_capture_session_handler,
    get_capture_requests, delete_capture_session, analyze_capture_session,
    download_capture_raw, download_capture_analysis,
    implement_capture_session,
)
from candidate_resume_router import (
    list_candidate_resumes as cr_list,
    get_candidate_resume as cr_get,
    download_candidate_resume_file as cr_download,
    delete_candidate_resume as cr_delete,
)
from tasks_router import (
    list_templates_handler as tasks_list_templates,
    run_task_handler as tasks_run,
    list_tasks_handler as tasks_list,
    get_task_handler as tasks_get,
    cancel_task_handler as tasks_cancel,
    resume_task_handler as tasks_resume,
    pause_task_handler as tasks_pause,
    patch_task_item_state_handler as tasks_patch_item_state,
    list_recommended_jobs_handler as jobs_recommended,
    evaluate_job_with_resume_handler as jobs_evaluate_with_resume,
    admin_list_tasks_handler as admin_tasks_list,
    admin_task_stats_handler as admin_tasks_stats,
    admin_task_detail_handler as admin_tasks_detail,
    admin_force_cancel_handler as admin_tasks_force_cancel,
    admin_template_dags_handler as admin_template_dags,
    admin_mcp_metrics_handler as admin_mcp_metrics,
)

def _setup_logging():
    os.makedirs(config.LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(config.LOG_DIR, "agent-gateway.log"),
        when="midnight", backupCount=30, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

_setup_logging()
log = logging.getLogger(__name__)



# ── REST endpoints ────────────────────────────────────────────────────────────

async def status(request: Request):
    sse_sessions = sse_manager.list_all()
    sem = sse_manager.turn_semaphore
    mem = _PROCESS.memory_info()
    try:
        db_pool = await db.get_pool_stats()
    except Exception:
        db_pool = {}
    mode_counts: dict[str, int] = {}
    for s in sse_sessions:
        m = s.get("current_mode", "search")
        mode_counts[m] = mode_counts.get(m, 0) + 1

    from modes import MODE_REGISTRY
    available_modes = [
        {"name": m.name, "display_name": m.display_name, "required_tier": m.required_tier}
        for m in MODE_REGISTRY.values()
    ]

    return JSONResponse({
        "ok": True,
        "service": "job-agent-gateway",
        "port": config.PORT,
        "model": config.MODEL,
        "job_api_gateway": config.JOB_API_GATEWAY_URL,
        "max_sessions": config.MAX_SESSIONS,
        "active_sessions": len(sse_sessions),
        "running_sessions": sum(1 for s in sse_sessions if s.get("running")),
        "concurrent_turns": config.CONCURRENT_TURNS,
        "turn_slots_available": sem._value,
        "turn_slots_total": config.CONCURRENT_TURNS,
        "uptime_sec": round(time.time() - _START_TIME, 1),
        "memory": {"rss_bytes": mem.rss, "vms_bytes": mem.vms},
        "db_pool": db_pool,
        "mode_distribution": mode_counts,
        "available_modes": available_modes,
    })


async def list_sessions(request: Request):
    return JSONResponse({"sessions": sse_manager.list_all()})


def _get_session_key(request: Request) -> tuple[str, str, str]:
    """Path: /agent/sse/{user_id}/...  Query: ?role=&platform=
    缺省 role/platform 时默认 jobseeker/boss(向下兼容老 admin UI)。
    """
    user_id = request.path_params["user_id"]
    role = (request.query_params.get("role") or "jobseeker").strip()
    platform = (request.query_params.get("platform") or "boss").strip()
    return user_id, role, platform


async def get_session(request: Request):
    user_id, role, platform = _get_session_key(request)
    sess = sse_manager.get(user_id, role, platform)
    if sess is None:
        return JSONResponse({"error": "session not found"}, status_code=404)
    running = sess.task is not None and not sess.task.done()
    recent = []
    for m in sess.messages[-20:]:
        msg_role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, str):
            recent.append({"role": msg_role, "text": content[:200]})
        elif isinstance(content, list):
            texts = [b.get("text", "") if isinstance(b, dict) else "" for b in content]
            recent.append({"role": msg_role, "text": " ".join(texts)[:200]})
    return JSONResponse({
        "user_id": sess.user_id,
        "role": sess.role_type,
        "platform": sess.platform,
        "app_user_id": sess.app_user_id or None,
        "ext_session_id": sess.ext_session_id or None,
        "history_length": len(sess.messages),
        "turn_count": sum(1 for m in sess.messages if m.get("role") == "user"),
        "running": running,
        "current_tool": sess.current_tool if running else None,
        "last_active_at": sess.last_active_at,
        "current_mode": sess.current_mode,
        "recent_messages": recent,
    })


async def abort_session(request: Request):
    """abort 指定 (user, role, platform) 的 session。
    若 role/platform 缺省 → 中止该用户所有活跃 session(管理员"全部停"语义)。
    """
    user_id = request.path_params["user_id"]
    role = (request.query_params.get("role") or "").strip()
    platform = (request.query_params.get("platform") or "").strip()

    if role and platform:
        sess = sse_manager.get(user_id, role, platform)
        if sess is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if sess.task and not sess.task.done():
            sess.task.cancel()
            sess.current_tool = None
            return JSONResponse({"ok": True, "aborted": True, "count": 1})
        return JSONResponse({"ok": True, "aborted": False, "reason": "not running"})

    # 全停模式
    aborted = 0
    for sess in sse_manager.get_all_for_user(user_id):
        if sess.task and not sess.task.done():
            sess.task.cancel()
            sess.current_tool = None
            aborted += 1
    if aborted == 0:
        return JSONResponse({"ok": True, "aborted": False, "count": 0, "reason": "no running sessions"})
    return JSONResponse({"ok": True, "aborted": True, "count": aborted})


async def delete_session(request: Request):
    """删指定 (user, role, platform) 或该用户所有 session 的内存态(history 仍在 DB)。"""
    user_id = request.path_params["user_id"]
    role = (request.query_params.get("role") or "").strip()
    platform = (request.query_params.get("platform") or "").strip()

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

    # 全删模式
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


# ── Users ─────────────────────────────────────────────────────────────────────

async def list_users(request: Request) -> JSONResponse:
    limit  = min(int(request.query_params.get("limit", 100)), 500)
    offset = int(request.query_params.get("offset", 0))
    try:
        users = await resume_db.list_users_with_resumes(limit=limit, offset=offset)
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)
    connected_ids = {s["user_id"] for s in sse_manager.list_all()}
    for u in users:
        u["is_connected"] = u["user_id"] in connected_ids
    return JSONResponse({"users": users, "limit": limit, "offset": offset})


# ── Mode Stats ────────────────────────────────────────────────────────────────

async def purge_history(request: Request) -> JSONResponse:
    """POST /admin/history/purge — 删除缺少必要字段的历史会话。"""
    try:
        result = await db.purge_sessions_without_messages()
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def mode_stats(request: Request) -> JSONResponse:
    days = min(int(request.query_params.get("days", 7)), 90)
    try:
        stats = await db.get_mode_stats(days)
        return JSONResponse({"stats": stats, "days": days})
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


# ── Admin: errors ─────────────────────────────────────────────────────────────

# ── 前端 JS 错误上报(audit P1 fix)─────────────────────────────────────
#
# 背景:sidepanel 崩了后端完全看不见 — 现在前端 window.onerror /
# unhandledrejection → POST /errors/client → DB(client_errors 表)。
# 无需鉴权(扩展环境无法做);用 in-memory rate-limit 防滥用。
#
# 频次硬上限:每 user_id 每 60s 最多 50 条;超过静默丢(返回 200,前端不感知)。

import time as _time

_client_err_window: dict[str, tuple[int, float]] = {}  # user_id -> (count, window_start_ts)
_CLIENT_ERR_MAX_PER_MIN = 50
_CLIENT_ERR_WINDOW_SEC = 60


def _client_err_should_drop(user_id: str) -> bool:
    """rate limit:同一 user_id 每分钟 ≤ 50 条。"""
    key = (user_id or "anon")[:64]
    now = _time.monotonic()
    cnt, start = _client_err_window.get(key, (0, now))
    if now - start > _CLIENT_ERR_WINDOW_SEC:
        cnt, start = 0, now
    cnt += 1
    _client_err_window[key] = (cnt, start)
    return cnt > _CLIENT_ERR_MAX_PER_MIN


async def report_client_error(request: Request) -> JSONResponse:
    """POST /errors/client — 前端 JS 错误上报。

    body:
      {
        user_id?: str,
        role?: str,
        platform?: str,
        ext_id?: str,
        ext_version?: str,
        surface?: 'sidepanel'|'popup'|'options'|'background',
        error_type?: 'error'|'unhandledrejection',
        message: str (必填),
        stack?: str,
        src_file?: str, src_line?: int, src_col?: int,
        url?: str,
      }

    前端 user_agent / url 后端会自动从 headers 兜底。
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "body must be JSON"}, status_code=400)

    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"ok": False, "error": "message required"}, status_code=400)

    user_id = (body.get("user_id") or "").strip()
    if _client_err_should_drop(user_id):
        # 超频 → 静默丢(不告诉前端,避免它瞎重试)
        return JSONResponse({"ok": True, "dropped": True})

    user_agent = (body.get("user_agent") or request.headers.get("user-agent") or "").strip()
    url        = (body.get("url") or "").strip()

    _bg(db.record_client_error(
        user_id=user_id,
        role=(body.get("role") or "").strip(),
        platform=(body.get("platform") or "").strip(),
        ext_id=(body.get("ext_id") or "").strip(),
        ext_version=(body.get("ext_version") or "").strip(),
        surface=(body.get("surface") or "sidepanel").strip(),
        error_type=(body.get("error_type") or "error").strip(),
        message=message,
        stack=body.get("stack"),
        src_file=body.get("src_file"),
        src_line=body.get("src_line"),
        src_col=body.get("src_col"),
        user_agent=user_agent,
        url=url,
    ))
    return JSONResponse({"ok": True})


async def admin_client_errors(request: Request) -> JSONResponse:
    """GET /admin/client-errors?hours=24&limit=20 — 前端错误聚合视图。"""
    try:
        hours = max(1, min(int(request.query_params.get("hours", "24")), 168))
        limit = max(1, min(int(request.query_params.get("limit", "20")), 200))
    except ValueError:
        return JSONResponse({"error": "hours/limit 必须是整数"}, status_code=400)
    try:
        data = await db.client_errors_overview(hours=hours, limit=limit)
        return JSONResponse(data)
    except Exception as e:
        log.exception("admin client_errors 查询失败")
        return JSONResponse({"error": str(e)}, status_code=503)


async def admin_list_errors(request: Request):
    """GET /admin/errors —— 跨 session 的全局错误日志。

    query: since_iso, user_id, keyword, limit, offset
    返回 {errors: [...], categories: [distinct], limit, offset}
    """
    since_iso = request.query_params.get("since_iso") or ""
    user_id   = request.query_params.get("user_id") or ""
    keyword   = request.query_params.get("keyword") or ""
    try:
        limit  = min(max(int(request.query_params.get("limit", "100")), 1), 500)
        offset = max(int(request.query_params.get("offset", "0")), 0)
    except ValueError:
        return JSONResponse({"error": "limit 和 offset 必须为整数"}, status_code=400)
    try:
        errors, categories = await asyncio.gather(
            db.list_recent_errors(
                since_iso=since_iso, limit=limit, offset=offset,
                user_id=user_id, keyword=keyword,
            ),
            db.list_error_categories(since_iso=since_iso),
        )
        return JSONResponse({
            "errors": errors,
            "categories": categories,
            "limit": limit,
            "offset": offset,
        })
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


# ── Chip personalizer endpoints(动态快捷指令)────────────────────────────
#
# GET /agent/cell/chips?role=&platform=&lang=&user_id=
#   返回经 personalizer 算出的 chip 列表(已按用户状态调过顺序)
#   前端启动 / 切平台 / 切角色时调一次,缓存到 chrome.storage.local
#   缓存 key 含 version 戳,admin 改 chip 时通过 WS 推 chips_updated 失效

async def cell_chips(request: Request) -> JSONResponse:
    user_id  = (request.query_params.get("user_id") or
                request.headers.get("x-user-id", "")).strip()
    role     = (request.query_params.get("role") or "jobseeker").strip()
    platform = (request.query_params.get("platform") or "boss").strip()
    lang     = (request.query_params.get("lang") or "zh").strip()
    # ── PRD §10.3 主动提示 caller overlay ─────────────────────────
    # page_type    list / detail / chat / off_platform / other(前端 ContextBar 上报)
    # page_item_count  list 页可见物料数(候选人/职位条数),做 "批量分析 X 位"
    page_type  = (request.query_params.get("page_type") or "").strip() or None
    try:
        page_item_count = int(request.query_params.get("page_item_count") or 0)
    except (TypeError, ValueError):
        page_item_count = 0
    if role not in ("jobseeker", "recruiter"):
        return JSONResponse({"error": f"invalid role: {role}"}, status_code=400)
    try:
        from platforms_config import list_platforms as _lp
        if platform not in _lp():
            return JSONResponse({"error": f"invalid platform: {platform}"}, status_code=400)
    except Exception:
        if platform not in ("boss", "linkedin", "indeed"):
            return JSONResponse({"error": f"invalid platform: {platform}"}, status_code=400)
    try:
        from personalization import chips as _chip_mod
        from personalization import user_state as _us
        # DB probes 先跑,再用 caller overlay 覆盖前端独有信号(page_type 等)
        state = await _us.collect(user_id, role, platform)
        if page_type:
            state["page_type"] = page_type
        if page_item_count > 0:
            state["page_item_count"] = page_item_count
        chips, dbg = await _chip_mod.personalize(
            user_id, role, platform, lang, user_state=state,
        )
        return JSONResponse({
            "chips":           chips,
            "version":         dbg.get("version", 0),
            "triggered_rules": dbg.get("triggered_rules", []),
            "pool_size":       dbg.get("pool_size", 0),
        })
    except Exception as e:
        log.exception("cell_chips failed user=%s role=%s platform=%s", user_id, role, platform)
        # 失败兜底:返一个最小静态列表,前端不至于完全空
        fallback_chips = (
            [{"id": "search_jobs", "label": "搜索工作", "send_text": "搜索工作", "icon": "🔍", "weight": 80, "group": "default"}]
            if role == "jobseeker" else
            [{"id": "search_candidates", "label": "搜索候选人", "send_text": "搜索候选人", "icon": "🔎", "weight": 80, "group": "default"}]
        )
        return JSONResponse({"chips": fallback_chips, "version": 0, "fallback": True, "error": str(e)})


# ── Admin chip CRUD endpoints(运营 / PM 在 admin 后台改 chip)─────────────
#
# 数据 / 策略分离:本组端点只管"chip 数据"(label/icon/weight/enabled),
# 规则 condition 在代码 personalization/chips.py。
#
# 没有 WS broadcast(agent-gateway 现无 admin WS 基建)— 但每次写 bump
# chip_configs_version。前端 chips-loader 启动 / 切平台时拉,看到 version
# 变了就 swap chip 显示。改 chip 后 30s 内全员见到新版(开发期完全够)。

async def admin_list_chips(request: Request) -> JSONResponse:
    """GET /admin/chips?role=&platform= — 列 chip 配置(admin UI 用)."""
    role     = (request.query_params.get("role") or "").strip() or None
    platform = (request.query_params.get("platform") or "").strip() or None
    try:
        rows = await db.chip_configs_list(role=role, platform=platform, only_enabled=False)
        version = await db.chip_configs_get_version()
        return JSONResponse({"chips": rows, "version": version})
    except Exception as e:
        log.exception("admin_list_chips failed")
        return JSONResponse({"error": str(e)}, status_code=503)


async def admin_create_chip(request: Request) -> JSONResponse:
    """POST /admin/chips — 新建一条 chip(走 upsert,(key,role,platform) 已存在则覆盖).

    body: {key, role, platform, label_zh, label_en, send_text_zh?, send_text_en?,
           icon?, weight?, grp?, enabled?, sort_order?, description?}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    required = ("key", "role", "platform", "label_zh", "label_en")
    miss = [k for k in required if not body.get(k)]
    if miss:
        return JSONResponse({"error": f"必填字段缺失: {miss}"}, status_code=400)
    if body["role"] not in ("jobseeker", "recruiter"):
        return JSONResponse({"error": f"invalid role"}, status_code=400)
    try:
        row = await db.chip_config_upsert(
            key=body["key"], role=body["role"], platform=body["platform"],
            label_zh=body["label_zh"], label_en=body["label_en"],
            send_text_zh=body.get("send_text_zh", "") or "",
            send_text_en=body.get("send_text_en", "") or "",
            icon=body.get("icon", "") or "",
            weight=int(body.get("weight", 50)),
            grp=body.get("grp", "default") or "default",
            enabled=bool(body.get("enabled", True)),
            sort_order=int(body.get("sort_order", 0)),
            description=body.get("description", "") or "",
            updated_by=(request.headers.get("x-admin-user") or "admin"),
        )
        version = await db.chip_configs_bump_version()
        return JSONResponse({"chip": row, "version": version})
    except Exception as e:
        log.exception("admin_create_chip failed")
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_patch_chip(request: Request) -> JSONResponse:
    """PATCH /admin/chips/{chip_id} — 改 chip(label/icon/weight/enabled/...)."""
    try:
        chip_id = int(request.path_params.get("chip_id"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid chip_id"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    try:
        row = await db.chip_config_patch(
            chip_id, body,
            updated_by=(request.headers.get("x-admin-user") or "admin"),
        )
        if row is None:
            return JSONResponse({"error": "chip not found"}, status_code=404)
        version = await db.chip_configs_bump_version()
        return JSONResponse({"chip": row, "version": version})
    except Exception as e:
        log.exception("admin_patch_chip failed id=%s", chip_id)
        return JSONResponse({"error": str(e)}, status_code=500)


async def admin_delete_chip(request: Request) -> JSONResponse:
    """DELETE /admin/chips/{chip_id} — 删 chip."""
    try:
        chip_id = int(request.path_params.get("chip_id"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid chip_id"}, status_code=400)
    try:
        ok = await db.chip_config_delete(chip_id)
        if not ok:
            return JSONResponse({"error": "chip not found"}, status_code=404)
        version = await db.chip_configs_bump_version()
        return JSONResponse({"ok": True, "version": version})
    except Exception as e:
        log.exception("admin_delete_chip failed id=%s", chip_id)
        return JSONResponse({"error": str(e)}, status_code=500)


async def chips_version(request: Request) -> JSONResponse:
    """GET /agent/cell/chips/version — 轻量端点,前端 polling 用.

    每次 admin CRUD 后 chip_configs_version +1。前端 chips-loader 每 30s 静默
    poll 这个端点(响应仅 ~30 字节),version 变了再去拉完整 chip 列表。
    避免 30s 拉完整 chip JSON(~5KB)的浪费。
    """
    try:
        v = await db.chip_configs_get_version()
        return JSONResponse({"version": v})
    except Exception as e:
        return JSONResponse({"version": 0, "error": str(e)}, status_code=503)


async def report_chip_click(request: Request) -> JSONResponse:
    """POST /events/chip_click — 前端 chips-loader 上报每次 chip 点击.

    body: {chip_key, role, platform, user_id?, chip_label?, lang?}
    fire-and-forget,失败静默,响应一律 200(避免前端重试)。
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": True})  # 静默
    chip_key = (body.get("chip_key") or "").strip()
    if not chip_key:
        return JSONResponse({"ok": True})
    _bg(db.record_chip_click(
        user_id=(body.get("user_id") or "").strip(),
        role=(body.get("role") or "").strip(),
        platform=(body.get("platform") or "").strip(),
        chip_key=chip_key,
        chip_label=(body.get("chip_label") or "").strip(),
        lang=(body.get("lang") or "zh").strip(),
    ))
    return JSONResponse({"ok": True})


async def admin_chip_clicks_top(request: Request) -> JSONResponse:
    """GET /admin/chip-clicks/top?hours=24&role=&platform=&limit=20 — 点击热榜."""
    try:
        hours = max(1, min(int(request.query_params.get("hours", "24")), 168))
        limit = max(1, min(int(request.query_params.get("limit", "20")), 200))
    except ValueError:
        return JSONResponse({"error": "hours/limit 必须整数"}, status_code=400)
    role     = (request.query_params.get("role") or "").strip() or None
    platform = (request.query_params.get("platform") or "").strip() or None
    try:
        rows = await db.chip_click_top(hours=hours, role=role, platform=platform, limit=limit)
        return JSONResponse({"top": rows, "hours": hours})
    except Exception as e:
        log.exception("admin_chip_clicks_top failed")
        return JSONResponse({"error": str(e)}, status_code=503)


async def admin_chip_rules(request: Request) -> JSONResponse:
    """GET /admin/chip-rules — admin UI 用,返当前代码里所有 personalizer 规则元数据.

    用于 admin UI 在 chip 行边显示"被规则 X / Y 引用",防止误删。
    """
    try:
        from personalization import chips as _chip_mod
        return JSONResponse({"rules": _chip_mod.list_rules_metadata()})
    except Exception as e:
        log.exception("admin_chip_rules failed")
        return JSONResponse({"error": str(e)}, status_code=503)


async def admin_reorder_chips(request: Request) -> JSONResponse:
    """POST /admin/chips/reorder — 批量重排.

    body: {items: [{id, sort_order}, {id, sort_order}, ...]}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    items = body.get("items") or []
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be list"}, status_code=400)
    try:
        n = await db.chip_configs_reorder(items)
        version = await db.chip_configs_bump_version()
        return JSONResponse({"updated": n, "version": version})
    except Exception as e:
        log.exception("admin_reorder_chips failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Phase 1.4 增长 Metrics endpoints ────────────────────────────────────────
#
# 都走 admin 鉴权(同 /admin/errors),供 job-api-admin GrowthMetricsPage 拉取。
# 60s in-memory cache 避免 dashboard 反复打 DB 慢查询。

import time
_metrics_cache: dict = {}
_METRICS_CACHE_TTL_SEC = 60


def _metrics_cache_get(key: str):
    entry = _metrics_cache.get(key)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    return None


def _metrics_cache_set(key: str, value):
    _metrics_cache[key] = (time.monotonic() + _METRICS_CACHE_TTL_SEC, value)


async def metrics_dau(request: Request) -> JSONResponse:
    """GET /admin/metrics/dau?date=YYYY-MM-DD&platform=linkedin
    返 {date, platform, dau} 或 {date, by_platform: {boss:N, linkedin:N, ...}, _total:N}
    """
    date_iso = request.query_params.get("date") or datetime.utcnow().date().isoformat()
    platform = request.query_params.get("platform") or ""
    cache_key = f"dau:{date_iso}:{platform}"
    cached = _metrics_cache_get(cache_key)
    if cached is not None:
        return JSONResponse(cached)
    try:
        if platform:
            dau = await db.get_dau(date_iso, platform=platform)
            payload = {"date": date_iso, "platform": platform, "dau": dau}
        else:
            by_platform = await db.get_dau_by_platform(date_iso)
            total = by_platform.pop("_total", 0)
            payload = {"date": date_iso, "by_platform": by_platform, "total": total}
        _metrics_cache_set(cache_key, payload)
        return JSONResponse(payload)
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def metrics_funnel(request: Request) -> JSONResponse:
    """GET /admin/metrics/funnel?date=YYYY-MM-DD&platform=linkedin
    返 {date, platform, steps: {welcome:N, role_selected:N, ...}}
    """
    date_iso = request.query_params.get("date") or datetime.utcnow().date().isoformat()
    platform = request.query_params.get("platform") or ""
    cache_key = f"funnel:{date_iso}:{platform}"
    cached = _metrics_cache_get(cache_key)
    if cached is not None:
        return JSONResponse(cached)
    try:
        steps = await db.get_funnel_counts(date_iso, platform=platform or None)
        payload = {
            "date": date_iso,
            "platform": platform or "all",
            "steps": steps,
        }
        _metrics_cache_set(cache_key, payload)
        return JSONResponse(payload)
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def metrics_cost(request: Request) -> JSONResponse:
    """GET /admin/metrics/cost?date=YYYY-MM-DD&limit=10
    返 {date, top_users: [{user_id, platform, cost_usd, turn_count}, ...]}
    """
    date_iso = request.query_params.get("date") or datetime.utcnow().date().isoformat()
    try:
        limit = max(1, min(100, int(request.query_params.get("limit", "10"))))
    except ValueError:
        return JSONResponse({"error": "limit 必须为整数"}, status_code=400)
    cache_key = f"cost:{date_iso}:{limit}"
    cached = _metrics_cache_get(cache_key)
    if cached is not None:
        return JSONResponse(cached)
    try:
        top = await db.get_top_cost_users(date_iso, limit=limit)
        payload = {"date": date_iso, "top_users": top}
        _metrics_cache_set(cache_key, payload)
        return JSONResponse(payload)
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def metrics_summary(request: Request) -> JSONResponse:
    """GET /admin/metrics/summary?days=7
    一站式聚合:近 N 天 DAU 折线 + 当日 funnel + 当日 top cost users。
    给 GrowthMetricsPage 一次拉完整 dashboard 数据。
    """
    try:
        days = max(1, min(90, int(request.query_params.get("days", "7"))))
    except ValueError:
        return JSONResponse({"error": "days 必须为整数"}, status_code=400)
    today_iso = datetime.utcnow().date().isoformat()
    cache_key = f"summary:{today_iso}:{days}"
    cached = _metrics_cache_get(cache_key)
    if cached is not None:
        return JSONResponse(cached)
    try:
        dau_series, dau_today, funnel, top_cost = await asyncio.gather(
            db.get_dau_series(days=days),
            db.get_dau_by_platform(today_iso),
            db.get_funnel_counts(today_iso),
            db.get_top_cost_users(today_iso, limit=10),
        )
        total_today = dau_today.pop("_total", 0)
        payload = {
            "as_of": today_iso,
            "days": days,
            "dau_series": dau_series,
            "dau_today": {"total": total_today, "by_platform": dau_today},
            "funnel_today": funnel,
            "top_cost_today": top_cost,
        }
        _metrics_cache_set(cache_key, payload)
        return JSONResponse(payload)
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


# ── History (Postgres) ────────────────────────────────────────────────────────

async def list_history(request: Request):
    user_id  = request.query_params.get("user_id") or None
    role     = request.query_params.get("role") or None       # B: per-ext filter
    platform = request.query_params.get("platform") or None   # B: 可选 sub-tab filter
    limit    = min(int(request.query_params.get("limit", 20)), 200)
    offset   = int(request.query_params.get("offset", 0))
    keyword  = request.query_params.get("keyword") or None
    try:
        sessions, total = await asyncio.gather(
            db.list_conv_sessions(user_id=user_id, role=role, platform=platform,
                                  limit=limit, offset=offset, keyword=keyword),
            db.count_conv_sessions(user_id=user_id, role=role, platform=platform,
                                   keyword=keyword),
        )
        return JSONResponse({"sessions": sessions, "total": total, "limit": limit, "offset": offset})
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def delete_history_session(request: Request):
    session_id = int(request.path_params["session_id"])
    try:
        ok = await db.delete_conv_session(session_id)
        return JSONResponse({"ok": ok})
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def get_history_events(request: Request):
    session_id = int(request.path_params["session_id"])
    try:
        events = await db.get_conv_events(session_id)
        return JSONResponse({"events": events, "session_id": session_id})
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def get_history_messages(request: Request):
    """GET /history/{session_id}/messages — 返回前端展示格式的消息列表。"""
    session_id = int(request.path_params["session_id"])
    user_id = request.query_params.get("user_id") or None
    try:
        if user_id:
            pool = await db._get_pool()
            row = await pool.fetchrow(
                "SELECT user_id FROM agent_conv_sessions WHERE id=$1", session_id)
            if row is None or row["user_id"] != user_id:
                return JSONResponse({"error": "not found"}, status_code=404)
        messages = await db.get_conv_messages_display(session_id)
        return JSONResponse({"messages": messages, "session_id": session_id})
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


async def create_history_session(request: Request):
    """REST 端点：前端「新对话」按钮调用，在 DB 中创建新会话记录。
    Body JSON: { user_id, role_type?, entry_type? }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)

    role_type  = (body.get("role_type")  or "jobseeker").strip()
    entry_type = (body.get("entry_type") or "").strip()
    platform   = (body.get("platform")   or "boss").strip()

    # 检查 SSE session
    sse_sess = sse_manager.get(user_id, role_type, platform)
    if sse_sess is not None:
        if sse_sess.task and not sse_sess.task.done():
            sse_sess.task.cancel()
        sse_sess.messages.clear()
        sse_sess.turn_index = 0
        sse_sess.title_set = False
        sse_sess.current_tool = None
        if role_type:
            sse_sess.role_type = role_type
        if entry_type:
            sse_sess.entry_type = entry_type
        if sse_sess.db_session_id:
            _bg(db.close_session(sse_sess.db_session_id))
        try:
            # Per-platform session: 三元组 (user_id, role, platform) 唯一,upsert
            _role = role_type or sse_sess.role_type or "jobseeker"
            _platform = sse_sess.platform or "boss"
            sse_sess.db_session_id = await db.get_or_create_session_id(
                user_id, role=_role, platform=_platform,
                app_user_id=sse_sess.app_user_id,
                user_tier=sse_sess.user_tier,
            )
            sse_sess.title_set = False
        except Exception as e:
            log.warning("DB get_or_create_session_id (REST new-session SSE) failed: %s", e)
            sse_sess.db_session_id = None
        return JSONResponse({"ok": True, "db_session_id": sse_sess.db_session_id, "live": True})

    # 无活跃 SSE 会话:在 DB 创建/复用记录(等首次 SSE 请求使用)
    try:
        session_id = await db.get_or_create_session_id(
            user_id,
            role=(role_type or "jobseeker"),
            platform="boss",  # admin 入口默认 boss,首次 SSE 时按实际 platform 重新 upsert
        )
        return JSONResponse({"ok": True, "db_session_id": session_id, "live": False})
    except Exception as e:
        log.exception("admin handler %s failed", request.url.path)
        return JSONResponse({"error": str(e)}, status_code=503)


# ── Jobs ──────────────────────────────────────────────────────────────────────

async def evaluate_jobs_handler(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "请求体必须为 JSON"}, status_code=400)
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"ok": False, "error": "user_id 不能为空"}, status_code=400)
    job_ids = body.get("job_ids") or None
    limit = min(int(body.get("limit", 20)), 50)
    try:
        result = await job_evaluator.evaluate_jobs(user_id, job_ids=job_ids, limit=limit)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


async def evaluate_job_by_id_handler(request: Request) -> JSONResponse:
    """POST /jobs/evaluate-by-id — 单职位即时评估(扩展列表注入按钮触发)。

    Body: {
      user_id:       <必填>,
      platform?:     "boss"|"linkedin"|"indeed" (仅记录用),
      encrypt_job_id: <必填,职位唯一 id>,
      job_snapshot:  {title, company, salary, experience?, education?,
                      tags?[], skills?[], jobLabels?[], ...}
    }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "请求体必须为 JSON"}, status_code=400)
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"ok": False, "error": "user_id 不能为空"}, status_code=400)
    snapshot = body.get("job_snapshot") or {}
    if not isinstance(snapshot, dict):
        return JSONResponse({"ok": False, "error": "job_snapshot 必须是对象"}, status_code=400)
    encrypt_job_id = (body.get("encrypt_job_id")
                      or snapshot.get("encryptJobId")
                      or snapshot.get("encrypt_job_id") or "").strip()
    if not encrypt_job_id:
        return JSONResponse({"ok": False, "error": "encrypt_job_id 不能为空"}, status_code=400)
    snapshot.setdefault("external_id", encrypt_job_id)
    snapshot.setdefault("encryptJobId", encrypt_job_id)
    try:
        result = await job_evaluator.evaluate_job_snapshot(user_id, snapshot)
        # 把 id 回填到 evaluated 里方便前端按 job 对位
        if result.get("evaluated") is not None:
            result["evaluated"].setdefault("job_id", encrypt_job_id)
        return JSONResponse(result)
    except Exception as e:
        log.exception("evaluate_job_by_id_handler failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


async def generate_intros_handler(request: Request) -> JSONResponse:
    """POST /jobs/intro/generate — 根据简历为选中职位生成个性化自我介绍消息。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "请求体必须为 JSON"}, status_code=400)
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"ok": False, "error": "user_id 不能为空"}, status_code=400)
    jobs = body.get("jobs") or []
    if not jobs:
        return JSONResponse({"ok": False, "error": "jobs 不能为空"}, status_code=400)
    try:
        result = await job_evaluator.generate_intros(user_id, jobs)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


# ── App ───────────────────────────────────────────────────────────────────────

# Module-level reference to background sweeper task — Python 3.10+ 强烈建议
# 持有 asyncio.Task 引用以防被 GC 静默回收
_BG_SWEEPER_TASK = None


async def _startup():
    # ── Redis ─────────────────────────────────────────────────────────────────
    import redis_client
    await redis_client.init()

    try:
        await db.init_db()
        log.info("DB initialised (agent_conv_sessions + agent_conv_events + chip_configs)")
    except Exception as e:
        log.error("DB init failed: %s — history logging disabled", e)

    # Chip configs 种子化(idempotent)— 保证 chip_configs 表至少有 default 数据
    try:
        from migrations import seed_chip_configs
        res = await seed_chip_configs.run()
        log.info("Chip configs seeded: %s", res)
    except Exception as e:
        log.warning("Chip configs seed failed (non-fatal): %s", e)

    # Phase 1.1 MVP 3.5: recruiter_preferences + message_templates 表
    try:
        await recruiter_db.init_recruiter_schema()
        log.info("Recruiter schema initialised (recruiter_preferences + message_templates)")
    except Exception as e:
        log.error("Recruiter schema init failed: %s", e)

    try:
        await resume_db.init_resume_db()
        log.info("DB initialised (users + resumes + resume_parsed)")
    except Exception as e:
        log.error("Resume DB init failed: %s", e)

    try:
        await init_autofill_db()
        log.info("DB initialised (autofill_profile)")
    except Exception as e:
        log.error("Autofill DB init failed: %s", e)

    try:
        await form_template_db.init_form_template_db()
        log.info("DB initialised (form_templates + form_template_captures)")
    except Exception as e:
        log.error("Form template DB init failed: %s", e)

    try:
        await asyncio.to_thread(portal_auth.load_jwks)
        log.info("Portal JWKS loaded (autofill token verification)")
    except Exception as e:
        log.error("Portal JWKS load failed (autofill 接口将 401，首个请求会重试): %s", e)

    try:
        asyncio.create_task(form_template_enrich.enrichment_loop())
        log.info("Form template enrichment loop scheduled")
    except Exception as e:
        log.error("Form template enrichment loop start failed: %s", e)

    try:
        import preferences_db
        await preferences_db.init_preferences_db()
        log.info("DB initialised (user_preferences)")
    except Exception as e:
        log.error("Preferences DB init failed: %s", e)

    try:
        import capture_db
        await capture_db.init_capture_db()
        log.info("DB initialised (api_capture_sessions + api_capture_requests)")
    except Exception as e:
        log.error("Capture DB init failed: %s", e)

    try:
        import candidate_resume_db
        await candidate_resume_db.init_candidate_resume_db()
        log.info("DB initialised (candidate_resumes)")
    except Exception as e:
        log.error("Candidate resume DB init failed: %s", e)

    from mcp_manager import mcp_manager
    await mcp_manager.start()

    # ── Task notifier(C6/Sprint 2):engine 回调 → agent_conv_messages + audit ──
    # (Sprint 2 模板 register 走 tasks/templates/__init__.py 自动 import,这里只挂 notifier)
    try:
        from tasks import notifier as task_notifier
        task_notifier.register()
        log.info("Task notifier registered (paused/resumed/completed → chat messages)")
    except Exception as e:
        log.warning("Task notifier register failed (non-fatal): %s", e)

    # ── Start SSE idle sweep ──────────────────────────────────────────────────
    sse_manager.start_idle_sweep()

    # ── Phase E: 长任务 sweeper(僵尸 task / paused 24h timeout)──────────────
    # audit P1 fix: 同 sweeper tick 顺带:
    #   1. mcp 失败率告警(>30% 直接 log.error 带 [ALERT] 前缀,日志聚合可 grep)
    #   2. client_errors 清理(>30 天)
    MCP_FAIL_RATE_ALERT_PCT = 30.0   # 失败率阈值
    MCP_FAIL_RATE_MIN_CALLS = 20     # 最少调用数(防低基数噪声)
    MCP_FAIL_RATE_HOURS = 1

    async def _task_sweeper():
        import asyncio
        while True:
            try:
                stats = await db.sweep_stale_tasks()
                if any(stats.values()):
                    log.info("[task_sweeper] cleaned: zombie=%d paused_timeout=%d pending_timeout=%d",
                             stats.get("zombie", 0),
                             stats.get("paused_timeout", 0),
                             stats.get("pending_timeout", 0))
            except Exception as e:
                log.warning("[task_sweeper] error: %s", e)
            # 顺带清 7 天前的 mcp_call_log(防表无限膨胀)
            try:
                deleted = await db.cleanup_old_mcp_logs(retain_days=7)
                if deleted:
                    log.info("[task_sweeper] mcp_call_log cleaned: %d rows", deleted)
            except Exception as e:
                log.warning("[task_sweeper] mcp log cleanup error: %s", e)
            # 清 30 天前的 client_errors
            try:
                deleted = await db.cleanup_old_client_errors(retain_days=30)
                if deleted:
                    log.info("[task_sweeper] client_errors cleaned: %d rows", deleted)
            except Exception as e:
                log.warning("[task_sweeper] client_errors cleanup error: %s", e)
            # 清 30 天前的 chip_click_events
            try:
                deleted = await db.cleanup_old_chip_clicks(retain_days=30)
                if deleted:
                    log.info("[task_sweeper] chip_click_events cleaned: %d rows", deleted)
            except Exception as e:
                log.warning("[task_sweeper] chip_clicks cleanup error: %s", e)
            # MCP 失败率告警(audit P1 fix)
            try:
                rates = await db.mcp_failure_rate_by_platform(
                    hours=MCP_FAIL_RATE_HOURS, min_calls=MCP_FAIL_RATE_MIN_CALLS,
                )
                for r in rates:
                    if r["fail_rate"] >= MCP_FAIL_RATE_ALERT_PCT:
                        log.error(
                            "[ALERT] MCP failure rate spike platform=%s fail_rate=%.1f%% total=%d failed=%d window=%dh",
                            r["platform"], r["fail_rate"], r["total"], r["failed"],
                            MCP_FAIL_RATE_HOURS,
                        )
            except Exception as e:
                log.warning("[task_sweeper] mcp fail-rate check error: %s", e)
            await asyncio.sleep(1800)  # 30 分钟跑一次
    import asyncio as _asyncio
    # 持有引用避免被 GC(Python 3.10+ 文档明确建议)
    global _BG_SWEEPER_TASK
    _BG_SWEEPER_TASK = _asyncio.create_task(_task_sweeper(), name="task-sweeper")

    # 列出 job-api-gateway 内置工具
    try:
        import config as _cfg
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(_cfg.BOSS_GATEWAY_MCP, timeout=10.0) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                names = [t.name for t in tools.tools]
                log.info(
                    "MCP Server [job-api-gateway] 内置工具数: %d\n  %s",
                    len(names), "\n  ".join(names),
                )
    except Exception as e:
        log.warning("列出 job-api-gateway 工具失败（job-api-gateway 未启动？）: %s", e)


async def _shutdown():
    sse_manager.stop_idle_sweep()
    from mcp_manager import mcp_manager
    await mcp_manager.stop()
    import redis_client
    await redis_client.close()


app = Starlette(
    on_startup=[_startup],
    on_shutdown=[_shutdown],
    routes=[
        # ── 用户侧：SSE ──────────────────────────────────────────────────
        Route("/agent/sse",                  sse_chat,           methods=["POST"]),
        Route("/agent/sse/session",          sse_init_session,   methods=["POST"]),
        Route("/agent/sse/{user_id}/abort",  sse_abort,          methods=["POST"]),
        Route("/agent/sse/{user_id}",        sse_delete,         methods=["DELETE"]),

        # ── 用户侧：对话历史 ─────────────────────────────────────────────
        Route("/history",                              list_history),
        Route("/history/new-session",                create_history_session,  methods=["POST"]),
        Route("/history/{session_id:int}/messages",  get_history_messages,    methods=["GET"]),
        Route("/history/{session_id:int}",           get_history_events),
        Route("/history/{session_id:int}",           delete_history_session,  methods=["DELETE"]),

        # ── 用户侧：通用表单自动填写（P2）────────────────────────────────
        Route("/autofill/match",             autofill_match,       methods=["POST"]),
        Route("/autofill/ocr",               autofill_ocr,         methods=["POST"]),
        Route("/autofill/template/record",   record_template_handler, methods=["POST"]),
        Route("/autofill/template/capture",  upload_capture_handler,  methods=["POST"]),
        Route("/profile",                    autofill_get_profile, methods=["GET"]),
        Route("/profile",                    autofill_put_profile, methods=["PUT"]),

        # ── 管理侧：autofill 知识库 / 抓包 ───────────────────────────────
        Route("/admin/autofill/stats",       admin_autofill_stats,  methods=["GET"]),
        Route("/admin/autofill/templates",   admin_list_templates,  methods=["GET"]),
        Route("/admin/autofill/templates/{template_id:int}",        admin_get_template,         methods=["GET"]),
        Route("/admin/autofill/templates/{template_id:int}",        admin_delete_template,      methods=["DELETE"]),
        Route("/admin/autofill/templates/{template_id:int}/fields", admin_patch_template_field, methods=["PATCH"]),
        Route("/admin/autofill/captures",    admin_list_captures,   methods=["GET"]),
        Route("/admin/autofill/captures/{capture_id:int}",          admin_get_capture,          methods=["GET"]),
        Route("/admin/autofill/captures/{capture_id:int}",          admin_delete_capture,       methods=["DELETE"]),
        Route("/admin/autofill/captures/{capture_id:int}/enrich",   admin_enrich_capture,       methods=["POST"]),

        # ── 用户侧：简历 ─────────────────────────────────────────────────
        Route("/resume/upload",              upload_resume,        methods=["POST"]),
        Route("/resume/status",              get_resume_status,    methods=["GET"]),
        Route("/resume/{user_id}/retry",     retry_resume_parse,   methods=["POST"]),
        Route("/resume/{user_id}",           get_resume,           methods=["GET"]),
        Route("/resume/{user_id}",           delete_resume,        methods=["DELETE"]),

        # ── 用户侧：求职偏好 ─────────────────────────────────────────────
        Route("/user-preferences/{user_id}/suggest", suggest_preferences, methods=["GET"]),
        Route("/user-preferences/{user_id}",         get_preferences,     methods=["GET"]),
        Route("/user-preferences/{user_id}",         save_preferences,    methods=["POST"]),

        # ── Recruiter 侧 (Phase 1.1 MVP 3.5) ────────────────────────────
        Route("/recruiter/preferences/{user_id}",  get_recruiter_preferences,  methods=["GET"]),
        Route("/recruiter/preferences/{user_id}",  save_recruiter_preferences, methods=["POST"]),
        Route("/recruiter/templates/{user_id}",    get_templates,              methods=["GET"]),
        Route("/recruiter/templates/{user_id}",    save_template,              methods=["POST"]),
        Route("/recruiter/my-jobs",                get_my_jobs,                methods=["GET"]),

        # ── 用户侧：职位评估 ─────────────────────────────────────────────
        Route("/jobs/evaluate",              evaluate_jobs_handler,   methods=["POST"]),
        Route("/jobs/evaluate-by-id",        evaluate_job_by_id_handler, methods=["POST"]),
        Route("/jobs/intro/generate",        generate_intros_handler, methods=["POST"]),

        # ── 用户侧：语音输入(讯飞 IAT 代理) ─────────────────────────
        Route("/voice/asr",                  voice_asr_handler,       methods=["POST"]),
        WebSocketRoute("/voice/stream",      voice_stream_handler),

        # ── 管理侧：服务状态 & 会话管理 ──────────────────────────────────
        Route("/admin/status",               status),
        Route("/admin/sessions",             list_sessions),
        Route("/admin/sessions/{user_id}",   get_session),
        Route("/admin/sessions/{user_id}/abort", abort_session, methods=["POST"]),
        Route("/admin/sessions/{user_id}",   delete_session,   methods=["DELETE"]),
        Route("/admin/sse/sessions",         sse_list_sessions, methods=["GET"]),

        # ── 管理侧：用户管理 ─────────────────────────────────────────────
        Route("/admin/users",                list_users,     methods=["GET"]),
        Route("/admin/mode-stats",           mode_stats,     methods=["GET"]),
        Route("/admin/errors",               admin_list_errors, methods=["GET"]),
        # 前端 JS 错误上报(audit P1 fix)
        Route("/errors/client",              report_client_error, methods=["POST"]),
        Route("/admin/client-errors",        admin_client_errors, methods=["GET"]),
        # 动态 chip personalizer:前端用这个拉当前 (role × platform × user) 的 chips
        Route("/agent/cell/chips",           cell_chips,          methods=["GET"]),
        # 轻量 version 探针(前端 30s polling 用,响应 30 bytes)
        Route("/agent/cell/chips/version",   chips_version,       methods=["GET"]),
        # Admin chip CRUD(运营 / PM 在 admin 后台改 chip)
        Route("/admin/chips",                admin_list_chips,    methods=["GET"]),
        Route("/admin/chips",                admin_create_chip,   methods=["POST"]),
        Route("/admin/chips/reorder",        admin_reorder_chips, methods=["POST"]),
        Route("/admin/chip-rules",           admin_chip_rules,    methods=["GET"]),
        # chip 点击埋点(前端) + 热榜(admin)
        Route("/events/chip_click",          report_chip_click,   methods=["POST"]),
        Route("/admin/chip-clicks/top",      admin_chip_clicks_top, methods=["GET"]),
        Route("/admin/chips/{chip_id:int}",  admin_patch_chip,    methods=["PATCH"]),
        Route("/admin/chips/{chip_id:int}",  admin_delete_chip,   methods=["DELETE"]),
        # Phase 1.4 增长 metrics(60s 内存缓存,免反复打 DB)
        Route("/admin/metrics/dau",          metrics_dau,      methods=["GET"]),
        Route("/admin/metrics/funnel",       metrics_funnel,   methods=["GET"]),
        Route("/admin/metrics/cost",         metrics_cost,     methods=["GET"]),
        Route("/admin/metrics/summary",      metrics_summary,  methods=["GET"]),
        Route("/admin/history/purge",        purge_history,  methods=["POST"]),

        # ── API 抓包（浏览器扩展 + 管理后台）─────────────────────────────
        Route("/api/health",                             capture_health),
        Route("/api/session/start",                      capture_session_start,         methods=["POST"]),
        Route("/api/capture",                            receive_capture,               methods=["POST"]),
        Route("/api/sessions",                           list_capture_sessions),
        Route("/api/sessions/{session_id}",              get_capture_session_handler),
        Route("/api/sessions/{session_id}/requests",     get_capture_requests),
        Route("/api/sessions/{session_id}",              delete_capture_session,        methods=["DELETE"]),
        Route("/api/sessions/{session_id}/analyze",      analyze_capture_session,       methods=["POST"]),
        Route("/api/sessions/{session_id}/download",     download_capture_raw),
        Route("/api/sessions/{session_id}/download-analysis", download_capture_analysis),
        Route("/api/sessions/{session_id}/implement",    implement_capture_session,     methods=["POST"]),

        # ── 长任务系统(Phase B/C) ─────────────────────────────────────
        Route("/tasks/templates",            tasks_list_templates, methods=["GET"]),
        Route("/tasks/run",                  tasks_run,            methods=["POST"]),
        Route("/tasks",                      tasks_list,           methods=["GET"]),
        Route("/tasks/{task_id:int}",        tasks_get,            methods=["GET"]),
        Route("/tasks/{task_id:int}/cancel", tasks_cancel,         methods=["POST"]),
        Route("/tasks/{task_id:int}/resume", tasks_resume,         methods=["POST"]),
        Route("/tasks/{task_id:int}/pause",  tasks_pause,          methods=["POST"]),
        Route("/tasks/{task_id:int}/items/{item_id}", tasks_patch_item_state, methods=["PATCH"]),
        Route("/jobs/recommended",                    jobs_recommended,       methods=["GET"]),
        Route("/jobs/evaluate-with-resume",           jobs_evaluate_with_resume, methods=["POST"]),

        # ── Admin 长任务监控 ──────────────────────────────────────────
        Route("/admin/tasks/stats",                       admin_tasks_stats,        methods=["GET"]),
        Route("/admin/tasks/templates",                   admin_template_dags,      methods=["GET"]),
        Route("/admin/tasks",                             admin_tasks_list,         methods=["GET"]),
        Route("/admin/tasks/{task_id:int}",               admin_tasks_detail,       methods=["GET"]),
        Route("/admin/tasks/{task_id:int}/force-cancel",  admin_tasks_force_cancel, methods=["POST"]),
        Route("/admin/mcp-metrics",                       admin_mcp_metrics,        methods=["GET"]),

        # ── 候选人简历管理 ──────────────────────────────────────────────
        Route("/candidate-resumes",                                      cr_list,     methods=["GET"]),
        Route("/candidate-resumes/{platform}/{candidate_id}",            cr_get,      methods=["GET"]),
        Route("/candidate-resumes/{platform}/{candidate_id}/file",       cr_download, methods=["GET"]),
        Route("/candidate-resumes/{platform}/{candidate_id}",            cr_delete,   methods=["DELETE"]),
    ],
)


if __name__ == "__main__":
    log.info("Starting job-agent-gateway on port %d", config.PORT)
    log.info("  Model:        %s", config.MODEL)
    log.info("  Boss GW:      %s", config.BOSS_GATEWAY_URL)
    log.info("  SSE endpoint: http://0.0.0.0:%d/agent/sse  (POST)", config.PORT)
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
