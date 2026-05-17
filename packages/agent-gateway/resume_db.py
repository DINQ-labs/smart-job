"""
简历相关数据库操作 — users / resumes / resume_parsed 三张表。
复用 db._get_pool() 现有连接池，零新连接池开销。
"""
from __future__ import annotations

import json
from typing import Any

from db import _get_pool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT        PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    meta          JSONB
);

CREATE TABLE IF NOT EXISTS resumes (
    id            BIGSERIAL   PRIMARY KEY,
    user_id       TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    original_name TEXT        NOT NULL,
    file_path     TEXT        NOT NULL,
    file_size     INTEGER     NOT NULL,
    file_type     TEXT        NOT NULL,
    parse_status  TEXT        NOT NULL DEFAULT 'pending',
    parse_error   TEXT,
    raw_text      TEXT,
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    parsed_at     TIMESTAMPTZ,
    UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS idx_resumes_user   ON resumes (user_id);
CREATE INDEX IF NOT EXISTS idx_resumes_status ON resumes (parse_status);

CREATE TABLE IF NOT EXISTS resume_parsed (
    id                  BIGSERIAL PRIMARY KEY,
    resume_id           BIGINT    NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    user_id             TEXT      NOT NULL,
    target_salary_min   INTEGER,
    target_salary_max   INTEGER,
    target_salary_raw   TEXT,
    target_cities       JSONB,
    target_positions    JSONB,
    name                TEXT,
    phone               TEXT,
    email               TEXT,
    work_years          TEXT,
    degree              TEXT,
    work_experience     JSONB,
    education           JSONB,
    skills              JSONB,
    projects            JSONB,
    self_evaluation     TEXT,
    raw_json            JSONB,
    model_used          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (resume_id)
);
CREATE INDEX IF NOT EXISTS idx_rp_user ON resume_parsed (user_id);
"""


async def init_resume_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)


async def upsert_user(user_id: str, meta: dict | None = None) -> None:
    pool = await _get_pool()
    await pool.execute(
        """INSERT INTO users (user_id, meta)
           VALUES ($1, $2)
           ON CONFLICT (user_id) DO UPDATE
               SET last_seen_at = NOW(),
                   meta = COALESCE($2::jsonb, users.meta)""",
        user_id,
        json.dumps(meta) if meta else None,
    )


async def create_resume(
    user_id: str,
    original_name: str,
    file_path: str,
    file_size: int,
    file_type: str,
) -> int:
    pool = await _get_pool()
    # 每用户只保留一份：先删旧记录（级联删 resume_parsed）
    await pool.execute("DELETE FROM resumes WHERE user_id = $1", user_id)
    row = await pool.fetchrow(
        """INSERT INTO resumes (user_id, original_name, file_path, file_size, file_type)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        user_id, original_name, file_path, file_size, file_type,
    )
    return row["id"]


async def update_resume_status(
    resume_id: int,
    status: str,
    raw_text: str | None = None,
    error: str | None = None,
) -> None:
    pool = await _get_pool()
    if status == "done":
        await pool.execute(
            """UPDATE resumes
               SET parse_status = $2, parsed_at = NOW(), raw_text = COALESCE($3, raw_text)
               WHERE id = $1""",
            resume_id, status, raw_text,
        )
    elif status == "failed":
        await pool.execute(
            """UPDATE resumes
               SET parse_status = $2, parse_error = $3, parsed_at = NOW()
               WHERE id = $1""",
            resume_id, status, error,
        )
    else:
        await pool.execute(
            """UPDATE resumes
               SET parse_status = $2, raw_text = COALESCE($3, raw_text)
               WHERE id = $1""",
            resume_id, status, raw_text,
        )


async def save_parsed_result(resume_id: int, user_id: str, parsed: dict) -> None:
    pool = await _get_pool()

    def _j(v: Any) -> str | None:
        return json.dumps(v, ensure_ascii=False) if v is not None else None

    await pool.execute(
        """INSERT INTO resume_parsed (
               resume_id, user_id,
               target_salary_min, target_salary_max, target_salary_raw,
               target_cities, target_positions,
               name, phone, email, work_years, degree,
               work_experience, education, skills, projects,
               self_evaluation, raw_json, model_used
           ) VALUES (
               $1, $2, $3, $4, $5,
               $6::jsonb, $7::jsonb,
               $8, $9, $10, $11, $12,
               $13::jsonb, $14::jsonb, $15::jsonb, $16::jsonb,
               $17, $18::jsonb, $19
           )
           ON CONFLICT (resume_id) DO UPDATE SET
               target_salary_min  = EXCLUDED.target_salary_min,
               target_salary_max  = EXCLUDED.target_salary_max,
               target_salary_raw  = EXCLUDED.target_salary_raw,
               target_cities      = EXCLUDED.target_cities,
               target_positions   = EXCLUDED.target_positions,
               name               = EXCLUDED.name,
               phone              = EXCLUDED.phone,
               email              = EXCLUDED.email,
               work_years         = EXCLUDED.work_years,
               degree             = EXCLUDED.degree,
               work_experience    = EXCLUDED.work_experience,
               education          = EXCLUDED.education,
               skills             = EXCLUDED.skills,
               projects           = EXCLUDED.projects,
               self_evaluation    = EXCLUDED.self_evaluation,
               raw_json           = EXCLUDED.raw_json,
               model_used         = EXCLUDED.model_used""",
        resume_id, user_id,
        parsed.get("target_salary_min"), parsed.get("target_salary_max"),
        parsed.get("target_salary_raw"),
        _j(parsed.get("target_cities")), _j(parsed.get("target_positions")),
        parsed.get("name"), parsed.get("phone"), parsed.get("email"),
        parsed.get("work_years"), parsed.get("degree"),
        _j(parsed.get("work_experience")), _j(parsed.get("education")),
        _j(parsed.get("skills")), _j(parsed.get("projects")),
        parsed.get("self_evaluation"),
        _j(parsed.get("_raw_json")),
        parsed.get("_model_used"),
    )


async def get_resume_by_user(user_id: str) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow(
        """SELECT r.id, r.user_id, r.original_name, r.file_path, r.file_size,
                  r.file_type, r.parse_status, r.parse_error,
                  r.uploaded_at, r.parsed_at,
                  p.target_salary_min, p.target_salary_max, p.target_salary_raw,
                  p.target_cities, p.target_positions,
                  p.name, p.phone, p.email, p.work_years, p.degree,
                  p.work_experience, p.education, p.skills, p.projects,
                  p.self_evaluation, p.model_used
           FROM resumes r
           LEFT JOIN resume_parsed p ON p.resume_id = r.id
           WHERE r.user_id = $1""",
        user_id,
    )
    if row is None:
        return None

    d: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif isinstance(v, str):
            # asyncpg returns JSONB columns as strings — parse them
            d[k] = v
        else:
            d[k] = v
    return d


async def list_users_with_resumes(limit: int = 100, offset: int = 0) -> list[dict]:
    """返回所有用户及其简历状态（LEFT JOIN）。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT u.user_id, u.first_seen_at, u.last_seen_at,
                  r.id AS resume_id, r.original_name, r.parse_status,
                  r.uploaded_at, r.parsed_at, r.parse_error,
                  (p.user_id IS NOT NULL) AS has_preferences
           FROM users u
           LEFT JOIN resumes r ON r.user_id = u.user_id
           LEFT JOIN user_preferences p ON p.user_id = u.user_id
           ORDER BY u.last_seen_at DESC
           LIMIT $1 OFFSET $2""",
        limit, offset,
    )
    result = []
    for row in rows:
        d: dict = {}
        for k, v in row.items():
            d[k] = v.isoformat() if hasattr(v, "isoformat") else v
        result.append(d)
    return result


async def delete_resume_by_user(user_id: str) -> str | None:
    """Returns file_path if a record existed, else None."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "DELETE FROM resumes WHERE user_id = $1 RETURNING file_path",
        user_id,
    )
    return row["file_path"] if row else None
