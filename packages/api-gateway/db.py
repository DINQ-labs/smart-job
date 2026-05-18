"""
db.py: PostgreSQL 数据库封装。

配置（环境变量）:
  DB_POSTGRES_URL  postgresql://user:pass@host:port/dbname
                   （默认 postgresql://postgres:password@localhost:5432/boss_gateway）

说明:
  - 响应体不截断，完整存储。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

DB_POSTGRES_URL = os.environ.get(
    "DB_POSTGRES_URL",
    "postgresql://postgres:password@localhost:5432/boss_gateway",
)


def _utc_now() -> str:
    # 用 ...Z 后缀(对齐 JS toISOString),避免 since_iso 字符串比较时
    # `+00:00` (ASCII 43) 与 `Z` (ASCII 90) 在同秒边界上漏掉一条 row。
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond:06d}Z"


def _json_full(obj: Any) -> str | None:
    """序列化为 JSON，不截断。"""
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


# ── 抽象接口 ────────────────────────────────────────────────────────────────


class _DbBackend(ABC):
    @abstractmethod
    async def init(self) -> None: ...

    @abstractmethod
    async def get_max_display_id(self) -> int: ...

    @abstractmethod
    async def log_command_start(
        self,
        session_id: str,
        agent_id: str,
        request_id: str,
        tool_name: str,
        method: str,
        path: str,
        body: Any = None,
        user_tier: str = "",
        is_dynamic: bool = False,
    ) -> None: ...

    @abstractmethod
    async def log_command_end(
        self,
        request_id: str,
        ok: bool,
        response_body: Any = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> None: ...

    @abstractmethod
    async def log_session_event(
        self,
        session_id: str | None,
        agent_id: str | None,
        event_type: str,
        detail: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def upsert_session(self, session_id: str, **fields: Any) -> None: ...

    @abstractmethod
    async def upsert_agent_session(self, agent_id: str, **fields: Any) -> None: ...

    @abstractmethod
    async def list_sessions(self, active_only: bool = False) -> list[dict]: ...

    @abstractmethod
    async def list_agent_sessions(self, active_only: bool = False) -> list[dict]: ...

    @abstractmethod
    async def list_commands(
        self,
        session_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        response_ok: int | None = None,
        tool_name: str = "",
        since_iso: str = "",
        keyword: str = "",
    ) -> list[dict]: ...

    @abstractmethod
    async def list_command_tool_names(
        self,
        since_iso: str = "",
        response_ok: int | None = None,
    ) -> list[str]: ...

    @abstractmethod
    async def get_stats(self) -> dict: ...

    @abstractmethod
    async def list_command_registry(self, ext_name: str | None = None) -> list[dict]: ...

    @abstractmethod
    async def get_command_registry(self, ext_name: str, path: str) -> dict | None: ...

    @abstractmethod
    async def update_command_registry(self, id: int, **fields: Any) -> None: ...

    @abstractmethod
    async def seed_command_registry(self, commands: list[dict]) -> None: ...

    @abstractmethod
    async def upsert_browser_slot(self, browser_id: str, **fields: Any) -> None: ...

    @abstractmethod
    async def get_browser_slot(self, browser_id: str) -> dict | None: ...

    @abstractmethod
    async def get_browser_slot_by_app_user(self, app_user_id: str) -> dict | None: ...

    @abstractmethod
    async def upsert_user_job_interest(self, app_user_id: str, encrypt_job_id: str, **fields: Any) -> None: ...

    @abstractmethod
    async def list_user_job_interests(self, app_user_id: str, status: str = "", platform: str = "boss") -> list[dict]: ...

    @abstractmethod
    async def update_user_job_interest_status(self, app_user_id: str, encrypt_job_id: str, status: str) -> None: ...

    @abstractmethod
    async def upsert_recruiter_geek_interest(self, app_user_id: str, platform: str, encrypt_geek_id: str, encrypt_job_id: str, **fields: Any) -> None: ...

    @abstractmethod
    async def get_recruiter_geek_interests(self, app_user_id: str, encrypt_job_id: str | None = None, interested_only: bool = False, limit: int = 50, offset: int = 0) -> list[dict]: ...

    @abstractmethod
    async def list_browser_slots(self, platform: str | None = None, state: str | None = None) -> list[dict]: ...

    @abstractmethod
    async def find_idle_browser_slot(self, platform: str) -> dict | None: ...

    @abstractmethod
    async def count_browser_slots(self, platform: str) -> int: ...

    @abstractmethod
    async def delete_browser_slot(self, browser_id: str) -> None: ...

    @abstractmethod
    async def log_pool_assignment(self, browser_id: str, app_user_id: str, platform: str, event_type: str, session_id: str = "") -> None: ...

    @abstractmethod
    async def list_platform_config(self) -> list[dict]: ...

    @abstractmethod
    async def upsert_platform_config(self, platform: str, **fields: Any) -> None: ...

    @abstractmethod
    async def list_proxy_pool(self) -> list[dict]: ...

    @abstractmethod
    async def add_proxy_pool_entry(self, url: str) -> bool: ...

    @abstractmethod
    async def remove_proxy_pool_entry(self, url: str) -> bool: ...

    @abstractmethod
    async def save_account_cookies(
        self, browser_id: str, session_id: str, account_name: str, app_user_id: str,
        platform: str, cookies_json: str, expires_at: str = "", notes: str = ""
    ) -> str: ...

    @abstractmethod
    async def get_account_cookies(self, id: str) -> dict | None: ...

    @abstractmethod
    async def list_account_cookies(self, platform: str | None = None) -> list[dict]: ...

    @abstractmethod
    async def update_account_cookies(self, id: str, cookies_json: str, updated_at: str) -> None: ...

    @abstractmethod
    async def delete_account_cookies(self, id: str) -> None: ...

    @abstractmethod
    async def update_cookie_metadata(self, id: str, **fields: str) -> None: ...

    @abstractmethod
    async def log_execution_decision(
        self,
        session_id: str,
        agent_id: str,
        tool_name: str,
        path: str,
        action: str,
        reason: str,
        detail: str,
        extra_delay_ms: int,
    ) -> None: ...

    @abstractmethod
    async def list_execution_decisions(
        self,
        session_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]: ...

# ── PostgreSQL 后端 ──────────────────────────────────────────────────────────


def _pg_placeholders(n: int, start: int = 1) -> str:
    """生成 $1, $2, ... $n（从 start 开始）的占位符字符串，逗号分隔。"""
    return ", ".join(f"${i}" for i in range(start, start + n))


class _PostgresBackend(_DbBackend):
    def __init__(self, url: str) -> None:
        self._url = url
        self._pool: Any | None = None
        self._lock = asyncio.Lock()

    async def _get_pool(self) -> Any:
        import asyncpg  # type: ignore

        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self._url, min_size=2, max_size=10
                )
        return self._pool

    _SCHEMA_STMTS = [
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id      TEXT PRIMARY KEY,
            browser_id      TEXT NOT NULL DEFAULT '',
            connected_at    TEXT NOT NULL,
            disconnected_at TEXT,
            account_name    TEXT DEFAULT '',
            user_id         TEXT DEFAULT '',
            app_user_id     TEXT DEFAULT '',
            status          TEXT DEFAULT 'connected',
            ip_address      TEXT DEFAULT '',
            display_id      INTEGER DEFAULT 0,
            ext_name        TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_sessions (
            agent_id        TEXT PRIMARY KEY,
            connected_at    TEXT NOT NULL,
            disconnected_at TEXT,
            bound_session   TEXT,
            status          TEXT DEFAULT 'connected',
            request_count   INTEGER DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS command_log (
            id            BIGSERIAL PRIMARY KEY,
            session_id    TEXT NOT NULL,
            agent_id      TEXT,
            request_id    TEXT NOT NULL UNIQUE,
            tool_name     TEXT,
            method        TEXT NOT NULL,
            path          TEXT NOT NULL,
            request_body  TEXT,
            response_ok   INTEGER,
            response_body TEXT,
            started_at    TEXT NOT NULL,
            ended_at      TEXT,
            duration_ms   REAL,
            error         TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS session_events (
            id          BIGSERIAL PRIMARY KEY,
            session_id  TEXT,
            agent_id    TEXT,
            event_type  TEXT NOT NULL,
            detail      TEXT,
            occurred_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cmdlog_session ON command_log(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_cmdlog_started ON command_log(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_cmdlog_tool    ON command_log(tool_name)",
        "CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id)",
        """
        CREATE TABLE IF NOT EXISTS command_registry (
            id              BIGSERIAL PRIMARY KEY,
            ext_name        TEXT NOT NULL DEFAULT '',
            cmd_group       TEXT NOT NULL DEFAULT '',
            path            TEXT NOT NULL,
            label           TEXT NOT NULL DEFAULT '',
            method          TEXT NOT NULL DEFAULT 'POST',
            description     TEXT NOT NULL DEFAULT '',
            enabled         INTEGER NOT NULL DEFAULT 1,
            webhook_url     TEXT NOT NULL DEFAULT '',
            webhook_secret  TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            UNIQUE(ext_name, path)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS browser_slots (
            browser_id       TEXT PRIMARY KEY,
            platform         TEXT NOT NULL DEFAULT 'bosszp',
            slot_state       TEXT NOT NULL DEFAULT 'offline',
            app_user_id      TEXT DEFAULT '',
            assigned_at      TEXT DEFAULT '',
            idle_timeout_sec INTEGER DEFAULT 7200,
            last_seen_at     TEXT DEFAULT '',
            last_session_id  TEXT DEFAULT '',
            display_name     TEXT DEFAULT '',
            ip_address       TEXT DEFAULT '',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS platform_config (
            platform         TEXT PRIMARY KEY,
            max_slots        INTEGER NOT NULL DEFAULT 50,
            idle_timeout_sec INTEGER NOT NULL DEFAULT 7200,
            description      TEXT DEFAULT '',
            updated_at       TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pool_assignments (
            id               BIGSERIAL PRIMARY KEY,
            browser_id       TEXT NOT NULL,
            app_user_id      TEXT NOT NULL,
            platform         TEXT NOT NULL,
            event_type       TEXT NOT NULL,
            session_id       TEXT DEFAULT '',
            occurred_at      TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS proxy_pool_entries (
            url              TEXT PRIMARY KEY,
            added_at         TEXT NOT NULL,
            note             TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS account_cookies (
            id           TEXT PRIMARY KEY,
            browser_id   TEXT DEFAULT '',
            session_id   TEXT DEFAULT '',
            account_name TEXT DEFAULT '',
            user_id      TEXT DEFAULT '',
            platform     TEXT NOT NULL DEFAULT 'bosszp',
            cookies_json TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            expires_at   TEXT DEFAULT '',
            notes        TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cached_jobs (
            id               BIGSERIAL    PRIMARY KEY,
            platform         TEXT         NOT NULL DEFAULT 'boss',
            external_id      TEXT         NOT NULL,
            title            TEXT,
            company          TEXT,
            city             TEXT,
            city_code        TEXT,
            country          TEXT,
            salary           TEXT,
            job_type         TEXT,
            experience       TEXT,
            education        TEXT,
            tags             JSONB,
            skills           JSONB,
            hr_name          TEXT,
            hr_title         TEXT,
            encrypt_boss_id  TEXT,
            list_security_id TEXT,
            remote           BOOLEAN      DEFAULT FALSE,
            posted_at        TIMESTAMPTZ,
            expires_at       TIMESTAMPTZ,
            apply_url        TEXT,
            source_url       TEXT,
            industry         TEXT,
            company_size     TEXT,
            raw_list         JSONB,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            search_session_id TEXT,
            UNIQUE(platform, external_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cj_platform_city ON cached_jobs(platform, city_code, fetched_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cj_title ON cached_jobs USING gin (to_tsvector('simple', COALESCE(title,'')))",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS country      TEXT",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS remote       BOOLEAN DEFAULT FALSE",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS posted_at    TIMESTAMPTZ",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS expires_at   TIMESTAMPTZ",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS apply_url    TEXT",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS source_url   TEXT",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS industry     TEXT",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS company_size TEXT",
        "ALTER TABLE cached_jobs ADD COLUMN IF NOT EXISTS search_session_id TEXT",
        """
        CREATE TABLE IF NOT EXISTS cached_job_details (
            id                 BIGSERIAL    PRIMARY KEY,
            platform           TEXT         NOT NULL DEFAULT 'boss',
            external_id        TEXT         NOT NULL,
            description        TEXT,
            address            TEXT,
            longitude          DOUBLE PRECISION,
            latitude           DOUBLE PRECISION,
            detail_security_id TEXT,
            raw_detail         JSONB,
            fetched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(platform, external_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cached_searches (
            id          BIGSERIAL    PRIMARY KEY,
            platform    TEXT         NOT NULL DEFAULT 'boss',
            keyword     TEXT         NOT NULL,
            city_code   TEXT         NOT NULL DEFAULT '',
            page        INTEGER      NOT NULL DEFAULT 1,
            job_ids     JSONB,
            total_count INTEGER,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(platform, keyword, city_code, page)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cs_platform_kw ON cached_searches(platform, keyword, city_code, fetched_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS chat_records (
            id               BIGSERIAL    PRIMARY KEY,
            platform         TEXT         NOT NULL DEFAULT 'boss',
            external_id      TEXT         NOT NULL,
            title            TEXT,
            company          TEXT,
            salary           TEXT,
            city             TEXT,
            hr_name          TEXT,
            hr_title         TEXT,
            encrypt_boss_id  TEXT,
            chat_security_id TEXT,
            account_name     TEXT,
            user_id          TEXT,
            session_id       TEXT,
            agent_id         TEXT,
            status           TEXT         NOT NULL DEFAULT 'sent',
            raw_result       JSONB,
            greeted_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cr_greeted ON chat_records(platform, greeted_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cr_account ON chat_records(account_name, greeted_at DESC)",
        # ── 招聘方：候选人缓存 ──────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS cached_geeks (
            id                     BIGSERIAL    PRIMARY KEY,
            platform               TEXT         NOT NULL DEFAULT 'boss',
            encrypt_geek_id        TEXT         NOT NULL,
            uid                    TEXT         NOT NULL DEFAULT '',
            name                   TEXT,
            city                   TEXT,
            work_year              TEXT,
            salary                 TEXT,
            current_work           TEXT,
            school                 TEXT,
            degree_name            TEXT,
            active_desc            TEXT,
            apply_status           INTEGER      DEFAULT -1,
            friend_relation_status INTEGER      DEFAULT -1,
            source_job_id          TEXT         NOT NULL DEFAULT '',
            account_name           TEXT         NOT NULL DEFAULT '',
            raw_card               JSONB,
            fetched_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(platform, encrypt_geek_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cgeek_platform ON cached_geeks(platform, fetched_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cgeek_job ON cached_geeks(platform, source_job_id, fetched_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cgeek_account ON cached_geeks(account_name, fetched_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS cached_geek_details (
            id                 BIGSERIAL    PRIMARY KEY,
            platform           TEXT         NOT NULL DEFAULT 'boss',
            encrypt_geek_id    TEXT         NOT NULL,
            search_security_id TEXT         NOT NULL DEFAULT '',
            detail_security_id TEXT         NOT NULL DEFAULT '',
            encrypt_expect_id  TEXT         NOT NULL DEFAULT '',
            authority_id       TEXT         NOT NULL DEFAULT '',
            raw_detail         JSONB,
            fetched_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(platform, encrypt_geek_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS geek_search_results (
            id               BIGSERIAL    PRIMARY KEY,
            platform         TEXT         NOT NULL DEFAULT 'boss',
            encrypt_job_id   TEXT         NOT NULL,
            keywords         TEXT         NOT NULL DEFAULT '',
            city_code        TEXT         NOT NULL DEFAULT '',
            page             INTEGER      NOT NULL DEFAULT 1,
            geek_ids         JSONB,
            has_more         BOOLEAN      DEFAULT FALSE,
            total_count      INTEGER,
            fetched_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(platform, encrypt_job_id, keywords, city_code, page)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_gsr_job ON geek_search_results(platform, encrypt_job_id, fetched_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS boss_contact_records (
            id               BIGSERIAL    PRIMARY KEY,
            platform         TEXT         NOT NULL DEFAULT 'boss',
            encrypt_geek_id  TEXT         NOT NULL,
            encrypt_job_id   TEXT         NOT NULL DEFAULT '',
            uid              TEXT         NOT NULL DEFAULT '',
            geek_name        TEXT         NOT NULL DEFAULT '',
            account_name     TEXT         NOT NULL DEFAULT '',
            user_id          TEXT         NOT NULL DEFAULT '',
            session_id       TEXT         NOT NULL DEFAULT '',
            agent_id         TEXT         NOT NULL DEFAULT '',
            status           TEXT         NOT NULL DEFAULT 'entered',
            raw_result       JSONB,
            contacted_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_bcr_platform ON boss_contact_records(platform, contacted_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_bcr_account ON boss_contact_records(account_name, contacted_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_bcr_geek ON boss_contact_records(platform, encrypt_geek_id, contacted_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS recruiter_jobs (
            id               BIGSERIAL    PRIMARY KEY,
            platform         TEXT         NOT NULL DEFAULT 'boss',
            encrypt_job_id   TEXT         NOT NULL,
            account_name     TEXT         NOT NULL DEFAULT '',
            user_id          TEXT         NOT NULL DEFAULT '',
            job_name         TEXT         NOT NULL DEFAULT '',
            city             TEXT         NOT NULL DEFAULT '',
            city_code        TEXT         NOT NULL DEFAULT '',
            area_code        TEXT         NOT NULL DEFAULT '',
            salary_desc      TEXT         NOT NULL DEFAULT '',
            low_salary       INTEGER      NOT NULL DEFAULT 0,
            high_salary      INTEGER      NOT NULL DEFAULT 0,
            salary_month     INTEGER      NOT NULL DEFAULT 12,
            experience_name  TEXT         NOT NULL DEFAULT '',
            degree_name      TEXT         NOT NULL DEFAULT '',
            job_type_name    TEXT         NOT NULL DEFAULT '',
            job_status       INTEGER      NOT NULL DEFAULT 0,
            job_audit_status INTEGER      NOT NULL DEFAULT 0,
            view_count       INTEGER      NOT NULL DEFAULT 0,
            concat_count     INTEGER      NOT NULL DEFAULT 0,
            add_time         BIGINT       NOT NULL DEFAULT 0,
            update_time      BIGINT       NOT NULL DEFAULT 0,
            raw              JSONB,
            fetched_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(platform, encrypt_job_id, account_name)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_rjob_account ON recruiter_jobs(platform, account_name, fetched_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_rjob_city ON recruiter_jobs(platform, account_name, city_code)",
        # 按 app_user_id (user_id 列) 查的强隔离索引 —— 三平台共用。
        # Boss=userId / LinkedIn=memberId / Indeed=PPID sub（user_id 列是 TEXT，三种格式都能存）。
        "CREATE INDEX IF NOT EXISTS idx_rjob_user ON recruiter_jobs(platform, user_id, fetched_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS user_job_interests (
            id               BIGSERIAL    PRIMARY KEY,
            app_user_id      TEXT         NOT NULL DEFAULT '',
            platform         TEXT         NOT NULL DEFAULT 'boss',
            encrypt_job_id   TEXT         NOT NULL,
            job_name         TEXT         NOT NULL DEFAULT '',
            company_name     TEXT         NOT NULL DEFAULT '',
            salary_desc      TEXT         NOT NULL DEFAULT '',
            city             TEXT         NOT NULL DEFAULT '',
            list_security_id TEXT         NOT NULL DEFAULT '',
            lid              TEXT         NOT NULL DEFAULT '',
            status           TEXT         NOT NULL DEFAULT 'new',
            interested       BOOLEAN      NOT NULL DEFAULT FALSE,
            conversation_id  TEXT         NOT NULL DEFAULT '',
            match_score      INTEGER      DEFAULT NULL,
            notes            TEXT         DEFAULT '',
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(app_user_id, platform, encrypt_job_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_uji_app_user ON user_job_interests(app_user_id, status, updated_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS recruiter_geek_interests (
            id                  BIGSERIAL    PRIMARY KEY,
            app_user_id         TEXT         NOT NULL DEFAULT '',
            platform            TEXT         NOT NULL DEFAULT 'boss',
            encrypt_geek_id     TEXT         NOT NULL,
            encrypt_job_id      TEXT         NOT NULL DEFAULT '',
            geek_name           TEXT         NOT NULL DEFAULT '',
            salary              TEXT         NOT NULL DEFAULT '',
            city                TEXT         NOT NULL DEFAULT '',
            degree              TEXT         NOT NULL DEFAULT '',
            work_year           TEXT         NOT NULL DEFAULT '',
            search_security_id  TEXT         NOT NULL DEFAULT '',
            status              TEXT         NOT NULL DEFAULT 'new',
            interested          BOOLEAN      NOT NULL DEFAULT FALSE,
            match_score         INTEGER      DEFAULT NULL,
            notes               TEXT         DEFAULT '',
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(app_user_id, platform, encrypt_geek_id, encrypt_job_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_rgi_app_user ON recruiter_geek_interests(app_user_id, interested, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_rgi_job ON recruiter_geek_interests(app_user_id, encrypt_job_id)",
        # 审核补丁 #3：老部署的 recruiter_geek_interests 表可能缺少 4 列组合 UNIQUE
        # 约束（建表语句含 UNIQUE，但 CREATE TABLE IF NOT EXISTS 对老表无追加效果）。
        # ON CONFLICT (4 列) upsert 必须有匹配的唯一约束，否则 PG 会 error 或降级。
        # 该 DO block 幂等：存在即跳过；表里有重复行加约束会失败，打 NOTICE 不阻断启动。
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uk_rgi_comp'
              AND conrelid = 'recruiter_geek_interests'::regclass
          ) THEN
            BEGIN
              ALTER TABLE recruiter_geek_interests
                ADD CONSTRAINT uk_rgi_comp
                UNIQUE (app_user_id, platform, encrypt_geek_id, encrypt_job_id);
            EXCEPTION WHEN OTHERS THEN
              RAISE NOTICE 'uk_rgi_comp add failed (existing dup rows?): %', SQLERRM;
            END;
          END IF;
        END $$;
        """,
        """
        CREATE TABLE IF NOT EXISTS playwright_sessions (
            session_id       TEXT PRIMARY KEY,
            platform         TEXT NOT NULL DEFAULT 'linkedin',
            app_user_id      TEXT DEFAULT '',
            cookie_id        TEXT DEFAULT '',
            session_state    TEXT NOT NULL DEFAULT 'active',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_active_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_pw_sess_platform ON playwright_sessions(platform, session_state)",
        """
        CREATE TABLE IF NOT EXISTS execution_decisions (
            id             BIGSERIAL PRIMARY KEY,
            session_id     TEXT NOT NULL,
            agent_id       TEXT DEFAULT '',
            tool_name      TEXT DEFAULT '',
            path           TEXT NOT NULL,
            action         TEXT NOT NULL,
            reason         TEXT NOT NULL,
            detail         TEXT DEFAULT '',
            extra_delay_ms INTEGER DEFAULT 0,
            decided_at     TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_exdec_session ON execution_decisions(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_exdec_action  ON execution_decisions(action, decided_at)",
        # ── 求职者消息中心（聊天列表）缓存 ──
        # 镜像 boss_geek_filter_by_label / boss_get_friend_list 返回的 friendList[]
        # PK: (platform, account_name, encrypt_friend_id) 多账号天然隔离
        # label_set: 一个好友可同时出现在多个 tab（0=全部 / 1=新招呼 / 2=仅沟通 / 3=有交换 / 4=有面试 / 5=不感兴趣）
        """
        CREATE TABLE IF NOT EXISTS cached_chats (
            id                BIGSERIAL    PRIMARY KEY,
            platform          TEXT         NOT NULL DEFAULT 'boss',
            account_name      TEXT         NOT NULL DEFAULT '',
            encrypt_friend_id TEXT         NOT NULL,
            friend_id         TEXT         NOT NULL DEFAULT '',
            name              TEXT,
            brand_name        TEXT,
            job_name          TEXT,
            position_name     TEXT,
            boss_title        TEXT,
            job_city          TEXT,
            update_time       BIGINT,
            water_level       INTEGER,
            label_set         JSONB        NOT NULL DEFAULT '[]'::jsonb,
            chat_security_id  TEXT,
            raw_card          JSONB,
            fetched_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(platform, account_name, encrypt_friend_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cchat_account_update  ON cached_chats(account_name, update_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cchat_account_fetched ON cached_chats(account_name, fetched_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cchat_label           ON cached_chats USING gin (label_set)",
        # 后增字段：从旧接口 getGeekFriendList.json merge 进来的 token chain
        # （新接口 geekFilterByLabel 不返回 securityId/encryptJobId/lastMsg）
        "ALTER TABLE cached_chats ADD COLUMN IF NOT EXISTS encrypt_job_id TEXT",
        "ALTER TABLE cached_chats ADD COLUMN IF NOT EXISTS last_msg       TEXT",
        "ALTER TABLE cached_chats ADD COLUMN IF NOT EXISTS last_msg_ts    BIGINT",
        # ── 招聘方全局聊天列表缓存 ──
        # 镜像 boss/recruiter_chat_list（POST filterByLabel, encJobId=""）的 zpData.result[]。
        # 字段比 cached_chats 稀疏（招聘方 API 只给 friendId/encryptFriendId/updateTime/waterLevel +
        # 偶有 name），name 留 NULL 让后续 boss_view_geek_detail / boss_geek_info 懒补。
        # labelId 语义：0=全部 / 1=默认 tab / 2-11=用户自定义（与求职者侧 0-5 不通用）。
        """
        CREATE TABLE IF NOT EXISTS cached_recruiter_chats (
            id                BIGSERIAL    PRIMARY KEY,
            platform          TEXT         NOT NULL DEFAULT 'boss',
            account_name      TEXT         NOT NULL DEFAULT '',
            encrypt_friend_id TEXT         NOT NULL,
            friend_id         TEXT         NOT NULL DEFAULT '',
            friend_source     INTEGER,
            name              TEXT,
            update_time       BIGINT,
            water_level       INTEGER,
            label_set         JSONB        NOT NULL DEFAULT '[]'::jsonb,
            raw_card          JSONB,
            fetched_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(platform, account_name, encrypt_friend_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_rcchat_account_update  ON cached_recruiter_chats(account_name, update_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_rcchat_account_fetched ON cached_recruiter_chats(account_name, fetched_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_rcchat_label           ON cached_recruiter_chats USING gin (label_set)",
        # ── 云端动态命令配置历史（Phase 1a） ──
        # gateway 把 admin 推送的 dynamic_commands / chains 落库 + 历史回滚 +
        # 新扩展连上时按版本对齐。append-only：每次 push 一行新版本。
        """
        CREATE TABLE IF NOT EXISTS ext_dynamic_config (
            version            TEXT         PRIMARY KEY,
            chains             JSONB        NOT NULL DEFAULT '{}'::jsonb,
            dynamic_commands   JSONB        NOT NULL DEFAULT '[]'::jsonb,
            notes              TEXT         NOT NULL DEFAULT '',
            source_ref         TEXT         NOT NULL DEFAULT '',
            created_by         TEXT         NOT NULL DEFAULT '',
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_extdc_created ON ext_dynamic_config(created_at DESC)",
        # ── 收藏扩展字段（PRD §收藏模块 / Sprint A1-A6） ──
        # tags: 自定义标签数组（求职：意向强/已投递/已拒绝；招聘：优先/待面试/已拒绝 + 用户自定义）
        # source: manual / ai_recommended / task_result —— 收藏来源
        # jd_excerpt / experience_excerpt: AI 分析所用摘录（避免每次 reanalyze 都回查原页面）
        "ALTER TABLE user_job_interests       ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE user_job_interests       ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'",
        "ALTER TABLE user_job_interests       ADD COLUMN IF NOT EXISTS jd_excerpt TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE recruiter_geek_interests ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE recruiter_geek_interests ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'",
        "ALTER TABLE recruiter_geek_interests ADD COLUMN IF NOT EXISTS experience_excerpt TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_uji_app_user_created ON user_job_interests(app_user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_rgi_app_user_created ON recruiter_geek_interests(app_user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_uji_tags ON user_job_interests USING gin (tags)",
        "CREATE INDEX IF NOT EXISTS idx_rgi_tags ON recruiter_geek_interests USING gin (tags)",
        # ── Billing：订阅 / Credit / Stripe events（PRD §订阅+Credit / Sprint B1） ──
        # 单一 user_subscriptions 表：一个 app_user_id 对应一条订阅，状态机由 webhook 维护
        """
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            app_user_id            TEXT         PRIMARY KEY,
            stripe_customer_id     TEXT         NOT NULL DEFAULT '',
            stripe_subscription_id TEXT         NOT NULL DEFAULT '',
            plan                   TEXT         NOT NULL DEFAULT 'free',
            status                 TEXT         NOT NULL DEFAULT 'active',
            current_period_end     TIMESTAMPTZ  DEFAULT NULL,
            cancel_at_period_end   BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_sub_customer ON user_subscriptions(stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_sub_plan_status ON user_subscriptions(plan, status)",
        # user_credits：当前余额 + 配额信息；扣费/入账走原子 UPDATE + credit_ledger
        # balance = 套餐当期余量 + topup_balance（永不过期）
        # last_daily_reset / last_monthly_reset 控制重置幂等，避免重复 grant
        """
        CREATE TABLE IF NOT EXISTS user_credits (
            app_user_id          TEXT         PRIMARY KEY,
            balance              INTEGER      NOT NULL DEFAULT 0,
            topup_balance        INTEGER      NOT NULL DEFAULT 0,
            monthly_grant        INTEGER      NOT NULL DEFAULT 0,
            daily_grant          INTEGER      NOT NULL DEFAULT 20,
            last_daily_reset     DATE         NOT NULL DEFAULT CURRENT_DATE,
            last_monthly_reset   DATE         DEFAULT NULL,
            lifetime_purchased   INTEGER      NOT NULL DEFAULT 0,
            lifetime_consumed    INTEGER      NOT NULL DEFAULT 0,
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """,
        # credit_ledger：所有 credit 变动的 append-only 账本，用于 audit + 调账 + 退款追溯
        # 永远先写 ledger 再更新 user_credits.balance，二者放在同一 transaction
        """
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id                BIGSERIAL    PRIMARY KEY,
            app_user_id       TEXT         NOT NULL,
            delta             INTEGER      NOT NULL,
            reason            TEXT         NOT NULL,
            sku               TEXT         NOT NULL DEFAULT '',
            balance_after     INTEGER      NOT NULL,
            stripe_event_id   TEXT         NOT NULL DEFAULT '',
            meta              JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cl_user_time ON credit_ledger(app_user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cl_reason ON credit_ledger(reason)",
        # stripe_events：webhook 幂等表。event_id 是 Stripe 全局唯一 ID，作为主键
        # 处理流程：先 INSERT（如 PK 冲突表示重放，直接 200 OK），再走业务逻辑
        """
        CREATE TABLE IF NOT EXISTS stripe_events (
            event_id      TEXT         PRIMARY KEY,
            event_type    TEXT         NOT NULL,
            payload       JSONB        NOT NULL,
            received_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            processed_at  TIMESTAMPTZ  DEFAULT NULL,
            process_error TEXT         NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_stripe_evt_type ON stripe_events(event_type, received_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id            SERIAL PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT '',
            last_login_at TEXT
        )
        """,
    ]

    async def init(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            for stmt in self._SCHEMA_STMTS:
                await conn.execute(stmt)
            # backward-compat migrations
            for col, defn in [
                ("ip_address",   "TEXT DEFAULT ''"),
                ("display_id",   "INTEGER DEFAULT 0"),
                ("ext_name",     "TEXT DEFAULT ''"),
                ("proxy_url",    "TEXT DEFAULT ''"),
                ("app_user_id",  "TEXT DEFAULT ''"),
            ]:
                await conn.execute(
                    f"ALTER TABLE sessions ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
            for col, defn in [
                ("cmd_group", "TEXT DEFAULT ''"),
            ]:
                await conn.execute(
                    f"ALTER TABLE command_registry ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
            for col, defn in [
                ("interested",      "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("conversation_id", "TEXT NOT NULL DEFAULT ''"),
            ]:
                await conn.execute(
                    f"ALTER TABLE user_job_interests ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
            for col, defn in [
                ("user_tier", "TEXT DEFAULT ''"),
                ("is_dynamic", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ]:
                await conn.execute(
                    f"ALTER TABLE command_log ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
            await conn.execute(
                "INSERT INTO platform_config (platform, max_slots, idle_timeout_sec, description, updated_at) "
                "VALUES ('bosszp', 50, 7200, 'Boss直聘', $1) ON CONFLICT (platform) DO NOTHING",
                _utc_now(),
            )

    async def get_max_display_id(self) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT MAX(display_id) FROM sessions")
        return int(row[0] or 0)

    async def log_command_start(
        self, session_id, agent_id, request_id, tool_name, method, path, body=None,
        user_tier="", is_dynamic=False,
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO command_log
                   (session_id, agent_id, request_id, tool_name, method, path,
                    request_body, started_at, user_tier, is_dynamic)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (request_id) DO NOTHING""",
                session_id,
                agent_id or None,
                request_id,
                tool_name or None,
                method,
                path,
                _json_full(body),
                _utc_now(),
                user_tier or None,
                bool(is_dynamic),
            )

    async def log_command_end(
        self, request_id, ok, response_body=None, error=None, duration_ms=None
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE command_log
                   SET response_ok=$1, response_body=$2, ended_at=$3, duration_ms=$4, error=$5
                   WHERE request_id=$6""",
                1 if ok else 0,
                _json_full(response_body),
                _utc_now(),
                duration_ms,
                error,
                request_id,
            )

    async def log_session_event(
        self, session_id, agent_id, event_type, detail=None
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO session_events (session_id, agent_id, event_type, detail, occurred_at)
                   VALUES ($1, $2, $3, $4, $5)""",
                session_id,
                agent_id,
                event_type,
                detail,
                _utc_now(),
            )

    async def upsert_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        pool = await self._get_pool()
        cols = list(fields.keys())
        vals = list(fields.values())
        set_clause = ", ".join(f"{c}=${i + 1}" for i, c in enumerate(cols))
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO sessions (session_id, browser_id, connected_at) "
                "VALUES ($1, '', $2) ON CONFLICT (session_id) DO NOTHING",
                session_id,
                _utc_now(),
            )
            await conn.execute(
                f"UPDATE sessions SET {set_clause} WHERE session_id=${len(cols) + 1}",
                *vals,
                session_id,
            )

    async def upsert_agent_session(self, agent_id: str, **fields: Any) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agent_sessions (agent_id, connected_at) "
                "VALUES ($1, $2) ON CONFLICT (agent_id) DO NOTHING",
                agent_id,
                _utc_now(),
            )
            if fields:
                cols = list(fields.keys())
                vals = list(fields.values())
                set_clause = ", ".join(f"{c}=${i + 1}" for i, c in enumerate(cols))
                await conn.execute(
                    f"UPDATE agent_sessions SET {set_clause} WHERE agent_id=${len(cols) + 1}",
                    *vals,
                    agent_id,
                )

    async def list_sessions(self, active_only: bool = False) -> list[dict]:
        pool = await self._get_pool()
        q = "SELECT * FROM sessions"
        if active_only:
            q += " WHERE status='connected'"
        q += " ORDER BY connected_at DESC"
        async with pool.acquire() as conn:
            rows = await conn.fetch(q)
        return [dict(r) for r in rows]

    async def list_agent_sessions(self, active_only: bool = False) -> list[dict]:
        pool = await self._get_pool()
        q = "SELECT * FROM agent_sessions"
        if active_only:
            q += " WHERE status='connected'"
        q += " ORDER BY connected_at DESC"
        async with pool.acquire() as conn:
            rows = await conn.fetch(q)
        return [dict(r) for r in rows]

    async def list_commands(
        self, session_id=None, agent_id=None, limit=100, offset=0,
        response_ok=None, tool_name="", since_iso="", keyword="",
    ) -> list[dict]:
        pool = await self._get_pool()
        conditions: list[str] = []
        params: list[Any] = []
        if session_id:
            params.append(session_id)
            conditions.append(f"session_id=${len(params)}")
        if agent_id:
            params.append(agent_id)
            conditions.append(f"agent_id=${len(params)}")
        if response_ok is not None:
            params.append(int(response_ok))
            conditions.append(f"response_ok=${len(params)}")
        if tool_name:
            params.append(tool_name)
            conditions.append(f"tool_name=${len(params)}")
        if since_iso:
            params.append(since_iso)
            conditions.append(f"started_at >= ${len(params)}")
        if keyword:
            params.append(f"%{keyword}%")
            conditions.append(f"error ILIKE ${len(params)}")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        limit_ph = f"${len(params)}"
        params.append(offset)
        offset_ph = f"${len(params)}"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM command_log {where} "
                f"ORDER BY started_at DESC LIMIT {limit_ph} OFFSET {offset_ph}",
                *params,
            )
        return [dict(r) for r in rows]

    async def list_command_tool_names(
        self, since_iso="", response_ok=None,
    ) -> list[str]:
        pool = await self._get_pool()
        conditions: list[str] = ["tool_name IS NOT NULL", "tool_name != ''"]
        params: list[Any] = []
        if since_iso:
            params.append(since_iso)
            conditions.append(f"started_at >= ${len(params)}")
        if response_ok is not None:
            params.append(int(response_ok))
            conditions.append(f"response_ok=${len(params)}")
        where = "WHERE " + " AND ".join(conditions)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT DISTINCT tool_name FROM command_log {where} "
                f"ORDER BY tool_name",
                *params,
            )
        return [r["tool_name"] for r in rows if r["tool_name"]]

    async def get_stats(self) -> dict:
        pool = await self._get_pool()
        today = _utc_now()[:10]
        async with pool.acquire() as conn:
            active_sessions = await conn.fetchval(
                "SELECT COUNT(*) FROM sessions WHERE status='connected'"
            )
            active_agents = await conn.fetchval(
                "SELECT COUNT(*) FROM agent_sessions WHERE status='connected'"
            )
            today_commands = await conn.fetchval(
                "SELECT COUNT(*) FROM command_log WHERE started_at LIKE $1", today + "%"
            )
            avg_duration = await conn.fetchval(
                "SELECT AVG(duration_ms) FROM command_log "
                "WHERE started_at LIKE $1 AND duration_ms IS NOT NULL",
                today + "%",
            )
            error_count = await conn.fetchval(
                "SELECT COUNT(*) FROM command_log WHERE started_at LIKE $1 AND response_ok=0",
                today + "%",
            )
        error_rate = (error_count / today_commands * 100) if today_commands > 0 else 0.0
        return {
            "active_sessions": int(active_sessions),
            "active_agents": int(active_agents),
            "today_commands": int(today_commands),
            "avg_duration_ms": round(float(avg_duration or 0), 1),
            "error_rate_pct": round(error_rate, 1),
        }

    async def list_command_registry(self, ext_name: str | None = None) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if ext_name:
                rows = await conn.fetch(
                    "SELECT * FROM command_registry WHERE ext_name=$1 ORDER BY ext_name, id", ext_name
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM command_registry ORDER BY ext_name, id"
                )
        return [dict(r) for r in rows]

    async def get_command_registry(self, ext_name: str, path: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM command_registry WHERE ext_name=$1 AND path=$2", ext_name, path
            )
        return dict(row) if row else None

    async def update_command_registry(self, id: int, **fields: Any) -> None:
        if not fields:
            return
        pool = await self._get_pool()
        fields["updated_at"] = _utc_now()
        cols = list(fields.keys())
        vals = list(fields.values())
        set_clause = ", ".join(f"{c}=${i + 1}" for i, c in enumerate(cols))
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE command_registry SET {set_clause} WHERE id=${len(cols) + 1}",
                *vals, id,
            )

    async def seed_command_registry(self, commands: list[dict]) -> None:
        pool = await self._get_pool()
        now = _utc_now()
        async with pool.acquire() as conn:
            for cmd in commands:
                await conn.execute(
                    """INSERT INTO command_registry
                       (ext_name, cmd_group, path, label, method, description, enabled,
                        webhook_url, webhook_secret, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, 1, '', '', $7, $8)
                       ON CONFLICT (ext_name, path) DO UPDATE SET
                         cmd_group = EXCLUDED.cmd_group,
                         updated_at = EXCLUDED.updated_at
                       WHERE command_registry.cmd_group = ''""",
                    cmd["ext_name"], cmd.get("cmd_group", ""), cmd["path"], cmd["label"],
                    cmd.get("method", "POST"), cmd.get("description", ""), now, now,
                )

    async def upsert_browser_slot(self, browser_id: str, **fields: Any) -> None:
        pool = await self._get_pool()
        now = _utc_now()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO browser_slots (browser_id, created_at, updated_at) VALUES ($1, $2, $3) ON CONFLICT (browser_id) DO NOTHING",
                browser_id, now, now,
            )
            fields["updated_at"] = now
            if fields:
                cols = list(fields.keys())
                vals = list(fields.values())
                set_clause = ", ".join(f"{c}=${i+1}" for i, c in enumerate(cols))
                await conn.execute(
                    f"UPDATE browser_slots SET {set_clause} WHERE browser_id=${len(cols)+1}",
                    *vals, browser_id,
                )

    async def get_browser_slot(self, browser_id: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM browser_slots WHERE browser_id=$1", browser_id)
        return dict(row) if row else None

    async def get_browser_slot_by_app_user(self, app_user_id: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM browser_slots WHERE app_user_id=$1 AND slot_state IN ('idle', 'busy', 'offline')"
                " ORDER BY last_seen_at DESC LIMIT 1",
                app_user_id,
            )
        return dict(row) if row else None

    async def upsert_user_job_interest(self, app_user_id: str, encrypt_job_id: str, **fields: Any) -> None:
        pool = await self._get_pool()
        now = datetime.now(timezone.utc)  # asyncpg requires datetime, not str
        platform = fields.get("platform", "boss")
        extra = {k: v for k, v in fields.items() if k != "platform"}
        base_cols = ["app_user_id", "platform", "encrypt_job_id", "created_at", "updated_at"]
        base_vals: list[Any] = [app_user_id, platform, encrypt_job_id, now, now]
        all_cols = base_cols + list(extra.keys())
        all_vals = base_vals + list(extra.values())
        placeholders = ", ".join(f"${i+1}" for i in range(len(all_cols)))
        update_cols = list(extra.keys()) + ["updated_at"]
        update_sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        async with pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO user_job_interests ({', '.join(all_cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(app_user_id, platform, encrypt_job_id) DO UPDATE SET {update_sets}",
                *all_vals,
            )

    async def list_user_job_interests(self, app_user_id: str, status: str = "", platform: str = "boss") -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT * FROM user_job_interests WHERE app_user_id=$1 AND platform=$2 AND status=$3 ORDER BY updated_at DESC",
                    app_user_id, platform, status,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM user_job_interests WHERE app_user_id=$1 AND platform=$2 ORDER BY updated_at DESC",
                    app_user_id, platform,
                )
        return [dict(r) for r in rows]

    async def update_user_job_interest_status(self, app_user_id: str, encrypt_job_id: str, status: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE user_job_interests SET status=$1, updated_at=$2 WHERE app_user_id=$3 AND encrypt_job_id=$4",
                status, datetime.now(timezone.utc), app_user_id, encrypt_job_id,
            )

    async def upsert_recruiter_geek_interest(self, app_user_id: str, platform: str, encrypt_geek_id: str, encrypt_job_id: str, **fields: Any) -> None:
        pool = await self._get_pool()
        now = datetime.now(timezone.utc)
        # 过滤掉 None：未显式指定的字段不进 INSERT，也不在 UPDATE 时触碰已有列。
        # （以前 match_score 被特判保留 None，会让 mark_geek_interest 默认调用把 batch
        # 刚写入的 match_score 清回 NULL —— 审核补丁 #C 的核心 race。）
        extra = {k: v for k, v in fields.items() if v is not None}
        base_cols = ["app_user_id", "platform", "encrypt_geek_id", "encrypt_job_id", "created_at", "updated_at"]
        base_vals: list[Any] = [app_user_id, platform, encrypt_geek_id, encrypt_job_id, now, now]
        all_cols = base_cols + list(extra.keys())
        all_vals = base_vals + list(extra.values())
        placeholders = ", ".join(f"${i+1}" for i in range(len(all_cols)))
        # COALESCE 语义：EXCLUDED.col 为 NULL 时保留表里已有值。双保险 —— 即使未来
        # 某个字段绕过上面的 None 过滤进来，也不会把并发写入的值抹掉。updated_at
        # 永远刷为当前时间。
        update_sets = ", ".join(
            f"{c}=COALESCE(EXCLUDED.{c}, recruiter_geek_interests.{c})"
            for c in extra.keys()
        )
        if update_sets:
            update_sets += ", updated_at=EXCLUDED.updated_at"
        else:
            update_sets = "updated_at=EXCLUDED.updated_at"
        async with pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO recruiter_geek_interests ({', '.join(all_cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(app_user_id, platform, encrypt_geek_id, encrypt_job_id) DO UPDATE SET {update_sets}",
                *all_vals,
            )

    async def get_recruiter_geek_interests(self, app_user_id: str, encrypt_job_id: str | None = None, interested_only: bool = False, limit: int = 50, offset: int = 0) -> list[dict]:
        pool = await self._get_pool()
        conditions = ["app_user_id=$1"]
        params: list[Any] = [app_user_id]
        if encrypt_job_id:
            params.append(encrypt_job_id)
            conditions.append(f"encrypt_job_id=${len(params)}")
        if interested_only:
            conditions.append("interested=TRUE")
        where = "WHERE " + " AND ".join(conditions)
        params.extend([limit, offset])
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM recruiter_geek_interests {where} ORDER BY updated_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
                *params,
            )
        return [dict(r) for r in rows]

    async def list_browser_slots(self, platform: str | None = None, state: str | None = None) -> list[dict]:
        pool = await self._get_pool()
        conditions: list[str] = []
        params: list[Any] = []
        if platform:
            params.append(platform)
            conditions.append(f"platform=${len(params)}")
        if state:
            params.append(state)
            conditions.append(f"slot_state=${len(params)}")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        async with pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT * FROM browser_slots {where} ORDER BY created_at ASC", *params)
        return [dict(r) for r in rows]

    async def find_idle_browser_slot(self, platform: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM browser_slots WHERE platform=$1 AND slot_state='idle' ORDER BY last_seen_at DESC LIMIT 1",
                platform,
            )
        return dict(row) if row else None

    async def count_browser_slots(self, platform: str) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT COUNT(*) FROM browser_slots WHERE platform=$1", platform)
        return int(val or 0)

    async def log_pool_assignment(self, browser_id: str, app_user_id: str, platform: str, event_type: str, session_id: str = "") -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO pool_assignments (browser_id, app_user_id, platform, event_type, session_id, occurred_at) VALUES ($1, $2, $3, $4, $5, $6)",
                browser_id, app_user_id, platform, event_type, session_id, _utc_now(),
            )

    async def list_platform_config(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM platform_config ORDER BY platform")
        return [dict(r) for r in rows]

    async def upsert_platform_config(self, platform: str, **fields: Any) -> None:
        pool = await self._get_pool()
        now = _utc_now()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO platform_config (platform, updated_at) VALUES ($1, $2) ON CONFLICT (platform) DO NOTHING",
                platform, now,
            )
            fields["updated_at"] = now
            if fields:
                cols = list(fields.keys())
                vals = list(fields.values())
                set_clause = ", ".join(f"{c}=${i+1}" for i, c in enumerate(cols))
                await conn.execute(
                    f"UPDATE platform_config SET {set_clause} WHERE platform=${len(cols)+1}",
                    *vals, platform,
                )

    async def delete_browser_slot(self, browser_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM browser_slots WHERE browser_id=$1", browser_id)

    async def list_proxy_pool(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT url, added_at, note FROM proxy_pool_entries ORDER BY added_at ASC")
        return [dict(r) for r in rows]

    async def add_proxy_pool_entry(self, url: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO proxy_pool_entries (url, added_at) VALUES ($1, $2) ON CONFLICT (url) DO NOTHING",
                url, _utc_now(),
            )
        return True

    async def remove_proxy_pool_entry(self, url: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM proxy_pool_entries WHERE url=$1", url)
        return True

    async def save_account_cookies(
        self, browser_id: str, session_id: str, account_name: str, app_user_id: str,
        platform: str, cookies_json: str, expires_at: str = "", notes: str = ""
    ) -> str:
        import uuid as _uuid
        pool = await self._get_pool()
        now = _utc_now()
        rec_id = _uuid.uuid4().hex
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO account_cookies
                   (id, browser_id, session_id, account_name, user_id, platform,
                    cookies_json, created_at, updated_at, expires_at, notes)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
                rec_id, browser_id, session_id, account_name, app_user_id, platform,
                cookies_json, now, now, expires_at, notes,
            )
        return rec_id

    async def get_account_cookies(self, id: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM account_cookies WHERE id=$1", id)
        return dict(row) if row else None

    async def list_account_cookies(self, platform: str | None = None) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if platform:
                rows = await conn.fetch(
                    "SELECT * FROM account_cookies WHERE platform=$1 ORDER BY created_at DESC", platform
                )
            else:
                rows = await conn.fetch("SELECT * FROM account_cookies ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def update_account_cookies(self, id: str, cookies_json: str, updated_at: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE account_cookies SET cookies_json=$1, updated_at=$2 WHERE id=$3",
                cookies_json, updated_at, id,
            )

    async def delete_account_cookies(self, id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM account_cookies WHERE id=$1", id)

    async def update_cookie_metadata(self, id: str, **fields: str) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("notes", "account_name")}
        if not allowed:
            return
        keys = list(allowed.keys())
        sets = ", ".join(f"{k}=${i+1}" for i, k in enumerate(keys))
        vals = list(allowed.values()) + [id]
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE account_cookies SET {sets} WHERE id=${len(vals)}", *vals
            )

    async def log_execution_decision(
        self,
        session_id: str,
        agent_id: str,
        tool_name: str,
        path: str,
        action: str,
        reason: str,
        detail: str,
        extra_delay_ms: int,
    ) -> None:
        pool = await self._get_pool()
        now = _utc_now()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO execution_decisions
                   (session_id, agent_id, tool_name, path, action, reason, detail,
                    extra_delay_ms, decided_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                session_id, agent_id, tool_name, path,
                action, reason, detail, extra_delay_ms, now,
            )

    async def list_execution_decisions(
        self,
        session_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        pool = await self._get_pool()
        conds: list[str] = []
        params: list[Any] = []
        if session_id:
            params.append(session_id)
            conds.append(f"session_id=${len(params)}")
        if action:
            params.append(action)
            conds.append(f"action=${len(params)}")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        params.append(limit)
        params.append(offset)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM execution_decisions {where} "
                f"ORDER BY decided_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
                *params,
            )
        return [dict(r) for r in rows]


# ── 后端初始化 ───────────────────────────────────────────────────────────────

_backend: _DbBackend = _PostgresBackend(DB_POSTGRES_URL)

print(f"[db] 后端: postgres  url={DB_POSTGRES_URL}", flush=True)


async def get_pool_stats() -> dict:
    """返回 asyncpg 连接池指标。"""
    pool = _backend._pool  # type: ignore[attr-defined]
    if pool is None:
        return {}
    return {
        "size": pool.get_size(),
        "min_size": pool.get_min_size(),
        "max_size": pool.get_max_size(),
        "free_size": pool.get_idle_size(),
    }

# ── 命令计时（模块级，与后端无关） ──────────────────────────────────────────

_cmd_start_times: dict[str, float] = {}


# ── 公开 API（向后兼容，与旧版 db.py 接口相同） ─────────────────────────────


async def init_db() -> None:
    await _backend.init()


async def get_max_display_id() -> int:
    return await _backend.get_max_display_id()


async def log_command_start(
    session_id: str,
    agent_id: str,
    request_id: str,
    tool_name: str,
    method: str,
    path: str,
    body: Any = None,
    user_tier: str = "",
    is_dynamic: bool = False,
) -> None:
    _cmd_start_times[request_id] = time.monotonic()
    try:
        await _backend.log_command_start(
            session_id, agent_id, request_id, tool_name, method, path, body, user_tier,
            is_dynamic=is_dynamic,
        )
    except Exception as e:
        print(f"[db] log_command_start 异常: {e}", flush=True)


async def log_command_end(
    request_id: str,
    ok: bool,
    response_body: Any = None,
    error: str | None = None,
) -> None:
    start = _cmd_start_times.pop(request_id, None)
    duration = (time.monotonic() - start) * 1000 if start is not None else None
    try:
        await _backend.log_command_end(request_id, ok, response_body, error, duration)
    except Exception as e:
        print(f"[db] log_command_end 异常: {e}", flush=True)


async def log_session_event(
    session_id: str | None,
    agent_id: str | None,
    event_type: str,
    detail: str | None = None,
) -> None:
    try:
        await _backend.log_session_event(session_id, agent_id, event_type, detail)
    except Exception as e:
        print(f"[db] log_session_event 异常: {e}", flush=True)


async def upsert_session(session_id: str, **fields: Any) -> None:
    try:
        await _backend.upsert_session(session_id, **fields)
    except Exception as e:
        print(f"[db] upsert_session 异常: {e}", flush=True)


async def upsert_agent_session(agent_id: str, **fields: Any) -> None:
    try:
        await _backend.upsert_agent_session(agent_id, **fields)
    except Exception as e:
        print(f"[db] upsert_agent_session 异常: {e}", flush=True)


async def list_sessions(active_only: bool = False) -> list[dict]:
    return await _backend.list_sessions(active_only)


async def list_agent_sessions(active_only: bool = False) -> list[dict]:
    return await _backend.list_agent_sessions(active_only)


async def list_commands(
    session_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    response_ok: int | None = None,
    tool_name: str = "",
    since_iso: str = "",
    keyword: str = "",
) -> list[dict]:
    return await _backend.list_commands(
        session_id, agent_id, limit, offset,
        response_ok=response_ok, tool_name=tool_name,
        since_iso=since_iso, keyword=keyword,
    )


async def list_command_tool_names(
    since_iso: str = "",
    response_ok: int | None = None,
) -> list[str]:
    return await _backend.list_command_tool_names(
        since_iso=since_iso, response_ok=response_ok,
    )


async def get_stats() -> dict:
    return await _backend.get_stats()


async def list_command_registry(ext_name: str | None = None) -> list[dict]:
    return await _backend.list_command_registry(ext_name)


async def get_command_registry(ext_name: str, path: str) -> dict | None:
    return await _backend.get_command_registry(ext_name, path)


async def update_command_registry(id: int, **fields: Any) -> None:
    try:
        await _backend.update_command_registry(id, **fields)
    except Exception as e:
        print(f"[db] update_command_registry 异常: {e}", flush=True)


async def seed_command_registry(commands: list[dict]) -> None:
    try:
        await _backend.seed_command_registry(commands)
    except Exception as e:
        print(f"[db] seed_command_registry 异常: {e}", flush=True)


async def upsert_browser_slot(browser_id: str, **fields: Any) -> None:
    try:
        await _backend.upsert_browser_slot(browser_id, **fields)
    except Exception as e:
        print(f"[db] upsert_browser_slot 异常: {e}", flush=True)


async def get_browser_slot(browser_id: str) -> dict | None:
    return await _backend.get_browser_slot(browser_id)


async def get_browser_slot_by_app_user(app_user_id: str) -> dict | None:
    return await _backend.get_browser_slot_by_app_user(app_user_id)


async def upsert_user_job_interest(app_user_id: str, encrypt_job_id: str, **fields: Any) -> None:
    try:
        await _backend.upsert_user_job_interest(app_user_id, encrypt_job_id, **fields)
    except Exception as e:
        print(f"[db] upsert_user_job_interest 异常: {e}", flush=True)


async def list_user_job_interests(app_user_id: str, status: str = "", platform: str = "boss") -> list[dict]:
    try:
        return await _backend.list_user_job_interests(app_user_id, status, platform)
    except Exception as e:
        print(f"[db] list_user_job_interests 异常: {e}", flush=True)
        return []


async def update_user_job_interest_status(app_user_id: str, encrypt_job_id: str, status: str) -> None:
    try:
        await _backend.update_user_job_interest_status(app_user_id, encrypt_job_id, status)
    except Exception as e:
        print(f"[db] update_user_job_interest_status 异常: {e}", flush=True)


async def upsert_recruiter_geek_interest(app_user_id: str, platform: str, encrypt_geek_id: str, encrypt_job_id: str, **fields: Any) -> None:
    try:
        await _backend.upsert_recruiter_geek_interest(app_user_id, platform, encrypt_geek_id, encrypt_job_id, **fields)
    except Exception as e:
        print(f"[db] upsert_recruiter_geek_interest 异常: {e}", flush=True)
        raise


async def get_recruiter_geek_interests(app_user_id: str, encrypt_job_id: str | None = None, interested_only: bool = False, limit: int = 50, offset: int = 0) -> list[dict]:
    try:
        return await _backend.get_recruiter_geek_interests(app_user_id, encrypt_job_id, interested_only, limit=limit, offset=offset)
    except Exception as e:
        print(f"[db] get_recruiter_geek_interests 异常: {e}", flush=True)
        return []


async def list_browser_slots(platform: str | None = None, state: str | None = None) -> list[dict]:
    return await _backend.list_browser_slots(platform, state)


async def find_idle_browser_slot(platform: str) -> dict | None:
    return await _backend.find_idle_browser_slot(platform)


async def count_browser_slots(platform: str) -> int:
    return await _backend.count_browser_slots(platform)


async def delete_browser_slot(browser_id: str) -> None:
    try:
        await _backend.delete_browser_slot(browser_id)
    except Exception as e:
        print(f"[db] delete_browser_slot 异常: {e}", flush=True)


async def log_pool_assignment(browser_id: str, app_user_id: str, platform: str, event_type: str, session_id: str = "") -> None:
    try:
        await _backend.log_pool_assignment(browser_id, app_user_id, platform, event_type, session_id)
    except Exception as e:
        print(f"[db] log_pool_assignment 异常: {e}", flush=True)


async def list_platform_config() -> list[dict]:
    return await _backend.list_platform_config()


async def upsert_platform_config(platform: str, **fields: Any) -> None:
    try:
        await _backend.upsert_platform_config(platform, **fields)
    except Exception as e:
        print(f"[db] upsert_platform_config 异常: {e}", flush=True)


async def list_proxy_pool() -> list[dict]:
    try:
        return await _backend.list_proxy_pool()
    except Exception as e:
        print(f"[db] list_proxy_pool 异常: {e}", flush=True)
        return []


async def add_proxy_pool_entry(url: str) -> bool:
    try:
        return await _backend.add_proxy_pool_entry(url)
    except Exception as e:
        print(f"[db] add_proxy_pool_entry 异常: {e}", flush=True)
        return False


async def remove_proxy_pool_entry(url: str) -> bool:
    try:
        return await _backend.remove_proxy_pool_entry(url)
    except Exception as e:
        print(f"[db] remove_proxy_pool_entry 异常: {e}", flush=True)
        return False


async def save_account_cookies(
    browser_id: str,
    session_id: str,
    account_name: str,
    app_user_id: str,
    platform: str,
    cookies_json: str,
    expires_at: str = "",
    notes: str = "",
) -> str:
    try:
        return await _backend.save_account_cookies(
            browser_id, session_id, account_name, app_user_id, platform,
            cookies_json, expires_at, notes
        )
    except Exception as e:
        print(f"[db] save_account_cookies 异常: {e}", flush=True)
        raise


async def get_account_cookies(id: str) -> dict | None:
    try:
        return await _backend.get_account_cookies(id)
    except Exception as e:
        print(f"[db] get_account_cookies 异常: {e}", flush=True)
        return None


async def list_account_cookies(platform: str | None = None) -> list[dict]:
    try:
        return await _backend.list_account_cookies(platform)
    except Exception as e:
        print(f"[db] list_account_cookies 异常: {e}", flush=True)
        return []


async def update_account_cookies(id: str, cookies_json: str, updated_at: str) -> None:
    try:
        await _backend.update_account_cookies(id, cookies_json, updated_at)
    except Exception as e:
        print(f"[db] update_account_cookies 异常: {e}", flush=True)


async def delete_account_cookies(id: str) -> None:
    try:
        await _backend.delete_account_cookies(id)
    except Exception as e:
        print(f"[db] delete_account_cookies 异常: {e}", flush=True)


async def update_cookie_metadata(id: str, **fields: str) -> None:
    try:
        await _backend.update_cookie_metadata(id, **fields)
    except Exception as e:
        print(f"[db] update_cookie_metadata 异常: {e}", flush=True)


async def log_execution_decision(**kwargs: Any) -> None:
    try:
        await _backend.log_execution_decision(**kwargs)
    except Exception as e:
        print(f"[db] log_execution_decision 异常: {e}", flush=True)


async def list_execution_decisions(
    session_id: str | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    try:
        return await _backend.list_execution_decisions(session_id, action, limit, offset)
    except Exception as e:
        print(f"[db] list_execution_decisions 异常: {e}", flush=True)
        return []


# ── Job Cache（仅 PostgreSQL） ──────────────────────────────────────────────


async def _get_pg_pool():
    """获取 PostgreSQL 连接池。"""
    return await _backend._get_pool()


async def upsert_job(
    platform: str,
    external_id: str,
    *,
    title: str | None = None,
    company: str | None = None,
    city: str | None = None,
    city_code: str | None = None,
    country: str | None = None,
    salary: str | None = None,
    job_type: str | None = None,
    experience: str | None = None,
    education: str | None = None,
    tags: list | None = None,
    skills: list | None = None,
    hr_name: str | None = None,
    hr_title: str | None = None,
    encrypt_boss_id: str | None = None,
    list_security_id: str | None = None,
    remote: bool | None = None,
    posted_at: str | None = None,
    expires_at: str | None = None,
    apply_url: str | None = None,
    source_url: str | None = None,
    industry: str | None = None,
    company_size: str | None = None,
    raw_list: dict | None = None,
    search_session_id: str | None = None,
) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO cached_jobs
               (platform, external_id, title, company, city, city_code, country, salary,
                job_type, experience, education, tags, skills, hr_name, hr_title,
                encrypt_boss_id, list_security_id, remote, posted_at, expires_at,
                apply_url, source_url, industry, company_size, raw_list, fetched_at,
                search_session_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,NOW(),$26)
           ON CONFLICT (platform, external_id) DO UPDATE SET
               title=EXCLUDED.title, company=EXCLUDED.company, city=EXCLUDED.city,
               city_code=EXCLUDED.city_code, country=EXCLUDED.country,
               salary=EXCLUDED.salary, job_type=EXCLUDED.job_type,
               experience=EXCLUDED.experience, education=EXCLUDED.education,
               tags=EXCLUDED.tags, skills=EXCLUDED.skills, hr_name=EXCLUDED.hr_name,
               hr_title=EXCLUDED.hr_title, encrypt_boss_id=EXCLUDED.encrypt_boss_id,
               list_security_id=EXCLUDED.list_security_id, remote=EXCLUDED.remote,
               posted_at=EXCLUDED.posted_at, expires_at=EXCLUDED.expires_at,
               apply_url=EXCLUDED.apply_url, source_url=EXCLUDED.source_url,
               industry=EXCLUDED.industry, company_size=EXCLUDED.company_size,
               raw_list=EXCLUDED.raw_list, fetched_at=NOW(),
               search_session_id=COALESCE(EXCLUDED.search_session_id, cached_jobs.search_session_id)""",
        platform, external_id, title, company, city, city_code, country, salary,
        job_type, experience, education,
        json.dumps(tags, ensure_ascii=False) if tags is not None else None,
        json.dumps(skills, ensure_ascii=False) if skills is not None else None,
        hr_name, hr_title, encrypt_boss_id, list_security_id,
        remote, posted_at, expires_at, apply_url, source_url, industry, company_size,
        json.dumps(raw_list, ensure_ascii=False) if raw_list is not None else None,
        search_session_id or None,
    )


async def upsert_job_detail(
    platform: str,
    external_id: str,
    *,
    description: str | None = None,
    address: str | None = None,
    longitude: float | None = None,
    latitude: float | None = None,
    detail_security_id: str | None = None,
    raw_detail: dict | None = None,
) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO cached_job_details
               (platform, external_id, description, address, longitude, latitude,
                detail_security_id, raw_detail, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW())
           ON CONFLICT (platform, external_id) DO UPDATE SET
               description=EXCLUDED.description, address=EXCLUDED.address,
               longitude=EXCLUDED.longitude, latitude=EXCLUDED.latitude,
               detail_security_id=EXCLUDED.detail_security_id,
               raw_detail=EXCLUDED.raw_detail, fetched_at=NOW()""",
        platform, external_id, description, address, longitude, latitude,
        detail_security_id,
        json.dumps(raw_detail, ensure_ascii=False) if raw_detail is not None else None,
    )


async def upsert_search(
    platform: str,
    keyword: str,
    city_code: str | None,
    page: int,
    *,
    job_ids: list | None = None,
    total_count: int | None = None,
) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO cached_searches
               (platform, keyword, city_code, page, job_ids, total_count, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,NOW())
           ON CONFLICT (platform, keyword, city_code, page) DO UPDATE SET
               job_ids=EXCLUDED.job_ids, total_count=EXCLUDED.total_count,
               fetched_at=NOW()""",
        platform, keyword, city_code or "", page,
        json.dumps(job_ids, ensure_ascii=False) if job_ids is not None else None,
        total_count,
    )


_JSONB_FIELDS = {"tags", "skills", "raw_list", "raw_detail", "job_ids", "meta", "payload"}


def _row_to_dict_pg(row: Any) -> dict:
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif k in _JSONB_FIELDS and isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                pass
    return d


async def list_cached_jobs(
    *,
    platform: str | None = None,
    keyword: str | None = None,
    city_code: str | None = None,
    has_detail: bool | None = None,
    search_session_id: str | None = None,
    fresh_within_days: int | None = None,
    include_expired: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """从本地职位缓存取列表。

    F3 新增的两个过滤（默认都不启用以保持向后兼容；调用方需要时显式传入）：
      - fresh_within_days：只返回最近 N 天内抓到的行。用于防止返回 30+ 天前的
        描述/薪资已过时的旧快照。
      - include_expired=False：默认排除已过招聘截止日期的职位
        （expires_at < NOW()）。Boss/Indeed/LinkedIn 返回的职位通常带有效期，
        过期的再展示给 Agent 只会浪费调用配额。
    """
    pool = await _get_pg_pool()
    if pool is None:
        return []
    conditions: list[str] = []
    args: list = []
    i = 1

    if platform:
        conditions.append(f"j.platform = ${i}")
        args.append(platform)
        i += 1
    if keyword:
        conditions.append(f"(j.title ILIKE ${i} OR j.company ILIKE ${i})")
        args.append(f"%{keyword}%")
        i += 1
    if city_code:
        conditions.append(f"j.city_code = ${i}")
        args.append(city_code)
        i += 1
    if has_detail is True:
        conditions.append("d.id IS NOT NULL")
    elif has_detail is False:
        conditions.append("d.id IS NULL")
    if search_session_id:
        conditions.append(f"j.search_session_id = ${i}")
        args.append(search_session_id)
        i += 1
    if fresh_within_days is not None and fresh_within_days > 0:
        # 用 make_interval 避免字符串插值 + SQL 注入；fresh_within_days 本身已被判正
        conditions.append(f"j.fetched_at > NOW() - make_interval(days => ${i})")
        args.append(int(fresh_within_days))
        i += 1
    if not include_expired:
        # 招聘截止已过的职位静默剔除；expires_at 为 NULL 视为永不过期（保留）
        conditions.append("(j.expires_at IS NULL OR j.expires_at > NOW())")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"""SELECT j.id, j.platform, j.external_id, j.title, j.company, j.city, j.city_code,
                   j.country, j.salary, j.job_type, j.experience, j.education,
                   j.hr_name, j.hr_title, j.remote, j.posted_at, j.expires_at,
                   j.apply_url, j.source_url, j.industry, j.company_size, j.fetched_at,
                   j.search_session_id, (d.id IS NOT NULL) AS has_detail
            FROM cached_jobs j
            LEFT JOIN cached_job_details d
                   ON d.platform = j.platform AND d.external_id = j.external_id
            {where}
            ORDER BY j.fetched_at DESC
            LIMIT ${i} OFFSET ${i+1}""",
        *args, limit, offset,
    )
    return [_row_to_dict_pg(r) for r in rows]


# ── 求职者：聊天列表缓存函数 ─────────────────────────────────────────────────────


async def upsert_chat(
    platform: str,
    account_name: str,
    encrypt_friend_id: str,
    *,
    friend_id: str = "",
    name: str | None = None,
    brand_name: str | None = None,
    job_name: str | None = None,
    position_name: str | None = None,
    boss_title: str | None = None,
    job_city: str | None = None,
    update_time: int | None = None,
    water_level: int | None = None,
    label_id: int | None = None,
    chat_security_id: str | None = None,
    encrypt_job_id: str | None = None,
    last_msg: str | None = None,
    last_msg_ts: int | None = None,
    raw_card: dict | None = None,
) -> None:
    """写入/合并一条好友。label_id 非空时合并进现有 label_set，去重。

    encrypt_job_id / chat_security_id / last_msg* 来自旧接口 merge（新接口缺这些）。
    所有字段 COALESCE 模式：None 不覆盖现有值。
    """
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO cached_chats
               (platform, account_name, encrypt_friend_id, friend_id, name,
                brand_name, job_name, position_name, boss_title, job_city,
                update_time, water_level, label_set, chat_security_id,
                encrypt_job_id, last_msg, last_msg_ts, raw_card, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                   CASE WHEN $13::INT IS NULL THEN '[]'::jsonb
                        ELSE jsonb_build_array($13::INT) END,
                   $14,$15,$16,$17,$18,NOW())
           ON CONFLICT (platform, account_name, encrypt_friend_id) DO UPDATE SET
               friend_id        = COALESCE(NULLIF(EXCLUDED.friend_id, ''), cached_chats.friend_id),
               name             = COALESCE(EXCLUDED.name, cached_chats.name),
               brand_name       = COALESCE(EXCLUDED.brand_name, cached_chats.brand_name),
               job_name         = COALESCE(EXCLUDED.job_name, cached_chats.job_name),
               position_name    = COALESCE(EXCLUDED.position_name, cached_chats.position_name),
               boss_title       = COALESCE(EXCLUDED.boss_title, cached_chats.boss_title),
               job_city         = COALESCE(EXCLUDED.job_city, cached_chats.job_city),
               update_time      = COALESCE(EXCLUDED.update_time, cached_chats.update_time),
               water_level      = COALESCE(EXCLUDED.water_level, cached_chats.water_level),
               label_set        = CASE WHEN $13::INT IS NULL THEN cached_chats.label_set
                                       ELSE COALESCE((
                                          SELECT jsonb_agg(DISTINCT v)
                                          FROM jsonb_array_elements(
                                              cached_chats.label_set || jsonb_build_array($13::INT)
                                          ) v
                                       ), cached_chats.label_set) END,
               chat_security_id = COALESCE(EXCLUDED.chat_security_id, cached_chats.chat_security_id),
               encrypt_job_id   = COALESCE(EXCLUDED.encrypt_job_id, cached_chats.encrypt_job_id),
               last_msg         = COALESCE(EXCLUDED.last_msg, cached_chats.last_msg),
               last_msg_ts      = COALESCE(EXCLUDED.last_msg_ts, cached_chats.last_msg_ts),
               raw_card         = COALESCE(EXCLUDED.raw_card, cached_chats.raw_card),
               fetched_at       = NOW()""",
        platform, account_name or "", encrypt_friend_id, friend_id or "",
        name, brand_name, job_name, position_name, boss_title, job_city,
        update_time, water_level, label_id, chat_security_id,
        encrypt_job_id, last_msg, last_msg_ts,
        json.dumps(raw_card, ensure_ascii=False) if raw_card is not None else None,
    )


async def list_cached_chats(
    *,
    platform: str = "boss",
    account_name: str | None = None,
    label_id: int | None = None,
    search: str | None = None,
    fresh_within_minutes: int | None = 10,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """读聊天列表缓存。

    - label_id=None / 0 → 不按 label 过滤
    - label_id=N (N>0) → label_set 含 N
    - fresh_within_minutes=None → 不按新鲜度过滤；默认 10 分钟
    - search → name / brand_name / job_name / position_name 上 ILIKE
    """
    pool = await _get_pg_pool()
    if pool is None:
        return []
    conditions: list[str] = ["platform = $1"]
    args: list = [platform]
    i = 2
    if account_name:
        conditions.append(f"account_name = ${i}")
        args.append(account_name)
        i += 1
    if label_id is not None and label_id > 0:
        conditions.append(f"label_set @> jsonb_build_array(${i}::INT)")
        args.append(int(label_id))
        i += 1
    if search:
        conditions.append(
            f"(name ILIKE ${i} OR brand_name ILIKE ${i} OR job_name ILIKE ${i} "
            f"OR position_name ILIKE ${i})"
        )
        args.append(f"%{search}%")
        i += 1
    if fresh_within_minutes is not None and fresh_within_minutes > 0:
        conditions.append(f"fetched_at > NOW() - make_interval(mins => ${i})")
        args.append(int(fresh_within_minutes))
        i += 1
    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"""SELECT platform, account_name, encrypt_friend_id, friend_id, name,
                   brand_name, job_name, position_name, boss_title, job_city,
                   update_time, water_level, label_set, chat_security_id,
                   encrypt_job_id, last_msg, last_msg_ts, fetched_at
            FROM cached_chats
            {where}
            ORDER BY COALESCE(update_time, 0) DESC, fetched_at DESC
            LIMIT ${i} OFFSET ${i+1}""",
        *args, limit, offset,
    )
    return [_row_to_dict_pg(r) for r in rows]


async def list_cached_geeks(
    *,
    platform: str = "boss",
    account_name: str | None = None,
    source_job_id: str | None = None,
    search: str | None = None,
    fresh_within_days: int | None = 1,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """读招聘方候选人缓存 —— 表已由 _cache_interaction_geeks / _cache_geek_list 写入。"""
    pool = await _get_pg_pool()
    if pool is None:
        return []
    conditions: list[str] = ["platform = $1"]
    args: list = [platform]
    i = 2
    if account_name:
        conditions.append(f"account_name = ${i}")
        args.append(account_name)
        i += 1
    if source_job_id:
        conditions.append(f"source_job_id = ${i}")
        args.append(source_job_id)
        i += 1
    if search:
        conditions.append(
            f"(name ILIKE ${i} OR current_work ILIKE ${i} OR school ILIKE ${i})"
        )
        args.append(f"%{search}%")
        i += 1
    if fresh_within_days is not None and fresh_within_days > 0:
        conditions.append(f"fetched_at > NOW() - make_interval(days => ${i})")
        args.append(int(fresh_within_days))
        i += 1
    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"""SELECT platform, account_name, encrypt_geek_id, uid, name, city,
                   work_year, salary, current_work, school, degree_name,
                   active_desc, apply_status, source_job_id, fetched_at
            FROM cached_geeks
            {where}
            ORDER BY fetched_at DESC
            LIMIT ${i} OFFSET ${i+1}""",
        *args, limit, offset,
    )
    return [_row_to_dict_pg(r) for r in rows]


# ── 招聘方：全局聊天列表缓存函数（cached_recruiter_chats） ──────────────────────


async def upsert_recruiter_chat(
    platform: str,
    account_name: str,
    encrypt_friend_id: str,
    *,
    friend_id: str = "",
    friend_source: int | None = None,
    name: str | None = None,
    update_time: int | None = None,
    water_level: int | None = None,
    label_id: int | None = None,
    raw_card: dict | None = None,
) -> None:
    """写入/合并一条招聘方聊天列表项。

    name 为 None 时不覆盖现有值（COALESCE 模式）—— 后续 boss_view_geek_detail
    等接口可机会主义地补 name。label_id 非空时合并进 label_set，去重。
    """
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO cached_recruiter_chats
               (platform, account_name, encrypt_friend_id, friend_id, friend_source,
                name, update_time, water_level, label_set, raw_card, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,
                   CASE WHEN $9::INT IS NULL THEN '[]'::jsonb
                        ELSE jsonb_build_array($9::INT) END,
                   $10,NOW())
           ON CONFLICT (platform, account_name, encrypt_friend_id) DO UPDATE SET
               friend_id     = COALESCE(NULLIF(EXCLUDED.friend_id, ''), cached_recruiter_chats.friend_id),
               friend_source = COALESCE(EXCLUDED.friend_source, cached_recruiter_chats.friend_source),
               name          = COALESCE(EXCLUDED.name, cached_recruiter_chats.name),
               update_time   = COALESCE(EXCLUDED.update_time, cached_recruiter_chats.update_time),
               water_level   = COALESCE(EXCLUDED.water_level, cached_recruiter_chats.water_level),
               label_set     = CASE WHEN $9::INT IS NULL THEN cached_recruiter_chats.label_set
                                    ELSE COALESCE((
                                       SELECT jsonb_agg(DISTINCT v)
                                       FROM jsonb_array_elements(
                                           cached_recruiter_chats.label_set || jsonb_build_array($9::INT)
                                       ) v
                                    ), cached_recruiter_chats.label_set) END,
               raw_card      = COALESCE(EXCLUDED.raw_card, cached_recruiter_chats.raw_card),
               fetched_at    = NOW()""",
        platform, account_name or "", encrypt_friend_id, friend_id or "",
        friend_source, name, update_time, water_level, label_id,
        json.dumps(raw_card, ensure_ascii=False) if raw_card is not None else None,
    )


async def list_cached_recruiter_chats(
    *,
    platform: str = "boss",
    account_name: str | None = None,
    label_id: int | None = None,
    search: str | None = None,
    fresh_within_minutes: int | None = 10,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """读招聘方聊天列表缓存。search 只在 name ILIKE 上做（其它字段招聘方不填）。"""
    pool = await _get_pg_pool()
    if pool is None:
        return []
    conditions: list[str] = ["platform = $1"]
    args: list = [platform]
    i = 2
    if account_name:
        conditions.append(f"account_name = ${i}")
        args.append(account_name)
        i += 1
    if label_id is not None and label_id > 0:
        conditions.append(f"label_set @> jsonb_build_array(${i}::INT)")
        args.append(int(label_id))
        i += 1
    if search:
        conditions.append(f"name ILIKE ${i}")
        args.append(f"%{search}%")
        i += 1
    if fresh_within_minutes is not None and fresh_within_minutes > 0:
        conditions.append(f"fetched_at > NOW() - make_interval(mins => ${i})")
        args.append(int(fresh_within_minutes))
        i += 1
    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"""SELECT platform, account_name, encrypt_friend_id, friend_id, friend_source,
                   name, update_time, water_level, label_set, fetched_at
            FROM cached_recruiter_chats
            {where}
            ORDER BY COALESCE(update_time, 0) DESC, fetched_at DESC
            LIMIT ${i} OFFSET ${i+1}""",
        *args, limit, offset,
    )
    return [_row_to_dict_pg(r) for r in rows]


# ── 招聘方：候选人缓存函数 ──────────────────────────────────────────────────────


async def upsert_geek(
    platform: str,
    encrypt_geek_id: str,
    *,
    uid: str | None = None,
    name: str | None = None,
    city: str | None = None,
    work_year: str | None = None,
    salary: str | None = None,
    current_work: str | None = None,
    school: str | None = None,
    degree_name: str | None = None,
    active_desc: str | None = None,
    apply_status: int | None = None,
    friend_relation_status: int | None = None,
    source_job_id: str | None = None,
    account_name: str | None = None,
    raw_card: dict | None = None,
) -> None:
    """从搜索结果更新候选人资料快照（upsert）。"""
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO cached_geeks
               (platform, encrypt_geek_id, uid, name, city, work_year, salary,
                current_work, school, degree_name, active_desc, apply_status,
                friend_relation_status, source_job_id, account_name, raw_card, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,NOW())
           ON CONFLICT (platform, encrypt_geek_id) DO UPDATE SET
               uid=COALESCE(EXCLUDED.uid, cached_geeks.uid),
               name=COALESCE(EXCLUDED.name, cached_geeks.name),
               city=COALESCE(EXCLUDED.city, cached_geeks.city),
               work_year=COALESCE(EXCLUDED.work_year, cached_geeks.work_year),
               salary=COALESCE(EXCLUDED.salary, cached_geeks.salary),
               current_work=COALESCE(EXCLUDED.current_work, cached_geeks.current_work),
               school=COALESCE(EXCLUDED.school, cached_geeks.school),
               degree_name=COALESCE(EXCLUDED.degree_name, cached_geeks.degree_name),
               active_desc=EXCLUDED.active_desc,
               apply_status=COALESCE(EXCLUDED.apply_status, cached_geeks.apply_status),
               friend_relation_status=COALESCE(EXCLUDED.friend_relation_status, cached_geeks.friend_relation_status),
               source_job_id=COALESCE(EXCLUDED.source_job_id, cached_geeks.source_job_id),
               account_name=COALESCE(EXCLUDED.account_name, cached_geeks.account_name),
               raw_card=COALESCE(EXCLUDED.raw_card, cached_geeks.raw_card),
               fetched_at=NOW()""",
        platform, encrypt_geek_id, uid or "", name, city, work_year, salary,
        current_work, school, degree_name, active_desc, apply_status,
        friend_relation_status, source_job_id or "", account_name or "",
        json.dumps(raw_card, ensure_ascii=False) if raw_card is not None else None,
    )


async def upsert_geek_detail(
    platform: str,
    encrypt_geek_id: str,
    *,
    search_security_id: str | None = None,
    detail_security_id: str | None = None,
    encrypt_expect_id: str | None = None,
    authority_id: str | None = None,
    raw_detail: dict | None = None,
) -> None:
    """更新候选人令牌链（boss_geek_info / boss_resume_preview_check 后调用）。"""
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO cached_geek_details
               (platform, encrypt_geek_id, search_security_id, detail_security_id,
                encrypt_expect_id, authority_id, raw_detail, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
           ON CONFLICT (platform, encrypt_geek_id) DO UPDATE SET
               search_security_id=COALESCE(EXCLUDED.search_security_id, cached_geek_details.search_security_id),
               detail_security_id=COALESCE(EXCLUDED.detail_security_id, cached_geek_details.detail_security_id),
               encrypt_expect_id=COALESCE(EXCLUDED.encrypt_expect_id, cached_geek_details.encrypt_expect_id),
               authority_id=COALESCE(EXCLUDED.authority_id, cached_geek_details.authority_id),
               raw_detail=COALESCE(EXCLUDED.raw_detail, cached_geek_details.raw_detail),
               fetched_at=NOW()""",
        platform, encrypt_geek_id,
        search_security_id or "", detail_security_id or "",
        encrypt_expect_id or "", authority_id or "",
        json.dumps(raw_detail, ensure_ascii=False) if raw_detail is not None else None,
    )


async def upsert_geek_search(
    platform: str,
    encrypt_job_id: str,
    keywords: str,
    city_code: str | None,
    page: int,
    *,
    geek_ids: list | None = None,
    has_more: bool = False,
    total_count: int | None = None,
) -> None:
    """记录一页搜索结果（encrypt_job_id + keywords + city + page 为联合唯一键）。"""
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO geek_search_results
               (platform, encrypt_job_id, keywords, city_code, page,
                geek_ids, has_more, total_count, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW())
           ON CONFLICT (platform, encrypt_job_id, keywords, city_code, page) DO UPDATE SET
               geek_ids=EXCLUDED.geek_ids, has_more=EXCLUDED.has_more,
               total_count=EXCLUDED.total_count, fetched_at=NOW()""",
        platform, encrypt_job_id, keywords or "", city_code or "", page,
        json.dumps(geek_ids, ensure_ascii=False) if geek_ids is not None else None,
        has_more, total_count,
    )


async def upsert_boss_contact(
    platform: str,
    encrypt_geek_id: str,
    *,
    encrypt_job_id: str | None = None,
    uid: str | None = None,
    geek_name: str | None = None,
    account_name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    status: str = "entered",
    raw_result: dict | None = None,
) -> None:
    """记录招聘官进入聊天会话的动作（boss_boss_enter 成功后调用）。"""
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO boss_contact_records
               (platform, encrypt_geek_id, encrypt_job_id, uid, geek_name,
                account_name, user_id, session_id, agent_id, status, raw_result, contacted_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())""",
        platform, encrypt_geek_id, encrypt_job_id or "", uid or "",
        geek_name or "", account_name or "", user_id or "",
        session_id or "", agent_id or "", status,
        json.dumps(raw_result, ensure_ascii=False) if raw_result is not None else None,
    )


async def get_cached_job(platform: str, external_id: str) -> dict | None:
    pool = await _get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """SELECT j.*, d.description, d.address, d.longitude, d.latitude,
                  d.detail_security_id, d.raw_detail,
                  (d.id IS NOT NULL) AS has_detail
           FROM cached_jobs j
           LEFT JOIN cached_job_details d
                  ON d.platform = j.platform AND d.external_id = j.external_id
           WHERE j.platform = $1 AND j.external_id = $2""",
        platform, external_id,
    )
    if row is None:
        return None
    return _row_to_dict_pg(row)


async def upsert_chat_record(
    platform: str,
    external_id: str,
    *,
    title: str | None = None,
    company: str | None = None,
    salary: str | None = None,
    city: str | None = None,
    hr_name: str | None = None,
    hr_title: str | None = None,
    encrypt_boss_id: str | None = None,
    chat_security_id: str | None = None,
    account_name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    status: str = "sent",
    raw_result: dict | None = None,
) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO chat_records
               (platform, external_id, title, company, salary, city,
                hr_name, hr_title, encrypt_boss_id, chat_security_id,
                account_name, user_id, session_id, agent_id, status, raw_result, greeted_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,NOW())""",
        platform, external_id, title, company, salary, city,
        hr_name, hr_title, encrypt_boss_id, chat_security_id,
        account_name, user_id, session_id, agent_id, status,
        json.dumps(raw_result, ensure_ascii=False) if raw_result is not None else None,
    )


async def list_chat_records(
    *,
    platform: str | None = None,
    account_name: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    pool = await _get_pg_pool()
    if pool is None:
        return []
    conditions: list[str] = []
    args: list = []
    i = 1

    if platform:
        conditions.append(f"platform = ${i}")
        args.append(platform)
        i += 1
    if account_name:
        conditions.append(f"account_name = ${i}")
        args.append(account_name)
        i += 1
    if keyword:
        conditions.append(f"(title ILIKE ${i} OR company ILIKE ${i})")
        args.append(f"%{keyword}%")
        i += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"""SELECT id, platform, external_id, title, company, salary, city,
                   hr_name, hr_title, account_name, user_id, session_id,
                   agent_id, status, chat_security_id, greeted_at
            FROM chat_records
            {where}
            ORDER BY greeted_at DESC
            LIMIT ${i} OFFSET ${i+1}""",
        *args, limit, offset,
    )
    return [_row_to_dict_pg(r) for r in rows]


async def upsert_recruiter_job(
    platform: str,
    encrypt_job_id: str,
    *,
    account_name: str = "",
    user_id: str = "",
    job_name: str = "",
    city: str = "",
    city_code: str = "",
    area_code: str = "",
    salary_desc: str = "",
    low_salary: int = 0,
    high_salary: int = 0,
    salary_month: int = 12,
    experience_name: str = "",
    degree_name: str = "",
    job_type_name: str = "",
    job_status: int = 0,
    job_audit_status: int = 0,
    view_count: int = 0,
    concat_count: int = 0,
    add_time: int = 0,
    update_time: int = 0,
    raw: dict | None = None,
) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO recruiter_jobs
               (platform, encrypt_job_id, account_name, user_id, job_name,
                city, city_code, area_code, salary_desc, low_salary, high_salary,
                salary_month, experience_name, degree_name, job_type_name,
                job_status, job_audit_status, view_count, concat_count,
                add_time, update_time, raw, fetched_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,NOW())
           ON CONFLICT (platform, encrypt_job_id, account_name) DO UPDATE SET
               job_name=EXCLUDED.job_name, city=EXCLUDED.city, city_code=EXCLUDED.city_code,
               area_code=EXCLUDED.area_code, salary_desc=EXCLUDED.salary_desc,
               low_salary=EXCLUDED.low_salary, high_salary=EXCLUDED.high_salary,
               salary_month=EXCLUDED.salary_month, experience_name=EXCLUDED.experience_name,
               degree_name=EXCLUDED.degree_name, job_type_name=EXCLUDED.job_type_name,
               job_status=EXCLUDED.job_status, job_audit_status=EXCLUDED.job_audit_status,
               view_count=EXCLUDED.view_count, concat_count=EXCLUDED.concat_count,
               add_time=EXCLUDED.add_time, update_time=EXCLUDED.update_time,
               raw=EXCLUDED.raw, fetched_at=NOW()""",
        platform, encrypt_job_id, account_name, user_id, job_name,
        city, city_code, area_code, salary_desc, low_salary, high_salary,
        salary_month, experience_name, degree_name, job_type_name,
        job_status, job_audit_status, view_count, concat_count,
        add_time, update_time,
        json.dumps(raw, ensure_ascii=False) if raw is not None else None,
    )


async def list_recruiter_jobs(
    *,
    platform: str = "boss",
    app_user_id: str | None = None,
    account_name: str | None = None,
    job_status: int | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """查询已缓存的招聘方职位。

    过滤优先级：
      - app_user_id（user_id 列，Boss=userId / LinkedIn=memberId / Indeed=PPID sub）
        —— 平台内唯一，推荐用这个做强隔离。
      - account_name —— 显示名，可能重名；仅在 app_user_id 未知时 fallback。
    """
    pool = await _get_pg_pool()
    if pool is None:
        return []
    conditions: list[str] = ["platform = $1"]
    args: list = [platform]
    i = 2
    if app_user_id:
        conditions.append(f"user_id = ${i}")
        args.append(app_user_id)
        i += 1
    elif account_name:
        conditions.append(f"account_name = ${i}")
        args.append(account_name)
        i += 1
    if job_status is not None:
        conditions.append(f"job_status = ${i}")
        args.append(job_status)
        i += 1
    if keyword:
        conditions.append(f"job_name ILIKE ${i}")
        args.append(f"%{keyword}%")
        i += 1
    where = f"WHERE {' AND '.join(conditions)}"
    rows = await pool.fetch(
        f"""SELECT encrypt_job_id, account_name, job_name, city, city_code, area_code,
                   salary_desc, low_salary, high_salary, experience_name, degree_name,
                   job_type_name, job_status, job_audit_status, view_count, concat_count,
                   add_time, update_time, fetched_at
            FROM recruiter_jobs
            {where}
            ORDER BY update_time DESC
            LIMIT ${i} OFFSET ${i+1}""",
        *args, limit, offset,
    )
    return [_row_to_dict_pg(r) for r in rows]


# ── 云端动态命令配置（Phase 1a）─────────────────────────────────────────────


async def upsert_dynamic_config(
    version: str,
    *,
    chains: dict | None = None,
    dynamic_commands: list | None = None,
    notes: str = "",
    source_ref: str = "",
    created_by: str = "",
) -> None:
    """写入一条动态配置历史。version 是 PK；同 version 重写覆盖（手动回滚场景）。"""
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        """INSERT INTO ext_dynamic_config
               (version, chains, dynamic_commands, notes, source_ref, created_by, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, NOW())
           ON CONFLICT (version) DO UPDATE SET
               chains           = EXCLUDED.chains,
               dynamic_commands = EXCLUDED.dynamic_commands,
               notes            = EXCLUDED.notes,
               source_ref       = EXCLUDED.source_ref,
               created_by       = EXCLUDED.created_by,
               created_at       = NOW()""",
        version,
        json.dumps(chains or {}, ensure_ascii=False),
        json.dumps(dynamic_commands or [], ensure_ascii=False),
        notes or "",
        source_ref or "",
        created_by or "",
    )


async def get_latest_dynamic_config() -> dict | None:
    """最新一条配置；用于扩展 register 时对齐 + admin 面板初始化。"""
    pool = await _get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        """SELECT version, chains, dynamic_commands, notes, source_ref,
                  created_by, created_at
           FROM ext_dynamic_config
           ORDER BY created_at DESC, version DESC
           LIMIT 1"""
    )
    return _row_to_dict_pg(row) if row else None


async def get_dynamic_config_by_version(version: str) -> dict | None:
    """按版本取一条；用于回滚 / 历史详情。"""
    pool = await _get_pg_pool()
    if pool is None or not version:
        return None
    row = await pool.fetchrow(
        """SELECT version, chains, dynamic_commands, notes, source_ref,
                  created_by, created_at
           FROM ext_dynamic_config
           WHERE version = $1""",
        version,
    )
    return _row_to_dict_pg(row) if row else None


async def list_dynamic_configs(limit: int = 50, offset: int = 0) -> list[dict]:
    """历史列表（最新在前），不带 chains/dynamic_commands 全文，避免 admin 列表巨大。"""
    pool = await _get_pg_pool()
    if pool is None:
        return []
    rows = await pool.fetch(
        """SELECT version, notes, source_ref, created_by, created_at,
                  COALESCE(jsonb_array_length(dynamic_commands), 0) AS commands_count,
                  CASE WHEN chains = '{}'::jsonb THEN 0
                       ELSE (SELECT count(*) FROM jsonb_object_keys(chains)) END
                       AS chains_count
           FROM ext_dynamic_config
           ORDER BY created_at DESC, version DESC
           LIMIT $1 OFFSET $2""",
        min(limit, 200), offset,
    )
    return [_row_to_dict_pg(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# 收藏查询/编辑（PRD §4 收藏模块 / Sprint A1-A4）
# ════════════════════════════════════════════════════════════════════════════

# user_job_interests 允许通过 API 编辑的字段（PATCH /bookmarks/:id 仅认这些列）
_BM_JOB_EDITABLE = {"status", "notes", "tags", "match_score", "interested"}
_BM_GEEK_EDITABLE = {"status", "notes", "tags", "match_score", "interested"}


def _build_bookmark_filters(
    base_params: list[Any],
    *,
    platform: str | None,
    status: str | None,
    source: str | None,
    interested_only: bool,
    tag: str | None,
    q: str | None,
    q_fields: list[str],
) -> tuple[str, list[Any]]:
    """共用 WHERE 构造：返回 ' AND xxx AND yyy' 片段 + 扩展后的 params。
    base_params 已包含 [app_user_id]，从 $2 开始追加。"""
    parts: list[str] = []
    params = list(base_params)
    if platform:
        params.append(platform)
        parts.append(f"platform=${len(params)}")
    if status:
        params.append(status)
        parts.append(f"status=${len(params)}")
    if source:
        params.append(source)
        parts.append(f"source=${len(params)}")
    if interested_only:
        parts.append("interested=TRUE")
    if tag:
        # JSONB array contains check: tags @> '["xxx"]'
        params.append(json.dumps([tag]))
        parts.append(f"tags @> ${len(params)}::jsonb")
    if q:
        like = f"%{q}%"
        params.append(like)
        idx = len(params)
        ors = " OR ".join(f"{f} ILIKE ${idx}" for f in q_fields)
        parts.append(f"({ors})")
    where_extra = (" AND " + " AND ".join(parts)) if parts else ""
    return where_extra, params


_BM_JOB_SORT = {"created_at", "updated_at", "match_score"}


async def query_user_job_interests(
    app_user_id: str,
    *,
    platform: str | None = None,
    status: str | None = None,
    source: str | None = None,
    interested_only: bool = False,
    tag: str | None = None,
    q: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    pool = await _get_pg_pool()
    if pool is None:
        return []
    where_extra, params = _build_bookmark_filters(
        [app_user_id],
        platform=platform, status=status, source=source,
        interested_only=interested_only, tag=tag, q=q,
        q_fields=["job_name", "company_name"],
    )
    sort_col = sort if sort in _BM_JOB_SORT else "created_at"
    order_dir = "ASC" if order.lower() == "asc" else "DESC"
    params.append(min(max(limit, 1), 200))
    params.append(max(offset, 0))
    rows = await pool.fetch(
        f"""SELECT * FROM user_job_interests
            WHERE app_user_id=$1{where_extra}
            ORDER BY {sort_col} {order_dir} NULLS LAST, id DESC
            LIMIT ${len(params)-1} OFFSET ${len(params)}""",
        *params,
    )
    return [_row_to_dict_pg(r) for r in rows]


async def count_user_job_interests(
    app_user_id: str,
    *,
    platform: str | None = None,
    status: str | None = None,
    source: str | None = None,
    interested_only: bool = False,
    tag: str | None = None,
    q: str | None = None,
) -> int:
    pool = await _get_pg_pool()
    if pool is None:
        return 0
    where_extra, params = _build_bookmark_filters(
        [app_user_id],
        platform=platform, status=status, source=source,
        interested_only=interested_only, tag=tag, q=q,
        q_fields=["job_name", "company_name"],
    )
    row = await pool.fetchrow(
        f"SELECT COUNT(*) AS n FROM user_job_interests WHERE app_user_id=$1{where_extra}",
        *params,
    )
    return int(row["n"]) if row else 0


async def query_recruiter_geek_interests(
    app_user_id: str,
    *,
    platform: str | None = None,
    status: str | None = None,
    source: str | None = None,
    interested_only: bool = False,
    tag: str | None = None,
    q: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    pool = await _get_pg_pool()
    if pool is None:
        return []
    where_extra, params = _build_bookmark_filters(
        [app_user_id],
        platform=platform, status=status, source=source,
        interested_only=interested_only, tag=tag, q=q,
        q_fields=["geek_name"],
    )
    sort_col = sort if sort in _BM_JOB_SORT else "created_at"
    order_dir = "ASC" if order.lower() == "asc" else "DESC"
    params.append(min(max(limit, 1), 200))
    params.append(max(offset, 0))
    rows = await pool.fetch(
        f"""SELECT * FROM recruiter_geek_interests
            WHERE app_user_id=$1{where_extra}
            ORDER BY {sort_col} {order_dir} NULLS LAST, id DESC
            LIMIT ${len(params)-1} OFFSET ${len(params)}""",
        *params,
    )
    return [_row_to_dict_pg(r) for r in rows]


async def count_recruiter_geek_interests(
    app_user_id: str,
    *,
    platform: str | None = None,
    status: str | None = None,
    source: str | None = None,
    interested_only: bool = False,
    tag: str | None = None,
    q: str | None = None,
) -> int:
    pool = await _get_pg_pool()
    if pool is None:
        return 0
    where_extra, params = _build_bookmark_filters(
        [app_user_id],
        platform=platform, status=status, source=source,
        interested_only=interested_only, tag=tag, q=q,
        q_fields=["geek_name"],
    )
    row = await pool.fetchrow(
        f"SELECT COUNT(*) AS n FROM recruiter_geek_interests WHERE app_user_id=$1{where_extra}",
        *params,
    )
    return int(row["n"]) if row else 0


async def get_user_job_interest_by_id(app_user_id: str, bm_id: int) -> dict | None:
    pool = await _get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT * FROM user_job_interests WHERE app_user_id=$1 AND id=$2",
        app_user_id, bm_id,
    )
    return _row_to_dict_pg(row) if row else None


async def get_recruiter_geek_interest_by_id(app_user_id: str, bm_id: int) -> dict | None:
    pool = await _get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT * FROM recruiter_geek_interests WHERE app_user_id=$1 AND id=$2",
        app_user_id, bm_id,
    )
    return _row_to_dict_pg(row) if row else None


def _bookmark_set_clauses(
    fields: dict[str, Any], editable: set[str], jsonb_keys: set[str],
) -> tuple[str, list[Any]]:
    """构造 UPDATE ... SET 子句，只允许 editable 集合内的列。"""
    cols: list[str] = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k not in editable:
            continue
        if k in jsonb_keys:
            vals.append(json.dumps(v if v is not None else []))
        else:
            vals.append(v)
        cols.append(f"{k}=${len(vals)}")
    if not cols:
        return "", []
    return ", ".join(cols), vals


async def update_user_job_interest_by_id(app_user_id: str, bm_id: int, **fields: Any) -> dict | None:
    set_clause, vals = _bookmark_set_clauses(fields, _BM_JOB_EDITABLE, {"tags"})
    if not set_clause:
        return await get_user_job_interest_by_id(app_user_id, bm_id)
    pool = await _get_pg_pool()
    if pool is None:
        return None
    vals.extend([app_user_id, bm_id])
    row = await pool.fetchrow(
        f"""UPDATE user_job_interests SET {set_clause}, updated_at=NOW()
            WHERE app_user_id=${len(vals)-1} AND id=${len(vals)} RETURNING *""",
        *vals,
    )
    return _row_to_dict_pg(row) if row else None


async def update_recruiter_geek_interest_by_id(app_user_id: str, bm_id: int, **fields: Any) -> dict | None:
    set_clause, vals = _bookmark_set_clauses(fields, _BM_GEEK_EDITABLE, {"tags"})
    if not set_clause:
        return await get_recruiter_geek_interest_by_id(app_user_id, bm_id)
    pool = await _get_pg_pool()
    if pool is None:
        return None
    vals.extend([app_user_id, bm_id])
    row = await pool.fetchrow(
        f"""UPDATE recruiter_geek_interests SET {set_clause}, updated_at=NOW()
            WHERE app_user_id=${len(vals)-1} AND id=${len(vals)} RETURNING *""",
        *vals,
    )
    return _row_to_dict_pg(row) if row else None


async def delete_user_job_interest_by_id(app_user_id: str, bm_id: int) -> bool:
    pool = await _get_pg_pool()
    if pool is None:
        return False
    result = await pool.execute(
        "DELETE FROM user_job_interests WHERE app_user_id=$1 AND id=$2",
        app_user_id, bm_id,
    )
    # asyncpg execute() 返回 "DELETE <n>"
    return result.endswith(" 1") if isinstance(result, str) else False


async def delete_recruiter_geek_interest_by_id(app_user_id: str, bm_id: int) -> bool:
    pool = await _get_pg_pool()
    if pool is None:
        return False
    result = await pool.execute(
        "DELETE FROM recruiter_geek_interests WHERE app_user_id=$1 AND id=$2",
        app_user_id, bm_id,
    )
    return result.endswith(" 1") if isinstance(result, str) else False


# ════════════════════════════════════════════════════════════════════════════
# Billing：订阅 / Credit / Stripe events（PRD §订阅+Credit / Sprint B1-B6）
# ════════════════════════════════════════════════════════════════════════════

async def get_subscription(app_user_id: str) -> dict | None:
    pool = await _get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT * FROM user_subscriptions WHERE app_user_id=$1",
        app_user_id,
    )
    return _row_to_dict_pg(row) if row else None


async def get_subscription_by_customer(stripe_customer_id: str) -> dict | None:
    pool = await _get_pg_pool()
    if pool is None or not stripe_customer_id:
        return None
    row = await pool.fetchrow(
        "SELECT * FROM user_subscriptions WHERE stripe_customer_id=$1 LIMIT 1",
        stripe_customer_id,
    )
    return _row_to_dict_pg(row) if row else None


_SUB_ALLOWED = {
    "stripe_customer_id", "stripe_subscription_id", "plan", "status",
    "current_period_end", "cancel_at_period_end",
}


async def upsert_subscription(app_user_id: str, **fields: Any) -> dict | None:
    """插入或更新订阅。仅接受 _SUB_ALLOWED 内的字段。"""
    pool = await _get_pg_pool()
    if pool is None:
        return None
    payload = {k: v for k, v in fields.items() if k in _SUB_ALLOWED}
    cols = ["app_user_id"] + list(payload.keys())
    vals = [app_user_id] + list(payload.values())
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    update_sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in payload.keys())
    if update_sets:
        update_sets += ", updated_at=NOW()"
    else:
        update_sets = "updated_at=NOW()"
    row = await pool.fetchrow(
        f"""INSERT INTO user_subscriptions ({', '.join(cols)}) VALUES ({placeholders})
            ON CONFLICT (app_user_id) DO UPDATE SET {update_sets}
            RETURNING *""",
        *vals,
    )
    return _row_to_dict_pg(row) if row else None


async def get_credit_account(app_user_id: str, *, create_if_missing: bool = True) -> dict | None:
    """读取 credit 账户。create_if_missing=True 时若不存在则插入免费版默认值（20cr/天）。
    注意：此函数不做 refill —— refill 由 billing.credit_ledger.refill_if_due 显式触发。"""
    pool = await _get_pg_pool()
    if pool is None:
        return None
    row = await pool.fetchrow(
        "SELECT * FROM user_credits WHERE app_user_id=$1",
        app_user_id,
    )
    if row is None and create_if_missing:
        row = await pool.fetchrow(
            """INSERT INTO user_credits (app_user_id, balance, daily_grant)
               VALUES ($1, 20, 20)
               ON CONFLICT (app_user_id) DO NOTHING
               RETURNING *""",
            app_user_id,
        )
        if row is None:
            row = await pool.fetchrow(
                "SELECT * FROM user_credits WHERE app_user_id=$1",
                app_user_id,
            )
    return _row_to_dict_pg(row) if row else None


async def credit_charge(
    app_user_id: str,
    delta: int,
    reason: str,
    *,
    sku: str = "",
    stripe_event_id: str = "",
    meta: dict | None = None,
    is_topup: bool = False,
    refill_pack: dict | None = None,
) -> dict:
    """原子地变动 credit 余额，并写入 ledger。

    - delta > 0：入账（grant_monthly / grant_daily / topup / refund / adjust+）
    - delta < 0：扣费（必须 balance + delta >= 0，否则返回 allowed=False，不写 ledger）
    - is_topup=True：同时增加 topup_balance + lifetime_purchased
    - refill_pack：{ 'monthly_grant'? , 'daily_grant'? , 'reset_kind': 'monthly'|'daily' }
                   配合 grant_monthly / grant_daily 使用，同步刷 reset 日期

    Returns: { 'allowed': bool, 'balance_before': int, 'balance_after': int, 'ledger_id': int|None }
    """
    pool = await _get_pg_pool()
    if pool is None:
        return {"allowed": False, "balance_before": 0, "balance_after": 0, "ledger_id": None}
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 锁行避免并发扣费超支
            row = await conn.fetchrow(
                "SELECT * FROM user_credits WHERE app_user_id=$1 FOR UPDATE",
                app_user_id,
            )
            if row is None:
                # 自动建账（同 get_credit_account 行为）
                row = await conn.fetchrow(
                    """INSERT INTO user_credits (app_user_id, balance, daily_grant)
                       VALUES ($1, 20, 20) RETURNING *""",
                    app_user_id,
                )
            balance_before = int(row["balance"])
            topup_before = int(row["topup_balance"])
            new_balance = balance_before + delta
            if new_balance < 0:
                return {
                    "allowed": False,
                    "balance_before": balance_before,
                    "balance_after": balance_before,
                    "ledger_id": None,
                }
            # 计算 topup_balance 联动
            new_topup = topup_before
            if is_topup and delta > 0:
                new_topup = topup_before + delta
            elif delta < 0:
                # 扣费优先扣"套餐余量"，topup 留到最后
                package_balance = max(balance_before - topup_before, 0)
                if package_balance + delta < 0:
                    # 套餐扣完了，余下从 topup 扣
                    new_topup = max(topup_before + (package_balance + delta), 0)
            # refill 处理（按 reset_kind 同步月/日重置标记）
            extra_set = ""
            extra_vals: list[Any] = []
            if refill_pack:
                rk = refill_pack.get("reset_kind", "")
                if rk == "monthly":
                    extra_set += ", last_monthly_reset=CURRENT_DATE"
                elif rk == "daily":
                    extra_set += ", last_daily_reset=CURRENT_DATE"
                for k in ("monthly_grant", "daily_grant"):
                    if k in refill_pack:
                        extra_vals.append(int(refill_pack[k]))
                        # 基础 SQL 占用 $1 (app_user_id), $2 (new_balance), $3 (new_topup)，
                        # extra_vals 从 $4 起，第 N 次 append 后 len=N，对应 $(3+N)
                        extra_set += f", {k}=${3 + len(extra_vals)}"
            lifetime_purchased_delta = delta if (is_topup and delta > 0) else 0
            lifetime_consumed_delta = -delta if delta < 0 else 0
            await conn.execute(
                f"""UPDATE user_credits
                    SET balance=$2, topup_balance=$3,
                        lifetime_purchased=lifetime_purchased+{lifetime_purchased_delta},
                        lifetime_consumed=lifetime_consumed+{lifetime_consumed_delta},
                        updated_at=NOW(){extra_set}
                    WHERE app_user_id=$1""",
                app_user_id, new_balance, new_topup, *extra_vals,
            )
            ledger_id = await conn.fetchval(
                """INSERT INTO credit_ledger
                    (app_user_id, delta, reason, sku, balance_after, stripe_event_id, meta)
                   VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                app_user_id, delta, reason, sku, new_balance,
                stripe_event_id, json.dumps(meta or {}, ensure_ascii=False),
            )
    return {
        "allowed": True,
        "balance_before": balance_before,
        "balance_after": new_balance,
        "ledger_id": int(ledger_id) if ledger_id else None,
    }


async def list_credit_ledger(app_user_id: str, *, limit: int = 50, offset: int = 0) -> list[dict]:
    pool = await _get_pg_pool()
    if pool is None:
        return []
    rows = await pool.fetch(
        """SELECT id, delta, reason, sku, balance_after, stripe_event_id, meta, created_at
           FROM credit_ledger WHERE app_user_id=$1
           ORDER BY created_at DESC, id DESC LIMIT $2 OFFSET $3""",
        app_user_id, min(max(limit, 1), 200), max(offset, 0),
    )
    return [_row_to_dict_pg(r) for r in rows]


async def record_stripe_event(event_id: str, event_type: str, payload: dict | str) -> bool:
    """幂等写入 Stripe 事件。返回 True 表示新事件，False 表示重放（已存在）。"""
    pool = await _get_pg_pool()
    if pool is None or not event_id:
        return False
    payload_json = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    result = await pool.execute(
        """INSERT INTO stripe_events (event_id, event_type, payload)
           VALUES ($1, $2, $3::jsonb)
           ON CONFLICT (event_id) DO NOTHING""",
        event_id, event_type, payload_json,
    )
    # asyncpg execute() 返回 "INSERT 0 1" 表示插入成功，"INSERT 0 0" 表示冲突未插入
    return isinstance(result, str) and result.endswith(" 1")


async def mark_stripe_event_processed(event_id: str, error: str = "") -> None:
    pool = await _get_pg_pool()
    if pool is None or not event_id:
        return
    await pool.execute(
        """UPDATE stripe_events SET processed_at=NOW(), process_error=$2
           WHERE event_id=$1""",
        event_id, error or "",
    )


# ── 管理后台账号（admin_users 表）────────────────────────────────────────────
# 管理后台登录账号。密码以 bcrypt 哈希存储;ADMIN_PASSWORD 仅作首次播种种子。

async def count_admin_users() -> int:
    pool = await _get_pg_pool()
    if pool is None:
        return 0
    return int(await pool.fetchval("SELECT COUNT(*) FROM admin_users") or 0)


async def get_admin_user(username: str) -> dict | None:
    pool = await _get_pg_pool()
    if pool is None or not username:
        return None
    row = await pool.fetchrow("SELECT * FROM admin_users WHERE username=$1", username)
    return dict(row) if row else None


async def list_admin_users() -> list[dict]:
    pool = await _get_pg_pool()
    if pool is None:
        return []
    rows = await pool.fetch(
        "SELECT id, username, created_at, last_login_at FROM admin_users ORDER BY id"
    )
    return [dict(r) for r in rows]


async def create_admin_user(username: str, password_hash: str) -> dict:
    pool = await _get_pg_pool()
    if pool is None:
        raise RuntimeError("db_unavailable")
    row = await pool.fetchrow(
        "INSERT INTO admin_users (username, password_hash, created_at) "
        "VALUES ($1, $2, $3) RETURNING id, username, created_at",
        username, password_hash, _utc_now(),
    )
    return dict(row)


async def delete_admin_user(user_id: int) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute("DELETE FROM admin_users WHERE id=$1", user_id)


async def set_admin_password(username: str, password_hash: str) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        "UPDATE admin_users SET password_hash=$1 WHERE username=$2",
        password_hash, username,
    )


async def touch_admin_login(username: str) -> None:
    pool = await _get_pg_pool()
    if pool is None:
        return
    await pool.execute(
        "UPDATE admin_users SET last_login_at=$1 WHERE username=$2",
        _utc_now(), username,
    )
