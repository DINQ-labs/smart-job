"""asyncpg 连接池 + identity schema + 业务 CRUD.

设计原则:
  身份(app_users)与凭证(auth_identities)分离
    app_users         — 真账户,一个真实的人 = 一行
    auth_identities   — 该人能用的登录方式(邮箱密码 / Google / 微信 / 手机 OTP / Passkey)
                        一个 user 可挂多条(同一个人邮箱 + Google + 微信 都行)
    refresh_tokens    — JWT 长 token,挂 user(任何登录方式发出的)
    email_verifications / phone_verifications — OTP
    auth_events       — 审计日志(登录 / 解绑 / 改密 等)

DEV: 启动时 IF NOT EXISTS,不破坏既有数据;
     如果你要彻底重建,把 schema drop 掉再重启:
       sudo -u postgres psql -d smart_job -c 'DROP SCHEMA identity CASCADE;'
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import asyncpg

import config

log = logging.getLogger("job-portal-api.db")

_pool: Optional[asyncpg.Pool] = None


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────────────
# pool lifecycle
# ──────────────────────────────────────────────────────────────────────────

async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(
        dsn=config.DATABASE_URL,
        min_size=config.DB_POOL_MIN,
        max_size=config.DB_POOL_MAX,
    )
    assert _pool is not None
    await _ensure_schema(_pool)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; did you forget lifespan startup?")
    return _pool


def _tbl(name: str) -> str:
    return f'"{config.DB_SCHEMA}".{name}'


# ──────────────────────────────────────────────────────────────────────────
# schema
# ──────────────────────────────────────────────────────────────────────────

async def _ensure_schema(pool: asyncpg.Pool) -> None:
    """全部 IF NOT EXISTS,幂等."""
    schema = config.DB_SCHEMA
    async with pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

        # ── app_users:真账户(单一身份)─────────────────────────
        # email/phone 都可空(微信注册无邮箱,Google 注册无手机)
        await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".app_users (
            id                TEXT        PRIMARY KEY,
            email             TEXT,
            email_verified_at TIMESTAMPTZ,
            phone             TEXT,
            phone_verified_at TIMESTAMPTZ,
            password_hash     TEXT,
            name              TEXT,
            image             TEXT,
            locale            TEXT        NOT NULL DEFAULT 'zh-CN',
            role              TEXT        NOT NULL DEFAULT 'user'
                              CHECK (role IN ('user','staff','admin')),
            disabled_at       TIMESTAMPTZ,
            deleted_at        TIMESTAMPTZ,
            last_login_at     TIMESTAMPTZ,
            metadata          JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
        # 仅在 email/phone 非空 + 未删除时唯一(允许多个微信注册账户 email=NULL)
        await conn.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS uq_app_users_email '
            f'ON "{schema}".app_users (LOWER(email)) '
            f'WHERE email IS NOT NULL AND deleted_at IS NULL'
        )
        await conn.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS uq_app_users_phone '
            f'ON "{schema}".app_users (phone) '
            f'WHERE phone IS NOT NULL AND deleted_at IS NULL'
        )

        # ── auth_identities:多种登录方式 ─────────────────────────
        # 老 oauth_accounts 已弃用,DEV 期直接干掉它
        await conn.execute(f'DROP TABLE IF EXISTS "{schema}".oauth_accounts')

        await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".auth_identities (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL
                            REFERENCES "{schema}".app_users(id) ON DELETE CASCADE,
            kind            TEXT NOT NULL
                            CHECK (kind IN (
                              'email_password','phone_otp','oauth','webauthn','magic_link'
                            )),
            provider        TEXT NOT NULL,    -- email/phone/google/wechat/github/passkey ...
            subject         TEXT NOT NULL,    -- 该 provider 下的稳定 ID
            union_id        TEXT,             -- 微信 unionid(跨产品锚点)
            email_at_link   TEXT,             -- 链接时 provider 报的 email/name/image
            name_at_link    TEXT,
            image_at_link   TEXT,
            refresh_token   TEXT,             -- TODO 应用层加密;默认 NULL
            scope           TEXT,
            expires_at      TIMESTAMPTZ,
            linked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at    TIMESTAMPTZ,
            UNIQUE (provider, subject)
        )
        """)
        await conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ai_user '
            f'ON "{schema}".auth_identities (user_id)'
        )
        await conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ai_union '
            f'ON "{schema}".auth_identities (union_id) WHERE union_id IS NOT NULL'
        )

        # ── email_verifications:OTP(沿用)──────────────────────
        await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".email_verifications (
            id           TEXT        PRIMARY KEY,
            email        TEXT        NOT NULL,
            user_id      TEXT        REFERENCES "{schema}".app_users(id) ON DELETE CASCADE,
            code_hash    TEXT        NOT NULL,
            purpose      TEXT        NOT NULL,
            attempts     INTEGER     NOT NULL DEFAULT 0,
            max_attempts INTEGER     NOT NULL DEFAULT 5,
            expires_at   TIMESTAMPTZ NOT NULL,
            consumed_at  TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
        await conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ev_email_purpose '
            f'ON "{schema}".email_verifications(email, purpose, created_at DESC)'
        )
        await conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ev_expires '
            f'ON "{schema}".email_verifications(expires_at)'
        )

        # ── phone_verifications:同构,给短信 OTP ────────────────
        await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".phone_verifications (
            id           TEXT        PRIMARY KEY,
            phone        TEXT        NOT NULL,
            user_id      TEXT        REFERENCES "{schema}".app_users(id) ON DELETE CASCADE,
            code_hash    TEXT        NOT NULL,
            purpose      TEXT        NOT NULL,
            attempts     INTEGER     NOT NULL DEFAULT 0,
            max_attempts INTEGER     NOT NULL DEFAULT 5,
            expires_at   TIMESTAMPTZ NOT NULL,
            consumed_at  TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
        await conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_pv_phone_purpose '
            f'ON "{schema}".phone_verifications(phone, purpose, created_at DESC)'
        )

        # ── refresh_tokens(沿用 + 加 device 字段)───────────────
        await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".refresh_tokens (
            id            TEXT        PRIMARY KEY,
            user_id       TEXT        NOT NULL
                          REFERENCES "{schema}".app_users(id) ON DELETE CASCADE,
            token_hash    TEXT        NOT NULL UNIQUE,
            expires_at    TIMESTAMPTZ NOT NULL,
            revoked_at    TIMESTAMPTZ,
            replaced_by   TEXT,
            user_agent    TEXT,
            ip_hash       TEXT,
            client        TEXT,
            device_id     TEXT,
            device_name   TEXT,
            last_seen_at  TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
        await conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_rt_user '
            f'ON "{schema}".refresh_tokens(user_id)'
        )
        # 老库可能没新列,加 ALTER 兼容
        for col, ddl in [
            ("device_id",    "TEXT"),
            ("device_name",  "TEXT"),
            ("last_seen_at", "TIMESTAMPTZ"),
        ]:
            await conn.execute(
                f'ALTER TABLE "{schema}".refresh_tokens '
                f'ADD COLUMN IF NOT EXISTS {col} {ddl}'
            )

        # ── auth_events:审计 ────────────────────────────────────
        await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{schema}".auth_events (
            id          BIGSERIAL  PRIMARY KEY,
            user_id     TEXT       REFERENCES "{schema}".app_users(id) ON DELETE SET NULL,
            event_type  TEXT       NOT NULL,
            provider    TEXT,
            ip_hash     TEXT,
            user_agent  TEXT,
            metadata    JSONB      NOT NULL DEFAULT '{{}}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
        await conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ae_user '
            f'ON "{schema}".auth_events (user_id, created_at DESC)'
        )

        log.info("schema %s ensured", schema)


# ──────────────────────────────────────────────────────────────────────────
# users
# ──────────────────────────────────────────────────────────────────────────

async def get_user_by_email(email: str) -> Optional[asyncpg.Record]:
    sql = (
        f"SELECT * FROM {_tbl('app_users')} "
        f"WHERE LOWER(email) = $1 AND deleted_at IS NULL"
    )
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, email.lower())


async def get_user_by_phone(phone: str) -> Optional[asyncpg.Record]:
    sql = f"SELECT * FROM {_tbl('app_users')} WHERE phone = $1 AND deleted_at IS NULL"
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, phone)


async def get_user_by_id(user_id: str) -> Optional[asyncpg.Record]:
    sql = f"SELECT * FROM {_tbl('app_users')} WHERE id = $1 AND deleted_at IS NULL"
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, user_id)


async def create_user(
    *,
    email: str | None = None,
    phone: str | None = None,
    password_hash: str | None = None,
    name: str | None = None,
    image: str | None = None,
    locale: str = "zh-CN",
    role: str = "user",
) -> asyncpg.Record:
    sql = f"""
    INSERT INTO {_tbl('app_users')}
        (id, email, phone, password_hash, name, image, locale, role)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    RETURNING *
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(
            sql,
            new_id(),
            email.lower() if email else None,
            phone,
            password_hash,
            name,
            image,
            locale,
            role,
        )


async def seed_default_user() -> None:
    """开发态播种固定默认账号:扩展免注册登录 + Claude Code 直连 MCP 用同一身份。

    - id 固定为 config.DEFAULT_USER_ID(非随机 UUID),好让 job-api-gateway 的
      X-User-Id 头与扩展 WS 的 ?user_id= 用同一个已知常量对上。
    - email_verified_at 直接置位,跳过 OTP,可立即 /auth/login。
    - 幂等:已存在则跳过(不覆盖,改密后重启不会被打回默认)。
    - 弱口令,严禁生产播种 —— 由 config.SEED_DEFAULT_USER 控制(生产默认关)。
    """
    from password import hash_password  # 局部 import,避开启动期模块环

    uid = config.DEFAULT_USER_ID
    email = config.DEFAULT_USER_EMAIL.lower()

    async with get_pool().acquire() as conn:
        if await conn.fetchrow(f"SELECT 1 FROM {_tbl('app_users')} WHERE id = $1", uid):
            log.info("默认账号已存在,跳过播种 (id=%s)", uid)
            return
        pwd_hash = hash_password(config.DEFAULT_USER_PASSWORD)
        async with conn.transaction():
            await conn.execute(
                f"""INSERT INTO {_tbl('app_users')}
                        (id, email, email_verified_at, password_hash, name, role)
                    VALUES ($1, $2, NOW(), $3, $4, 'user')""",
                uid, email, pwd_hash, "SmartJob 默认账号",
            )
            await conn.execute(
                f"""INSERT INTO {_tbl('auth_identities')}
                        (id, user_id, kind, provider, subject)
                    VALUES ($1, $2, 'email_password', 'email', $3)
                    ON CONFLICT (provider, subject) DO NOTHING""",
                new_id(), uid, email,
            )
    log.info("默认账号已播种: id=%s email=%s password=%s",
             uid, email, config.DEFAULT_USER_PASSWORD)


async def update_user_password(user_id: str, password_hash: str) -> None:
    sql = (
        f"UPDATE {_tbl('app_users')} "
        f"SET password_hash = $2, updated_at = NOW() WHERE id = $1"
    )
    async with get_pool().acquire() as conn:
        await conn.execute(sql, user_id, password_hash)


async def mark_email_verified(user_id: str) -> asyncpg.Record:
    sql = f"""
    UPDATE {_tbl('app_users')}
    SET email_verified_at = NOW(), updated_at = NOW()
    WHERE id = $1
    RETURNING *
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, user_id)


async def touch_user_login(user_id: str) -> None:
    sql = (
        f"UPDATE {_tbl('app_users')} "
        f"SET last_login_at = NOW(), updated_at = NOW() WHERE id = $1"
    )
    async with get_pool().acquire() as conn:
        await conn.execute(sql, user_id)


# ── admin 端列表 / 详情 ────────────────────────────────────────────────────

async def list_users(
    *,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    role: str | None = None,
) -> tuple[list[asyncpg.Record], int]:
    """admin /admin/users 用.返 (rows, total).
    q 在 email / phone / name 上 ILIKE,支持简单搜索;role 精确过滤."""
    where = ["deleted_at IS NULL"]
    params: list[Any] = []
    if q:
        params.append(f"%{q}%")
        where.append(
            f"(email ILIKE ${len(params)} OR phone ILIKE ${len(params)} "
            f"OR name ILIKE ${len(params)})"
        )
    if role:
        params.append(role)
        where.append(f"role = ${len(params)}")

    where_sql = " AND ".join(where)
    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM {_tbl('app_users')} WHERE {where_sql}",
            *params,
        )
        params.extend([limit, offset])
        rows = await conn.fetch(
            f"""
            SELECT u.*,
                   (SELECT count(*) FROM {_tbl('auth_identities')} ai
                     WHERE ai.user_id = u.id) AS identities_count
            FROM {_tbl('app_users')} u
            WHERE {where_sql}
            ORDER BY u.created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
    return list(rows), int(total or 0)


async def set_user_role(user_id: str, role: str) -> None:
    assert role in ("user", "staff", "admin"), "invalid role"
    sql = (
        f"UPDATE {_tbl('app_users')} "
        f"SET role = $2, updated_at = NOW() WHERE id = $1"
    )
    async with get_pool().acquire() as conn:
        await conn.execute(sql, user_id, role)


async def set_user_disabled(user_id: str, disabled: bool) -> None:
    sql = (
        f"UPDATE {_tbl('app_users')} "
        f"SET disabled_at = {'NOW()' if disabled else 'NULL'}, "
        f"    updated_at = NOW() "
        f"WHERE id = $1"
    )
    async with get_pool().acquire() as conn:
        await conn.execute(sql, user_id)


async def soft_delete_user(user_id: str) -> None:
    sql = (
        f"UPDATE {_tbl('app_users')} "
        f"SET deleted_at = NOW(), updated_at = NOW() WHERE id = $1"
    )
    async with get_pool().acquire() as conn:
        await conn.execute(sql, user_id)


async def update_user_profile(
    user_id: str, **updates: str | None
) -> Optional[asyncpg.Record]:
    """更新用户资料字段（name, image, locale）。返回更新后的行。"""
    if not updates:
        return None

    set_clauses = []
    params: list[Any] = []
    for key, value in updates.items():
        if value is not None:
            params.append(value)
            set_clauses.append(f"{key} = ${len(params)}")

    if not set_clauses:
        return None

    params.append(user_id)
    sql = f"""
    UPDATE {_tbl('app_users')}
    SET {', '.join(set_clauses)}, updated_at = NOW()
    WHERE id = ${len(params)} AND deleted_at IS NULL
    RETURNING *
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, *params)


# ──────────────────────────────────────────────────────────────────────────
# auth_identities
# ──────────────────────────────────────────────────────────────────────────

async def link_identity(
    *,
    user_id: str,
    kind: str,
    provider: str,
    subject: str,
    union_id: str | None = None,
    email_at_link: str | None = None,
    name_at_link: str | None = None,
    image_at_link: str | None = None,
    refresh_token: str | None = None,
    scope: str | None = None,
    expires_at: datetime | None = None,
) -> asyncpg.Record:
    """挂一条登录身份;若 (provider, subject) 冲突,返回已有行(idempotent)."""
    sql = f"""
    INSERT INTO {_tbl('auth_identities')}
        (id, user_id, kind, provider, subject, union_id,
         email_at_link, name_at_link, image_at_link,
         refresh_token, scope, expires_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
    ON CONFLICT (provider, subject) DO UPDATE
        SET last_used_at  = NOW(),
            email_at_link = EXCLUDED.email_at_link,
            name_at_link  = EXCLUDED.name_at_link,
            image_at_link = EXCLUDED.image_at_link
    RETURNING *
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(
            sql,
            new_id(), user_id, kind, provider, subject, union_id,
            email_at_link, name_at_link, image_at_link,
            refresh_token, scope, expires_at,
        )


async def find_identity(provider: str, subject: str) -> Optional[asyncpg.Record]:
    sql = (
        f"SELECT * FROM {_tbl('auth_identities')} "
        f"WHERE provider = $1 AND subject = $2"
    )
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, provider, subject)


async def find_identity_by_union(union_id: str) -> Optional[asyncpg.Record]:
    """微信用户跨产品锚点查找;若用户曾在 mp/web/h5 任一处登过会有 union_id."""
    sql = (
        f"SELECT * FROM {_tbl('auth_identities')} "
        f"WHERE union_id = $1 ORDER BY linked_at ASC LIMIT 1"
    )
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, union_id)


async def list_user_identities(user_id: str) -> list[asyncpg.Record]:
    sql = (
        f"SELECT * FROM {_tbl('auth_identities')} "
        f"WHERE user_id = $1 ORDER BY linked_at ASC"
    )
    async with get_pool().acquire() as conn:
        return list(await conn.fetch(sql, user_id))


async def unlink_identity(identity_id: str, user_id: str) -> bool:
    """解绑.要求 user_id 匹配防越权;若是该用户唯一登录方式拒绝(防自锁)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        cnt = await conn.fetchval(
            f"SELECT count(*) FROM {_tbl('auth_identities')} WHERE user_id = $1",
            user_id,
        )
        if (cnt or 0) <= 1:
            return False
        await conn.execute(
            f"DELETE FROM {_tbl('auth_identities')} WHERE id = $1 AND user_id = $2",
            identity_id, user_id,
        )
        return True


async def touch_identity_used(identity_id: str) -> None:
    sql = (
        f"UPDATE {_tbl('auth_identities')} "
        f"SET last_used_at = NOW() WHERE id = $1"
    )
    async with get_pool().acquire() as conn:
        await conn.execute(sql, identity_id)


# ──────────────────────────────────────────────────────────────────────────
# email_verifications(沿用)
# ──────────────────────────────────────────────────────────────────────────

async def create_verification(
    *,
    email: str,
    user_id: str | None,
    code_hash: str,
    purpose: str,
    expires_at: datetime,
    max_attempts: int,
) -> asyncpg.Record:
    sql = f"""
    INSERT INTO {_tbl('email_verifications')}
        (id, email, user_id, code_hash, purpose, max_attempts, expires_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    RETURNING *
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(
            sql, new_id(), email.lower(), user_id, code_hash, purpose,
            max_attempts, expires_at,
        )


async def get_verification(verification_id: str) -> Optional[asyncpg.Record]:
    sql = f"SELECT * FROM {_tbl('email_verifications')} WHERE id = $1"
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, verification_id)


async def increment_verification_attempts(verification_id: str) -> asyncpg.Record:
    sql = f"""
    UPDATE {_tbl('email_verifications')}
    SET attempts = attempts + 1
    WHERE id = $1
    RETURNING attempts, max_attempts
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, verification_id)


async def consume_verification(verification_id: str) -> None:
    sql = f"UPDATE {_tbl('email_verifications')} SET consumed_at = NOW() WHERE id = $1"
    async with get_pool().acquire() as conn:
        await conn.execute(sql, verification_id)


async def latest_verification(email: str, purpose: str) -> Optional[asyncpg.Record]:
    sql = f"""
    SELECT * FROM {_tbl('email_verifications')}
    WHERE email = $1 AND purpose = $2
    ORDER BY created_at DESC
    LIMIT 1
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, email.lower(), purpose)


# ──────────────────────────────────────────────────────────────────────────
# refresh_tokens(沿用)
# ──────────────────────────────────────────────────────────────────────────

async def insert_refresh_token(
    *,
    user_id: str,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None,
    ip_hash: str | None,
    client: str | None,
    device_id: str | None = None,
    device_name: str | None = None,
) -> asyncpg.Record:
    sql = f"""
    INSERT INTO {_tbl('refresh_tokens')}
        (id, user_id, token_hash, expires_at,
         user_agent, ip_hash, client, device_id, device_name, last_seen_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
    RETURNING *
    """
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(
            sql, new_id(), user_id, token_hash, expires_at,
            user_agent, ip_hash, client, device_id, device_name,
        )


async def find_refresh_token(token_hash: str) -> Optional[asyncpg.Record]:
    sql = f"SELECT * FROM {_tbl('refresh_tokens')} WHERE token_hash = $1"
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(sql, token_hash)


async def revoke_refresh_token(token_id: str, replaced_by: str | None = None) -> None:
    sql = f"""
    UPDATE {_tbl('refresh_tokens')}
    SET revoked_at = NOW(), replaced_by = COALESCE($2, replaced_by)
    WHERE id = $1 AND revoked_at IS NULL
    """
    async with get_pool().acquire() as conn:
        await conn.execute(sql, token_id, replaced_by)


async def revoke_all_user_tokens(user_id: str) -> None:
    sql = f"""
    UPDATE {_tbl('refresh_tokens')}
    SET revoked_at = NOW()
    WHERE user_id = $1 AND revoked_at IS NULL
    """
    async with get_pool().acquire() as conn:
        await conn.execute(sql, user_id)


# ──────────────────────────────────────────────────────────────────────────
# auth_events:审计日志
# ──────────────────────────────────────────────────────────────────────────

async def log_auth_event(
    *,
    user_id: str | None,
    event_type: str,
    provider: str | None = None,
    ip_hash: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """非阻塞最佳努力:失败只 warn,不抛."""
    import json
    sql = f"""
    INSERT INTO {_tbl('auth_events')}
        (user_id, event_type, provider, ip_hash, user_agent, metadata)
    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
    """
    try:
        async with get_pool().acquire() as conn:
            await conn.execute(
                sql, user_id, event_type, provider, ip_hash, user_agent,
                json.dumps(metadata or {}),
            )
    except Exception as e:
        log.warning("log_auth_event failed event=%s err=%s", event_type, e)


async def list_user_events(user_id: str, limit: int = 50) -> list[asyncpg.Record]:
    sql = f"""
    SELECT * FROM {_tbl('auth_events')}
    WHERE user_id = $1
    ORDER BY created_at DESC
    LIMIT $2
    """
    async with get_pool().acquire() as conn:
        return list(await conn.fetch(sql, user_id, limit))


# ──────────────────────────────────────────────────────────────────────────
# 序列化 helpers
# ──────────────────────────────────────────────────────────────────────────

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def user_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":                row["id"],
        "email":             row["email"],
        "email_verified_at": _iso(row["email_verified_at"]),
        "phone":             row["phone"],
        "phone_verified_at": _iso(row["phone_verified_at"]),
        "name":              row["name"],
        "image":             row["image"],
        "locale":            row["locale"],
        "role":              row["role"],
        "disabled_at":       _iso(row["disabled_at"]),
        "last_login_at":     _iso(row["last_login_at"]),
        "metadata":          row["metadata"],
        "created_at":        _iso(row["created_at"]),
        "updated_at":        _iso(row["updated_at"]),
    }


def identity_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":            row["id"],
        "user_id":       row["user_id"],
        "kind":          row["kind"],
        "provider":      row["provider"],
        "subject":       row["subject"],
        "union_id":      row["union_id"],
        "email_at_link": row["email_at_link"],
        "name_at_link":  row["name_at_link"],
        "image_at_link": row["image_at_link"],
        "linked_at":     _iso(row["linked_at"]),
        "last_used_at":  _iso(row["last_used_at"]),
    }


def event_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":         row["id"],
        "user_id":    row["user_id"],
        "event_type": row["event_type"],
        "provider":   row["provider"],
        "metadata":   row["metadata"],
        "created_at": _iso(row["created_at"]),
    }
