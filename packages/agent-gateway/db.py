"""
job-agent-gateway 对话历史持久化 — asyncpg + PostgreSQL。

表：
  agent_conv_sessions  每次 WebSocket 连接一行（user 连入/断开各一次）
  agent_conv_events    该连接内每条消息/工具调用一行

环境变量：
  AGENT_DB_URL  默认复用 job-api-gateway 的 DB_POSTGRES_URL，
                再 fallback 到 postgresql://postgres:password@localhost:5432/boss_gateway
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import asyncpg  # type: ignore

import config


def _parse_iso_tz(s: str) -> datetime | None:
    """把 admin 前端传的 ISO 字符串转成 timezone-aware datetime,asyncpg 要求。

    兼容 'Z' 后缀(JS toISOString() 输出)和 '+00:00' 后缀。失败返 None,
    上层把 None filter 掉(等价于不加 since_iso 条件)。
    """
    if not s:
        return None
    try:
        # JS Date.toISOString() 用 'Z',Python 3.10 fromisoformat 不识别
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

_AGENT_DB_URL = config.AGENT_DB_URL

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(_AGENT_DB_URL, min_size=2, max_size=32)
    return _pool


async def get_pool_stats() -> dict:
    """返回 asyncpg 连接池指标。"""
    pool = await _get_pool()
    return {
        "size": pool.get_size(),
        "min_size": pool.get_min_size(),
        "max_size": pool.get_max_size(),
        "free_size": pool.get_idle_size(),
    }


# ── Schema (Phase 2 — per-platform sessions) ─────────────────────────────────
# 每个 (user_id, role, platform) 三元组对应**一个**持久化 session 行(UNIQUE)。
# 同一用户最多 6 个 session(2 role × 3 platform)。lazy-spawn,idle sweep 关闭。
# 历史(messages)和事件(events)挂在 session_id 下,role/platform 冗余存储方便聚合查询。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_conv_sessions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT        NOT NULL,
    role            TEXT        NOT NULL CHECK (role IN ('jobseeker', 'recruiter')),
    platform        TEXT        NOT NULL CHECK (length(platform) > 0),
    app_user_id     TEXT,
    title           TEXT,
    primary_mode    TEXT,
    mode_switches   INTEGER     NOT NULL DEFAULT 0,
    user_tier       TEXT,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    UNIQUE (user_id, role, platform)
);
CREATE INDEX IF NOT EXISTS idx_acs_user
    ON agent_conv_sessions (user_id, last_active_at DESC);
CREATE INDEX IF NOT EXISTS idx_acs_idle
    ON agent_conv_sessions (last_active_at)
    WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS agent_conv_events (
    id              BIGSERIAL PRIMARY KEY,
    -- session_id 允许 NULL: 简历上传等 pre-session 事件没有绑定 session 也能记 funnel
    session_id      BIGINT REFERENCES agent_conv_sessions(id) ON DELETE CASCADE,
    user_id         TEXT        NOT NULL,
    role            TEXT        NOT NULL,
    platform        TEXT        NOT NULL,
    event_type      TEXT        NOT NULL,
    turn_index      INTEGER     NOT NULL DEFAULT 0,
    tool_name       TEXT,
    content         TEXT,
    ok              BOOLEAN,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_creation_input_tokens INTEGER,
    cache_read_input_tokens     INTEGER,
    cost_usd        DOUBLE PRECISION,
    duration_ms     REAL,
    mode            TEXT,
    payload         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ace_session
    ON agent_conv_events (session_id, id);
CREATE INDEX IF NOT EXISTS idx_ace_user_event
    ON agent_conv_events (user_id, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ace_platform_date
    ON agent_conv_events (platform, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ace_role_platform
    ON agent_conv_events (user_id, role, platform, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ace_funnel_unique
    ON agent_conv_events (user_id, platform, tool_name)
    WHERE event_type = 'funnel';

CREATE TABLE IF NOT EXISTS agent_conv_messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  BIGINT NOT NULL REFERENCES agent_conv_sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,        -- 'user' | 'assistant'(LLM 消息层 role,不是身份 role)
    content     JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_acm_session ON agent_conv_messages (session_id, seq);

-- ── Phase B: 长任务 (Task Workflow) ────────────────────────────────────
-- 用户一键启动 → 后台跑多个 step → 出汇总报告。每个 task 对应一次执行。
-- 同 (user_id, platform) 一次只跑一个 running task(unique partial index 强制),
-- 跨 platform 允许并行(同 user 最多 3 个并发,boss/linkedin/indeed 各 1)。
CREATE TABLE IF NOT EXISTS agent_tasks (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT        NOT NULL,
    role            TEXT        NOT NULL CHECK (role IN ('jobseeker', 'recruiter')),
    platform        TEXT        NOT NULL CHECK (length(platform) > 0),
    template_id     TEXT        NOT NULL,        -- 'jobseeker:find_best_jobs' 等
    title           TEXT,                         -- 用户能读的任务名
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','paused_user_action',
                                       'completed','failed','cancelled')),
    inputs          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    progress        JSONB       NOT NULL DEFAULT '{}'::jsonb,
                                                   -- {step,current,total,log_msg,...}
    -- 暂停信息(status='paused_user_action' 时填)
    paused_signal   TEXT,                         -- 'boss:captcha' / 'linkedin:region_block' / ...
    paused_kind     TEXT NOT NULL DEFAULT '',     -- '' / 'verification' (风控信号) / 'user' (用户主动 pause)
    paused_at_idx   INTEGER,                      -- 暂停时停在第几个 item(用于断点续跑)
    -- 中途跳过的 item 列表 [{idx, reason}]
    skipped         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    -- 完成结果
    result_summary  TEXT,                         -- markdown 报告
    artifacts       JSONB       NOT NULL DEFAULT '{}'::jsonb,
                                                   -- {top_jobs:[...], drafts:[...]}
    -- 详情页用户操作状态(per item):{<item_id>: {viewed_at, starred, ...}}
    item_states     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT,
    -- timing
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    paused_at       TIMESTAMPTZ,
    resumed_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    cancelled_at    TIMESTAMPTZ,
    -- heartbeat (TaskRunner 每 30s 更新,> 60s 没动 → 视为僵尸,admin 可 force-fail)
    heartbeat_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 同 (user_id, platform) 一次只能有一个 running 或 paused 任务(防雪崩)
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_running_unique
    ON agent_tasks (user_id, platform)
    WHERE status IN ('running', 'paused_user_action');
CREATE INDEX IF NOT EXISTS idx_tasks_user_created
    ON agent_tasks (user_id, role, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON agent_tasks (status, heartbeat_at)
    WHERE status IN ('running', 'paused_user_action');
-- 老库兼容:paused_kind 字段后增,旧 row paused_kind=''(表示 legacy 暂停,语义同 'verification')
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS paused_kind TEXT NOT NULL DEFAULT '';

-- ── MCP 调用观测日志(MVP 5 P4 观测面)──────────────────────────────
-- 每次 TaskMcpSession.call → 1 行,记 user/platform/tool/op/duration/success/risk_signal。
-- 给 admin 看每用户每平台调用速率 + 风控命中曲线,排查 abuse 用户 / 风控热点。
-- 保留 7 天(后台 sweep 清理),数据量按每天 1 万次估算 = 7 万行,完全可接受。
CREATE TABLE IF NOT EXISTS mcp_call_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    tool_name   TEXT NOT NULL,
    op_type     TEXT NOT NULL DEFAULT 'other',  -- search/detail/chat/list/other
    duration_ms INTEGER NOT NULL DEFAULT 0,
    success     BOOLEAN NOT NULL,
    risk_signal TEXT                              -- boss:code_37 / linkedin:429 / 等
);
CREATE INDEX IF NOT EXISTS idx_mcp_log_ts ON mcp_call_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_log_user_ts ON mcp_call_log (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_log_platform_ts ON mcp_call_log (platform, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_log_risk_ts
    ON mcp_call_log (risk_signal, ts DESC) WHERE risk_signal IS NOT NULL;

-- ── 前端 JS 错误上报(audit P1 fix)─────────────────────────────────
-- 之前 sidepanel 崩了后端完全看不见 — 现在前端 window.onerror /
-- unhandledrejection → POST /errors/client → 这张表。admin 后台扫每天
-- error 数 / message 聚合 → 知道哪个 ext 版本在哪条路径上炸。
-- 保留 30 天(后台 sweep 清理),量预估 < 1000/天。
CREATE TABLE IF NOT EXISTS client_errors (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    ext_id      TEXT NOT NULL DEFAULT '',          -- chrome runtime.id
    ext_version TEXT NOT NULL DEFAULT '',
    surface     TEXT NOT NULL DEFAULT 'sidepanel', -- sidepanel/popup/options/background
    error_type  TEXT NOT NULL DEFAULT 'error',     -- error / unhandledrejection
    message     TEXT NOT NULL,
    stack       TEXT,                               -- 截断到 4kb
    src_file    TEXT,                               -- error 事件 filename
    src_line    INTEGER,
    src_col     INTEGER,
    user_agent  TEXT,
    url         TEXT                                -- location.href(隐私无关,只 chrome-extension://...)
);
CREATE INDEX IF NOT EXISTS idx_client_errors_ts ON client_errors (ts DESC);
CREATE INDEX IF NOT EXISTS idx_client_errors_user_ts ON client_errors (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_client_errors_msg_ts
    ON client_errors (left(message, 100), ts DESC);

-- ── 快捷 chip 配置(后端动态控制 sidepanel 底部快捷指令)──────────────
-- 数据 / 策略分离:本表存"有哪些 chip"(数据,admin 后台 CRUD)
-- 规则 condition 在代码 personalization/chips.py(策略,工程改)
-- 规则 action 用 key 引用本表行
CREATE TABLE IF NOT EXISTS chip_configs (
    id              BIGSERIAL PRIMARY KEY,
    key             TEXT NOT NULL,             -- "search_jobs" 等,代码规则 by key 引用
    role            TEXT NOT NULL,             -- jobseeker / recruiter
    platform        TEXT NOT NULL,             -- boss / linkedin / indeed
    label_zh        TEXT NOT NULL,
    label_en        TEXT NOT NULL,
    send_text_zh    TEXT NOT NULL,             -- 点击后发送的文本(可与 label 不同)
    send_text_en    TEXT NOT NULL,
    icon            TEXT NOT NULL DEFAULT '',  -- 可选 emoji,如 "🔍"
    weight          INTEGER NOT NULL DEFAULT 50,    -- 0-100,规则用 +/- 调
    grp             TEXT NOT NULL DEFAULT 'default',-- default / onboarding / shortcut / task
    enabled         BOOLEAN NOT NULL DEFAULT true,
    sort_order      INTEGER NOT NULL DEFAULT 0,    -- 同 weight 内手动排
    description     TEXT NOT NULL DEFAULT '',      -- admin 自留备注
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      TEXT NOT NULL DEFAULT 'system',
    UNIQUE(key, role, platform)
);
CREATE INDEX IF NOT EXISTS idx_chip_configs_lookup
    ON chip_configs (role, platform, enabled);

-- 单行 version 表 — 每次 admin 写就 +1,前端 / personalizer 用作缓存戳
CREATE TABLE IF NOT EXISTS chip_configs_version (
    id          INTEGER PRIMARY KEY,           -- 永远 = 1
    version     BIGINT NOT NULL DEFAULT 1,
    bumped_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO chip_configs_version (id, version) VALUES (1, 1)
    ON CONFLICT (id) DO NOTHING;

-- chip 点击埋点 — 用户每点一次快捷指令记一行,后续 personalizer 可学习权重.
-- 保留 30 天(后台 sweep 清理),量预估 < 10000/天。
CREATE TABLE IF NOT EXISTS chip_click_events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    chip_key    TEXT NOT NULL,                  -- 对应 chip_configs.key
    chip_label  TEXT NOT NULL DEFAULT '',       -- 点击时的 label(便于反查改文案前的)
    lang        TEXT NOT NULL DEFAULT 'zh'
);
CREATE INDEX IF NOT EXISTS idx_chip_clicks_ts ON chip_click_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_chip_clicks_key_ts
    ON chip_click_events (chip_key, role, platform, ts DESC);
CREATE INDEX IF NOT EXISTS idx_chip_clicks_user_ts
    ON chip_click_events (user_id, ts DESC);
"""


async def init_db() -> None:
    """启动时创建表 + 索引。新装环境直接 CREATE,无 ALTER 兼容(开发期已 reset)。"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)


async def reset_schema() -> None:
    """开发期一次性脚本:DROP & 重建 3 张 agent 表(含所有数据丢失)。
    生产环境**不要**调。供 `python -c 'import asyncio,db; asyncio.run(db.reset_schema())'` 使用。
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            DROP TABLE IF EXISTS agent_conv_messages CASCADE;
            DROP TABLE IF EXISTS agent_conv_events CASCADE;
            DROP TABLE IF EXISTS agent_conv_sessions CASCADE;
        """)
        await conn.execute(_SCHEMA)


# ── session lifecycle ─────────────────────────────────────────────────────────

async def get_or_create_session_id(
    user_id: str,
    role: str,
    platform: str,
    *,
    app_user_id: str = "",
    user_tier: str = "",
) -> int:
    """Per-platform session 模型: 三元组唯一,已存在返已存在 id 并 bump last_active_at。

    role: 'jobseeker' | 'recruiter'  (extension 硬钉)
    platform: 'boss' | 'linkedin' | 'indeed' (前端 sub-tab)

    - 没找到 → INSERT 新行,返新 id
    - 找到了 → UPDATE last_active_at = NOW(),返已有 id
    """
    if role not in ("jobseeker", "recruiter"):
        raise ValueError(f"invalid role: {role!r}")
    if platform not in ("boss", "linkedin", "indeed"):
        raise ValueError(f"invalid platform: {platform!r}")

    pool = await _get_pool()
    # ON CONFLICT DO UPDATE 是原子 upsert,避免 SELECT-then-INSERT 的 race
    row = await pool.fetchrow(
        """INSERT INTO agent_conv_sessions
              (user_id, role, platform, app_user_id, user_tier)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (user_id, role, platform) DO UPDATE SET
              last_active_at = NOW(),
              updated_at     = NOW(),
              -- ended_at 之前关过的话,这次 reopen 清掉
              ended_at = NULL,
              -- 这两个字段允许更新到最新值
              app_user_id = COALESCE(EXCLUDED.app_user_id, agent_conv_sessions.app_user_id),
              user_tier   = COALESCE(EXCLUDED.user_tier,   agent_conv_sessions.user_tier)
           RETURNING id""",
        user_id, role, platform,
        app_user_id or None,
        user_tier or None,
    )
    return row["id"]


async def get_session_by_key(user_id: str, role: str, platform: str) -> dict | None:
    """读取 (user_id, role, platform) 对应的 session 行,返 None 表示不存在。"""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """SELECT id, user_id, role, platform, app_user_id, title, primary_mode,
                  mode_switches, user_tier, metadata,
                  created_at, updated_at, last_active_at, ended_at
             FROM agent_conv_sessions
            WHERE user_id = $1 AND role = $2 AND platform = $3""",
        user_id, role, platform,
    )
    return _row_to_dict(row) if row else None


async def touch_session_active(session_id: int) -> None:
    """活跃心跳:bump last_active_at + updated_at。每次 turn 开始时调。"""
    pool = await _get_pool()
    await pool.execute(
        "UPDATE agent_conv_sessions SET last_active_at = NOW(), updated_at = NOW() WHERE id = $1",
        session_id,
    )


async def update_session_title(session_id: int, title: str) -> None:
    pool = await _get_pool()
    await pool.execute(
        "UPDATE agent_conv_sessions SET title = $1, updated_at = NOW() WHERE id = $2",
        title or None,
        session_id,
    )


async def close_session(session_id: int) -> None:
    """标记 session 关闭(idle sweep 用)。不删数据,history 保留供下次 reopen。"""
    pool = await _get_pool()
    await pool.execute(
        "UPDATE agent_conv_sessions SET ended_at = NOW(), updated_at = NOW() WHERE id = $1",
        session_id,
    )


async def list_idle_sessions(idle_seconds: int = 1800) -> list[dict]:
    """列出 last_active_at 比阈值早的活跃(ended_at IS NULL)session,供 idle sweep 调用。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT id, user_id, role, platform, last_active_at
             FROM agent_conv_sessions
            WHERE ended_at IS NULL
              AND last_active_at < NOW() - make_interval(secs => $1)""",
        int(idle_seconds),
    )
    return [_row_to_dict(r) for r in rows]


# ── task lifecycle (Phase B) ──────────────────────────────────────────────────

async def create_task(
    *,
    user_id: str,
    role: str,
    platform: str,
    template_id: str,
    title: str = "",
    inputs: dict | None = None,
) -> int:
    """创建新任务。同 (user_id, platform) 已有 running/paused 时抛 unique violation
    → caller 应捕获转换为友好"已有任务跑着,等它完"提示。"""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """INSERT INTO agent_tasks
              (user_id, role, platform, template_id, title, status, inputs)
           VALUES ($1, $2, $3, $4, $5, 'pending', $6::jsonb)
           RETURNING id""",
        user_id, role, platform, template_id, title or template_id,
        json.dumps(inputs or {}, ensure_ascii=False),
    )
    return row["id"]


async def get_task(task_id: int) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM agent_tasks WHERE id=$1", task_id)
    return _row_to_dict(row) if row else None


async def update_task_status(task_id: int, status: str, **fields) -> None:
    """通用 status 更新 + 可选其它字段。fields 支持: error, paused_signal,
    paused_at_idx, started_at, paused_at, resumed_at, completed_at, cancelled_at,
    progress, result_summary, artifacts, skipped, heartbeat_at。
    JSONB 字段(progress/artifacts/skipped)自动 json.dumps。"""
    pool = await _get_pool()
    sets = ["status = $1"]
    params: list = [status]
    JSONB_FIELDS = {"progress", "artifacts", "skipped", "item_states"}
    for k, v in fields.items():
        params.append(json.dumps(v, ensure_ascii=False) if k in JSONB_FIELDS else v)
        if k in JSONB_FIELDS:
            sets.append(f"{k} = ${len(params)}::jsonb")
        else:
            sets.append(f"{k} = ${len(params)}")
    # 任何 update 都 bump heartbeat 以维护"活着"信号
    sets.append("heartbeat_at = NOW()")
    params.append(task_id)
    await pool.execute(
        f"UPDATE agent_tasks SET {', '.join(sets)} WHERE id = ${len(params)}",
        *params,
    )


async def heartbeat_task(task_id: int) -> None:
    """TaskRunner 每 30s 调,标记任务还活着(防 backend 崩 → 任务卡 running)。"""
    pool = await _get_pool()
    await pool.execute("UPDATE agent_tasks SET heartbeat_at = NOW() WHERE id=$1", task_id)


async def patch_task_item_state(task_id: int, item_id: str, patch: dict) -> dict | None:
    """原子 merge `patch` 到 `agent_tasks.item_states[item_id]`。
    用 jsonb_set 避免覆盖其它 item;不存在的 key 自动创建。
    返回更新后的整个 item_states dict(给 caller 回写前端用),task 不存在返 None。"""
    if not item_id:
        return None
    pool = await _get_pool()
    payload_json = json.dumps(patch or {}, ensure_ascii=False)
    row = await pool.fetchrow(
        """UPDATE agent_tasks
              SET item_states = jsonb_set(
                  coalesce(item_states, '{}'::jsonb),
                  ARRAY[$2::text],
                  coalesce(item_states->$2, '{}'::jsonb) || $3::jsonb,
                  true
              )
            WHERE id = $1
        RETURNING item_states""",
        task_id, item_id, payload_json,
    )
    if not row:
        return None
    states = row["item_states"]
    if isinstance(states, str):
        try: states = json.loads(states)
        except Exception: states = {}
    return states or {}


async def list_tasks_for_user(
    *,
    user_id: str,
    role: str | None = None,
    platform: str | None = None,
    status_filter: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    """列任务,默认按 created_at desc。status_filter 传 ['running','paused_user_action']
    可只拿"进行中"。"""
    pool = await _get_pool()
    conds = ["user_id = $1"]
    params: list = [user_id]
    if role:
        params.append(role); conds.append(f"role = ${len(params)}")
    if platform:
        params.append(platform); conds.append(f"platform = ${len(params)}")
    if status_filter:
        params.append(status_filter); conds.append(f"status = ANY(${len(params)}::text[])")
    params.append(limit)
    rows = await pool.fetch(
        f"""SELECT id, user_id, role, platform, template_id, title, status,
                   inputs, progress, paused_signal, paused_at_idx, skipped,
                   result_summary, artifacts, item_states, error,
                   created_at, started_at, paused_at, resumed_at,
                   completed_at, cancelled_at, heartbeat_at
              FROM agent_tasks
             WHERE {' AND '.join(conds)}
             ORDER BY created_at DESC
             LIMIT ${len(params)}""",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def count_running_tasks_for_user(user_id: str) -> int:
    """全局并发上限检查:该 user 当前有多少 running/paused 任务。"""
    pool = await _get_pool()
    return await pool.fetchval(
        """SELECT count(*) FROM agent_tasks
            WHERE user_id = $1
              AND status IN ('running', 'paused_user_action')""",
        user_id,
    )


async def count_global_running_tasks() -> int:
    """全局上限保护:防 backend 失控。"""
    pool = await _get_pool()
    return await pool.fetchval(
        """SELECT count(*) FROM agent_tasks
            WHERE status IN ('running', 'paused_user_action')"""
    )


async def admin_list_tasks(
    *,
    status_filter: list[str] | None = None,
    user_id: str | None = None,
    role: str | None = None,
    platform: str | None = None,
    template_id: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """admin 视角列任务(跨 user)。返 (tasks, total_count)。"""
    pool = await _get_pool()
    conds: list[str] = []
    params: list = []
    if status_filter:
        params.append(status_filter); conds.append(f"status = ANY(${len(params)}::text[])")
    if user_id:
        params.append(user_id); conds.append(f"user_id = ${len(params)}")
    if role:
        params.append(role); conds.append(f"role = ${len(params)}")
    if platform:
        params.append(platform); conds.append(f"platform = ${len(params)}")
    if template_id:
        params.append(template_id); conds.append(f"template_id = ${len(params)}")
    if keyword:
        params.append(f"%{keyword}%")
        conds.append(f"(title ILIKE ${len(params)} OR template_id ILIKE ${len(params)})")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    total = await pool.fetchval(
        f"SELECT count(*) FROM agent_tasks {where}",
        *params,
    )

    params.append(limit); params.append(offset)
    rows = await pool.fetch(
        f"""SELECT id, user_id, role, platform, template_id, title, status,
                   inputs, progress, paused_signal, paused_at_idx, skipped,
                   result_summary, artifacts, item_states, error,
                   created_at, started_at, paused_at, resumed_at,
                   completed_at, cancelled_at, heartbeat_at
              FROM agent_tasks
              {where}
             ORDER BY created_at DESC
             LIMIT ${len(params)-1} OFFSET ${len(params)}""",
        *params,
    )
    return [_row_to_dict(r) for r in rows], int(total or 0)


async def admin_task_stats() -> dict:
    """admin dashboard 顶部计数:按 status 分组。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT status, count(*) AS n
             FROM agent_tasks
            GROUP BY status"""
    )
    out = {"pending": 0, "running": 0, "paused_user_action": 0,
           "completed": 0, "failed": 0, "cancelled": 0}
    total = 0
    for r in rows:
        out[r["status"]] = int(r["n"])
        total += int(r["n"])
    out["total"] = total
    return out


async def list_recommended_jobs(
    user_id: str,
    role: str | None = None,
    platform: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """跨任务聚合用户的精选岗位池。

    实现:扫该 user 所有 status='completed' 且 template_id 含 'find_best_jobs'
    的任务,展开 artifacts.top_jobs[],按 (platform, job_id) 去重(保留 started_at
    最新一份的字段,因为薪资/JD 摘要可能在新任务里更新)。

    每条结果带 source_task_id / source_task_title / source_started_at + 该任务
    item_states[job_id] 里的 viewed_at / starred(让前端 UI 跨任务保持"已看过"标记)。
    """
    pool = await _get_pool()
    conds = ["user_id = $1", "status = 'completed'",
             "template_id LIKE '%find_best_jobs'"]
    params: list = [user_id]
    if role:
        params.append(role)
        conds.append(f"role = ${len(params)}")
    if platform:
        params.append(platform)
        conds.append(f"platform = ${len(params)}")
    rows = await pool.fetch(
        f"""SELECT id, platform, template_id, title, started_at,
                   artifacts, item_states
              FROM agent_tasks
             WHERE {' AND '.join(conds)}
             ORDER BY started_at DESC NULLS LAST""",
        *params,
    )

    # Python 端去重(seen 是 dict 保持插入顺序;ORDER BY DESC 时第一次见到 key 就是最新一份)
    seen: dict[tuple[str, str], dict] = {}
    for r in rows:
        artifacts = r["artifacts"]
        if isinstance(artifacts, str):
            try: artifacts = json.loads(artifacts)
            except Exception: continue
        item_states = r["item_states"] or {}
        if isinstance(item_states, str):
            try: item_states = json.loads(item_states)
            except Exception: item_states = {}
        top_jobs = (artifacts or {}).get("top_jobs") or []
        for j in top_jobs:
            jid = str((j or {}).get("job_id") or "").strip()
            if not jid:
                continue
            key = (r["platform"], jid)
            if key in seen:
                continue  # 更早的 task 命中过同 job,跳过
            st = (item_states or {}).get(jid) or {}
            started_at = r["started_at"]
            seen[key] = {
                **j,
                "platform":          r["platform"],
                "source_task_id":    r["id"],
                "source_task_title": r["title"] or r["template_id"],
                "source_started_at": started_at.isoformat() if started_at else None,
                "viewed_at":         st.get("viewed_at"),
                "starred":           bool(st.get("starred")),
            }
            if len(seen) >= limit:
                return list(seen.values())
    return list(seen.values())


# ── MCP 调用观测埋点 ──────────────────────────────────────────────
def infer_mcp_op_type(tool_name: str) -> str:
    """从工具名推断 op 类别(给观测面分桶用)。
    暴露给 task engine 和 agent_loop 共用,确保 op_type 取值一致。"""
    n = (tool_name or "").lower()
    if "search" in n:                                       return "search"
    if "detail" in n or "get_candidate" in n:               return "detail"
    if "recommend" in n:                                    return "search"
    if "contact" in n or "send_message" in n or "connect" in n: return "chat"
    if "list_jobs" in n or "list_pending" in n:             return "list"
    if "download_resume" in n:                              return "detail"
    return "other"


async def record_mcp_call(
    *,
    user_id: str,
    role: str,
    platform: str,
    tool_name: str,
    op_type: str,
    duration_ms: int,
    success: bool,
    risk_signal: str | None = None,
) -> None:
    """记一条 MCP 调用日志。失败静默(不阻塞业务)。"""
    pool = await _get_pool()
    try:
        await pool.execute(
            """INSERT INTO mcp_call_log
                  (user_id, role, platform, tool_name, op_type,
                   duration_ms, success, risk_signal)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            user_id or "", role or "", platform or "",
            tool_name, op_type, int(duration_ms),
            bool(success), risk_signal or None,
        )
    except Exception:
        pass


async def record_client_error(
    *,
    user_id: str = "",
    role: str = "",
    platform: str = "",
    ext_id: str = "",
    ext_version: str = "",
    surface: str = "sidepanel",
    error_type: str = "error",
    message: str,
    stack: str | None = None,
    src_file: str | None = None,
    src_line: int | None = None,
    src_col: int | None = None,
    user_agent: str = "",
    url: str = "",
) -> None:
    """记一条前端 JS 错误。失败静默(不阻塞前端)。"""
    pool = await _get_pool()
    try:
        # 截断,防止巨型 stack 撑爆
        msg = (message or "")[:4000]
        stk = (stack or "")[:4000] if stack else None
        ua  = (user_agent or "")[:500]
        await pool.execute(
            """INSERT INTO client_errors
                  (user_id, role, platform, ext_id, ext_version, surface,
                   error_type, message, stack, src_file, src_line, src_col,
                   user_agent, url)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
            user_id or "", role or "", platform or "",
            ext_id or "", ext_version or "", surface or "sidepanel",
            error_type or "error", msg, stk,
            src_file or None,
            int(src_line) if src_line is not None else None,
            int(src_col) if src_col is not None else None,
            ua, (url or "")[:500],
        )
    except Exception:
        pass


async def client_errors_overview(hours: int = 24, limit: int = 20) -> dict:
    """前端错误 KPI:最近 N 小时总数 + 按 message 聚合 top N。"""
    pool = await _get_pool()
    total_row = await pool.fetchrow(
        """SELECT count(*) AS total,
                  count(DISTINCT user_id) FILTER (WHERE user_id <> '') AS users
           FROM client_errors
           WHERE ts >= NOW() - make_interval(hours => $1)""",
        int(hours),
    )
    top_rows = await pool.fetch(
        """SELECT left(message, 150) AS message,
                  count(*) AS hits,
                  max(ts) AS last_ts,
                  count(DISTINCT user_id) FILTER (WHERE user_id <> '') AS users
           FROM client_errors
           WHERE ts >= NOW() - make_interval(hours => $1)
           GROUP BY left(message, 150)
           ORDER BY hits DESC
           LIMIT $2""",
        int(hours), int(limit),
    )
    def _row_to_jsonable(r):
        d = dict(r)
        # max(ts) → datetime,转 ISO 字符串供 JSONResponse 序列化
        if "last_ts" in d and d["last_ts"] is not None:
            try:
                d["last_ts"] = d["last_ts"].isoformat()
            except Exception:
                d["last_ts"] = str(d["last_ts"])
        return d
    return {
        "total": total_row["total"] if total_row else 0,
        "users": total_row["users"] if total_row else 0,
        "top": [_row_to_jsonable(r) for r in top_rows],
    }


async def mcp_metrics_overview(hours: int = 24) -> dict:
    """KPI 总览:最近 N 小时的总调用 / 成功 / 风控 / 平均 duration。"""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """SELECT
              count(*)                                        AS total,
              count(*) FILTER (WHERE success)                 AS ok,
              count(*) FILTER (WHERE NOT success)             AS failed,
              count(*) FILTER (WHERE risk_signal IS NOT NULL) AS risk,
              coalesce(round(avg(duration_ms))::int, 0)       AS avg_ms
           FROM mcp_call_log
           WHERE ts >= NOW() - make_interval(hours => $1)""",
        int(hours),
    )
    return dict(row) if row else {"total": 0, "ok": 0, "failed": 0, "risk": 0, "avg_ms": 0}


async def mcp_metrics_timeseries(
    hours: int = 1, bucket: str = "minute",
) -> list[dict]:
    """时间序列:每 minute / hour 分桶,per platform 计调用数 + 风控数。"""
    if bucket not in ("minute", "hour"):
        bucket = "minute"
    pool = await _get_pool()
    rows = await pool.fetch(
        f"""SELECT
              date_trunc($2, ts) AS ts_bucket,
              platform,
              count(*) AS calls,
              count(*) FILTER (WHERE risk_signal IS NOT NULL) AS risks
           FROM mcp_call_log
           WHERE ts >= NOW() - make_interval(hours => $1)
           GROUP BY ts_bucket, platform
           ORDER BY ts_bucket""",
        int(hours), bucket,
    )
    return [
        {
            "ts": r["ts_bucket"].isoformat(),
            "platform": r["platform"],
            "calls": int(r["calls"]),
            "risks": int(r["risks"]),
        }
        for r in rows
    ]


async def mcp_metrics_risk_top(hours: int = 24, limit: int = 10) -> list[dict]:
    """风控信号热点 top N(按命中次数降序)。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT risk_signal, platform, count(*) AS hits,
                  max(ts) AS last_seen
           FROM mcp_call_log
           WHERE risk_signal IS NOT NULL
             AND ts >= NOW() - make_interval(hours => $1)
           GROUP BY risk_signal, platform
           ORDER BY hits DESC
           LIMIT $2""",
        int(hours), int(limit),
    )
    return [
        {
            "risk_signal": r["risk_signal"],
            "platform":    r["platform"],
            "hits":        int(r["hits"]),
            "last_seen":   r["last_seen"].isoformat() if r["last_seen"] else None,
        }
        for r in rows
    ]


async def mcp_metrics_user_top(hours: int = 24, limit: int = 20) -> list[dict]:
    """用户调用排行 top N — 排查可能的 abuse 用户。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT user_id, platform, count(*) AS calls,
                  count(*) FILTER (WHERE risk_signal IS NOT NULL) AS risks,
                  coalesce(round(avg(duration_ms))::int, 0) AS avg_ms,
                  max(ts) AS last_call
           FROM mcp_call_log
           WHERE ts >= NOW() - make_interval(hours => $1)
             AND user_id <> ''
           GROUP BY user_id, platform
           ORDER BY calls DESC
           LIMIT $2""",
        int(hours), int(limit),
    )
    return [
        {
            "user_id":   r["user_id"],
            "platform":  r["platform"],
            "calls":     int(r["calls"]),
            "risks":     int(r["risks"]),
            "avg_ms":    int(r["avg_ms"]),
            "last_call": r["last_call"].isoformat() if r["last_call"] else None,
        }
        for r in rows
    ]


async def cleanup_old_mcp_logs(retain_days: int = 7) -> int:
    """删 retain_days 之前的 mcp_call_log 行,返删除条数。日清理任务用。"""
    pool = await _get_pool()
    res = await pool.execute(
        "DELETE FROM mcp_call_log WHERE ts < NOW() - make_interval(days => $1)",
        int(retain_days),
    )
    # res 是 'DELETE N' 字符串
    try: return int(str(res).split()[-1])
    except Exception: return 0


async def cleanup_old_client_errors(retain_days: int = 30) -> int:
    """删 retain_days 之前的 client_errors 行,返删除条数。"""
    pool = await _get_pool()
    res = await pool.execute(
        "DELETE FROM client_errors WHERE ts < NOW() - make_interval(days => $1)",
        int(retain_days),
    )
    try: return int(str(res).split()[-1])
    except Exception: return 0


async def mcp_failure_rate_by_platform(hours: int = 1, min_calls: int = 20) -> list[dict]:
    """每 platform 的失败率(audit P1 fix:监控告警用)。

    只返回近 hours 小时内调用次数 ≥ min_calls 的平台 —— 避免低基数下噪声。
    返回 [{platform, total, failed, fail_rate}, ...] 按 fail_rate desc。
    """
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT platform,
                  count(*) AS total,
                  count(*) FILTER (WHERE NOT success) AS failed,
                  round(count(*) FILTER (WHERE NOT success)::numeric
                        / NULLIF(count(*), 0) * 100, 2) AS fail_rate
             FROM mcp_call_log
            WHERE ts >= NOW() - make_interval(hours => $1)
              AND platform <> ''
            GROUP BY platform
           HAVING count(*) >= $2
         ORDER BY fail_rate DESC NULLS LAST""",
        int(hours), int(min_calls),
    )
    return [
        {
            "platform":  r["platform"],
            "total":     r["total"],
            "failed":    r["failed"],
            "fail_rate": float(r["fail_rate"]) if r["fail_rate"] is not None else 0.0,
        }
        for r in rows
    ]


# ── 快捷 chip 配置 helpers(数据 / 策略分离架构 — 数据层)───────────────

async def chip_configs_get_version() -> int:
    """读全局 version 戳(用于前端缓存失效 + WS broadcast 比对)。"""
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT version FROM chip_configs_version WHERE id = 1")
    return int(row["version"]) if row else 1


async def chip_configs_bump_version() -> int:
    """admin 每次 CRUD 后调一次 → 前端缓存失效。"""
    pool = await _get_pool()
    row = await pool.fetchrow(
        """UPDATE chip_configs_version
              SET version = version + 1, bumped_at = NOW()
            WHERE id = 1
        RETURNING version"""
    )
    return int(row["version"]) if row else 1


async def chip_configs_list(
    *, role: str | None = None, platform: str | None = None,
    only_enabled: bool = False,
) -> list[dict]:
    """列 chip 配置。admin 列表用 only_enabled=False;personalizer 用 True。"""
    pool = await _get_pool()
    conds, args = [], []
    if role:     conds.append(f"role = ${len(args)+1}");     args.append(role)
    if platform: conds.append(f"platform = ${len(args)+1}"); args.append(platform)
    if only_enabled: conds.append("enabled = true")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = await pool.fetch(
        f"""SELECT id, key, role, platform, label_zh, label_en,
                   send_text_zh, send_text_en, icon, weight, grp,
                   enabled, sort_order, description, updated_at, updated_by
              FROM chip_configs
              {where}
          ORDER BY weight DESC, sort_order ASC, id ASC""",
        *args,
    )
    return [_chip_row_to_dict(r) for r in rows]


def _chip_row_to_dict(r) -> dict:
    d = dict(r)
    if d.get("updated_at") is not None:
        try: d["updated_at"] = d["updated_at"].isoformat()
        except Exception: d["updated_at"] = str(d["updated_at"])
    return d


async def chip_config_upsert(
    *, key: str, role: str, platform: str,
    label_zh: str, label_en: str,
    send_text_zh: str = "", send_text_en: str = "",
    icon: str = "", weight: int = 50, grp: str = "default",
    enabled: bool = True, sort_order: int = 0,
    description: str = "", updated_by: str = "admin",
) -> dict:
    """按 (key, role, platform) UNIQUE 约束 upsert。返回新行。
    种子脚本 + admin POST 都走这个。"""
    pool = await _get_pool()
    # send_text 默认等于 label
    if not send_text_zh: send_text_zh = label_zh
    if not send_text_en: send_text_en = label_en
    row = await pool.fetchrow(
        """INSERT INTO chip_configs
              (key, role, platform, label_zh, label_en, send_text_zh, send_text_en,
               icon, weight, grp, enabled, sort_order, description,
               updated_at, updated_by)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,NOW(),$14)
           ON CONFLICT (key, role, platform) DO UPDATE SET
              label_zh     = EXCLUDED.label_zh,
              label_en     = EXCLUDED.label_en,
              send_text_zh = EXCLUDED.send_text_zh,
              send_text_en = EXCLUDED.send_text_en,
              icon         = EXCLUDED.icon,
              weight       = EXCLUDED.weight,
              grp          = EXCLUDED.grp,
              enabled      = EXCLUDED.enabled,
              sort_order   = EXCLUDED.sort_order,
              description  = EXCLUDED.description,
              updated_at   = NOW(),
              updated_by   = EXCLUDED.updated_by
        RETURNING *""",
        key, role, platform, label_zh, label_en, send_text_zh, send_text_en,
        icon, int(weight), grp, bool(enabled), int(sort_order), description,
        updated_by,
    )
    return _chip_row_to_dict(row)


async def chip_config_patch(chip_id: int, patch: dict, updated_by: str = "admin") -> dict | None:
    """admin 编辑单条。patch dict 里只允许下列 key,其它静默丢。"""
    ALLOWED = {"label_zh", "label_en", "send_text_zh", "send_text_en",
               "icon", "weight", "grp", "enabled", "sort_order", "description"}
    cleaned = {k: v for k, v in patch.items() if k in ALLOWED}
    if not cleaned:
        # 只想 touch updated_at 也行(no-op patch)
        cleaned = {}
    pool = await _get_pool()
    if not cleaned:
        row = await pool.fetchrow("SELECT * FROM chip_configs WHERE id = $1", chip_id)
        return _chip_row_to_dict(row) if row else None
    sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(cleaned.keys()))
    args = [chip_id, *cleaned.values()]
    sets += f", updated_at = NOW(), updated_by = ${len(args)+1}"
    args.append(updated_by)
    row = await pool.fetchrow(
        f"UPDATE chip_configs SET {sets} WHERE id = $1 RETURNING *",
        *args,
    )
    return _chip_row_to_dict(row) if row else None


async def chip_config_delete(chip_id: int) -> bool:
    pool = await _get_pool()
    res = await pool.execute("DELETE FROM chip_configs WHERE id = $1", chip_id)
    try: return int(str(res).split()[-1]) > 0
    except Exception: return False


async def chip_configs_reorder(items: list[dict]) -> int:
    """批量更新 sort_order;items=[{id, sort_order}, ...]。返回 affected rows."""
    pool = await _get_pool()
    n = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for it in items:
                if "id" not in it or "sort_order" not in it:
                    continue
                res = await conn.execute(
                    "UPDATE chip_configs SET sort_order = $1, updated_at = NOW() WHERE id = $2",
                    int(it["sort_order"]), int(it["id"]),
                )
                try: n += int(str(res).split()[-1])
                except Exception: pass
    return n


async def record_chip_click(
    *,
    user_id: str = "",
    role: str = "",
    platform: str = "",
    chip_key: str,
    chip_label: str = "",
    lang: str = "zh",
) -> None:
    """记一条 chip 点击。失败静默(不阻塞前端)。"""
    if not chip_key:
        return
    pool = await _get_pool()
    try:
        await pool.execute(
            """INSERT INTO chip_click_events
                  (user_id, role, platform, chip_key, chip_label, lang)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            user_id or "", role or "", platform or "",
            chip_key, (chip_label or "")[:200], (lang or "zh")[:10],
        )
    except Exception:
        pass


async def chip_click_top(
    *, hours: int = 24, role: str | None = None, platform: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """admin 看哪些 chip 最常被点。返 [{chip_key, hits, users, last_ts}, ...]."""
    pool = await _get_pool()
    conds, args = ["ts >= NOW() - make_interval(hours => $1)"], [int(hours)]
    if role:
        args.append(role); conds.append(f"role = ${len(args)}")
    if platform:
        args.append(platform); conds.append(f"platform = ${len(args)}")
    where = "WHERE " + " AND ".join(conds)
    args.append(int(limit))
    rows = await pool.fetch(
        f"""SELECT chip_key, role, platform,
                   count(*) AS hits,
                   count(DISTINCT user_id) FILTER (WHERE user_id <> '') AS users,
                   max(ts) AS last_ts
              FROM chip_click_events
              {where}
          GROUP BY chip_key, role, platform
          ORDER BY hits DESC
          LIMIT ${len(args)}""",
        *args,
    )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("last_ts"):
            try: d["last_ts"] = d["last_ts"].isoformat()
            except Exception: d["last_ts"] = str(d["last_ts"])
        out.append(d)
    return out


async def cleanup_old_chip_clicks(retain_days: int = 30) -> int:
    """删 retain_days 之前的行,返删除条数。"""
    pool = await _get_pool()
    res = await pool.execute(
        "DELETE FROM chip_click_events WHERE ts < NOW() - make_interval(days => $1)",
        int(retain_days),
    )
    try: return int(str(res).split()[-1])
    except Exception: return 0


async def list_zombie_tasks(stale_seconds: int = 60) -> list[dict]:
    """heartbeat 超过 stale_seconds 还在 running → 视为僵尸,admin/sweep 用。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT id, user_id, role, platform, status, heartbeat_at
             FROM agent_tasks
            WHERE status = 'running'
              AND heartbeat_at < NOW() - make_interval(secs => $1)""",
        int(stale_seconds),
    )
    return [_row_to_dict(r) for r in rows]


async def sweep_stale_tasks(
    *,
    zombie_running_seconds: int = 300,
    paused_timeout_seconds: int = 86400,
    pending_timeout_seconds: int = 300,
) -> dict:
    """sweeper:清理三类卡死任务。
    1. running 但 heartbeat > zombie_running_seconds(5 min)→ 进程崩了 → mark failed
    2. paused_user_action 超 paused_timeout_seconds(24h)→ 用户没来 → mark failed 释放槽位
    3. pending 超 pending_timeout_seconds(5 min)→ 创建后 backend 崩没 promote → mark failed
    返回 {'zombie': N, 'paused_timeout': N, 'pending_timeout': N}
    """
    pool = await _get_pool()
    z = await pool.execute(
        """UPDATE agent_tasks
              SET status='failed',
                  error=COALESCE(error, '') || ' [zombie: heartbeat stale]',
                  completed_at=NOW(), heartbeat_at=NOW()
            WHERE status='running'
              AND heartbeat_at < NOW() - make_interval(secs => $1)""",
        int(zombie_running_seconds),
    )
    pend = await pool.execute(
        """UPDATE agent_tasks
              SET status='failed',
                  error=COALESCE(error, '') || ' [pending stuck: never promoted to running]',
                  completed_at=NOW(), heartbeat_at=NOW()
            WHERE status='pending'
              AND created_at < NOW() - make_interval(secs => $1)""",
        int(pending_timeout_seconds),
    )
    p = await pool.execute(
        """UPDATE agent_tasks
              SET status='failed',
                  error=COALESCE(error, '') || ' [paused timeout: user did not resume]',
                  completed_at=NOW(), heartbeat_at=NOW()
            WHERE status='paused_user_action'
              AND COALESCE(paused_at, NOW()) < NOW() - make_interval(secs => $1)""",
        int(paused_timeout_seconds),
    )
    # asyncpg execute returns 'UPDATE N' string
    return {
        "zombie": int(z.split()[-1]) if z.startswith("UPDATE") else 0,
        "paused_timeout": int(p.split()[-1]) if p.startswith("UPDATE") else 0,
        "pending_timeout": int(pend.split()[-1]) if pend.startswith("UPDATE") else 0,
    }


# ── event logging ─────────────────────────────────────────────────────────────

async def log_event(
    session_id: int | None,
    user_id: str,
    role: str,
    platform: str,
    event_type: str,
    *,
    turn_index: int = 0,
    tool_name: str | None = None,
    content: str | None = None,
    ok: bool | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    cost_usd: float | None = None,
    duration_ms: float | None = None,
    mode: str = "",
    payload: dict | None = None,
) -> None:
    """记录一条 agent 事件。role 和 platform 现在是 NOT NULL 列,必传。

    payload: 可选的结构化 JSONB(event 详情,如 tool input/output,事件元数据)。
    """
    pool = await _get_pool()
    await pool.execute(
        """INSERT INTO agent_conv_events
               (session_id, user_id, role, platform, event_type,
                turn_index, tool_name, content, ok,
                input_tokens, output_tokens, cache_creation_input_tokens,
                cache_read_input_tokens, cost_usd, duration_ms, mode, payload)
           VALUES ($1, $2, $3, $4, $5,
                   $6, $7, $8, $9,
                   $10, $11, $12,
                   $13, $14, $15, $16, $17::jsonb)""",
        session_id, user_id, role, platform, event_type,
        turn_index, tool_name, content, ok,
        input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens, cost_usd,
        duration_ms, mode or None,
        json.dumps(payload, ensure_ascii=False) if payload is not None else None,
    )


async def log_funnel_step(
    session_id: int | None,
    user_id: str,
    role: str,
    platform: str,
    step: str,
) -> bool:
    """增长漏斗:同一 user_id × platform × step 只记一次。

    走 idx_ace_funnel_unique 唯一索引去重(WHERE event_type='funnel'),
    INSERT ON CONFLICT DO NOTHING 命中重复时静默跳过。返 True 表示首次记录。

    step: welcome / role_selected / resume_uploaded / first_search /
          first_apply / first_message
    """
    if not user_id or not platform or not step or not role:
        return False
    pool = await _get_pool()
    result = await pool.execute(
        """INSERT INTO agent_conv_events
               (session_id, user_id, role, platform, event_type, tool_name)
           VALUES ($1, $2, $3, $4, 'funnel', $5)
           ON CONFLICT (user_id, platform, tool_name)
             WHERE event_type = 'funnel'
             DO NOTHING""",
        session_id, user_id, role, platform, step,
    )
    return result.endswith("INSERT 0 1")


# ── Phase 1.4 增长 metrics 聚合查询 ──────────────────────────────────────────

async def get_dau(date_iso: str, platform: str | None = None) -> int:
    """指定日期(UTC)的 distinct 活跃用户数。
    platform=None 时返当日全平台 DAU。
    """
    target = _parse_iso_tz(date_iso) or datetime.now(timezone.utc)
    pool = await _get_pool()
    if platform:
        row = await pool.fetchrow(
            """SELECT COUNT(DISTINCT user_id) AS dau
               FROM agent_conv_events
               WHERE platform = $1 AND created_at::date = $2::date""",
            platform, target,
        )
    else:
        row = await pool.fetchrow(
            """SELECT COUNT(DISTINCT user_id) AS dau
               FROM agent_conv_events
               WHERE created_at::date = $1::date""",
            target,
        )
    return int(row["dau"] if row else 0)


async def get_dau_by_platform(date_iso: str) -> dict[str, int]:
    """{linkedin: 234, boss: 445, indeed: 56, unknown: 12, _total: 678}"""
    target = _parse_iso_tz(date_iso) or datetime.now(timezone.utc)
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT COALESCE(platform, 'unknown') AS p, COUNT(DISTINCT user_id) AS dau
           FROM agent_conv_events
           WHERE created_at::date = $1::date
           GROUP BY p""",
        target,
    )
    out: dict[str, int] = {r["p"]: int(r["dau"]) for r in rows}
    # 单独算 _total (跨 platform distinct,不能按 sum)
    total_row = await pool.fetchrow(
        """SELECT COUNT(DISTINCT user_id) AS dau
           FROM agent_conv_events
           WHERE created_at::date = $1::date""",
        target,
    )
    out["_total"] = int(total_row["dau"] if total_row else 0)
    return out


async def get_funnel_counts(
    date_iso: str,
    platform: str | None = None,
) -> dict[str, int]:
    """7 个 funnel step 的当日 distinct user 数。
    返回 {welcome: 234, role_selected: 200, resume_uploaded: 100, first_search: 90, ...}
    缺失的 step 用 0 填充,前端漏斗图直接绘制。
    """
    target = _parse_iso_tz(date_iso) or datetime.now(timezone.utc)
    pool = await _get_pool()
    if platform:
        rows = await pool.fetch(
            """SELECT tool_name AS step, COUNT(DISTINCT user_id) AS users
               FROM agent_conv_events
               WHERE event_type = 'funnel'
                 AND platform = $1
                 AND created_at::date = $2::date
               GROUP BY tool_name""",
            platform, target,
        )
    else:
        rows = await pool.fetch(
            """SELECT tool_name AS step, COUNT(DISTINCT user_id) AS users
               FROM agent_conv_events
               WHERE event_type = 'funnel'
                 AND created_at::date = $1::date
               GROUP BY tool_name""",
            target,
        )
    out = {r["step"]: int(r["users"]) for r in rows}
    # 兜底全部 0,保持稳定 schema 给前端
    for step in ("welcome", "role_selected", "resume_uploaded",
                 "first_search", "first_apply", "first_message"):
        out.setdefault(step, 0)
    return out


async def get_top_cost_users(
    date_iso: str,
    limit: int = 10,
) -> list[dict]:
    """[{user_id, cost_usd, turn_count, platform}, ...] 按 cost_usd desc"""
    target = _parse_iso_tz(date_iso) or datetime.now(timezone.utc)
    limit = max(1, min(100, int(limit)))
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT user_id,
                  COALESCE(platform, 'unknown') AS platform,
                  COALESCE(SUM(cost_usd), 0)::DOUBLE PRECISION AS cost_usd,
                  COUNT(DISTINCT turn_index) AS turn_count
           FROM agent_conv_events
           WHERE created_at::date = $1::date
             AND cost_usd IS NOT NULL
           GROUP BY user_id, platform
           ORDER BY cost_usd DESC
           LIMIT $2""",
        target, limit,
    )
    return [dict(r) for r in rows]


async def get_dau_series(
    days: int = 7,
    platform: str | None = None,
) -> list[dict]:
    """近 N 天每日 DAU,前端折线图用。返 [{date, dau, platform}, ...] desc"""
    days = max(1, min(90, int(days)))
    pool = await _get_pool()
    if platform:
        rows = await pool.fetch(
            """SELECT created_at::date AS date,
                      COUNT(DISTINCT user_id) AS dau
               FROM agent_conv_events
               WHERE platform = $1
                 AND created_at >= CURRENT_DATE - $2::int
               GROUP BY date
               ORDER BY date DESC""",
            platform, days,
        )
    else:
        rows = await pool.fetch(
            """SELECT created_at::date AS date,
                      COALESCE(platform, 'unknown') AS platform,
                      COUNT(DISTINCT user_id) AS dau
               FROM agent_conv_events
               WHERE created_at >= CURRENT_DATE - $1::int
               GROUP BY date, platform
               ORDER BY date DESC, platform""",
            days,
        )
    # date 序列化:isoformat
    out = []
    for r in rows:
        d = dict(r)
        d["date"] = d["date"].isoformat() if hasattr(d["date"], "isoformat") else str(d["date"])
        out.append(d)
    return out


# ── admin queries ─────────────────────────────────────────────────────────────

async def list_conv_sessions(
    *,
    user_id: str | None = None,
    role: str | None = None,
    platform: str | None = None,
    limit: int = 50,
    offset: int = 0,
    keyword: str | None = None,
) -> list[dict]:
    """B (Phase 2): role + platform 是可选过滤,前端历史 tab 按当前 ext 的
    role 必传 + 当前 platform sub-tab 可选。"""
    pool = await _get_pool()
    # 动态拼 WHERE — 全部走位置参数,避免 SQL 注入
    base_select = """
        SELECT s.id, s.user_id, s.app_user_id,
               s.role, s.platform, s.title,
               s.user_tier,
               s.primary_mode, s.mode_switches,
               s.created_at AS started_at, s.last_active_at, s.ended_at,
               COUNT(e.id) AS event_count,
               COUNT(e.id) FILTER (WHERE e.event_type = 'user_message') AS turn_count,
               COALESCE(SUM(e.input_tokens), 0)  AS total_input_tokens,
               COALESCE(SUM(e.output_tokens), 0) AS total_output_tokens,
               COALESCE(SUM(e.cache_creation_input_tokens), 0) AS total_cache_creation_input_tokens,
               COALESCE(SUM(e.cache_read_input_tokens), 0)     AS total_cache_read_input_tokens,
               COALESCE(SUM(e.cost_usd), 0)::DOUBLE PRECISION  AS total_cost_usd
        FROM agent_conv_sessions s
        LEFT JOIN agent_conv_events e ON e.session_id = s.id
    """
    conds: list[str] = []
    params: list[Any] = []
    if user_id:
        params.append(user_id); conds.append(f"s.user_id = ${len(params)}")
    if role:
        params.append(role); conds.append(f"s.role = ${len(params)}")
    if platform:
        params.append(platform); conds.append(f"s.platform = ${len(params)}")
    if keyword:
        params.append(f"%{keyword}%"); conds.append(f"s.title ILIKE ${len(params)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    params.append(limit); limit_ph = f"${len(params)}"
    params.append(offset); offset_ph = f"${len(params)}"
    rows = await pool.fetch(
        base_select + f"""
           {where}
           GROUP BY s.id
           HAVING COUNT(e.id) > 0
           ORDER BY s.last_active_at DESC
           LIMIT {limit_ph} OFFSET {offset_ph}""",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def count_conv_sessions(
    *,
    user_id: str | None = None,
    role: str | None = None,
    platform: str | None = None,
    keyword: str | None = None,
) -> int:
    """只统计有实际事件的会话(与 list_conv_sessions 的 HAVING 条件一致)。"""
    pool = await _get_pool()
    conds: list[str] = []
    params: list[Any] = []
    if user_id:
        params.append(user_id); conds.append(f"s.user_id = ${len(params)}")
    if role:
        params.append(role); conds.append(f"s.role = ${len(params)}")
    if platform:
        params.append(platform); conds.append(f"s.platform = ${len(params)}")
    if keyword:
        params.append(f"%{keyword}%"); conds.append(f"s.title ILIKE ${len(params)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return await pool.fetchval(
        f"""SELECT COUNT(*) FROM (
                SELECT s.id FROM agent_conv_sessions s
                JOIN agent_conv_events e ON e.session_id = s.id
                {where}
                GROUP BY s.id
            ) t""",
        *params,
    )


async def delete_conv_session(session_id: int) -> bool:
    pool = await _get_pool()
    result = await pool.execute(
        "DELETE FROM agent_conv_sessions WHERE id=$1", session_id)
    return result != "DELETE 0"


async def list_recent_errors(
    since_iso: str = "",
    limit: int = 100,
    offset: int = 0,
    user_id: str = "",
    keyword: str = "",
) -> list[dict]:
    """跨 session 抓 event_type='error' 的事件，按时间倒序返回。

    用于 admin 面板的全局错误日志页（job-api-admin）。
    返回每行 {id, session_id, user_id, error_category, error_message, duration_ms, mode, created_at}。
    error_category 来自 tool_name 列（agent_events.py 把分类塞这里），
    error_message 来自 content 列。
    """
    pool = await _get_pool()
    conditions: list[str] = ["event_type = 'error'"]
    params: list[Any] = []
    since_dt = _parse_iso_tz(since_iso)
    if since_dt is not None:
        # asyncpg timestamptz 列要求 datetime 实例(string 会抛 InvalidArgumentError);
        # 之前直接传 since_iso 字符串 → /admin/errors 全部 503。
        params.append(since_dt)
        conditions.append(f"created_at >= ${len(params)}")
    if user_id:
        params.append(user_id)
        conditions.append(f"user_id = ${len(params)}")
    if keyword:
        params.append(f"%{keyword}%")
        conditions.append(f"content ILIKE ${len(params)}")
    where = "WHERE " + " AND ".join(conditions)
    params.append(limit)
    limit_ph = f"${len(params)}"
    params.append(offset)
    offset_ph = f"${len(params)}"
    rows = await pool.fetch(
        f"""SELECT id, session_id, user_id,
                   tool_name AS error_category, content AS error_message,
                   duration_ms, mode, created_at
            FROM agent_conv_events
            {where}
            ORDER BY created_at DESC
            LIMIT {limit_ph} OFFSET {offset_ph}""",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def list_error_categories(since_iso: str = "") -> list[str]:
    """前端下拉用：拿 event_type='error' 的 distinct tool_name（即 error_category）。"""
    pool = await _get_pool()
    conditions: list[str] = [
        "event_type = 'error'",
        "tool_name IS NOT NULL",
        "tool_name != ''",
    ]
    params: list[Any] = []
    since_dt = _parse_iso_tz(since_iso)
    if since_dt is not None:
        params.append(since_dt)
        conditions.append(f"created_at >= ${len(params)}")
    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT DISTINCT tool_name FROM agent_conv_events {where} ORDER BY tool_name",
        *params,
    )
    return [r["tool_name"] for r in rows if r["tool_name"]]


async def get_conv_events(session_id: int) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT id, event_type, turn_index, tool_name, content, ok,
                  input_tokens, output_tokens,
                  cache_creation_input_tokens, cache_read_input_tokens, cost_usd,
                  duration_ms, mode, created_at
           FROM agent_conv_events
           WHERE session_id = $1
           ORDER BY id""",
        session_id,
    )
    return [_row_to_dict(r) for r in rows]


async def update_session_mode(session_id: int, mode: str, mode_switches: int = 0) -> None:
    """Update primary_mode and mode_switches on a session."""
    pool = await _get_pool()
    await pool.execute(
        "UPDATE agent_conv_sessions SET primary_mode=$2, mode_switches=$3 WHERE id=$1",
        session_id, mode, mode_switches,
    )


async def get_mode_stats(days: int = 7) -> list[dict]:
    """Per-mode usage stats over the last N days."""
    pool = await _get_pool()
    rows = await pool.fetch("""
        SELECT
            COALESCE(s.primary_mode, 'unknown') AS mode,
            COUNT(*)::int AS session_count,
            COALESCE(SUM(
                (SELECT COUNT(*) FROM agent_conv_events e
                 WHERE e.session_id = s.id AND e.event_type = 'user_message')
            ), 0)::int AS total_turns,
            COALESCE(SUM(
                (SELECT SUM(e.input_tokens) FROM agent_conv_events e
                 WHERE e.session_id = s.id AND e.event_type = 'token_usage')
            ), 0)::int AS total_input_tokens,
            COALESCE(SUM(
                (SELECT SUM(e.output_tokens) FROM agent_conv_events e
                 WHERE e.session_id = s.id AND e.event_type = 'token_usage')
            ), 0)::int AS total_output_tokens,
            COALESCE(SUM(
                (SELECT SUM(e.cache_creation_input_tokens) FROM agent_conv_events e
                 WHERE e.session_id = s.id AND e.event_type = 'token_usage')
            ), 0)::int AS total_cache_creation_input_tokens,
            COALESCE(SUM(
                (SELECT SUM(e.cache_read_input_tokens) FROM agent_conv_events e
                 WHERE e.session_id = s.id AND e.event_type = 'token_usage')
            ), 0)::int AS total_cache_read_input_tokens,
            COALESCE(SUM(
                (SELECT SUM(e.cost_usd) FROM agent_conv_events e
                 WHERE e.session_id = s.id AND e.event_type = 'token_usage')
            ), 0)::DOUBLE PRECISION AS total_cost_usd
        FROM agent_conv_sessions s
        WHERE s.created_at > NOW() - make_interval(days := $1)
        GROUP BY COALESCE(s.primary_mode, 'unknown')
        ORDER BY session_count DESC
    """, days)
    return [_row_to_dict(r) for r in rows]


# ── message persistence ───────────────────────────────────────────────────────

async def load_session_messages(session_id: int) -> list[dict]:
    """按 seq 升序加载会话的完整消息列表，供 SseSession.messages 初始化。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT role, content FROM agent_conv_messages "
        "WHERE session_id=$1 ORDER BY seq ASC",
        session_id,
    )
    # asyncpg 自动将 JSONB 反序列化为 Python 对象
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def purge_sessions_without_messages() -> dict:
    """删除没有任何消息记录的历史会话（无法恢复，属于无效数据）。
    同时删除 user_id 为空的会话（创建时数据不完整）。
    返回 {deleted_no_messages, deleted_no_user, total}。
    """
    pool = await _get_pool()
    # 1. 删除没有消息的会话（ON DELETE CASCADE 会连带删除 events）
    r1 = await pool.execute("""
        DELETE FROM agent_conv_sessions
        WHERE id NOT IN (SELECT DISTINCT session_id FROM agent_conv_messages)
    """)
    # 2. 删除 user_id 为空的会话（正常情况不应存在）
    r2 = await pool.execute("""
        DELETE FROM agent_conv_sessions
        WHERE user_id IS NULL OR user_id = ''
    """)
    def _count(result: str) -> int:
        try:
            return int(result.split()[-1])
        except Exception:
            return 0
    n1 = _count(r1)
    n2 = _count(r2)
    return {"deleted_no_messages": n1, "deleted_no_user": n2, "total": n1 + n2}


def _extract_message_text(content) -> str:
    """从各种存储格式中提取纯文本。
    content 可能是：
    - str: 原始文本 / JSON 字符串（需解析）
    - list: Anthropic SDK content blocks [{type,text,...}]
    - dict: 单个 content block
    """
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    if isinstance(content, str):
        # 先尝试当 JSON 解析（double-serialized 的场景）
        try:
            parsed = json.loads(content)
            return _extract_message_text(parsed)
        except (json.JSONDecodeError, ValueError):
            pass
        return content
    if isinstance(content, dict) and content.get("type") == "text":
        return content.get("text", "")
    return str(content) if content is not None else ""


async def get_conv_messages_display(session_id: int) -> list[dict]:
    """返回前端展示格式的消息列表（BossAgentMessage[]）。"""
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT role, content, created_at FROM agent_conv_messages "
        "WHERE session_id=$1 ORDER BY seq ASC",
        session_id,
    )
    result = []
    for i, row in enumerate(rows):
        text = _extract_message_text(row["content"])
        created_ms = int(row["created_at"].timestamp() * 1000) if row["created_at"] else 0
        result.append({
            "id": f"h-{session_id}-{i}",
            "role": row["role"],
            "text": text,
            "toolEvents": [],
            "createdAt": created_ms,
        })
    return result


def _json_safe(obj):
    """将 Anthropic SDK Pydantic 对象转为可 JSON 序列化的结构。"""
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    # Pydantic v2（model_dump）或 v1（dict）
    if hasattr(obj, "model_dump"):
        return _json_safe(obj.model_dump())
    if hasattr(obj, "dict") and callable(obj.dict):
        return _json_safe(obj.dict())
    return obj


async def append_messages(session_id: int, messages: list[dict], from_index: int) -> None:
    """将 messages[from_index:] 写入 DB，seq 从 from_index 开始。
    ON CONFLICT DO NOTHING 防止重复写入。"""
    new_msgs = messages[from_index:]
    if not new_msgs:
        return
    pool = await _get_pool()
    await pool.executemany(
        "INSERT INTO agent_conv_messages (session_id, seq, role, content) "
        "VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (session_id, seq) DO NOTHING",
        [
            (session_id, from_index + i, msg["role"],
             json.dumps(_json_safe(msg["content"]), ensure_ascii=False))
            for i, msg in enumerate(new_msgs)
        ],
    )


def _row_to_dict(row: Any) -> dict:
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


async def fetch_jobs_for_eval(
    job_ids: list[str] | None = None,
    limit: int = 20,
    platform: str = "",
) -> list[dict]:
    """从 cached_jobs LEFT JOIN cached_job_details 取职位数据用于 LLM 评估。
    platform 为空时查询所有平台。"""
    pool = await _get_pool()
    if job_ids:
        if platform:
            rows = await pool.fetch(
                """SELECT j.external_id, j.platform, j.title, j.company, j.salary, j.experience,
                          j.education, j.tags, j.skills, d.description
                   FROM cached_jobs j
                   LEFT JOIN cached_job_details d
                          ON d.platform = j.platform AND d.external_id = j.external_id
                   WHERE j.platform = $2 AND j.external_id = ANY($1::text[])""",
                job_ids, platform,
            )
        else:
            rows = await pool.fetch(
                """SELECT j.external_id, j.platform, j.title, j.company, j.salary, j.experience,
                          j.education, j.tags, j.skills, d.description
                   FROM cached_jobs j
                   LEFT JOIN cached_job_details d
                          ON d.platform = j.platform AND d.external_id = j.external_id
                   WHERE j.external_id = ANY($1::text[])""",
                job_ids,
            )
    else:
        if platform:
            rows = await pool.fetch(
                """SELECT j.external_id, j.platform, j.title, j.company, j.salary, j.experience,
                          j.education, j.tags, j.skills, d.description
                   FROM cached_jobs j
                   LEFT JOIN cached_job_details d
                          ON d.platform = j.platform AND d.external_id = j.external_id
                   WHERE j.platform = $2
                   ORDER BY j.fetched_at DESC
                   LIMIT $1""",
                limit, platform,
            )
        else:
            rows = await pool.fetch(
                """SELECT j.external_id, j.platform, j.title, j.company, j.salary, j.experience,
                          j.education, j.tags, j.skills, d.description
                   FROM cached_jobs j
                   LEFT JOIN cached_job_details d
                          ON d.platform = j.platform AND d.external_id = j.external_id
                   ORDER BY j.fetched_at DESC
                   LIMIT $1""",
                limit,
            )
    return [_row_to_dict(r) for r in rows]
