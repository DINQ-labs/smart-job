"""收藏 HTTP 路由（PRD §4 / Sprint A1-A6）。

统一端点 `/bookmarks`，用 `role=jobseeker|recruiter` 切换底层表：
    jobseeker → user_job_interests       (字段：job_name / company_name / salary_desc / city)
    recruiter → recruiter_geek_interests (字段：geek_name / salary / city / work_year / degree)

响应统一为 `BookmarkDTO` 形状（见 `_to_dto`），前端不感知底层两张表。

注册见 http_routes.register_routes()：
    GET    /bookmarks                  列表 + 筛选 + 搜索 + 排序 + 分页
    POST   /bookmarks                  创建（来源 manual / ai_recommended / task_result）
    GET    /bookmarks/stats            配额统计
    GET    /bookmarks/{bm_id}          单条详情
    PATCH  /bookmarks/{bm_id}          编辑（notes / tags / status / match_score）
    DELETE /bookmarks/{bm_id}          删除
    POST   /bookmarks/{bm_id}/reanalyze  重新分析（扣 Credit + 调 agent-gateway）

鉴权：沿用现有 app_user_id query/body 参数模式。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

import db
from billing import credit_ledger, pricing

log = logging.getLogger(__name__)

ROLE_JOBSEEKER = "jobseeker"
ROLE_RECRUITER = "recruiter"
VALID_ROLES = (ROLE_JOBSEEKER, ROLE_RECRUITER)
VALID_SOURCES = ("manual", "ai_recommended", "task_result")
VALID_PLATFORMS = ("boss", "linkedin", "indeed")
VALID_STATUSES_JOBSEEKER = ("new", "viewed", "interested", "applied", "rejected", "archived")
VALID_STATUSES_RECRUITER = ("new", "viewed", "priority", "to_interview", "rejected", "archived")


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _need_user_role(data: dict) -> tuple[str, str, JSONResponse | None]:
    uid = (data.get("app_user_id") or "").strip()
    role = (data.get("role") or "").strip().lower()
    if not uid:
        return "", "", _err("app_user_id required", 400)
    if role not in VALID_ROLES:
        return "", "", _err(f"role must be one of {VALID_ROLES}", 400)
    return uid, role, None


# ── DTO：把两张表的字段统一映射到前端用的 schema ──────────────────────────────

def _to_dto(row: dict, role: str) -> dict:
    """把 user_job_interests 或 recruiter_geek_interests 一行变成 BookmarkDTO。"""
    tags = row.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []

    if role == ROLE_JOBSEEKER:
        return {
            "id": row["id"],
            "role": ROLE_JOBSEEKER,
            "kind": "job",
            "platform": row.get("platform", "boss"),
            "title": row.get("job_name", ""),
            "subtitle": row.get("company_name", ""),
            "salary": row.get("salary_desc", ""),
            "city": row.get("city", ""),
            "match_score": row.get("match_score"),
            "status": row.get("status", "new"),
            "source": row.get("source", "manual"),
            "tags": tags,
            "notes": row.get("notes", ""),
            "interested": bool(row.get("interested", False)),
            "external_id": row.get("encrypt_job_id", ""),
            "metadata": {
                "list_security_id": row.get("list_security_id", ""),
                "lid": row.get("lid", ""),
                "conversation_id": row.get("conversation_id", ""),
                "jd_excerpt": row.get("jd_excerpt", ""),
            },
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    # recruiter
    return {
        "id": row["id"],
        "role": ROLE_RECRUITER,
        "kind": "candidate",
        "platform": row.get("platform", "boss"),
        "title": row.get("geek_name", ""),
        "subtitle": row.get("degree", ""),
        "salary": row.get("salary", ""),
        "city": row.get("city", ""),
        "match_score": row.get("match_score"),
        "status": row.get("status", "new"),
        "source": row.get("source", "manual"),
        "tags": tags,
        "notes": row.get("notes", ""),
        "interested": bool(row.get("interested", False)),
        "external_id": row.get("encrypt_geek_id", ""),
        "metadata": {
            "encrypt_job_id": row.get("encrypt_job_id", ""),
            "work_year": row.get("work_year", ""),
            "search_security_id": row.get("search_security_id", ""),
            "experience_excerpt": row.get("experience_excerpt", ""),
        },
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /bookmarks —— 列表（A1）
# Query: app_user_id, role, platform?, status?, source?, tag?, q?, sort?, order?, limit?, offset?
# ════════════════════════════════════════════════════════════════════════════
async def list_bookmarks(request: Request) -> JSONResponse:
    qp = dict(request.query_params)
    uid, role, err = _need_user_role(qp)
    if err:
        return err

    platform = (qp.get("platform") or "").strip().lower() or None
    if platform and platform not in VALID_PLATFORMS:
        return _err(f"platform must be one of {VALID_PLATFORMS}", 400)

    status = (qp.get("status") or "").strip() or None
    source = (qp.get("source") or "").strip().lower() or None
    if source and source not in VALID_SOURCES:
        return _err(f"source must be one of {VALID_SOURCES}", 400)
    tag = (qp.get("tag") or "").strip() or None
    q = (qp.get("q") or "").strip() or None
    interested_only = qp.get("interested_only", "").lower() in ("1", "true", "yes")
    sort = (qp.get("sort") or "created_at").strip()
    order = (qp.get("order") or "desc").strip().lower()
    try:
        limit = int(qp.get("limit", "50"))
        offset = int(qp.get("offset", "0"))
    except ValueError:
        return _err("limit/offset must be int", 400)

    kw = dict(
        platform=platform, status=status, source=source,
        interested_only=interested_only, tag=tag, q=q,
        sort=sort, order=order, limit=limit, offset=offset,
    )
    if role == ROLE_JOBSEEKER:
        rows = await db.query_user_job_interests(uid, **kw)
        total = await db.count_user_job_interests(
            uid, platform=platform, status=status, source=source,
            interested_only=interested_only, tag=tag, q=q,
        )
    else:
        rows = await db.query_recruiter_geek_interests(uid, **kw)
        total = await db.count_recruiter_geek_interests(
            uid, platform=platform, status=status, source=source,
            interested_only=interested_only, tag=tag, q=q,
        )
    items = [_to_dto(r, role) for r in rows]
    return JSONResponse({
        "ok": True,
        "items": items,
        "count": len(items),
        "total": total,
        "limit": limit,
        "offset": offset,
    })


# ════════════════════════════════════════════════════════════════════════════
# POST /bookmarks —— 创建（A2）
# Body: {
#   app_user_id, role, platform,
#   source: 'manual'|'ai_recommended'|'task_result',
#   external_id: encrypt_job_id 或 encrypt_geek_id,
#   // 求职端可选：job_name / company_name / salary_desc / city / jd_excerpt / list_security_id / lid
#   // 招聘端可选：geek_name / salary / city / work_year / degree / experience_excerpt / encrypt_job_id / search_security_id
#   match_score?, tags?, notes?, status?
# }
# ════════════════════════════════════════════════════════════════════════════
async def create_bookmark(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON", 400)
    uid, role, err = _need_user_role(body)
    if err:
        return err

    platform = (body.get("platform") or "boss").strip().lower()
    if platform not in VALID_PLATFORMS:
        return _err(f"platform must be one of {VALID_PLATFORMS}", 400)

    source = (body.get("source") or "manual").strip().lower()
    if source not in VALID_SOURCES:
        return _err(f"source must be one of {VALID_SOURCES}", 400)

    external_id = (body.get("external_id") or "").strip()
    if not external_id:
        return _err("external_id required", 400)

    # 配额检查：免费版受限
    sub = await db.get_subscription(uid)
    plan = (sub.get("plan") if sub else pricing.PLAN_FREE) or pricing.PLAN_FREE
    quota = pricing.bookmark_quota(plan, role)
    if quota is not None:
        used = (await db.count_user_job_interests(uid) if role == ROLE_JOBSEEKER
                else await db.count_recruiter_geek_interests(uid))
        if used >= quota:
            return JSONResponse({
                "ok": False,
                "error": "bookmark_quota_exceeded",
                "quota": quota,
                "used": used,
                "plan": plan,
            }, status_code=402)

    # 共享字段
    tags = body.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []

    common = {
        "interested": True,
        "source": source,
        "status": (body.get("status") or "new").strip(),
        "notes": (body.get("notes") or "").strip(),
        "tags": tags,
        "platform": platform,
    }
    if "match_score" in body and body["match_score"] is not None:
        try:
            common["match_score"] = max(0, min(100, int(body["match_score"])))
        except (TypeError, ValueError):
            pass

    if role == ROLE_JOBSEEKER:
        fields = dict(common)
        for k in ("job_name", "company_name", "salary_desc", "city",
                  "list_security_id", "lid", "jd_excerpt", "conversation_id"):
            v = body.get(k)
            if v is not None:
                fields[k] = str(v)
        await db.upsert_user_job_interest(uid, external_id, **fields)
        row = await _find_jobseeker_by_external(uid, platform, external_id)
    else:
        fields = dict(common)
        # encrypt_job_id 在招聘端是 metadata（同一个候选人可能对接多个 job）
        job_ext = (body.get("metadata", {}).get("encrypt_job_id") if isinstance(body.get("metadata"), dict)
                   else body.get("encrypt_job_id") or "")
        for k in ("geek_name", "salary", "city", "degree", "work_year",
                  "search_security_id", "experience_excerpt"):
            v = body.get(k)
            if v is not None:
                fields[k] = str(v)
        await db.upsert_recruiter_geek_interest(uid, platform, external_id, job_ext or "", **fields)
        row = await _find_recruiter_by_external(uid, platform, external_id, job_ext or "")

    if not row:
        return _err("created but failed to read back", 500)
    return JSONResponse({"ok": True, "item": _to_dto(row, role)}, status_code=201)


async def _find_jobseeker_by_external(uid: str, platform: str, ext: str) -> dict | None:
    rows = await db.query_user_job_interests(uid, platform=platform, limit=1)
    # query 不带 external 过滤；先用一行 raw SQL 取
    pool = await db._get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT * FROM user_job_interests WHERE app_user_id=$1 AND platform=$2 AND encrypt_job_id=$3",
        uid, platform, ext,
    )
    return db._row_to_dict_pg(row) if row else None


async def _find_recruiter_by_external(uid: str, platform: str, ext: str, job_ext: str) -> dict | None:
    pool = await db._get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """SELECT * FROM recruiter_geek_interests
           WHERE app_user_id=$1 AND platform=$2 AND encrypt_geek_id=$3 AND encrypt_job_id=$4""",
        uid, platform, ext, job_ext,
    )
    return db._row_to_dict_pg(row) if row else None


# ════════════════════════════════════════════════════════════════════════════
# GET /bookmarks/stats —— 配额统计（A5）
# Query: app_user_id, role
# ════════════════════════════════════════════════════════════════════════════
async def bookmark_stats(request: Request) -> JSONResponse:
    qp = dict(request.query_params)
    uid, role, err = _need_user_role(qp)
    if err:
        return err

    sub = await db.get_subscription(uid)
    plan = (sub.get("plan") if sub else pricing.PLAN_FREE) or pricing.PLAN_FREE
    quota = pricing.bookmark_quota(plan, role)

    if role == ROLE_JOBSEEKER:
        total = await db.count_user_job_interests(uid)
    else:
        total = await db.count_recruiter_geek_interests(uid)

    is_full = quota is not None and total >= quota
    return JSONResponse({
        "ok": True,
        "role": role,
        "plan": plan,
        "total": total,
        "quota": quota,
        "is_full": is_full,
        "remaining": (quota - total) if quota is not None else None,
    })


# ════════════════════════════════════════════════════════════════════════════
# GET /bookmarks/{bm_id} —— 详情
# ════════════════════════════════════════════════════════════════════════════
async def get_bookmark(request: Request) -> JSONResponse:
    qp = dict(request.query_params)
    uid, role, err = _need_user_role(qp)
    if err:
        return err
    try:
        bm_id = int(request.path_params["bm_id"])
    except (KeyError, ValueError):
        return _err("invalid bm_id", 400)

    if role == ROLE_JOBSEEKER:
        row = await db.get_user_job_interest_by_id(uid, bm_id)
    else:
        row = await db.get_recruiter_geek_interest_by_id(uid, bm_id)
    if not row:
        return _err("bookmark not found", 404)
    return JSONResponse({"ok": True, "item": _to_dto(row, role)})


# ════════════════════════════════════════════════════════════════════════════
# PATCH /bookmarks/{bm_id} —— 编辑（A3）
# Body: { app_user_id, role, status?, notes?, tags?, match_score?, interested? }
# ════════════════════════════════════════════════════════════════════════════
async def update_bookmark(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON", 400)
    uid, role, err = _need_user_role(body)
    if err:
        return err
    try:
        bm_id = int(request.path_params["bm_id"])
    except (KeyError, ValueError):
        return _err("invalid bm_id", 400)

    fields: dict[str, Any] = {}
    if "status" in body:
        st = (body.get("status") or "").strip()
        allowed = VALID_STATUSES_JOBSEEKER if role == ROLE_JOBSEEKER else VALID_STATUSES_RECRUITER
        if st not in allowed:
            return _err(f"status must be one of {allowed}", 400)
        fields["status"] = st
    if "notes" in body:
        fields["notes"] = str(body.get("notes") or "")
    if "tags" in body:
        tags = body.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = [t.strip() for t in tags.split(",") if t.strip()]
        if not isinstance(tags, list):
            return _err("tags must be list", 400)
        fields["tags"] = tags
    if "match_score" in body and body["match_score"] is not None:
        try:
            fields["match_score"] = max(0, min(100, int(body["match_score"])))
        except (TypeError, ValueError):
            return _err("match_score must be int 0-100", 400)
    if "interested" in body:
        fields["interested"] = bool(body["interested"])
    if not fields:
        return _err("no editable fields provided", 400)

    if role == ROLE_JOBSEEKER:
        row = await db.update_user_job_interest_by_id(uid, bm_id, **fields)
    else:
        row = await db.update_recruiter_geek_interest_by_id(uid, bm_id, **fields)
    if not row:
        return _err("bookmark not found", 404)
    return JSONResponse({"ok": True, "item": _to_dto(row, role)})


# ════════════════════════════════════════════════════════════════════════════
# DELETE /bookmarks/{bm_id} —— 删除（A4）
# Query: app_user_id, role
# ════════════════════════════════════════════════════════════════════════════
async def delete_bookmark(request: Request) -> JSONResponse:
    qp = dict(request.query_params)
    uid, role, err = _need_user_role(qp)
    if err:
        return err
    try:
        bm_id = int(request.path_params["bm_id"])
    except (KeyError, ValueError):
        return _err("invalid bm_id", 400)

    if role == ROLE_JOBSEEKER:
        deleted = await db.delete_user_job_interest_by_id(uid, bm_id)
    else:
        deleted = await db.delete_recruiter_geek_interest_by_id(uid, bm_id)
    if not deleted:
        return _err("bookmark not found", 404)
    return JSONResponse({"ok": True, "deleted": bm_id})


# ════════════════════════════════════════════════════════════════════════════
# POST /bookmarks/{bm_id}/reanalyze —— 重新分析（A6）
# 调 agent-gateway 的 evaluate 能力，扣 Credit。
# Body: { app_user_id, role }
# ════════════════════════════════════════════════════════════════════════════
async def reanalyze_bookmark(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON", 400)
    uid, role, err = _need_user_role(body)
    if err:
        return err
    try:
        bm_id = int(request.path_params["bm_id"])
    except (KeyError, ValueError):
        return _err("invalid bm_id", 400)

    if role == ROLE_JOBSEEKER:
        row = await db.get_user_job_interest_by_id(uid, bm_id)
    else:
        row = await db.get_recruiter_geek_interest_by_id(uid, bm_id)
    if not row:
        return _err("bookmark not found", 404)

    # 扣 Credit
    sub = await db.get_subscription(uid)
    plan = (sub.get("plan") if sub else pricing.PLAN_FREE) or pricing.PLAN_FREE
    charge = await credit_ledger.charge_for_sku(
        uid, "action_reanalyze", 1, plan=plan,
        meta={"bookmark_id": bm_id, "role": role},
    )
    if not charge["allowed"]:
        return JSONResponse({
            "ok": False,
            "error": "insufficient_credit",
            "cost": charge["cost"],
            "balance": charge["balance_after"],
            "plan": plan,
        }, status_code=402)

    # 调 agent-gateway evaluate（best-effort：若 agent-gw 离线/招聘端暂未实现，仍返回当前 bookmark）
    # 求职端 → POST /jobs/evaluate-by-id  (agent-gw server.py:923)
    # 招聘端 → 暂无对应单条评估端点（后续 Sprint）；这里只扣 Credit 占位
    agent_gw_url = os.environ.get("AGENT_GATEWAY_URL", "http://127.0.0.1:8769").rstrip("/")
    score: int | None = None
    if role == ROLE_JOBSEEKER:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{agent_gw_url}/jobs/evaluate-by-id",
                    json={
                        "user_id": uid,
                        "platform": row.get("platform", "boss"),
                        "encrypt_job_id": row.get("encrypt_job_id", ""),
                        "job_snapshot": {
                            "title": row.get("job_name", ""),
                            "company": row.get("company_name", ""),
                            "salary": row.get("salary_desc", ""),
                            "city": row.get("city", ""),
                            "jd": row.get("jd_excerpt", ""),
                        },
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    score = data.get("match_score") or data.get("score")
                    # agent-gw 也可能返回 {ok: true, result: {match_score: ...}}
                    if score is None and isinstance(data.get("result"), dict):
                        score = data["result"].get("match_score") or data["result"].get("score")
        except Exception as e:
            log.warning("agent-gateway reanalyze failed: %s", e)
    else:
        log.info("recruiter reanalyze not yet wired to agent-gw; ledger only (bm_id=%s)", bm_id)

    # 更新 match_score（若拿到）
    new_row = row
    if score is not None:
        update_fn = (db.update_user_job_interest_by_id if role == ROLE_JOBSEEKER
                     else db.update_recruiter_geek_interest_by_id)
        new_row = await update_fn(uid, bm_id, match_score=int(score)) or row

    return JSONResponse({
        "ok": True,
        "item": _to_dto(new_row, role),
        "credit_charged": charge["cost"],
        "balance_after": charge["balance_after"],
        "score_updated": score is not None,
    })
