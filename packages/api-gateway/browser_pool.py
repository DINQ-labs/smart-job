"""
browser_pool.py: 浏览器槽位池管理器

职责：
- 槽位注册/释放/清理（状态机）
- 外部用户排队与分配
- 超时自动驱逐（asyncio background task）
- 数据隔离：用户切换时彻底清理旧数据

状态机:
  offline ──(WS connect, first time)──→ idle
  offline ──(WS connect, had assignment)──→ busy  (需管理员 force-release)
  idle    ──(acquire)──→ busy
  busy    ──(release / timeout)──→ cleaning
  cleaning──(cleanup OK)──→ idle
  cleaning──(WS drops during cleanup)──→ offline
  any     ──(WS disconnect)──→ offline
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import db
from session_store import session_store
from quota_tracker import quota_tracker
from job_context import JobContextStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _QueueEntry:
    app_user_id: str
    platform: str
    timeout_sec: int
    future: asyncio.Future  # resolved with {"ok": True, "browser_id": ..., "session_id": ...}


class BrowserPool:
    """浏览器槽位池单例。"""

    def __init__(self) -> None:
        self._assignments: dict[str, str] = {}       # browser_id -> app_user_id
        self._queues: dict[str, asyncio.Queue] = {}   # platform -> FIFO Queue[_QueueEntry]
        self._waiters: dict[str, _QueueEntry] = {}    # app_user_id -> QueueEntry
        self._max_slots: dict[str, int] = {}          # platform -> limit
        self._idle_timeout: dict[str, int] = {}       # platform -> seconds
        self._lock = asyncio.Lock()

    # ── 启动时初始化 ──────────────────────────────────────────────────────────

    async def load_config(self) -> None:
        """从 DB 加载平台容量配置。"""
        try:
            configs = await db.list_platform_config()
            for cfg in configs:
                plat = cfg["platform"]
                self._max_slots[plat] = int(cfg.get("max_slots", 50))
                self._idle_timeout[plat] = int(cfg.get("idle_timeout_sec", 7200))
            print(f"[pool] 已加载平台配置: {list(self._max_slots.keys())}", flush=True)
        except Exception as e:
            print(f"[pool] load_config 失败，使用默认值: {e}", flush=True)
            self._max_slots.setdefault("bosszp", 50)
            self._idle_timeout.setdefault("bosszp", 7200)

    async def restore_assignments(self) -> None:
        """启动时从 DB 恢复 busy 槽位状态到内存。"""
        try:
            slots = await db.list_browser_slots(state="busy")
            for slot in slots:
                bid = slot["browser_id"]
                uid = slot.get("app_user_id", "")
                if bid and uid:
                    self._assignments[bid] = uid
            if slots:
                print(f"[pool] 恢复 {len(slots)} 个 busy 槽位", flush=True)
        except Exception as e:
            print(f"[pool] restore_assignments 失败: {e}", flush=True)

    # ── 扩展连接/断开事件 ─────────────────────────────────────────────────────

    async def on_browser_connected(self, browser_id: str, platform: str, session_id: str, ip: str) -> None:
        """扩展 WS 连接时调用。"""
        async with self._lock:
            existing = await db.get_browser_slot(browser_id)
            if existing and existing.get("app_user_id"):
                # 曾经有分配，重连后状态需人工确认
                new_state = "busy"
            else:
                new_state = "idle"
            await db.upsert_browser_slot(
                browser_id,
                platform=platform,
                slot_state=new_state,
                last_seen_at=_utc_now(),
                last_session_id=session_id,
                ip_address=ip,
            )
            print(f"[pool] 浏览器连接: {browser_id[:12]} platform={platform} state={new_state}", flush=True)
            # 若变为 idle，尝试分配给排队用户
            if new_state == "idle":
                asyncio.create_task(self._dispatch_next(platform))

    async def on_browser_disconnected(self, browser_id: str) -> None:
        """扩展 WS 断开时调用。保留槽位记录并标记为 offline，防止重连后 Cookie 泄漏给新用户。"""
        async with self._lock:
            uid = self._assignments.pop(browser_id, None)
            # 保留记录（含 app_user_id），标记为 offline
            # 重连时 on_browser_connected 会检测到 app_user_id 存在，将状态设为 busy 而非 idle，
            # 避免将含有旧用户 Cookie 的浏览器自动分配给排队用户
            await db.upsert_browser_slot(
                browser_id,
                slot_state="offline",
                last_seen_at=_utc_now(),
            )
            if uid:
                print(f"[pool] 浏览器 {browser_id[:12]} 断开 → offline（原分配用户: {uid}）", flush=True)
            else:
                print(f"[pool] 浏览器 {browser_id[:12]} 已离线", flush=True)

    # ── 公开 API ──────────────────────────────────────────────────────────────

    async def acquire(self, app_user_id: str, platform: str, timeout_sec: int = 60) -> dict:
        """
        为外部用户获取一个浏览器槽位。
        若有空闲槽位立即返回；否则排队等待 timeout_sec 秒。
        返回 {"ok": True, "browser_id": ..., "session_id": ...}
        或   {"ok": False, "error": ...}
        """
        if not app_user_id:
            return {"ok": False, "error": "app_user_id 不能为空"}

        async with self._lock:
            # 检查是否已有分配（内存）
            for bid, uid in self._assignments.items():
                if uid == app_user_id:
                    slot = await db.get_browser_slot(bid)
                    sid = (slot or {}).get("last_session_id", "")
                    return {"ok": True, "browser_id": bid, "session_id": sid, "reused": True}

            # 内存未命中时兜底查 DB（浏览器重连后 _assignments 未同步、或服务重启后 offline 槽位未恢复）
            db_slot = await db.get_browser_slot_by_app_user(app_user_id)
            if db_slot:
                bid = db_slot["browser_id"]
                sid = db_slot.get("last_session_id", "")
                self._assignments[bid] = app_user_id
                print(f"[pool] 从 DB 恢复分配: browser={bid[:12]} → user={app_user_id} state={db_slot.get('slot_state')}", flush=True)
                return {"ok": True, "browser_id": bid, "session_id": sid, "reused": True}

            # 尝试找空闲槽位
            idle = await db.find_idle_browser_slot(platform)
            if idle:
                return await self._do_assign(idle["browser_id"], app_user_id, platform)

        # 没有空闲槽位，排队
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        entry = _QueueEntry(
            app_user_id=app_user_id,
            platform=platform,
            timeout_sec=timeout_sec,
            future=fut,
        )
        queue = self._queues.setdefault(platform, asyncio.Queue())
        await queue.put(entry)
        self._waiters[app_user_id] = entry
        print(f"[pool] 用户 {app_user_id} 排队等待 {platform} 浏览器（超时 {timeout_sec}s）", flush=True)

        try:
            result = await asyncio.wait_for(fut, timeout=timeout_sec)
            return result
        except asyncio.TimeoutError:
            self._waiters.pop(app_user_id, None)
            return {"ok": False, "error": f"等待超时（{timeout_sec}s），没有可用浏览器"}

    async def release(self, app_user_id: str) -> dict:
        """释放用户占用的浏览器槽位，触发清理流程。"""
        browser_id = None
        async with self._lock:
            for bid, uid in list(self._assignments.items()):
                if uid == app_user_id:
                    browser_id = bid
                    break

        if not browser_id:
            return {"ok": False, "error": f"用户 {app_user_id} 没有已分配的浏览器"}

        asyncio.create_task(self._do_cleanup(browser_id, app_user_id))
        return {"ok": True, "message": f"正在释放浏览器 {browser_id[:12]}，清理中..."}

    async def get_session_for_user(self, app_user_id: str) -> str | None:
        """根据外部用户 ID 查询当前 session_id。"""
        async with self._lock:
            for bid, uid in self._assignments.items():
                if uid == app_user_id:
                    slot = await db.get_browser_slot(bid)
                    return (slot or {}).get("last_session_id") or None
        return None

    async def get_pool_stats(self, platform: str | None = None) -> dict:
        """返回槽位统计信息。"""
        slots = await db.list_browser_slots(platform=platform)
        counts: dict[str, int] = {}
        for s in slots:
            state = s.get("slot_state", "unknown")
            counts[state] = counts.get(state, 0) + 1

        # 计算排队数
        queue_sizes: dict[str, int] = {}
        for plat, q in self._queues.items():
            if platform is None or plat == platform:
                queue_sizes[plat] = q.qsize()

        total_queued = sum(queue_sizes.values())
        total_slots = len(slots)
        return {
            "platform": platform,
            "total": total_slots,
            "idle": counts.get("idle", 0),
            "busy": counts.get("busy", 0),
            "cleaning": counts.get("cleaning", 0),
            "offline": counts.get("offline", 0),
            "queued": total_queued,
            "queue_by_platform": queue_sizes,
            "max_slots": self._max_slots.get(platform, self._max_slots.get("bosszp", 50)) if platform else self._max_slots,
        }

    async def set_platform_capacity(self, platform: str, max_slots: int, idle_timeout_sec: int | None = None) -> None:
        """更新平台容量配置。"""
        self._max_slots[platform] = max_slots
        fields: dict[str, Any] = {"max_slots": max_slots}
        if idle_timeout_sec is not None:
            self._idle_timeout[platform] = idle_timeout_sec
            fields["idle_timeout_sec"] = idle_timeout_sec
        await db.upsert_platform_config(platform, **fields)

    async def force_release(self, browser_id: str) -> dict:
        """管理员强制释放槽位（跳过清理流程直接置为 idle）。"""
        async with self._lock:
            uid = self._assignments.pop(browser_id, None)
            slot = await db.get_browser_slot(browser_id)
            if not slot:
                return {"ok": False, "error": f"browser_id {browser_id} 不存在"}
            await db.upsert_browser_slot(
                browser_id,
                slot_state="idle",
                app_user_id="",
                assigned_at="",
            )
            if uid:
                await db.log_pool_assignment(browser_id, uid, slot.get("platform", ""), "force_evicted",
                                             slot.get("last_session_id", ""))
            platform = slot.get("platform", "bosszp")

        asyncio.create_task(self._dispatch_next(platform))
        return {"ok": True, "browser_id": browser_id, "evicted_user": uid or ""}

    async def list_queue(self, platform: str | None = None) -> list[dict]:
        """返回排队中的用户列表。"""
        result = []
        for plat, q in self._queues.items():
            if platform and plat != platform:
                continue
            # asyncio.Queue 不支持直接迭代，通过 _waiters 获取
        for uid, entry in self._waiters.items():
            if platform and entry.platform != platform:
                continue
            result.append({
                "app_user_id": uid,
                "platform": entry.platform,
                "timeout_sec": entry.timeout_sec,
            })
        return result

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    async def _do_assign(self, browser_id: str, app_user_id: str, platform: str) -> dict:
        """将槽位分配给用户（在锁内调用）。"""
        now = _utc_now()
        self._assignments[browser_id] = app_user_id
        slot = await db.get_browser_slot(browser_id)
        session_id = (slot or {}).get("last_session_id", "")
        await db.upsert_browser_slot(
            browser_id,
            slot_state="busy",
            app_user_id=app_user_id,
            assigned_at=now,
        )
        await db.log_pool_assignment(browser_id, app_user_id, platform, "acquired", session_id)
        print(f"[pool] 分配: browser={browser_id[:12]} → user={app_user_id} session={session_id[:16] if session_id else ''}", flush=True)
        return {"ok": True, "browser_id": browser_id, "session_id": session_id}

    async def _do_cleanup(self, browser_id: str, app_user_id: str) -> bool:
        """
        清理数据隔离关键路径：
        1. 标记 cleaning
        2. 发 logout 命令给 extension（清除 cookies + tokenStore）
        3. 清空内存中该会话状态
        4. 置为 idle，通知下一个排队用户
        """
        from ext_client import send_command_to  # 延迟导入避免循环

        slot = await db.get_browser_slot(browser_id)
        if not slot:
            return False
        platform = slot.get("platform", "bosszp")
        session_id = slot.get("last_session_id", "")

        # Step 1: 标记 cleaning
        await db.upsert_browser_slot(browser_id, slot_state="cleaning")

        # Step 2: 发 logout 命令给 extension
        try:
            await send_command_to(session_id or None, "POST", "boss/logout", timeout_ms=15000)
            print(f"[pool] cleanup: logout 成功 browser={browser_id[:12]}", flush=True)
        except Exception as e:
            print(f"[pool] cleanup: logout 失败（继续清理）: {e}", flush=True)

        # Step 3: 清空内存中会话状态
        if session_id:
            entry = session_store.get(session_id)
            if entry:
                entry.job_store = JobContextStore()
                entry.account_name = ""
                entry.app_user_id = ""
            quota_tracker.reset_session(session_id)

        # Step 4: 更新内存和 DB
        async with self._lock:
            self._assignments.pop(browser_id, None)
            await db.upsert_browser_slot(
                browser_id,
                slot_state="idle",
                app_user_id="",
                assigned_at="",
            )
            await db.log_pool_assignment(browser_id, app_user_id, platform, "cleaned", session_id)

        print(f"[pool] cleanup 完成: browser={browser_id[:12]} → idle", flush=True)

        # Step 5: 分配给下一个排队用户
        asyncio.create_task(self._dispatch_next(platform))
        return True

    async def _dispatch_next(self, platform: str) -> None:
        """将平台队列中的下一个等待用户分配到刚空出的槽位。"""
        queue = self._queues.get(platform)
        if not queue or queue.empty():
            return

        try:
            entry = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        self._waiters.pop(entry.app_user_id, None)

        if entry.future.done():
            return  # 已超时取消

        async with self._lock:
            idle = await db.find_idle_browser_slot(platform)
            if idle:
                result = await self._do_assign(idle["browser_id"], entry.app_user_id, platform)
                if not entry.future.done():
                    entry.future.set_result(result)
            else:
                # 没有空槽了，重新放回队头（简单实现：放回队尾）
                await queue.put(entry)
                self._waiters[entry.app_user_id] = entry

    async def _start_timeout_watcher(self) -> None:
        """后台任务：每60秒扫描 busy 槽位，超时则自动释放。"""
        print("[pool] 超时监控任务已启动（每60秒扫描）", flush=True)
        while True:
            try:
                await asyncio.sleep(60)
                await self._check_timeouts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[pool] 超时监控异常: {e}", flush=True)

    async def _check_timeouts(self) -> None:
        """检查超时的 busy 槽位并自动释放。"""
        slots = await db.list_browser_slots(state="busy")
        now_ts = datetime.now(timezone.utc).timestamp()
        for slot in slots:
            timeout_sec = int(slot.get("idle_timeout_sec") or 0)
            if timeout_sec <= 0:
                continue
            assigned_at_str = slot.get("assigned_at", "")
            if not assigned_at_str:
                continue
            try:
                assigned_ts = datetime.fromisoformat(assigned_at_str).timestamp()
            except Exception:
                continue
            if now_ts - assigned_ts > timeout_sec:
                uid = slot.get("app_user_id", "")
                browser_id = slot["browser_id"]
                print(f"[pool] 超时驱逐: browser={browser_id[:12]} user={uid} (>{timeout_sec}s)", flush=True)
                if uid:
                    await db.log_pool_assignment(
                        browser_id, uid, slot.get("platform", ""), "timeout_evicted",
                        slot.get("last_session_id", ""),
                    )
                asyncio.create_task(self._do_cleanup(browser_id, uid))


# 单例
browser_pool = BrowserPool()
