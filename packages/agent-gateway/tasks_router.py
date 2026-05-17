"""tasks_router.py — 长任务系统的 HTTP API (Phase C)

路由列表(挂在 server.py):
  GET  /tasks/templates?role=jobseeker  列可用模板
  POST /tasks/run                        启动任务 → 返回 {task_id}
  GET  /tasks?user_id=&role=&platform=  列任务(可选 ?status=running)
  GET  /tasks/{id}                       任务详情
  POST /tasks/{id}/cancel                取消
  POST /tasks/{id}/resume                用户解决问题后继续

设计:前端走 polling — sidepanel 每 5s GET /tasks 拉进行中,
不开第二个 SSE 流。任务事件天然 idempotent(状态+进度一并拿)。
"""
from __future__ import annotations

import asyncio
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

import db
import job_evaluator
from tasks.engine import (
    run_task_in_background, cancel_task, resume_task, pause_task, MAX_GLOBAL_TASKS,
)
from tasks.registry import TEMPLATES, get_template, list_templates_for_role

log = logging.getLogger(__name__)


# ── 内部 status ↔ PRD 用户可见态映射(C1:5 态正名) ─────────────────
# PRD §3.1 期望 5 个用户可见态: queue / executing / verification_required / paused / completed
# DB 实际 6 态: pending / running / paused_user_action(verification|user) / completed / failed / cancelled
# 通过 paused_kind 字段消歧 paused_user_action,统一对外暴露为 display_status。
_STATUS_DISPLAY_MAP = {
    "pending":   "queue",
    "running":   "executing",
    "completed": "completed",
    "cancelled": "cancelled",
    "failed":    "failed",
}


def _derive_display_status(status: str, paused_kind: str) -> str:
    if status == "paused_user_action":
        # 老库 paused_kind='' 视为 verification(旧行为只有风控触发暂停)
        return "paused" if paused_kind == "user" else "verification_required"
    return _STATUS_DISPLAY_MAP.get(status, status)


# ── 工具:把 task row 序列化成 JSON 友好结构 ──────────────────────
def _serialize_task(row: dict | None) -> dict | None:
    if not row:
        return None
    out = dict(row)
    # JSONB 字段可能已经是 dict/list,也可能是 str(取决于驱动)
    for k in ("inputs", "progress", "skipped", "artifacts", "item_states"):
        v = out.get(k)
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = {} if k != "skipped" else []
    # datetime → ISO string
    for k in ("created_at", "started_at", "paused_at", "resumed_at",
              "completed_at", "cancelled_at", "heartbeat_at"):
        v = out.get(k)
        if v is not None and hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    # C1: 派生 PRD 5 态 display_status(前端用,后端 status 字段保留兼容)
    out["display_status"] = _derive_display_status(
        out.get("status", ""), out.get("paused_kind", "") or "",
    )
    return out


# ── GET /tasks/templates ─────────────────────────────────────────
async def list_templates_handler(request: Request) -> JSONResponse:
    """列模板,可选 ?role= 和 ?platform= 过滤。
    返回 supported_platforms 让前端在不支持当前 platform 时灰掉/隐藏。"""
    role = request.query_params.get("role") or ""
    platform = request.query_params.get("platform") or ""
    if role:
        tpls = list_templates_for_role(role, platform=platform or None)
    else:
        tpls = list(TEMPLATES.values())
        if platform:
            tpls = [t for t in tpls if platform in t.supported_platforms or t.steps]
    return JSONResponse({
        "templates": [
            {
                "id": t.id,
                "role": t.role,
                "title": t.title,
                "description": t.description,
                "emoji": t.emoji,
                "estimated_min": t.estimated_min,
                "supported_platforms": t.supported_platforms,
                # step_count: 优先按当前 platform,fallback 任意一个
                "step_count": (
                    len(t.steps_for(platform) or [])
                    if platform else
                    (len(next(iter(t.steps_by_platform.values()), []))
                     if t.steps_by_platform else len(t.steps))
                ),
            }
            for t in tpls
        ]
    })


# ── POST /tasks/run ──────────────────────────────────────────────
async def run_task_handler(request: Request) -> JSONResponse:
    """Body: {user_id?, role, platform, template_id, inputs?:{}, title?:""}
    user_id 优先取 x-user-id header(Go gateway 注入),body.user_id 是 fallback;
    若两者都给且不一致,以 header 为准(防恶意客户端代发别人的任务)。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    header_user = _request_user_id(request)
    body_user = (body.get("user_id") or "").strip()
    user_id = header_user or body_user
    # audit P2 fix:跨端点字段统一 — `role` 是规范名(对齐 DB 列 + cells key);
    # `role_type` 兼容旧客户端,两个都给且不一致时 log warning。
    body_role      = (body.get("role") or "").strip()
    body_role_type = (body.get("role_type") or "").strip()
    role = body_role or body_role_type
    if body_role and body_role_type and body_role != body_role_type:
        log.warning(
            "/tasks/run role 与 role_type 字段都给但值不一致 user=%s role=%s role_type=%s — 选用 role",
            user_id, body_role, body_role_type,
        )
    platform = (body.get("platform") or "").strip()
    template_id = (body.get("template_id") or "").strip()
    inputs = body.get("inputs") or {}
    title = (body.get("title") or "").strip()

    if not (user_id and role and platform and template_id):
        return JSONResponse(
            {"error": "user_id/role/platform/template_id 不能为空"},
            status_code=400,
        )
    # 防代写:有 header 又有不同的 body.user_id → 拒绝
    if header_user and body_user and header_user != body_user:
        return JSONResponse({"error": "user_id mismatch with auth header"}, status_code=403)

    tpl = get_template(template_id)
    if tpl is None:
        return JSONResponse(
            {"error": f"unknown template: {template_id}"},
            status_code=404,
        )
    if tpl.role != role:
        return JSONResponse(
            {"error": f"template {template_id} is for role={tpl.role}, not {role}"},
            status_code=400,
        )
    # 新结构:模板按 platform 编排 steps,不支持的平台直接拒
    if not tpl.steps_for(platform):
        return JSONResponse(
            {
                "error": f"模板 {template_id} 暂不支持 {platform} 平台",
                "supported_platforms": tpl.supported_platforms,
            },
            status_code=400,
        )

    # 全局并发上限快速 check(create 之前)
    n_global = await db.count_global_running_tasks()
    if n_global >= MAX_GLOBAL_TASKS:
        return JSONResponse(
            {"error": f"系统忙(全局任务上限 {MAX_GLOBAL_TASKS}),请稍后再试"},
            status_code=429,
        )

    try:
        task_id = await db.create_task(
            user_id=user_id, role=role, platform=platform,
            template_id=template_id, title=title or tpl.title,
            inputs=inputs,
        )
    except Exception as e:
        # unique partial index 冲突 = 该 (user, platform) 已有 running/paused
        msg = str(e)
        if "idx_tasks_running_unique" in msg or "duplicate key" in msg.lower():
            return JSONResponse(
                {
                    "error": f"该平台已有任务在跑,等它完成或取消后再试",
                    "code": "platform_busy",
                },
                status_code=409,
            )
        log.exception("create_task failed")
        return JSONResponse({"error": str(e)}, status_code=500)

    # 启动后台 runner(非阻塞)
    asyncio.create_task(
        run_task_in_background(task_id, template_id, inputs),
        name=f"task-bootstrap-{task_id}",
    )

    return JSONResponse({"task_id": task_id, "status": "running"})


# ── GET /tasks ───────────────────────────────────────────────────
async def list_tasks_handler(request: Request) -> JSONResponse:
    # 信赖 Go gateway 注入的 x-user-id(已校验过 cookie),query 是 fallback
    user_id = _request_user_id(request)
    role = request.query_params.get("role") or None
    platform = request.query_params.get("platform") or None
    status_filter_raw = request.query_params.get("status") or ""
    limit = int(request.query_params.get("limit") or "50")

    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)

    status_filter = None
    if status_filter_raw:
        # 支持 ?status=running,paused_user_action 多值
        status_filter = [s.strip() for s in status_filter_raw.split(",") if s.strip()]

    rows = await db.list_tasks_for_user(
        user_id=user_id, role=role, platform=platform,
        status_filter=status_filter, limit=limit,
    )
    return JSONResponse({
        "tasks": [_serialize_task(r) for r in rows],
    })


# ── GET /tasks/{id} ──────────────────────────────────────────────
async def get_task_handler(request: Request) -> JSONResponse:
    task_id = int(request.path_params["task_id"])
    row, err = await _ensure_owns_task(request, task_id)
    if err:
        return err
    return JSONResponse({"task": _serialize_task(row)})


# ── POST /jobs/evaluate-with-resume ──────────────────────────────
async def evaluate_job_with_resume_handler(request: Request) -> JSONResponse:
    """单 job vs 用户简历的详细 LLM 评估(含面试准备)。

    body: {task_id, item_id, force_refresh?}
    cache 在 agent_tasks.item_states[item_id].evaluation,默认命中直接返。
    """
    user_id = _request_user_id(request)
    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be json object"}, status_code=400)
    try:
        task_id = int(body.get("task_id") or 0)
    except Exception:
        task_id = 0
    item_id = str(body.get("item_id") or "").strip()
    force = bool(body.get("force_refresh"))
    if not task_id or not item_id:
        return JSONResponse({"error": "task_id + item_id required"}, status_code=400)

    task = await db.get_task(task_id)
    if not task:
        return JSONResponse({"error": "task not found"}, status_code=404)
    if task.get("user_id") != user_id:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # cache 检查
    item_states = task.get("item_states") or {}
    if isinstance(item_states, str):
        try: item_states = json.loads(item_states)
        except Exception: item_states = {}
    cached = (item_states.get(item_id) or {}).get("evaluation")
    if cached and not force:
        return JSONResponse({"evaluation": cached, "cached": True})

    # 找该 task 里这个 item 对应的 job artifact
    artifacts = task.get("artifacts") or {}
    if isinstance(artifacts, str):
        try: artifacts = json.loads(artifacts)
        except Exception: artifacts = {}
    top_jobs = artifacts.get("top_jobs") or []
    job = next((j for j in top_jobs if str((j or {}).get("job_id")) == item_id), None)
    if not job:
        return JSONResponse({"error": "job not in this task's artifacts"}, status_code=404)

    try:
        evaluation = await job_evaluator.evaluate_job_with_interview(user_id, job)
    except Exception as e:
        log.exception("[evaluate-with-resume] LLM failed")
        return JSONResponse({"error": f"LLM 评估失败: {e}"}, status_code=502)
    if evaluation is None:
        return JSONResponse(
            {"error": "请先在「我」tab 上传并解析简历", "no_resume": True},
            status_code=400,
        )

    # 缓存:patch 进 item_states[item_id].evaluation,viewed_at 等其它字段不动
    await db.patch_task_item_state(task_id, item_id, {"evaluation": evaluation})
    return JSONResponse({"evaluation": evaluation, "cached": False})


# ── GET /jobs/recommended ─────────────────────────────────────────
async def list_recommended_jobs_handler(request: Request) -> JSONResponse:
    """跨任务聚合用户精选岗位池。前端「我的精选」入口拉这个。"""
    user_id = _request_user_id(request)
    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)
    role = (request.query_params.get("role") or "").strip() or None
    platform = (request.query_params.get("platform") or "").strip() or None
    try:
        limit = max(1, min(int(request.query_params.get("limit") or 50), 200))
    except Exception:
        limit = 50
    jobs = await db.list_recommended_jobs(
        user_id, role=role, platform=platform, limit=limit
    )
    return JSONResponse({"jobs": jobs, "count": len(jobs)})


# ── PATCH /tasks/{id}/items/{item_id} ────────────────────────────
async def patch_task_item_state_handler(request: Request) -> JSONResponse:
    """更新某个 task artifact item 的用户操作状态(viewed/starred 等)。

    body 例:{"viewed_at": "2026-05-08T13:30:00Z"}  或  {"starred": true}
    会原子 merge 到 agent_tasks.item_states[item_id] dict 里。
    """
    task_id = int(request.path_params["task_id"])
    item_id = (request.path_params.get("item_id") or "").strip()
    if not item_id:
        return JSONResponse({"error": "item_id required"}, status_code=400)
    row, err = await _ensure_owns_task(request, task_id)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or not body:
        return JSONResponse({"error": "body must be non-empty json object"}, status_code=400)
    # 白名单字段(防滥用)
    ALLOWED = {"viewed_at", "starred", "starred_at", "hidden", "note"}
    patch = {k: v for k, v in body.items() if k in ALLOWED}
    if not patch:
        return JSONResponse(
            {"error": f"no allowed fields in body (allowed: {sorted(ALLOWED)})"},
            status_code=400,
        )

    states = await db.patch_task_item_state(task_id, item_id, patch)
    if states is None:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return JSONResponse({"ok": True, "item_states": states})


# ── POST /tasks/{id}/cancel ──────────────────────────────────────
async def cancel_task_handler(request: Request) -> JSONResponse:
    task_id = int(request.path_params["task_id"])
    row, err = await _ensure_owns_task(request, task_id)
    if err:
        return err
    status = row.get("status")
    if status in ("completed", "failed", "cancelled"):
        return JSONResponse({"ok": True, "already_terminal": True, "status": status})
    # 触发 cancel:本地 runner → 立即 cancel;远程 runner → 写 Redis signal
    found = await cancel_task(task_id)
    if not found:
        # 没人持有该 task(进程重启 + lease 已过期) → DB 直接置 cancelled
        await db.update_task_status(
            task_id, "cancelled",
            cancelled_at=_now(),
            error="cancelled (no runner holding lease)",
        )
    return JSONResponse({"ok": True, "found_runner": found})


# ── POST /tasks/{id}/resume ──────────────────────────────────────
async def resume_task_handler(request: Request) -> JSONResponse:
    """用户解决了 paused_user_action 状态,点继续。"""
    task_id = int(request.path_params["task_id"])
    row, err = await _ensure_owns_task(request, task_id)
    if err:
        return err
    if row.get("status") != "paused_user_action":
        return JSONResponse(
            {"error": f"task is not paused (current: {row.get('status')})"},
            status_code=400,
        )
    found = await resume_task(task_id)
    if not found:
        # runner 不在内存 + lease 也没了 → 把任务标记为 failed,让用户重新启动一个新的
        await db.update_task_status(
            task_id, "failed",
            error="runner lost (server restarted), please re-run task",
            completed_at=_now(),
        )
        return JSONResponse(
            {"ok": False, "reason": "runner lost, task marked failed"},
            status_code=410,
        )
    return JSONResponse({"ok": True})


# ── POST /tasks/{id}/pause ─────────────────────────────────────── (C2)
async def pause_task_handler(request: Request) -> JSONResponse:
    """用户主动暂停一个 running 任务。下个 step 边界生效,任务转 paused_user_action(kind='user')。
    若任务已是 paused 状态返 200 + already_paused;cancelled/completed 返 400。"""
    task_id = int(request.path_params["task_id"])
    row, err = await _ensure_owns_task(request, task_id)
    if err:
        return err
    status = row.get("status")
    if status == "paused_user_action":
        return JSONResponse({"ok": True, "already_paused": True,
                             "paused_kind": row.get("paused_kind", "")})
    if status in ("completed", "failed", "cancelled"):
        return JSONResponse(
            {"error": f"cannot pause task in terminal state: {status}"},
            status_code=400,
        )
    if status != "running":
        return JSONResponse(
            {"error": f"task is not running (current: {status})"},
            status_code=400,
        )
    ok = await pause_task(task_id, kind="user")
    if not ok:
        return JSONResponse(
            {"ok": False, "error": "runner not held by this instance (cross-instance pause not implemented)"},
            status_code=409,
        )
    return JSONResponse({"ok": True, "note": "pause requested; will take effect at next step boundary"})


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _request_user_id(request: Request) -> str:
    """跟 sse_router 同样的 trust 模型:prod 信赖 Go gateway 注入的
    `x-user-id` header(已校验 cookie/JWT);开发 / fallback 用 query param。"""
    return (request.headers.get("x-user-id", "")
            or request.query_params.get("user_id", "")).strip()


async def _ensure_owns_task(request: Request, task_id: int) -> tuple[dict | None, JSONResponse | None]:
    """拿 task row,verify 当前请求的 user_id 是任务 owner。
    返 (row, None) 表示 OK;返 (None, JSONResponse) 表示已生成 4xx 应答,caller 直接返。"""
    row = await db.get_task(task_id)
    if not row:
        return None, JSONResponse({"error": "not found"}, status_code=404)
    req_user = _request_user_id(request)
    # admin 路径不调这个 helper(他们已经在 server.py route 层级有自己的 auth);
    # 用户路径必须匹配 owner
    if not req_user:
        return None, JSONResponse({"error": "user_id required"}, status_code=400)
    if row.get("user_id") != req_user:
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return row, None


# ── admin 视角(跨 user)─────────────────────────────────────────

async def admin_list_tasks_handler(request: Request) -> JSONResponse:
    """GET /admin/tasks
    Query:
      status      ?status=running,paused_user_action 多值
      user_id     精确匹配
      role        jobseeker / recruiter
      platform    boss / linkedin / indeed
      template_id 精确匹配
      keyword     模糊匹配 title / template_id
      limit/offset
    """
    qp = request.query_params
    status_filter = None
    if qp.get("status"):
        status_filter = [s.strip() for s in qp.get("status", "").split(",") if s.strip()]
    rows, total = await db.admin_list_tasks(
        status_filter=status_filter,
        user_id=qp.get("user_id") or None,
        role=qp.get("role") or None,
        platform=qp.get("platform") or None,
        template_id=qp.get("template_id") or None,
        keyword=qp.get("keyword") or None,
        limit=int(qp.get("limit") or "100"),
        offset=int(qp.get("offset") or "0"),
    )
    return JSONResponse({
        "tasks": [_serialize_task(r) for r in rows],
        "total": total,
    })


async def admin_task_stats_handler(request: Request) -> JSONResponse:
    """GET /admin/tasks/stats — 按 status 分组计数,给 dashboard 顶部 chip 用。"""
    stats = await db.admin_task_stats()
    return JSONResponse({"stats": stats})


async def admin_mcp_metrics_handler(request: Request) -> JSONResponse:
    """GET /admin/mcp-metrics?hours=24&bucket=hour — 一次性返观测面所需的全部数据。

    {
      "overview":   {total, ok, failed, risk, avg_ms},
      "timeseries": [{ts, platform, calls, risks}, ...],   # 按 minute / hour 分桶
      "risk_top":   [{risk_signal, platform, hits, last_seen}, ...],
      "user_top":   [{user_id, platform, calls, risks, avg_ms, last_call}, ...],
      "hours":      <int>, "bucket": <str>
    }
    """
    try:
        hours = max(1, min(int(request.query_params.get("hours") or 24), 168))
    except Exception:
        hours = 24
    bucket = (request.query_params.get("bucket") or "").strip()
    if bucket not in ("minute", "hour"):
        # 默认:1h 范围用 minute,>1h 用 hour(避免点数太多)
        bucket = "minute" if hours <= 2 else "hour"

    overview = await db.mcp_metrics_overview(hours=hours)
    timeseries = await db.mcp_metrics_timeseries(hours=hours, bucket=bucket)
    risk_top = await db.mcp_metrics_risk_top(hours=hours, limit=10)
    user_top = await db.mcp_metrics_user_top(hours=hours, limit=20)
    return JSONResponse({
        "hours": hours,
        "bucket": bucket,
        "overview": overview,
        "timeseries": timeseries,
        "risk_top": risk_top,
        "user_top": user_top,
    })


async def admin_template_dags_handler(request: Request) -> JSONResponse:
    """GET /admin/tasks/templates — 列所有逻辑模板的完整 DAG 结构。

    返回每个 template 的:
      - 元信息:id, role, title, description, emoji, estimated_min
      - steps_by_platform:{platform: [{id, title, op_type, iter_items}]}
        给 admin 后台可视化 — 看每个平台的步骤编排
    """
    out = []
    for t in TEMPLATES.values():
        platforms = {}
        for p, steps in t.steps_by_platform.items():
            platforms[p] = [
                {
                    "id": s.id,
                    "title": s.title,
                    "op_type": s.op_type,
                    "iter_items": s.iter_items,
                    "fn_name": getattr(s.fn, "__name__", str(s.fn)),
                    "fn_module": getattr(s.fn, "__module__", ""),
                }
                for s in steps
            ]
        out.append({
            "id": t.id,
            "role": t.role,
            "title": t.title,
            "description": t.description,
            "emoji": t.emoji,
            "estimated_min": t.estimated_min,
            "supported_platforms": t.supported_platforms,
            "steps_by_platform": platforms,
        })
    return JSONResponse({"templates": out})


async def admin_task_detail_handler(request: Request) -> JSONResponse:
    """GET /admin/tasks/{id} — 详情(同 /tasks/{id} 但走 admin 路径,语义清晰)。"""
    task_id = int(request.path_params["task_id"])
    row = await db.get_task(task_id)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    # 加上 lease 持有者信息(运行中任务知道哪个实例在跑)
    holder = None
    try:
        from tasks import lease as _lease
        holder = await _lease.get_lease_holder(task_id)
    except Exception:
        pass
    out = _serialize_task(row)
    if out is not None:
        out["_lease_holder"] = holder
    return JSONResponse({"task": out})


async def admin_force_cancel_handler(request: Request) -> JSONResponse:
    """POST /admin/tasks/{id}/force-cancel — 管理员强制取消(任意状态)。"""
    task_id = int(request.path_params["task_id"])
    row = await db.get_task(task_id)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    # 复用 cancel 逻辑:先 in-memory + 发 Redis signal
    found = await cancel_task(task_id)
    # 兜底:不管 runner 找没找到,DB 强 mark cancelled(对 zombie / paused 都生效)
    if row.get("status") not in ("completed", "failed", "cancelled"):
        await db.update_task_status(
            task_id, "cancelled",
            cancelled_at=_now(),
            error=(row.get("error") or "") + " [admin force-cancel]",
        )
    return JSONResponse({"ok": True, "found_runner": found})
