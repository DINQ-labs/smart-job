"""
简历 REST 端点 — 上传、状态查询、内容获取、删除。

简历管理完全本地化：只读写本地 DB + 磁盘文件，不调用任何外部服务（不拉取、不推送）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import db  # Phase 1.4: log_funnel_step
import resume_db
from resume_parser import parse_and_save

log = logging.getLogger(__name__)

_ALLOWED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_ALLOWED_EXT = {"pdf", "docx", "doc"}
_MAX_BYTES = config.RESUME_MAX_SIZE_MB * 1024 * 1024


def _ext_to_type(ext: str) -> str:
    return "pdf" if ext == "pdf" else "docx"


# ── POST /resume/upload ────────────────────────────────────────────────────────

async def upload_resume(request: Request) -> JSONResponse:
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"ok": False, "error": "请求必须为 multipart/form-data"}, status_code=400)

    user_id = (form.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"ok": False, "error": "user_id 不能为空"}, status_code=400)

    upload = form.get("file")
    if upload is None or not hasattr(upload, "filename"):
        return JSONResponse({"ok": False, "error": "请上传 file 字段"}, status_code=400)

    filename: str = upload.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXT:
        return JSONResponse(
            {"ok": False, "error": f"仅支持 PDF / DOCX，当前扩展名：{ext}"},
            status_code=400,
        )

    content_type = upload.content_type or ""
    if content_type and content_type not in _ALLOWED_MIME:
        # 允许 content_type 缺失（某些客户端不发送）
        log.warning("Unexpected content_type=%s for file=%s", content_type, filename)

    data: bytes = await upload.read()
    if len(data) > _MAX_BYTES:
        return JSONResponse(
            {"ok": False, "error": f"文件大小超过 {config.RESUME_MAX_SIZE_MB}MB 限制"},
            status_code=413,
        )

    # 确保目录存在
    save_dir = os.path.join(config.RESUME_UPLOAD_DIR, user_id)
    os.makedirs(save_dir, exist_ok=True)

    safe_filename = f"{int(time.time())}_{os.path.basename(filename)}"
    abs_path = os.path.join(save_dir, safe_filename)
    rel_path = os.path.join(user_id, safe_filename)

    with open(abs_path, "wb") as f:
        f.write(data)

    # 确保用户记录存在
    try:
        await resume_db.upsert_user(user_id)
    except Exception as e:
        log.warning("upsert_user failed: %s", e)

    try:
        resume_id = await resume_db.create_resume(
            user_id=user_id,
            original_name=filename,
            file_path=rel_path,
            file_size=len(data),
            file_type=_ext_to_type(ext),
        )
    except Exception as e:
        log.exception("create_resume failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # 后台解析（不阻塞响应）
    asyncio.create_task(
        parse_and_save(resume_id, user_id, abs_path, _ext_to_type(ext))
    )

    # 简历上传成功 → 'resume_uploaded' funnel(per user-platform 去重)。
    # 一份简历对所有 3 个平台可用,所以为 boss/linkedin/indeed 各写一次。
    # log_funnel_step 走唯一索引去重,重复上传同一用户不会膨胀计数。
    for _p in ("boss", "linkedin", "indeed"):
        try:
            await db.log_funnel_step(None, user_id, "jobseeker", _p, "resume_uploaded")
        except Exception as _e:
            log.debug("funnel resume_uploaded(%s) log failed: %s", _p, _e)

    return JSONResponse(
        {"ok": True, "resume_id": resume_id, "parse_status": "pending",
         "message": "简历已上传，正在后台解析"},
        status_code=202,
    )


# ── GET /resume/status ────────────────────────────────────────────────────────

async def get_resume_status(request: Request) -> JSONResponse:
    user_id = (request.query_params.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"ok": False, "error": "user_id 不能为空"}, status_code=400)

    try:
        row = await resume_db.get_resume_by_user(user_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    if row is not None:
        # 本地优先：已有简历直接返回，不触碰外部
        return JSONResponse({
            "ok": True,
            "has_resume": True,
            "resume_id": row["id"],
            "parse_status": row["parse_status"],
            "original_name": row["original_name"],
            "uploaded_at": row.get("uploaded_at"),
            "parsed_at": row.get("parsed_at"),
        })

    # 本地无简历 —— 简历管理完全本地化，不再向任何外部服务拉取
    return JSONResponse({"ok": True, "has_resume": False})


# ── GET /resume/{user_id} ─────────────────────────────────────────────────────

async def get_resume(request: Request) -> JSONResponse:
    user_id = request.path_params["user_id"]

    try:
        row = await resume_db.get_resume_by_user(user_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    if row is None:
        return JSONResponse({"ok": False, "error": "未找到简历"}, status_code=404)

    status = row["parse_status"]
    resp: dict = {"ok": True, "parse_status": status}

    if status == "done":
        resp["parsed"] = {
            "name": row.get("name"),
            "phone": row.get("phone"),
            "email": row.get("email"),
            "work_years": row.get("work_years"),
            "degree": row.get("degree"),
            "target_salary_min": row.get("target_salary_min"),
            "target_salary_max": row.get("target_salary_max"),
            "target_salary_raw": row.get("target_salary_raw"),
            "target_cities": _parse_jsonb(row.get("target_cities")),
            "target_positions": _parse_jsonb(row.get("target_positions")),
            "work_experience": _parse_jsonb(row.get("work_experience")),
            "education": _parse_jsonb(row.get("education")),
            "skills": _parse_jsonb(row.get("skills")),
            "projects": _parse_jsonb(row.get("projects")),
            "self_evaluation": row.get("self_evaluation"),
        }
    elif status == "failed":
        resp["error"] = row.get("parse_error")

    return JSONResponse(resp)


# ── POST /resume/{user_id}/retry ──────────────────────────────────────────────

async def retry_resume_parse(request: Request) -> JSONResponse:
    """重新触发简历解析（文件已在磁盘上）。"""
    user_id = request.path_params["user_id"]
    try:
        row = await resume_db.get_resume_by_user(user_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    if row is None:
        return JSONResponse({"ok": False, "error": "未找到简历"}, status_code=404)

    if row["parse_status"] == "parsing":
        return JSONResponse({"ok": False, "error": "正在解析中，请稍后再试"}, status_code=409)

    abs_path = os.path.join(config.RESUME_UPLOAD_DIR, row["file_path"])
    if not os.path.exists(abs_path):
        return JSONResponse({"ok": False, "error": "简历文件不存在，请重新上传"}, status_code=404)

    resume_id = row["id"]
    file_type = row["file_type"]
    try:
        await resume_db.update_resume_status(resume_id, "pending")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    asyncio.create_task(parse_and_save(resume_id, user_id, abs_path, file_type))

    # NOTE: 不在 retry 重新写 resume_uploaded —— retry 是重新解析,不是重新上传,
    # funnel 应该只在 POST /resume/upload 成功时埋(写 boss/linkedin/indeed 三份)。

    return JSONResponse({"ok": True, "resume_id": resume_id, "parse_status": "pending"})


# ── DELETE /resume/{user_id} ──────────────────────────────────────────────────

async def delete_resume(request: Request) -> JSONResponse:
    user_id = request.path_params["user_id"]

    try:
        file_path = await resume_db.delete_resume_by_user(user_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    if file_path is None:
        return JSONResponse({"ok": False, "error": "未找到简历"}, status_code=404)

    # 删除磁盘文件（不阻塞，失败仅记录日志）
    abs_path = os.path.join(config.RESUME_UPLOAD_DIR, file_path)
    try:
        if os.path.exists(abs_path):
            os.remove(abs_path)
    except Exception as e:
        log.warning("Failed to delete file %s: %s", abs_path, e)

    return JSONResponse({"ok": True, "deleted": True})


# ── helper ────────────────────────────────────────────────────────────────────

def _parse_jsonb(value):
    """asyncpg 有时以字符串形式返回 JSONB，统一解析为 Python 对象。"""
    if value is None:
        return None
    if isinstance(value, str):
        import json
        try:
            return json.loads(value)
        except Exception:
            return value
    return value
