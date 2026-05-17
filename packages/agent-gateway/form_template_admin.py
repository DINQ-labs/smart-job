"""
form_template_admin.py — autofill 知识库 / 抓包 的管理后台接口。

挂 /admin/autofill/*，与 gateway 既有 /admin/* 同层 —— 鉴权由 nginx / 网络边界统一
处理（与 /admin/chips、/admin/metrics/* 一致，应用层不做鉴权）。

设计文档：autofill-ext/docs/admin-panel-architecture.md
"""
from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

import form_template_db
import form_template_enrich

log = logging.getLogger(__name__)


def _qint(request: Request, key: str, default: int) -> int:
    try:
        return int(request.query_params.get(key, default))
    except (TypeError, ValueError):
        return default


async def _stat_get(key: str) -> int:
    try:
        import redis_client
        v = await redis_client.get("autofill:stat:" + key)
        return int(v) if v else 0
    except Exception:
        return 0


# ── 知识库 ──────────────────────────────────────────────────────────────────

async def admin_list_templates(request: Request) -> JSONResponse:
    q = (request.query_params.get("q") or "").strip()
    limit = min(max(_qint(request, "limit", 50), 1), 200)
    offset = max(_qint(request, "offset", 0), 0)
    try:
        data = await form_template_db.list_templates(q, limit, offset)
    except Exception as e:
        log.warning("admin_list_templates failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True, **data})


async def admin_get_template(request: Request) -> JSONResponse:
    tid = request.path_params["template_id"]
    try:
        tpl = await form_template_db.get_template_by_id(tid)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    if not tpl:
        return JSONResponse({"ok": False, "error": "模板不存在"}, status_code=404)
    return JSONResponse({"ok": True, "template": tpl})


async def admin_patch_template_field(request: Request) -> JSONResponse:
    """运营人工纠正字段：body { ident, profile_key?, widget? }。"""
    tid = request.path_params["template_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    ident = (body.get("ident") or "").strip()
    if not ident:
        return JSONResponse({"ok": False, "error": "缺 ident"}, status_code=400)
    try:
        updated = await form_template_db.patch_template_field(
            tid, ident,
            profile_key=body.get("profile_key"),
            widget=body.get("widget"),
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    if updated is None:
        return JSONResponse({"ok": False, "error": "模板或字段不存在"}, status_code=404)
    return JSONResponse({"ok": True, "field": updated})


async def admin_delete_template(request: Request) -> JSONResponse:
    tid = request.path_params["template_id"]
    try:
        ok = await form_template_db.delete_template(tid)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": ok})


# ── 抓包 ────────────────────────────────────────────────────────────────────

async def admin_list_captures(request: Request) -> JSONResponse:
    site = (request.query_params.get("site") or "").strip()
    pq = request.query_params.get("processed")
    processed = None
    if pq in ("true", "1"):
        processed = True
    elif pq in ("false", "0"):
        processed = False
    limit = min(max(_qint(request, "limit", 50), 1), 200)
    offset = max(_qint(request, "offset", 0), 0)
    try:
        data = await form_template_db.list_captures(processed, site, limit, offset)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True, **data})


async def admin_get_capture(request: Request) -> JSONResponse:
    cid = request.path_params["capture_id"]
    try:
        cap = await form_template_db.get_capture(cid)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    if not cap:
        return JSONResponse({"ok": False, "error": "抓包不存在"}, status_code=404)
    return JSONResponse({"ok": True, "capture": cap})


async def admin_enrich_capture(request: Request) -> JSONResponse:
    cid = request.path_params["capture_id"]
    try:
        cap = await form_template_db.get_capture(cid)
        if not cap:
            return JSONResponse({"ok": False, "error": "抓包不存在"}, status_code=404)
        merged = await form_template_enrich.process_capture(cap)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True, "merged": merged})


async def admin_delete_capture(request: Request) -> JSONResponse:
    cid = request.path_params["capture_id"]
    try:
        ok = await form_template_db.delete_capture(cid)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": ok})


# ── 概览 ────────────────────────────────────────────────────────────────────

async def admin_autofill_stats(request: Request) -> JSONResponse:
    try:
        base = await form_template_db.autofill_stats()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    match_calls = await _stat_get("match_calls")
    match_hit = await _stat_get("match_template_hit")
    stats = dict(base)
    stats.update({
        "match_calls": match_calls,
        "match_template_hit": match_hit,
        "match_llm_fail": await _stat_get("match_llm_fail"),
        "match_hit_rate": round(match_hit / match_calls, 3) if match_calls else 0,
        "ocr_calls": await _stat_get("ocr_calls"),
        "ocr_fail": await _stat_get("ocr_fail"),
    })
    return JSONResponse({"ok": True, "stats": stats})
