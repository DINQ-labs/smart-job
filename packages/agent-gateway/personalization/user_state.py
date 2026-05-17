"""user_state.py — 汇总 personalizer 用得到的用户上下文信号.

设计:
- 只用已有 DB 字段(0 额外 IO 成本,~50ms 并发完成)
- 每个 probe 都做 try/except,任意失败 → 返回安全 default,绝不影响 chip 输出
- 后续可扩 unread_msgs / login_ok 等贵字段(需 cache 5-10 分钟)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TypedDict

import db

log = logging.getLogger(__name__)


class UserState(TypedDict, total=False):
    has_resume:        bool
    has_preferences:   bool
    has_running_task:  bool
    has_paused_task:   bool                  # 有 status=paused_user_action 任务 → PRD 120 提示
    recent_completed_with_matches: int       # 最近 24h 完成且 result_summary 提到匹配的任务数 → PRD 121 提示
    last_mode:         str | None            # "search" | "evaluate" | "apply" | "interview" | None
    session_count_7d:  int
    account_age_days:  int
    # ── caller overlay(由 API 端点从请求里填,不走 DB)──────────────
    # 前端按当前 sidepanel 看到的 platform 页面类型/物料数推上来,
    # 让 chips 引擎对应到 PRD §10.3 9 种主动提示场景。
    page_type:         str | None            # "list" | "detail" | "chat" | "off_platform" | "other"
    page_item_count:   int                   # list 页可见物料数(候选人/职位),做 "批量分析 X 位" 用


_DEFAULT: UserState = {
    "has_resume":       False,
    "has_preferences":  False,
    "has_running_task": False,
    "has_paused_task":  False,
    "recent_completed_with_matches": 0,
    "last_mode":        None,
    "session_count_7d": 0,
    "account_age_days": 0,
    "page_type":        None,
    "page_item_count":  0,
}


async def _safe(coro, fallback):
    try:
        return await coro
    except Exception as e:
        log.debug("user_state probe failed (fallback=%r): %s", fallback, e)
        return fallback


async def collect(user_id: str, role: str, platform: str) -> UserState:
    """并发拉所有信号,返回 UserState dict。绝不抛错(任何 probe 失败用默认)。"""
    if not user_id:
        return dict(_DEFAULT)  # type: ignore

    has_resume_t  = _safe(_probe_has_resume(user_id), False)
    has_prefs_t   = _safe(_probe_has_preferences(user_id, role, platform), False)
    has_task_t    = _safe(_probe_has_running_task(user_id), False)
    has_paused_t  = _safe(_probe_has_paused_task(user_id), False)
    recent_done_t = _safe(_probe_recent_completed_with_matches(user_id), 0)
    last_mode_t   = _safe(_probe_last_mode(user_id, role, platform), None)
    sess_cnt_t    = _safe(_probe_session_count_7d(user_id, role, platform), 0)
    age_t         = _safe(_probe_account_age_days(user_id), 0)

    (has_resume, has_prefs, has_task, has_paused, recent_done,
     last_mode, sess_cnt, age) = await asyncio.gather(
        has_resume_t, has_prefs_t, has_task_t, has_paused_t, recent_done_t,
        last_mode_t, sess_cnt_t, age_t,
    )
    return {
        "has_resume":       bool(has_resume),
        "has_preferences":  bool(has_prefs),
        "has_running_task": bool(has_task),
        "has_paused_task":  bool(has_paused),
        "recent_completed_with_matches": int(recent_done or 0),
        "last_mode":        last_mode,
        "session_count_7d": int(sess_cnt or 0),
        "account_age_days": int(age or 0),
        # page_type / page_item_count 不在这里 probe — caller overlay
        "page_type":        None,
        "page_item_count":  0,
    }


# ── 单个 probe(每个用 try/except 绕掉缺表 / 缺字段)─────────────────

async def _probe_has_resume(user_id: str) -> bool:
    try:
        import resume_db
        row = await resume_db.get_resume_by_user(user_id)
        return row is not None
    except Exception:
        return False


async def _probe_has_preferences(user_id: str, role: str, platform: str) -> bool:
    try:
        if role == "recruiter":
            import recruiter_db
            row = await recruiter_db.get_preferences(user_id, platform)
            return bool(row)
        else:
            import preferences_db
            row = await preferences_db.get_user_preferences(user_id, platform)
            return bool(row)
    except Exception:
        return False


async def _probe_has_running_task(user_id: str) -> bool:
    try:
        n = await db.count_running_tasks_for_user(user_id)
        return n > 0
    except Exception:
        return False


async def _probe_has_paused_task(user_id: str) -> bool:
    """有没有 status=paused_user_action 的任务等用户处理(PRD 120 主动提示)。"""
    try:
        pool = await db._get_pool()
        n = await pool.fetchval(
            """SELECT count(*) FROM agent_tasks
                WHERE user_id=$1 AND status='paused_user_action'""",
            user_id,
        )
        return (n or 0) > 0
    except Exception:
        return False


async def _probe_recent_completed_with_matches(user_id: str) -> int:
    """最近 24h 完成的任务里 result_summary 提到匹配的数量。
    PRD 121: '完成,发现 X 高匹配' — 取 24h 内 status=completed 且 summary 非空作为近似。"""
    try:
        pool = await db._get_pool()
        n = await pool.fetchval(
            """SELECT count(*) FROM agent_tasks
                WHERE user_id=$1 AND status='completed'
                  AND completed_at >= NOW() - INTERVAL '24 hours'
                  AND result_summary IS NOT NULL""",
            user_id,
        )
        return int(n or 0)
    except Exception:
        return 0


async def _probe_last_mode(user_id: str, role: str, platform: str) -> str | None:
    """读最近一条 agent_conv_sessions 的 last_mode(若有)。"""
    try:
        pool = await db._get_pool()
        row = await pool.fetchrow(
            """SELECT last_mode FROM agent_conv_sessions
                WHERE user_id=$1 AND role=$2 AND platform=$3
             ORDER BY last_active_at DESC NULLS LAST
                LIMIT 1""",
            user_id, role, platform,
        )
        return row["last_mode"] if row and "last_mode" in row else None
    except Exception:
        return None


async def _probe_session_count_7d(user_id: str, role: str, platform: str) -> int:
    """近 7 天该 cell 启动过几次 SSE session。"""
    try:
        pool = await db._get_pool()
        row = await pool.fetchrow(
            """SELECT count(*) AS n FROM agent_conv_sessions
                WHERE user_id=$1 AND role=$2 AND platform=$3
                  AND last_active_at >= NOW() - INTERVAL '7 days'""",
            user_id, role, platform,
        )
        return int(row["n"]) if row else 0
    except Exception:
        return 0


async def _probe_account_age_days(user_id: str) -> int:
    """从 agent_conv_sessions 取最早 created_at(没 users 表,这是最便宜近似)。"""
    try:
        pool = await db._get_pool()
        row = await pool.fetchrow(
            """SELECT EXTRACT(DAY FROM (NOW() - min(created_at)))::int AS days
                 FROM agent_conv_sessions WHERE user_id=$1""",
            user_id,
        )
        return int(row["days"]) if row and row["days"] is not None else 0
    except Exception:
        return 0
