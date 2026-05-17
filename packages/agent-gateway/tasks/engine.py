"""tasks/engine.py — TaskRunner 主循环 (Phase B)

任务在 backend 后台 asyncio.Task 跑,**独立于 SSE 流**:sidepanel 关掉
任务继续跑;sidepanel 重连用 GET /tasks 拉状态。

核心循环:
  1. running 状态运行下一个 step(或 step 内的下一个 item)
  2. 工具抛 RiskControlSignal → 按 signal 策略处置:
       auto_retry  → sleep wait_sec → 重试同 item;到 max_retries 走 skip
       user_action → status='paused_user_action',asyncio.Event 阻塞;
                     等用户调 resume_task() 后继续
       skip_item   → 记 skipped,继续下一 item
       abort       → status='failed' 退出
  3. 每 step 之间 + 每 item 之间 check cancel + paused
  4. 每 30s heartbeat,30s 没动作 sweep 视为僵尸

事件回调(由 caller 注入)用于 SSE event emit:
  on_progress(task_id, payload)         → SSE task_progress
  on_paused(task_id, signal_id, spec)    → SSE task_paused_user_action
  on_resumed(task_id, from_idx)          → SSE task_resumed
  on_done(task_id, summary, artifacts)   → SSE task_completed / task_failed
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
import traceback
from typing import Any, Callable, Awaitable

import db

# audit P2 fix:从跨服务 sys.path.insert 解耦,改用 repo-root 共享包 job_common
from job_common.risk_signals import RISK_SIGNALS, RiskControlSignal, get_strategy

from tasks.registry import TaskTemplate, TaskStep, get_template
from tasks import lease

log = logging.getLogger(__name__)


# 全局并发控制
MAX_GLOBAL_TASKS = int(os.getenv("MAX_GLOBAL_TASKS", "50"))
HEARTBEAT_INTERVAL_SEC = 30.0


# ── Runner 实例(每个 task 一个,引用持有在 _running_runners) ──────────────
_running_runners: dict[int, "TaskRunner"] = {}


def get_running_runner(task_id: int) -> "TaskRunner | None":
    """通过 task_id 拿到正在跑的 runner(用于 cancel / resume)。"""
    return _running_runners.get(task_id)


# ── 事件回调(由 sse_router 注入,空默认) ──────────────────────────────
EventCallback = Callable[[int, dict], Awaitable[None]]


async def _noop_cb(*args, **kwargs):
    pass


_callbacks = {
    "on_progress": _noop_cb,
    "on_paused": _noop_cb,
    "on_resumed": _noop_cb,
    "on_done": _noop_cb,
}


def register_callbacks(*, on_progress=None, on_paused=None, on_resumed=None, on_done=None):
    """sse_router 启动时注入 SSE event emit 函数。"""
    if on_progress: _callbacks["on_progress"] = on_progress
    if on_paused:   _callbacks["on_paused"] = on_paused
    if on_resumed:  _callbacks["on_resumed"] = on_resumed
    if on_done:     _callbacks["on_done"] = on_done


# ── TaskRunner ───────────────────────────────────────────────────────────────

class TaskRunner:
    """一次任务执行的控制对象。"""

    def __init__(self, task_id: int, template: TaskTemplate):
        self.task_id = task_id
        self.template = template
        self._cancel_event = asyncio.Event()
        self._resume_event = asyncio.Event()
        self._resume_event.set()              # 默认非暂停状态
        self._heartbeat_task: asyncio.Task | None = None
        self._lease_lost = False              # 续约失败时置 True,finalize 走 'failed'
        # 用户主动暂停标记:由外部 request_pause() 设置,主循环 step 边界检测并 enter pause
        # (区别于风控信号触发的 _emit_paused —— 那条路径走 paused_kind='verification')
        self._pause_request_kind: str = ""

    # ── 控制信号 ─────────────────────────────────────────────
    def cancel(self) -> None:
        """从外部触发取消。下个 step/item 边界生效。"""
        self._cancel_event.set()
        # 若当前阻塞在 paused,也唤醒(让 cancel 立即生效)
        self._resume_event.set()

    def resume(self) -> None:
        """用户解决了人工介入问题,点继续。"""
        self._resume_event.set()

    def request_pause(self, kind: str = "user") -> None:
        """外部触发主动暂停。下个 step 边界生效;running → paused_user_action(paused_kind='user')。
        风控信号引发的暂停(kind='verification')不走这条路径,而是在 _handle_signal 内直接 emit。"""
        self._pause_request_kind = kind or "user"

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    async def _wait_if_paused(self) -> None:
        if not self._resume_event.is_set():
            log.info("[task %d] paused, waiting for user resume...", self.task_id)
            await self._resume_event.wait()
            log.info("[task %d] resumed", self.task_id)

    # ── 事件 emit ────────────────────────────────────────────
    async def _emit_progress(self, **payload) -> None:
        await _callbacks["on_progress"](self.task_id, payload)
        # 同步把 progress 写 DB,前端 polling 能看到进度条移动
        # 限频:只在能产出有意义 current/total 的 phase 写,避免覆盖之前的进度数字
        phase = payload.get("phase", "")
        try:
            progress = self._build_progress_dict(payload)
            if progress is not None:
                # 保留之前的 progress.current/total(step_started 之外不覆盖整个 dict)
                # 简单合并:在 DB 字段层做不到 partial update,所以让 _build_progress_dict
                # 只在能给出完整 current/total 时才返 dict;否则返 None 跳过写
                await db.update_task_status(self.task_id, "running", progress=progress)
        except Exception as e:
            log.debug("[task %d] progress write failed: %s", self.task_id, e)

    def _build_progress_dict(self, payload: dict) -> dict | None:
        """把 emit_progress 的 payload 转成给前端用的 progress JSON。
        只在 payload 能产出完整 current/total 时返 dict,其他 phase 返 None 不写 DB
        (避免 step_done 等没 current/total 的 phase 把之前的进度覆盖成 0)。"""
        phase = payload.get("phase", "")
        # iter step 进度:current / total 出自 payload(item_started/done/skipped)
        if "current" in payload and "total" in payload:
            out = {
                "phase": phase,
                "current": payload["current"],
                "total": payload["total"],
                "step_id": payload.get("step_id", ""),
            }
            # batch_pausing: 保留 pause_sec / batch_size + 写 pause_started_at(ISO),
            # 前端 polling 看到 phase=batch_pausing 时可算剩余秒数渲染"批次休息中"提示
            if phase == "batch_pausing":
                import datetime as _dt
                out["pause_sec"] = payload.get("pause_sec")
                out["batch_size"] = payload.get("batch_size")
                out["pause_started_at"] = _dt.datetime.utcnow().isoformat() + "Z"
            return out
        if phase == "step_started":
            # step 开始:已完成 step 数 = step_idx (0-based)
            # 例如 step 0 (第 1 步)开始时 current=0, total=4(还没完成任何 step)
            return {
                "phase": phase,
                "step_id": payload.get("step_id", ""),
                "step_idx": payload.get("step_idx", 0),
                "total_steps": payload.get("total_steps", 0),
                "current": payload.get("step_idx", 0),
                "total": payload.get("total_steps", 0),
            }
        if phase == "step_done":
            # step 完成:current = step_idx + 1(完成多少个 step)
            # 最后一个 step (idx=N-1) done 时 current=N, total=N → 4/4 ✓
            return {
                "phase": phase,
                "step_id": payload.get("step_id", ""),
                "step_idx": payload.get("step_idx", 0),
                "total_steps": payload.get("total_steps", 0),
                "current": payload.get("step_idx", 0) + 1,
                "total": payload.get("total_steps", 0),
            }
        if phase == "started":
            return {"phase": "started", "current": 0, "total": 0}
        # retry_waiting 等不写 DB(避免清掉 current/total)
        return None

    async def _charge_item_credit(self, sku: str, item_idx: int) -> None:
        """每完成一个 item 调一次 api-gw /billing/charge 扣 Credit。

        Sprint 6 / G4: 任务级扣费 hook。BILLING_ENFORCE=1 时余额不足 → 抛 _AbortSignal
        让任务转 failed + summary 提示用户升级。fail-open 模式下仅 log。"""
        try:
            import billing_client
        except ImportError:
            return  # 模块不可用（开发环境）→ 跳过
        # task row 拿 user_id（缓存到 ctx 也行，简化先每次取）
        row = await db.get_task(self.task_id)
        if not row:
            return
        user_id = row.get("user_id") or ""
        if not user_id:
            return
        try:
            result = await billing_client.charge(
                user_id, sku=sku, count=1,
                meta={"task_id": self.task_id, "item_idx": item_idx,
                      "template_id": row.get("template_id", "")},
            )
        except Exception as e:
            log.warning("[task %d] credit charge dispatch failed: %s", self.task_id, e)
            return
        if not result.get("charged") and result.get("insufficient"):
            log.warning("[task %d] credit insufficient at item %d (sku=%s cost=%s balance=%s)",
                        self.task_id, item_idx, sku,
                        result.get("cost"), result.get("balance"))
            # BILLING_ENFORCE=1 → 抛 _AbortSignal 转 failed;=0(开发默认)→ 仅 log,任务继续。
            # 此前漏了这个判断,fail-open 模式下任务也会被余额不足中断(本地/测试环境踩坑)。
            if billing_client._enforce():
                raise _AbortSignal(
                    f"余额不足：还差 {result.get('cost', 0)} Credit，"
                    f"剩余 {result.get('balance', 0)}。{result.get('upgrade_hint', '')}"
                )

    async def _emit_paused(self, signal_id: str, spec: dict, paused_at_idx: int) -> None:
        await _callbacks["on_paused"](self.task_id, {
            "signal_id": signal_id,
            "guide": spec.get("guide", ""),
            "open_url": spec.get("open_url", ""),
            "label": spec.get("label", ""),
            "paused_at_idx": paused_at_idx,
            "paused_kind": "verification",
        })

    async def _enter_user_pause(self, paused_at_idx: int) -> None:
        """用户主动 pause(C2):将 running → paused_user_action(paused_kind='user'),阻塞等 resume。
        风控暂停走 _handle_signal,paused_kind='verification'。"""
        kind = self._pause_request_kind or "user"
        self._pause_request_kind = ""  # 清标记,避免重复 enter
        self._resume_event.clear()
        await db.update_task_status(
            self.task_id, "paused_user_action",
            paused_kind=kind,
            paused_signal=None,
            paused_at_idx=paused_at_idx,
            paused_at=_now(),
        )
        await _callbacks["on_paused"](self.task_id, {
            "signal_id": "",
            "guide": "任务已暂停,等待用户继续",
            "open_url": "",
            "label": "用户主动暂停",
            "paused_at_idx": paused_at_idx,
            "paused_kind": kind,
        })
        await self._resume_event.wait()
        if self._cancel_event.is_set():
            return
        await db.update_task_status(
            self.task_id, "running",
            paused_kind="",
            paused_signal=None,
            resumed_at=_now(),
        )
        await self._emit_resumed(paused_at_idx)

    async def _emit_resumed(self, from_idx: int) -> None:
        await _callbacks["on_resumed"](self.task_id, {"from_idx": from_idx})

    async def _emit_done(self, status: str, summary: str = "", artifacts: dict | None = None) -> None:
        await _callbacks["on_done"](self.task_id, {
            "status": status,
            "result_summary": summary,
            "artifacts": artifacts or {},
        })

    # ── lease 续约 + cancel/resume signal poll(后台 5s tick) ──
    # 旧 heartbeat 改为 Redis lease 续约,顺便 sync DB heartbeat_at(给 admin 工具看)。
    # 5s 一次的 tick 主要是为了 cancel signal 响应快;lease 续约只在 30s 边界跑。
    async def _heartbeat_loop(self) -> None:
        last_renew = 0.0
        while not self._cancel_event.is_set():
            now = time.monotonic()
            try:
                if now - last_renew >= lease.LEASE_RENEW_INTERVAL_SEC:
                    ok = await lease.renew_lease(self.task_id)
                    if not ok:
                        log.warning("[task %d] lease lost, aborting", self.task_id)
                        self._lease_lost = True
                        self._cancel_event.set()
                        self._resume_event.set()  # 唤醒 paused 状态
                        return
                    last_renew = now
                    # DB heartbeat 顺便更新(供老 sweeper / admin 工具)
                    await db.heartbeat_task(self.task_id)

                # 跨实例 cancel signal 检测
                if await lease.check_cancel(self.task_id):
                    log.info("[task %d] cancel signal received via Redis", self.task_id)
                    await lease.clear_cancel(self.task_id)
                    self._cancel_event.set()
                    self._resume_event.set()
                    return

                # 跨实例 resume signal(只在 paused 时消费)
                if not self._resume_event.is_set() and await lease.check_resume(self.task_id):
                    log.info("[task %d] resume signal received via Redis", self.task_id)
                    self._resume_event.set()
            except Exception as e:
                log.debug("[task %d] heartbeat tick error: %s", self.task_id, e)
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                return

    # ── 主入口 ──────────────────────────────────────────────
    async def run(self, ctx: dict | None = None) -> None:
        """跑完整模板。失败/取消/完成时返回(不抛)。
        多实例:run() 入口先抢 Redis lease。抢不到=别人在跑,本实例直接返。"""
        ctx = ctx or {}

        # 抢 lease(Redis 不在时返 True 走单实例 fallback)
        got = await lease.acquire_lease(self.task_id)
        if not got:
            log.info("[task %d] lease held by another instance, skipping", self.task_id)
            return

        # 从 DB 拿 task 字段补到 ctx,模板的 _ensure_mcp 等需要 user_id/role/platform
        task_row = await db.get_task(self.task_id)
        if task_row:
            ctx.setdefault("user_id", task_row.get("user_id") or "")
            ctx.setdefault("role", task_row.get("role") or "")
            ctx.setdefault("platform", task_row.get("platform") or "")

        # 提前挂 ctx,**任何路径走 _finalize 时**都能埋点 task_failed(含未匹配 platform)
        self._ctx = ctx

        # 按 platform 选 steps(逻辑模板 + 平台特化编排)
        platform = ctx.get("platform", "")
        steps = self.template.steps_for(platform)
        if not steps:
            await db.update_task_status(self.task_id, "running", started_at=_now())
            return await self._finalize(
                "failed",
                f"模板 {self.template.id} 不支持平台 '{platform}'(可用:{self.template.supported_platforms})",
                {}, [],
            )

        # 启动续约 + signal poll 后台任务
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        _running_runners[self.task_id] = self
        await db.update_task_status(self.task_id, "running", started_at=_now())
        await self._emit_progress(phase="started", template_id=self.template.id)

        # 埋点:task_started 写 agent_conv_events,让任务用户也算进 DAU/funnel
        # session_id=NULL(任务不挂在 chat session 下)。失败不冒泡。
        try:
            await db.log_event(
                session_id=None,
                user_id=ctx.get("user_id", ""),
                role=ctx.get("role", ""),
                platform=ctx.get("platform", ""),
                event_type="task_started",
                tool_name=self.template.id,
                payload={"task_id": self.task_id},
            )
        except Exception as e:
            log.debug("[task %d] log_event task_started failed: %s", self.task_id, e)

        artifacts: dict = {}
        skipped: list[dict] = []
        ctx["_task_id"] = self.task_id
        ctx["_artifacts"] = artifacts
        ctx["_skipped"] = skipped

        try:
            for step_idx, step in enumerate(steps):
                if self._cancel_event.is_set():
                    return await self._finalize("cancelled", "已取消", artifacts, skipped)

                # 用户主动暂停(C2):POST /tasks/{id}/pause 触发,在 step 边界生效
                if self._pause_request_kind:
                    await self._enter_user_pause(step_idx)
                    if self._cancel_event.is_set():
                        return await self._finalize("cancelled", "已取消", artifacts, skipped)

                await self._wait_if_paused()
                await self._emit_progress(
                    phase="step_started",
                    step_id=step.id, step_title=step.title,
                    step_idx=step_idx, total_steps=len(steps),
                )

                try:
                    if step.iter_items:
                        await self._run_step_per_item(step, ctx, artifacts, skipped)
                    else:
                        await self._run_step_once(step, ctx, artifacts, skipped)
                except _AbortSignal as e:
                    return await self._finalize("failed", str(e), artifacts, skipped)

                await self._emit_progress(
                    phase="step_done",
                    step_id=step.id, step_idx=step_idx,
                    total_steps=len(steps),
                )

            # 所有 step 跑完 → 用 ctx['result_summary'](最后一个 summarize step 写入)
            summary = ctx.get("result_summary", "任务完成 ✅")
            return await self._finalize("completed", summary, artifacts, skipped)

        except asyncio.CancelledError:
            return await self._finalize("cancelled", "任务被取消", artifacts, skipped)
        except Exception as e:
            log.exception("[task %d] unexpected error", self.task_id)
            tb = traceback.format_exc(limit=5)
            return await self._finalize("failed", f"{type(e).__name__}: {e}", artifacts, skipped, error_text=tb)

    async def _finalize(self, status: str, summary: str,
                         artifacts: dict, skipped: list,
                         error_text: str | None = None) -> None:
        # lease lost 的特殊路径:status 改为 failed,但**不写 DB**(让接走的实例做主)
        if self._lease_lost:
            log.warning("[task %d] finalize skipped: lease lost (other instance owns it)", self.task_id)
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            _running_runners.pop(self.task_id, None)
            return
        try:
            updates: dict = {
                "result_summary": summary,
                "artifacts": artifacts,
                "skipped": skipped,
            }
            if status == "completed":
                updates["completed_at"] = _now()
            elif status == "cancelled":
                updates["cancelled_at"] = _now()
            elif status == "failed":
                updates["completed_at"] = _now()
                if error_text:
                    updates["error"] = error_text
            await db.update_task_status(self.task_id, status, **updates)
        finally:
            # heartbeat 关
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            _running_runners.pop(self.task_id, None)
            # 释放 lease(校验自己持有再 DEL)
            try:
                await lease.release_lease(self.task_id)
            except Exception:
                pass
            # 兜底 cleanup:模板可能注册了 MCP session 等资源,任务结束统一关
            ctx = getattr(self, "_ctx", None) or {}
            for cleanup in ctx.get("_cleanup", []):
                try:
                    await cleanup()
                except Exception as e:
                    log.warning("[task %d] cleanup failed: %s", self.task_id, e)
            # 埋点:task_completed/failed/cancelled 写 agent_conv_events
            event_type_map = {
                "completed": "task_completed",
                "failed": "task_failed",
                "cancelled": "task_cancelled",
            }
            et = event_type_map.get(status)
            if et:
                try:
                    await db.log_event(
                        session_id=None,
                        user_id=ctx.get("user_id", ""),
                        role=ctx.get("role", ""),
                        platform=ctx.get("platform", ""),
                        event_type=et,
                        tool_name=self.template.id,
                        content=(summary or "")[:500] or None,
                        payload={
                            "task_id": self.task_id,
                            "skipped_count": len(skipped) if skipped else 0,
                        },
                    )
                except Exception as e:
                    log.debug("[task %d] log_event %s failed: %s", self.task_id, et, e)
            await self._emit_done(status, summary, artifacts)

    # ── step 执行(单次 / 批量) ────────────────────────────
    async def _run_step_once(self, step: TaskStep, ctx: dict,
                              artifacts: dict, skipped: list) -> None:
        """非 iter step:整个 step 调一次 fn。带风控 retry。"""
        for retry in range(3):
            await self._wait_if_paused()
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()
            try:
                result = await step.fn(ctx, ctx)
                if isinstance(result, dict):
                    ctx.update(result)
                return
            except RiskControlSignal as sig:
                handled = await self._handle_risk_signal(sig, retry, item_idx=None)
                if handled == "retry":
                    continue
                if handled == "user_resumed":
                    # 用户解决问题 → 重试当前 step(跟 iter 路径行为一致)
                    continue
                if handled == "skip":
                    return  # step 整体放弃 → 进入下个 step
                # abort
                raise _AbortSignal(sig.spec.get("reason", str(sig)))

    async def _run_step_per_item(self, step: TaskStep, ctx: dict,
                                  artifacts: dict, skipped: list) -> None:
        """iter_items step:对 ctx['items'] 列表逐项 fn。从 task.paused_at_idx 续跑。

        Sprint 4 / E2 / PRD §104 分批策略:
          每 batch_size (默认 15) 个 item 完成后, sleep batch_pause_sec ∈
          [batch_pause_min, batch_pause_max] (默认 30-120s) 模拟"批次间休息"。
          减少瞬时高频请求触发风控的概率。配置可由 task.inputs 覆盖：
              batch_size, batch_pause_min, batch_pause_max。
          设为 0 关闭分批。最后一批后不暂停。
        """
        items = ctx.get("items") or []
        # 续跑断点
        task_row = await db.get_task(self.task_id)
        start_idx = (task_row.get("paused_at_idx") or 0) if task_row else 0
        # 用户暂停后第一次进 → 清掉续跑标志
        if task_row and task_row.get("status") == "running" and start_idx > 0:
            await db.update_task_status(self.task_id, "running", paused_at_idx=None)

        # 分批参数（task.inputs 可覆盖）
        inputs = ctx.get("inputs") or {}
        batch_size = max(0, int(inputs.get("batch_size", 15)))
        batch_pause_min = max(0, int(inputs.get("batch_pause_min", 30)))
        batch_pause_max = max(batch_pause_min, int(inputs.get("batch_pause_max", 120)))

        total = len(items)
        for idx in range(start_idx, total):
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()
            await self._wait_if_paused()

            item = items[idx]
            ctx["_item"] = item
            ctx["_item_idx"] = idx
            await self._emit_progress(
                phase="item_started",
                step_id=step.id, current=idx + 1, total=total,
                item_summary=_item_summary(item),
            )

            for retry in range(3):
                await self._wait_if_paused()
                if self._cancel_event.is_set():
                    raise asyncio.CancelledError()
                try:
                    result = await step.fn(ctx, ctx)
                    if isinstance(result, dict):
                        ctx.update(result)
                    await self._emit_progress(
                        phase="item_done",
                        step_id=step.id, current=idx + 1, total=total,
                    )
                    # Sprint 6 / G4: 每 item 完成后按 step.credit_sku 扣费
                    # 跨服务 HTTP 调 job-api-gateway /billing/charge
                    # 余额不足 + BILLING_ENFORCE=1 → _AbortSignal 把任务转 failed
                    if step.credit_sku:
                        await self._charge_item_credit(step.credit_sku, idx)
                    break
                except RiskControlSignal as sig:
                    handled = await self._handle_risk_signal(sig, retry, item_idx=idx)
                    if handled == "retry":
                        continue
                    if handled == "skip":
                        skipped.append({
                            "idx": idx,
                            "signal": sig.signal_id,
                            "reason": sig.spec.get("reason", sig.spec.get("label", "")),
                            "item_summary": _item_summary(item),
                        })
                        await db.update_task_status(self.task_id, "running", skipped=skipped)
                        await self._emit_progress(
                            phase="item_skipped",
                            step_id=step.id, current=idx + 1, total=total,
                            reason=sig.spec.get("reason", ""),
                        )
                        break
                    if handled == "user_resumed":
                        # 用户解决问题 → 重试当前 item(continue 外层 retry 循环)
                        continue
                    # abort
                    raise _AbortSignal(sig.spec.get("reason", str(sig)))

            # Sprint 4 / E2: 批次边界 → 长间隔休息
            # 只在 idx 不是最后一项 且 (idx+1) 是 batch_size 的整数倍时触发
            if (batch_size > 0
                    and (idx + 1) < total
                    and (idx + 1) % batch_size == 0
                    and batch_pause_max > 0):
                pause_sec = batch_pause_min + (
                    (batch_pause_max - batch_pause_min) * random.random()
                )
                log.info(
                    "[task %d] batch boundary at item %d/%d → 休息 %.1fs",
                    self.task_id, idx + 1, total, pause_sec,
                )
                await self._emit_progress(
                    phase="batch_pausing",
                    step_id=step.id,
                    current=idx + 1, total=total,
                    pause_sec=round(pause_sec, 1),
                    batch_size=batch_size,
                )
                # cancel-aware sleep（同 _handle_risk_signal 的 auto_retry 模式）
                try:
                    await asyncio.wait_for(
                        self._cancel_event.wait(), timeout=pause_sec,
                    )
                    raise asyncio.CancelledError()
                except asyncio.TimeoutError:
                    pass  # 正常等满
                # 暂停由用户主动 pause API 触发的也响应（_wait_if_paused 已在下次循环顶生效）

    async def _handle_risk_signal(
        self, sig: RiskControlSignal, retry: int, item_idx: int | None,
    ) -> str:
        """根据 signal 返回 'retry' / 'skip' / 'user_resumed' / 'abort'。"""
        spec = sig.spec
        action = spec["action"]

        if action == "auto_retry":
            max_retries = spec.get("max_retries", 1)
            if retry < max_retries:
                wait_sec = spec.get("wait_sec", 30)
                log.warning("[task %d] auto_retry %s: 等 %ds 后重试 (%d/%d)",
                            self.task_id, sig.signal_id, wait_sec, retry + 1, max_retries)
                await self._emit_progress(
                    phase="retry_waiting",
                    signal_id=sig.signal_id,
                    wait_sec=wait_sec,
                    attempt=retry + 1,
                    max_retries=max_retries,
                )
                # 用 wait_for + cancel_event 让 sleep 期间 cancel 也立即生效
                try:
                    await asyncio.wait_for(self._cancel_event.wait(), timeout=wait_sec)
                    # 取消信号触发
                    raise asyncio.CancelledError()
                except asyncio.TimeoutError:
                    pass  # 正常等满,继续 retry
                return "retry"
            # 重试用完 → 降级 skip
            log.warning("[task %d] auto_retry %s 重试用完,降级 skip_item",
                        self.task_id, sig.signal_id)
            return "skip"

        if action == "user_action":
            # 暂停任务,等用户点继续 —— 风控触发 → paused_kind='verification'
            log.warning("[task %d] user_action %s: 暂停等用户介入", self.task_id, sig.signal_id)
            self._resume_event.clear()
            await db.update_task_status(
                self.task_id, "paused_user_action",
                paused_signal=sig.signal_id,
                paused_kind="verification",
                paused_at_idx=item_idx,
                paused_at=_now(),
            )
            await self._emit_paused(sig.signal_id, spec, item_idx or 0)
            # 阻塞等
            await self._resume_event.wait()
            # 用户已 resume(或 cancel)
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()
            await db.update_task_status(
                self.task_id, "running",
                paused_signal=None,
                paused_kind="",
                resumed_at=_now(),
            )
            await self._emit_resumed(item_idx or 0)
            return "user_resumed"

        if action == "skip_item":
            return "skip"

        # abort
        return "abort"


# ── 工具函数 ──────────────────────────────────────────────────────────────

class _AbortSignal(Exception):
    """内部信号,_run_step_* 抛 → run() 捕获走 finalize('failed')。"""
    pass


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _item_summary(item: Any) -> str:
    """item 的简短摘要(给前端进度条 + skipped 列表显示)。
    覆盖三平台的常见命名:
      Boss:    jobName / brandName / geekName
      LinkedIn: title / firstName+lastName / publicIdentifier
      Indeed:  title / companyName / candidateName
    """
    if isinstance(item, dict):
        # 优先级:具体 title 字段 > 名字 > 公司 > id
        for k in ("title", "jobName", "job_title",
                  "name", "candidate_name", "candidateName",
                  "geekName", "publicIdentifier",
                  "brandName", "companyName", "company",
                  "id"):
            v = item.get(k)
            if v:
                return str(v)[:40]
        # firstName + lastName 拼接(LinkedIn profile)
        first = item.get("firstName", "")
        last = item.get("lastName", "")
        if first or last:
            return f"{first} {last}".strip()[:40]
    return str(item)[:40]


# ── 启动后台 task ─────────────────────────────────────────────────────────

# 持有所有运行中 asyncio.Task 引用,避免被 GC 回收
_BG_TASKS: set[asyncio.Task] = set()


async def run_task_in_background(
    task_id: int,
    template_id: str,
    inputs: dict,
) -> None:
    """启动新任务的后台 asyncio.Task。
    caller(tasks_router POST /tasks/run)调完立即返,任务在 backend 自己跑。"""
    template = get_template(template_id)
    if template is None:
        await db.update_task_status(task_id, "failed", error=f"unknown template: {template_id}")
        return

    # 全局并发上限
    n_global = await db.count_global_running_tasks()
    if n_global >= MAX_GLOBAL_TASKS:
        await db.update_task_status(
            task_id, "failed",
            error=f"全局任务上限 {MAX_GLOBAL_TASKS},稍后重试",
        )
        return

    runner = TaskRunner(task_id, template)

    async def _wrap():
        try:
            await runner.run({"inputs": inputs})
        except Exception:
            log.exception("[task %d] runner crashed", task_id)

    bt = asyncio.create_task(_wrap(), name=f"task-runner-{task_id}")
    _BG_TASKS.add(bt)
    bt.add_done_callback(_BG_TASKS.discard)


async def cancel_task(task_id: int) -> bool:
    """外部触发取消。本进程持有 → 立即 cancel;不持有 → 写 Redis signal,
    持有者(另一个 gateway 实例)的 heartbeat tick 会捕获并取消。
    返 True = 信号已投递(本地或 Redis 至少一个成功)。"""
    runner = _running_runners.get(task_id)
    if runner:
        runner.cancel()
        return True
    # 跨实例:写 Redis signal
    holder = await lease.get_lease_holder(task_id)
    if holder:
        await lease.signal_cancel(task_id)
        return True
    return False  # task 已结束 / 无人持有


async def resume_task(task_id: int) -> bool:
    """用户点"继续"。本进程持有 → 直接 set event;不持有 → 写 Redis signal。"""
    runner = _running_runners.get(task_id)
    if runner:
        runner.resume()
        return True
    holder = await lease.get_lease_holder(task_id)
    if holder:
        await lease.signal_resume(task_id)
        return True
    return False


async def pause_task(task_id: int, kind: str = "user") -> bool:
    """用户主动暂停(C2)。本进程持有 → 标记 _pause_request_kind,下个 step 边界生效;
    不持有 → 返 False(暂不跨实例,后续可通过 Redis signal_pause 扩)。
    返 True = 本地标记成功(任务最终会在 step 边界 enter pause);False = 任务不存在或被其它实例持有。

    注意:这是软暂停,不强行打断当前 step 执行 —— iter step 中途不暂停,只在 step 之间生效。
    若需要立即暂停,应该用 cancel(不可恢复)。"""
    runner = _running_runners.get(task_id)
    if runner:
        runner.request_pause(kind)
        return True
    # 跨实例暂时不支持(lease 没 signal_pause 通道);后续 sprint 加
    holder = await lease.get_lease_holder(task_id)
    if holder:
        log.warning("[task %d] pause_task: held by other instance, cross-instance pause not implemented", task_id)
        return False
    return False
