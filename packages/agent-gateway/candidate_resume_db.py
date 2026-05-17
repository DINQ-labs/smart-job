"""
候选人简历管理 — 存储从外部平台下载的候选人简历（Indeed / Boss / LinkedIn）。

与 resume_db 不同：
  - resume_db 管理的是**求职用户自己**上传的简历（每用户一份）
  - 本模块管理的是**招聘方下载的候选人**简历（按 platform + candidate_id + downloaded_by 去重）
"""
from __future__ import annotations

import json
from typing import Any

from db import _get_pool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_resumes (
    id              BIGSERIAL    PRIMARY KEY,
    platform        TEXT         NOT NULL,
    candidate_id    TEXT         NOT NULL,
    candidate_name  TEXT,
    candidate_meta  JSONB,
    file_path       TEXT,
    file_size       INTEGER,
    file_type       TEXT         NOT NULL DEFAULT 'pdf',
    parse_status    TEXT         NOT NULL DEFAULT 'pending',
    raw_text        TEXT,
    parsed_data     JSONB,
    source_job_id   TEXT,
    source_url      TEXT,
    downloaded_by   TEXT         NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (platform, candidate_id, downloaded_by)
);
CREATE INDEX IF NOT EXISTS idx_cr_platform      ON candidate_resumes (platform);
CREATE INDEX IF NOT EXISTS idx_cr_source_job    ON candidate_resumes (source_job_id);
CREATE INDEX IF NOT EXISTS idx_cr_status        ON candidate_resumes (parse_status);
CREATE INDEX IF NOT EXISTS idx_cr_downloaded_by ON candidate_resumes (downloaded_by);
"""

_MIGRATION = """
-- 迁移：从旧的双列唯一约束升级到三列（加 downloaded_by）
DO $$
BEGIN
    -- 先给已有空值行设默认值
    UPDATE candidate_resumes SET downloaded_by = '' WHERE downloaded_by IS NULL;
    -- 改列为 NOT NULL DEFAULT ''
    ALTER TABLE candidate_resumes ALTER COLUMN downloaded_by SET NOT NULL;
    ALTER TABLE candidate_resumes ALTER COLUMN downloaded_by SET DEFAULT '';
    -- 删旧约束（如果存在）
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'candidate_resumes_platform_candidate_id_key') THEN
        ALTER TABLE candidate_resumes DROP CONSTRAINT candidate_resumes_platform_candidate_id_key;
    END IF;
    -- 加新约束（如果不存在）
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'candidate_resumes_platform_candidate_id_downloaded_by_key') THEN
        ALTER TABLE candidate_resumes ADD CONSTRAINT candidate_resumes_platform_candidate_id_downloaded_by_key
            UNIQUE (platform, candidate_id, downloaded_by);
    END IF;
END $$;
"""


async def init_candidate_resume_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)
        await conn.execute(_MIGRATION)


# ── CRUD ────────────────────────────────────────────────────────────────────


async def upsert_candidate_meta(
    platform: str,
    candidate_id: str,
    candidate_name: str | None = None,
    candidate_meta: dict | None = None,
    source_job_id: str | None = None,
    downloaded_by: str = "",
) -> int:
    """搜索候选人时调用 — 只写入/更新元数据，不触碰文件和解析状态。"""
    pool = await _get_pool()
    meta_json = json.dumps(candidate_meta, ensure_ascii=False) if candidate_meta else None
    row = await pool.fetchrow(
        """INSERT INTO candidate_resumes
               (platform, candidate_id, candidate_name, candidate_meta,
                source_job_id, downloaded_by)
           VALUES ($1, $2, $3, $4::jsonb, $5, $6)
           ON CONFLICT (platform, candidate_id, downloaded_by) DO UPDATE SET
               candidate_name = COALESCE(EXCLUDED.candidate_name, candidate_resumes.candidate_name),
               candidate_meta = COALESCE(EXCLUDED.candidate_meta, candidate_resumes.candidate_meta),
               source_job_id  = COALESCE(EXCLUDED.source_job_id, candidate_resumes.source_job_id),
               updated_at     = NOW()
           RETURNING id""",
        platform, candidate_id, candidate_name, meta_json,
        source_job_id, downloaded_by or "",
    )
    return row["id"]


async def upsert_candidate_resume(
    platform: str,
    candidate_id: str,
    candidate_name: str | None = None,
    candidate_meta: dict | None = None,
    file_path: str | None = None,
    file_size: int | None = None,
    file_type: str = "pdf",
    source_job_id: str | None = None,
    source_url: str | None = None,
    downloaded_by: str = "",
) -> int:
    """下载简历时调用 — 写入文件信息，重置解析状态，保留已有 meta。"""
    pool = await _get_pool()
    meta_json = json.dumps(candidate_meta, ensure_ascii=False) if candidate_meta else None
    row = await pool.fetchrow(
        """INSERT INTO candidate_resumes
               (platform, candidate_id, candidate_name, candidate_meta,
                file_path, file_size, file_type,
                source_job_id, source_url, downloaded_by)
           VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10)
           ON CONFLICT (platform, candidate_id, downloaded_by) DO UPDATE SET
               candidate_name = COALESCE(EXCLUDED.candidate_name, candidate_resumes.candidate_name),
               candidate_meta = COALESCE(EXCLUDED.candidate_meta, candidate_resumes.candidate_meta),
               file_path      = EXCLUDED.file_path,
               file_size      = EXCLUDED.file_size,
               source_job_id  = COALESCE(EXCLUDED.source_job_id, candidate_resumes.source_job_id),
               source_url     = COALESCE(EXCLUDED.source_url, candidate_resumes.source_url),
               parse_status   = 'pending',
               updated_at     = NOW()
           RETURNING id""",
        platform, candidate_id, candidate_name, meta_json,
        file_path, file_size, file_type,
        source_job_id, source_url, downloaded_by or "",
    )
    return row["id"]


async def get_candidate_resume(
    platform: str, candidate_id: str, downloaded_by: str = "",
) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow(
        """SELECT * FROM candidate_resumes
           WHERE platform = $1 AND candidate_id = $2 AND downloaded_by = $3""",
        platform, candidate_id, downloaded_by or "",
    )
    return _row_to_dict(row) if row else None


async def update_parse_status(
    resume_id: int,
    status: str,
    raw_text: str | None = None,
    parsed_data: dict | None = None,
) -> None:
    pool = await _get_pool()
    await pool.execute(
        """UPDATE candidate_resumes
           SET parse_status = $2,
               raw_text     = COALESCE($3, raw_text),
               parsed_data  = COALESCE($4::jsonb, parsed_data),
               updated_at   = NOW()
           WHERE id = $1""",
        resume_id, status, raw_text,
        json.dumps(parsed_data, ensure_ascii=False) if parsed_data else None,
    )


async def list_candidate_resumes(
    platform: str | None = None,
    source_job_id: str | None = None,
    downloaded_by: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    pool = await _get_pool()
    conditions = []
    params: list[Any] = []
    idx = 1

    if platform:
        conditions.append(f"platform = ${idx}")
        params.append(platform)
        idx += 1
    if source_job_id:
        conditions.append(f"source_job_id = ${idx}")
        params.append(source_job_id)
        idx += 1
    if downloaded_by:
        conditions.append(f"downloaded_by = ${idx}")
        params.append(downloaded_by)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"""SELECT id, platform, candidate_id, candidate_name, candidate_meta,
                   file_size, file_type, parse_status, source_job_id,
                   downloaded_by, created_at, updated_at
            FROM candidate_resumes
            {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}""",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def delete_candidate_resume(
    platform: str, candidate_id: str, downloaded_by: str = "",
) -> str | None:
    """Returns file_path if existed, else None."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """DELETE FROM candidate_resumes
           WHERE platform = $1 AND candidate_id = $2 AND downloaded_by = $3
           RETURNING file_path""",
        platform, candidate_id, downloaded_by or "",
    )
    return row["file_path"] if row else None


async def count_candidate_resumes(platform: str | None = None) -> int:
    pool = await _get_pool()
    if platform:
        return await pool.fetchval(
            "SELECT COUNT(*) FROM candidate_resumes WHERE platform = $1", platform
        ) or 0
    return await pool.fetchval("SELECT COUNT(*) FROM candidate_resumes") or 0


# ── helpers ─────────────────────────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict:
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d
