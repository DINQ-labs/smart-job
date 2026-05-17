"""
API 抓包数据持久化 — asyncpg + PostgreSQL。

表：
  api_capture_sessions   每次扩展录制一行（extension 生成的 session_id 为主键）
  api_capture_requests   该录制内每条 HTTP 请求一行
"""
from __future__ import annotations

import json
from typing import Any

from db import _get_pool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_capture_sessions (
    id              TEXT        PRIMARY KEY,
    name            TEXT        NOT NULL DEFAULT '',
    tab_url         TEXT        NOT NULL DEFAULT '',
    request_count   INTEGER     NOT NULL DEFAULT 0,
    analysis_status TEXT        NOT NULL DEFAULT 'none',
    analysis_text   TEXT,
    analysis_model  TEXT,
    analyzed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_capture_requests (
    id                   BIGSERIAL   PRIMARY KEY,
    session_id           TEXT        NOT NULL REFERENCES api_capture_sessions(id) ON DELETE CASCADE,
    request_id           TEXT,
    resource_type        TEXT,
    timestamp            DOUBLE PRECISION,
    method               TEXT        NOT NULL DEFAULT 'GET',
    url                  TEXT        NOT NULL,
    request_headers      JSONB,
    request_body         TEXT,
    response_status      INTEGER,
    response_status_text TEXT,
    response_headers     JSONB,
    response_mime_type   TEXT,
    response_body        TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_acr_session ON api_capture_requests (session_id, id);
"""


async def init_capture_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)
        # 兼容旧表：补充新列
        await conn.execute(
            "ALTER TABLE api_capture_sessions ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT ''"
        )


# ── session lifecycle ────────────────────────────────────────────────────────


async def upsert_session(session_id: str, tab_url: str = "", name: str = "") -> None:
    pool = await _get_pool()
    await pool.execute(
        """INSERT INTO api_capture_sessions (id, tab_url, name)
           VALUES ($1, $2, $3)
           ON CONFLICT (id) DO UPDATE SET
               tab_url    = COALESCE(NULLIF(EXCLUDED.tab_url, ''), api_capture_sessions.tab_url),
               name       = COALESCE(NULLIF(EXCLUDED.name, ''), api_capture_sessions.name),
               updated_at = NOW()""",
        session_id, tab_url, name,
    )


async def add_capture(session_id: str, tab_url: str, capture: dict) -> int:
    """插入一条抓包记录，返回该 session 的新请求总数。"""
    pool = await _get_pool()

    # 确保 session 存在
    await upsert_session(session_id, tab_url)

    req = capture.get("request", {})
    resp = capture.get("response") or {}

    await pool.execute(
        """INSERT INTO api_capture_requests
               (session_id, request_id, resource_type, timestamp,
                method, url, request_headers, request_body,
                response_status, response_status_text,
                response_headers, response_mime_type, response_body)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
        session_id,
        capture.get("id"),
        capture.get("resourceType"),
        capture.get("timestamp"),
        req.get("method", "GET"),
        req.get("url", ""),
        json.dumps(req.get("headers", {}), ensure_ascii=False) if req.get("headers") else None,
        req.get("postData"),
        resp.get("status"),
        resp.get("statusText"),
        json.dumps(resp.get("headers", {}), ensure_ascii=False) if resp.get("headers") else None,
        resp.get("mimeType"),
        resp.get("body"),
    )

    row = await pool.fetchrow(
        """UPDATE api_capture_sessions
           SET request_count = request_count + 1, updated_at = NOW()
           WHERE id = $1
           RETURNING request_count""",
        session_id,
    )
    return row["request_count"] if row else 0


# ── queries ──────────────────────────────────────────────────────────────────


async def list_sessions(limit: int = 50, offset: int = 0) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT id, name, tab_url, request_count, analysis_status,
                  analysis_model, analyzed_at, created_at, updated_at
           FROM api_capture_sessions
           ORDER BY updated_at DESC
           LIMIT $1 OFFSET $2""",
        limit, offset,
    )
    return [_row_to_dict(r) for r in rows]


async def get_session(session_id: str) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow(
        """SELECT id, name, tab_url, request_count, analysis_status,
                  analysis_text, analysis_model, analyzed_at,
                  created_at, updated_at
           FROM api_capture_sessions
           WHERE id = $1""",
        session_id,
    )
    return _row_to_dict(row) if row else None


async def get_requests(
    session_id: str,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    pool = await _get_pool()
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM api_capture_requests WHERE session_id = $1",
        session_id,
    )
    rows = await pool.fetch(
        """SELECT id, session_id, request_id, resource_type, timestamp,
                  method, url, request_headers, request_body,
                  response_status, response_status_text,
                  response_headers, response_mime_type, response_body,
                  created_at
           FROM api_capture_requests
           WHERE session_id = $1
           ORDER BY id
           LIMIT $2 OFFSET $3""",
        session_id, limit, offset,
    )
    return total, [_row_to_dict(r) for r in rows]


async def get_all_requests(session_id: str) -> list[dict]:
    """加载 session 全部请求用于 LLM 分析。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT request_id, resource_type, timestamp,
                  method, url, request_headers, request_body,
                  response_status, response_status_text,
                  response_headers, response_mime_type, response_body
           FROM api_capture_requests
           WHERE session_id = $1
           ORDER BY id""",
        session_id,
    )
    return [_row_to_dict(r) for r in rows]


async def delete_session(session_id: str) -> bool:
    pool = await _get_pool()
    result = await pool.execute(
        "DELETE FROM api_capture_sessions WHERE id = $1",
        session_id,
    )
    return result == "DELETE 1"


async def update_analysis(
    session_id: str,
    status: str,
    text: str | None = None,
    model: str | None = None,
) -> None:
    pool = await _get_pool()
    if status == "done":
        await pool.execute(
            """UPDATE api_capture_sessions
               SET analysis_status = $2, analysis_text = $3,
                   analysis_model = $4, analyzed_at = NOW(), updated_at = NOW()
               WHERE id = $1""",
            session_id, status, text, model,
        )
    else:
        await pool.execute(
            """UPDATE api_capture_sessions
               SET analysis_status = $2, updated_at = NOW()
               WHERE id = $1""",
            session_id, status,
        )


async def session_count() -> int:
    pool = await _get_pool()
    return await pool.fetchval("SELECT COUNT(*) FROM api_capture_sessions") or 0


# ── helpers ──────────────────────────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict:
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d
