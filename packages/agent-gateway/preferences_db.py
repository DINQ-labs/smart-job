"""用户职业偏好数据库操作。

schema 演进历史:
  v1: PK=(user_id)   —— Boss 专属,一行一用户
  v2: 加 language   —— 记录 chat 语言偏好
  v3 (本文件): PK=(user_id, platform) —— 三平台各一行,Boss/LinkedIn/Indeed 互不覆盖
"""
from __future__ import annotations

from db import _get_pool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id      TEXT        NOT NULL,
    platform     TEXT        NOT NULL DEFAULT 'boss',
    job_role     TEXT        NOT NULL DEFAULT '',
    city         TEXT        NOT NULL DEFAULT '',
    salary_range TEXT        NOT NULL DEFAULT '',
    notes        TEXT        NOT NULL DEFAULT '',
    language     TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, platform)
);
"""

_MIGRATE_LANGUAGE = """
ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT '';
"""

_MIGRATE_PLATFORM = """
ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'boss';
"""

# 把老的单列 PK (user_id) 换成复合 PK (user_id, platform)。
# 已经是复合 PK 的话此步是 no-op;旧 PK 存在时先 drop 再 add。
# 所有老数据的 platform 都是 'boss'(ALTER ADD COLUMN 时默认写入),不会冲突。
_MIGRATE_PK = """
DO $$
DECLARE
    pk_cols TEXT[];
BEGIN
    SELECT array_agg(a.attname ORDER BY array_position(c.conkey, a.attnum))
      INTO pk_cols
      FROM pg_constraint c
      JOIN pg_attribute  a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
     WHERE c.conrelid = 'user_preferences'::regclass AND c.contype = 'p';
    IF pk_cols IS NULL OR pk_cols = ARRAY['user_id']::TEXT[] THEN
        ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS user_preferences_pkey;
        ALTER TABLE user_preferences ADD  PRIMARY KEY (user_id, platform);
    END IF;
END $$;
"""


async def init_preferences_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)
        await conn.execute(_MIGRATE_LANGUAGE)
        await conn.execute(_MIGRATE_PLATFORM)
        await conn.execute(_MIGRATE_PK)


# ── Language (global across user's platforms) ────────────────────────────

async def get_user_language(user_id: str) -> str:
    """读 chat 语言偏好。跨平台取任意一行的值(设计上所有平台行共享同一 language)。"""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT language FROM user_preferences WHERE user_id = $1 AND language != '' LIMIT 1",
        user_id,
    )
    return row["language"] if row else ""


async def save_user_language(user_id: str, language: str) -> None:
    """语言是用户级全局设置;同步写到该用户所有已存在的平台行,并保底一条 boss 行。"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        # Update all existing rows for this user
        updated = await conn.execute(
            "UPDATE user_preferences SET language = $1, updated_at = NOW() WHERE user_id = $2",
            language, user_id,
        )
        # If no rows existed, create a Boss placeholder so the value persists
        if updated == "UPDATE 0":
            await conn.execute(
                """INSERT INTO user_preferences (user_id, platform, language)
                   VALUES ($1, 'boss', $2)
                   ON CONFLICT (user_id, platform) DO UPDATE SET
                       language   = EXCLUDED.language,
                       updated_at = NOW()""",
                user_id, language,
            )


# ── Per-platform job preferences ──────────────────────────────────────────

async def get_user_preferences(user_id: str, platform: str = "boss") -> dict | None:
    """按 (user_id, platform) 读取职业偏好。缺省 platform='boss' 保持旧调用兼容。"""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """SELECT user_id, platform, job_role, city, salary_range, notes
             FROM user_preferences
            WHERE user_id = $1 AND platform = $2""",
        user_id, platform,
    )
    return dict(row) if row else None


async def save_user_preferences(
    user_id: str,
    job_role: str = "",
    city: str = "",
    salary_range: str = "",
    notes: str = "",
    platform: str = "boss",
) -> None:
    """按 (user_id, platform) upsert 职业偏好。"""
    pool = await _get_pool()
    await pool.execute(
        """INSERT INTO user_preferences (user_id, platform, job_role, city, salary_range, notes)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (user_id, platform) DO UPDATE SET
               job_role     = EXCLUDED.job_role,
               city         = EXCLUDED.city,
               salary_range = EXCLUDED.salary_range,
               notes        = EXCLUDED.notes,
               updated_at   = NOW()""",
        user_id, platform, job_role, city, salary_range, notes,
    )
