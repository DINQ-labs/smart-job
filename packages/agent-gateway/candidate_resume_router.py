"""
候选人简历 REST 端点 — 供 admin 后台查看/下载候选人简历。
"""
from __future__ import annotations

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import candidate_resume_db
import config

log = logging.getLogger(__name__)

CANDIDATE_RESUME_DIR = os.getenv(
    "CANDIDATE_RESUME_DIR", "uploads/candidate_resumes"
)


# ── GET /candidate-resumes ──────────────────────────────────────────────────

async def list_candidate_resumes(request: Request) -> JSONResponse:
    platform = request.query_params.get("platform")
    source_job_id = request.query_params.get("job_id")
    downloaded_by = request.query_params.get("user_id")
    limit = min(int(request.query_params.get("limit", 50)), 200)
    offset = int(request.query_params.get("offset", 0))

    resumes = await candidate_resume_db.list_candidate_resumes(
        platform=platform, source_job_id=source_job_id,
        downloaded_by=downloaded_by,
        limit=limit, offset=offset,
    )
    return JSONResponse({"resumes": resumes, "limit": limit, "offset": offset})


# ── GET /candidate-resumes/{platform}/{candidate_id} ────────────────────────

async def get_candidate_resume(request: Request) -> JSONResponse:
    platform = request.path_params["platform"]
    candidate_id = request.path_params["candidate_id"]
    downloaded_by = request.query_params.get("user_id", "")

    row = await candidate_resume_db.get_candidate_resume(
        platform, candidate_id, downloaded_by=downloaded_by,
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    return JSONResponse(row)


# ── GET /candidate-resumes/{platform}/{candidate_id}/file ───────────────────

async def download_candidate_resume_file(request: Request) -> Response:
    platform = request.path_params["platform"]
    candidate_id = request.path_params["candidate_id"]
    downloaded_by = request.query_params.get("user_id", "")

    row = await candidate_resume_db.get_candidate_resume(
        platform, candidate_id, downloaded_by=downloaded_by,
    )
    if not row or not row.get("file_path"):
        return JSONResponse({"error": "not found"}, status_code=404)

    abs_path = os.path.join(CANDIDATE_RESUME_DIR, row["file_path"])
    if not os.path.exists(abs_path):
        return JSONResponse({"error": "file missing"}, status_code=404)

    with open(abs_path, "rb") as f:
        data = f.read()

    name = row.get("candidate_name", candidate_id)
    ext = row.get("file_type", "pdf")
    ascii_fallback = f"resume_{candidate_id}.{ext}"
    from urllib.parse import quote
    filename = f"{name}.{ext}" if name else ascii_fallback
    cd = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(filename)}'

    mime = "application/pdf" if ext == "pdf" else "application/octet-stream"
    return Response(content=data, media_type=mime, headers={"Content-Disposition": cd})


# ── DELETE /candidate-resumes/{platform}/{candidate_id} ─────────────────────

async def delete_candidate_resume(request: Request) -> JSONResponse:
    platform = request.path_params["platform"]
    candidate_id = request.path_params["candidate_id"]
    downloaded_by = request.query_params.get("user_id", "")

    file_path = await candidate_resume_db.delete_candidate_resume(
        platform, candidate_id, downloaded_by=downloaded_by,
    )
    if file_path is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    abs_path = os.path.join(CANDIDATE_RESUME_DIR, file_path)
    try:
        if os.path.exists(abs_path):
            os.remove(abs_path)
    except Exception as e:
        log.warning("Failed to delete file %s: %s", abs_path, e)

    return JSONResponse({"ok": True, "deleted": True})
