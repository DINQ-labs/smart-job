"""tasks/notifier.py — 任务事件双向通知（C6 / PRD §10.5）。

绑定到 engine.register_callbacks(on_*)，把任务生命周期事件写入：
1. agent_conv_events 表（audit + 前端历史查询）
2. agent_conv_messages 表（assistant 消息，让在线 SSE 流和重连后历史都能看到）

PRD §10.5 双向通知:
  - 任务 Tab 卡片状态变化   → 前端按 polling /tasks/:id 拿到 status / display_status 自行渲染
  - AI 助手 Tab 插入新消息  → 后端这里 INSERT agent_conv_messages 一条 assistant 消息

PRD §10.3 主动提示文案:
  - 进行中: "🔄 批量分析进行中 X/Y · 剩余约 Z 分钟"  ← progress 写在 task.progress JSON,前端渲染
  - 需验证: "⚠️ 需要处理验证,点击处理后自动恢复"
  - 完成:   "✅ 批量分析完成,发现 X 位高匹配候选人"

设计：
- 4 个 callback 都 best-effort:任一失败不影响 engine 流程,仅 log.warning
- on_progress 不写消息(高频,会刷屏);仅 status_change 类事件写消息
- 消息 role='assistant' content 是 JSON blob 兼容 SSE 协议:
    {"type": "task_event", "task_id": N, "phase": "paused/resumed/completed", "title":"...", "body":"..."}
  前端识别这种 type 时渲染特殊任务卡片(不是普通气泡)。
"""
from __future__ import annotations

import json
import logging

import db
from tasks import engine

log = logging.getLogger(__name__)


# ── 公共工具 ────────────────────────────────────────────────────────────────

async def _task_meta(task_id: int) -> dict | None:
    """根据 task_id 拉 (user_id, role, platform, template_id, title)，给后续 log_event 用。"""
    row = await db.get_task(task_id) if hasattr(db, "get_task") else None
    if row is None:
        # fallback: 直接走 _get_pool（db 模块未必导出 get_task）
        try:
            pool = await db._get_pool()
            row = await pool.fetchrow(
                """SELECT id, user_id, role, platform, template_id, title,
                          status, paused_kind, progress, artifacts
                     FROM agent_tasks WHERE id=$1""",
                task_id,
            )
            row = dict(row) if row else None
        except Exception as e:
            log.warning("[notifier] _task_meta(%s) failed: %s", task_id, e)
            return None
    return row


async def _find_session_id(user_id: str, role: str, platform: str) -> int | None:
    """查 (user_id, role, platform) 三元组的 active session_id，无则 None。
    用于 INSERT agent_conv_messages 时挂载 session_id —— 没 session 就只写 events 表。"""
    if not user_id:
        return None
    try:
        pool = await db._get_pool()
        row = await pool.fetchrow(
            """SELECT id FROM agent_conv_sessions
                WHERE user_id=$1 AND role=$2 AND platform=$3
                ORDER BY last_active_at DESC NULLS LAST, id DESC
                LIMIT 1""",
            user_id, role, platform,
        )
        return int(row["id"]) if row else None
    except Exception as e:
        log.warning("[notifier] _find_session_id failed: %s", e)
        return None


async def _append_assistant_task_message(
    session_id: int | None,
    phase: str,
    task_id: int,
    title: str,
    body: str,
    extra: dict | None = None,
) -> None:
    """往 agent_conv_messages 追加一条 assistant 消息（type=task_event），
    让在线 SSE 流和历史查询都能看到。session_id 为 None 时跳过（写不进表）。"""
    if not session_id:
        return
    content = {
        "type": "task_event",
        "task_id": task_id,
        "phase": phase,    # 'paused' / 'verification_required' / 'resumed' / 'completed' / 'failed' / 'cancelled'
        "title": title,
        "body": body,
        **(extra or {}),
    }
    try:
        pool = await db._get_pool()
        # 取该 session 的 next seq（最大 seq + 1）
        row = await pool.fetchrow(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM agent_conv_messages WHERE session_id=$1",
            session_id,
        )
        next_seq = int(row["next_seq"]) if row else 0
        await pool.execute(
            """INSERT INTO agent_conv_messages (session_id, seq, role, content)
               VALUES ($1, $2, 'assistant', $3::jsonb)""",
            session_id, next_seq, json.dumps(content, ensure_ascii=False),
        )
    except Exception as e:
        log.warning("[notifier] append message failed: %s", e)


# ── Callback 实现 ───────────────────────────────────────────────────────────

async def on_progress(task_id: int, payload: dict) -> None:
    """高频事件，仅在 step_started / step_done 阶段记 audit；不写 chat 消息。
    （task.progress JSON 字段由 engine._emit_progress 已经 UPDATE，前端 polling 拿得到。）"""
    phase = payload.get("phase", "")
    if phase not in ("started", "step_started", "step_done"):
        return
    meta = await _task_meta(task_id)
    if not meta:
        return
    try:
        await db.log_event(
            session_id=None,
            user_id=meta["user_id"],
            role=meta["role"],
            platform=meta["platform"],
            event_type=f"task_{phase}",
            payload={"task_id": task_id, **payload},
        )
    except Exception as e:
        log.debug("[notifier] on_progress log_event failed: %s", e)


async def on_paused(task_id: int, payload: dict) -> None:
    """任务暂停：风控触发 (kind='verification') 或用户主动 (kind='user')。
    都写一条 assistant 消息 + audit event。"""
    meta = await _task_meta(task_id)
    if not meta:
        return
    kind = (payload.get("paused_kind") or "verification").strip()
    phase = "verification_required" if kind == "verification" else "paused"
    if kind == "verification":
        title = "⚠️ 需要处理验证"
        body = payload.get("guide") or "页面出现验证码或异常，请前往原页面处理，处理完成后任务将自动恢复。"
    else:
        title = "⏸️ 任务已暂停"
        body = "你已手动暂停任务，点击「继续」恢复执行。"

    session_id = await _find_session_id(meta["user_id"], meta["role"], meta["platform"])
    await _append_assistant_task_message(
        session_id, phase, task_id, title, body,
        extra={
            "signal_id": payload.get("signal_id", ""),
            "open_url": payload.get("open_url", ""),
            "paused_at_idx": payload.get("paused_at_idx", 0),
        },
    )
    try:
        await db.log_event(
            session_id=session_id,
            user_id=meta["user_id"], role=meta["role"], platform=meta["platform"],
            event_type=f"task_paused_{kind}",
            content=body,
            payload={"task_id": task_id, **payload},
        )
    except Exception as e:
        log.debug("[notifier] on_paused log_event failed: %s", e)


async def on_resumed(task_id: int, payload: dict) -> None:
    """任务恢复：写 ✅ 验证完成消息 + audit。"""
    meta = await _task_meta(task_id)
    if not meta:
        return
    session_id = await _find_session_id(meta["user_id"], meta["role"], meta["platform"])
    await _append_assistant_task_message(
        session_id, "resumed", task_id,
        title="✅ 验证完成",
        body="任务已恢复执行。",
        extra={"from_idx": payload.get("from_idx", 0)},
    )
    try:
        await db.log_event(
            session_id=session_id,
            user_id=meta["user_id"], role=meta["role"], platform=meta["platform"],
            event_type="task_resumed",
            content="任务已恢复执行",
            payload={"task_id": task_id, **payload},
        )
    except Exception as e:
        log.debug("[notifier] on_resumed log_event failed: %s", e)


async def on_done(task_id: int, payload: dict) -> None:
    """任务完成：根据 status 写不同消息（completed/failed/cancelled）。
    PRD §10.3.121 'task_completed': '✅ 批量分析完成,发现 X 位高匹配候选人'。
    完成时聚合 artifacts.items 的 match_score（C7）→ 高/中/低分桶。"""
    meta = await _task_meta(task_id)
    if not meta:
        return
    status = payload.get("status", "")
    artifacts = payload.get("artifacts") or {}
    summary = payload.get("result_summary") or ""

    # C7: 匹配度聚合
    score_buckets = _bucket_match_scores(artifacts)

    if status == "completed":
        title = "✅ 任务完成"
        body = _format_done_body(meta.get("template_id", ""), score_buckets, summary)
        phase = "completed"
    elif status == "failed":
        title = "❌ 任务失败"
        body = summary or "执行过程中遇到错误。"
        phase = "failed"
    elif status == "cancelled":
        title = "⛔ 任务已取消"
        body = summary or "任务被取消。"
        phase = "cancelled"
    else:
        title = "🏁 任务结束"
        body = summary or ""
        phase = "done"

    session_id = await _find_session_id(meta["user_id"], meta["role"], meta["platform"])
    await _append_assistant_task_message(
        session_id, phase, task_id, title, body,
        extra={
            "score_buckets": score_buckets,
            "artifacts_keys": list(artifacts.keys()) if isinstance(artifacts, dict) else [],
        },
    )
    # task_completed/failed/cancelled 的 audit event 已经由 engine._finalize 写过；
    # 这里只补 chat 消息层，不重复写 audit event。


# ── C7: 匹配度聚合 ───────────────────────────────────────────────────────────

def _bucket_match_scores(artifacts: dict) -> dict:
    """从 artifacts 任意位置抓 items[].match_score（或 score）做高/中/低分桶。

    返回: {'high': N, 'mid': N, 'low': N, 'unknown': N, 'total': N}
      high  >= 80
      mid   60-79
      low   < 60
      unknown 没有 score 字段
    """
    if not isinstance(artifacts, dict):
        return {"high": 0, "mid": 0, "low": 0, "unknown": 0, "total": 0}

    items: list = []
    # 通用桥接：常见 artifact key
    for key in ("top_jobs", "candidates", "items", "ranked", "applied", "contacted"):
        v = artifacts.get(key)
        if isinstance(v, list):
            items.extend(v)

    buckets = {"high": 0, "mid": 0, "low": 0, "unknown": 0}
    for item in items:
        if not isinstance(item, dict):
            buckets["unknown"] += 1
            continue
        s = item.get("match_score")
        if s is None:
            s = item.get("score")
        if s is None or not isinstance(s, (int, float)):
            buckets["unknown"] += 1
            continue
        si = int(s)
        if si >= 80:
            buckets["high"] += 1
        elif si >= 60:
            buckets["mid"] += 1
        else:
            buckets["low"] += 1
    buckets["total"] = sum(buckets.values())
    return buckets


def _format_done_body(template_id: str, buckets: dict, summary: str) -> str:
    """根据模板类型选合适的完成文案 + 高匹配数量。"""
    total = buckets.get("total", 0)
    high = buckets.get("high", 0)
    if not total:
        return summary or "任务完成。"
    object_label = "候选人" if "recruiter" in template_id or "candidate" in template_id else "职位"
    if "apply" in template_id:
        return f"完成投递 {total} 个 {object_label}（高匹配 {high}，中匹配 {buckets.get('mid', 0)}）。"
    if "track" in template_id:
        return f"已追踪 {total} 个投递记录。"
    return f"分析完成，共 {total} 个 {object_label}，其中高匹配 {high} 个、中匹配 {buckets.get('mid', 0)} 个。"


# ── 启动注册（server.py:_startup 调用） ────────────────────────────────────

def register() -> None:
    """把上面 4 个 callback 注册到 engine。server.py:_startup 调用一次。"""
    engine.register_callbacks(
        on_progress=on_progress,
        on_paused=on_paused,
        on_resumed=on_resumed,
        on_done=on_done,
    )
    log.info("[notifier] task event callbacks registered")
