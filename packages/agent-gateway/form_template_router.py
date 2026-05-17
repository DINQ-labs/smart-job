"""
form_template_router.py — 站点表单知识库 REST 端点。

  POST /autofill/template/record    填完后记录表单结构 + outcome（零用户值）
  POST /autofill/template/capture   上传 opt-in 抓到的 HTTP 请求

设计文档：autofill-ext/docs/form-knowledge-base.md
"""
from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

import form_template_db
import portal_auth

log = logging.getLogger(__name__)

_MAX_REQUESTS = 60   # 单次抓包上传的请求条数上限


@portal_auth.require_user
async def record_template_handler(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)

    user_id = request.state.user_id
    page_url = (body.get("page_url") or "").strip()
    fields = body.get("fields") or []
    if not page_url or not isinstance(fields, list) or not fields:
        return JSONResponse({"ok": False, "error": "缺 page_url 或 fields"}, status_code=400)

    try:
        template_id, learned = await form_template_db.record_template(
            page_url=page_url,
            page_title=body.get("page_title") or "",
            fields=fields,
            outcome=body.get("outcome") or [],
            user_id=user_id,
        )
    except Exception as e:
        log.warning("record_template failed user_id=%s: %s", user_id, e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    return JSONResponse({"ok": True, "template_id": template_id, "fields_learned": learned})


@portal_auth.require_user
async def upload_capture_handler(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)

    user_id = request.state.user_id
    page_url = (body.get("page_url") or "").strip()
    requests_ = body.get("requests") or []
    if not page_url or not isinstance(requests_, list) or not requests_:
        return JSONResponse({"ok": False, "error": "缺 page_url 或 requests"}, status_code=400)

    requests_ = requests_[:_MAX_REQUESTS]
    try:
        capture_id = await form_template_db.insert_capture(user_id, page_url, requests_)
    except Exception as e:
        log.warning("upload_capture failed user_id=%s: %s", user_id, e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    return JSONResponse({"ok": True, "capture_id": capture_id})
