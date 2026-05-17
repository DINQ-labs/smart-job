"""recruiter_preferences + message_templates DB 操作 (Phase 1.1 MVP 3.5)。

跟 preferences_db / resume_db 共享 _get_pool() (db.py)。Schema 在 init 时
通过 db.init_db() 的 ALTER TABLE 兼容旧库 — 但因为是新表,直接 CREATE。
"""
from __future__ import annotations

from typing import Any

import db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recruiter_preferences (
    user_id          TEXT NOT NULL,
    platform         TEXT NOT NULL,
    target_role      TEXT,
    must_skills      TEXT[],
    nice_skills      TEXT[],
    exp_min          INTEGER,
    exp_max          INTEGER,
    degree           TEXT,
    background_pref  TEXT,
    exclude_pref     TEXT,
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, platform)
);

CREATE TABLE IF NOT EXISTS message_templates (
    user_id     TEXT NOT NULL,
    role_type   TEXT NOT NULL,
    platform    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    content     TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, role_type, platform, kind)
);
"""


async def init_recruiter_schema() -> None:
    """init_db() 启动时调一次,创建表。"""
    pool = await db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)


# ── 候选人画像 ────────────────────────────────────────────────────────────

async def get_preferences(user_id: str, platform: str) -> dict | None:
    pool = await db._get_pool()
    row = await pool.fetchrow(
        """SELECT user_id, platform, target_role,
                  must_skills, nice_skills, exp_min, exp_max,
                  degree, background_pref, exclude_pref, updated_at
             FROM recruiter_preferences
            WHERE user_id = $1 AND platform = $2""",
        user_id, platform,
    )
    if not row:
        return None
    d = dict(row)
    d["must_skills"] = list(d["must_skills"] or [])
    d["nice_skills"] = list(d["nice_skills"] or [])
    # asyncpg datetime → ISO string for JSON serialization
    if d.get("updated_at"):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


async def save_preferences(
    *,
    user_id: str,
    platform: str,
    target_role: str = "",
    must_skills: list[str] | None = None,
    nice_skills: list[str] | None = None,
    exp_min: int | None = None,
    exp_max: int | None = None,
    degree: str | None = None,
    background_pref: str = "",
    exclude_pref: str = "",
) -> None:
    pool = await db._get_pool()
    await pool.execute(
        """INSERT INTO recruiter_preferences
              (user_id, platform, target_role, must_skills, nice_skills,
               exp_min, exp_max, degree, background_pref, exclude_pref, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
           ON CONFLICT (user_id, platform) DO UPDATE SET
              target_role     = EXCLUDED.target_role,
              must_skills     = EXCLUDED.must_skills,
              nice_skills     = EXCLUDED.nice_skills,
              exp_min         = EXCLUDED.exp_min,
              exp_max         = EXCLUDED.exp_max,
              degree          = EXCLUDED.degree,
              background_pref = EXCLUDED.background_pref,
              exclude_pref    = EXCLUDED.exclude_pref,
              updated_at      = NOW()""",
        user_id, platform, target_role,
        must_skills or [], nice_skills or [],
        exp_min, exp_max, degree, background_pref, exclude_pref,
    )


# ── 招呼模板 ──────────────────────────────────────────────────────────────

async def get_templates(user_id: str, role_type: str, platform: str) -> list[dict]:
    pool = await db._get_pool()
    rows = await pool.fetch(
        """SELECT kind, content, updated_at
             FROM message_templates
            WHERE user_id = $1 AND role_type = $2 AND platform = $3""",
        user_id, role_type, platform,
    )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("updated_at"):
            d["updated_at"] = d["updated_at"].isoformat()
        out.append(d)
    return out


async def save_template(user_id: str, role_type: str, platform: str, kind: str, content: str) -> None:
    pool = await db._get_pool()
    await pool.execute(
        """INSERT INTO message_templates
              (user_id, role_type, platform, kind, content, updated_at)
           VALUES ($1, $2, $3, $4, $5, NOW())
           ON CONFLICT (user_id, role_type, platform, kind) DO UPDATE SET
              content    = EXCLUDED.content,
              updated_at = NOW()""",
        user_id, role_type, platform, kind, content,
    )
