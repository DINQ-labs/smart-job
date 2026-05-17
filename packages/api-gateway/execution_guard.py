# job-api-gateway/execution_guard.py
"""
执行决策服务（ExecutionGuard）
在 send_command_to() 汇聚点上做统一风险评估，决策可拦截，每次写入 execution_decisions 表。
"""
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

CONSUMPTIVE_PATHS = {"boss/start_chat", "boss/contact_candidate", "boss/send_message"}
BEIJING = timezone(timedelta(hours=8))


@dataclass
class Decision:
    action: str          # "allow" | "delay" | "warn" | "block"
    reason: str          # e.g. "burst_rate_exceeded"
    detail: str = ""     # human-readable
    extra_delay_ms: int = 0


class ExecutionGuard:
    def __init__(self) -> None:
        # session_id → deque of monotonic timestamps for consumptive ops (last 60s)
        self._burst: dict[str, deque] = defaultdict(deque)
        # session_id → last command monotonic time
        self._last_cmd: dict[str, float] = {}

    async def evaluate(
        self,
        session_id: str,
        agent_id: str,
        tool_name: str,
        path: str,
        app_user_id: str = "",
    ) -> Decision:
        import db  # 避免循环导入
        now = time.monotonic()
        now_wall = time.time()
        decision = Decision(action="allow", reason="ok")

        # Rule 2: 操作间隔过短检测
        last = self._last_cmd.get(session_id)
        if last is not None:
            gap = now - last
            if gap < 0.8:
                wait_ms = int((0.8 - gap) * 1000)
                decision = Decision("delay", "min_interval",
                    f"距上次命令仅 {gap*1000:.0f}ms，需等待至800ms间隔",
                    extra_delay_ms=wait_ms)

        self._last_cmd[session_id] = now

        # Rule 1: 突发检测（消耗型操作，滑动窗口60s）
        if path in CONSUMPTIVE_PATHS:
            dq = self._burst[session_id]
            cutoff = now - 60
            while dq and dq[0] < cutoff:
                dq.popleft()
            dq.append(now)
            count = len(dq)
            if count > 6:
                decision = Decision("block", "burst_rate_exceeded",
                    f"消耗型操作在60s内已执行 {count} 次（上限6次）")
            elif count > 3 and decision.action != "block":
                extra = random.randint(3000, 6000)
                decision = Decision("delay", "burst_rate_warning",
                    f"消耗型操作60s内 {count} 次，追加延迟", extra_delay_ms=extra)

        # Rule 3: 近期错误率检测（仅消耗型，查DB）
        if path in CONSUMPTIVE_PATHS and decision.action != "block":
            try:
                recent = await db.list_commands(session_id=session_id, limit=10)
                if len(recent) >= 5:
                    fails = sum(1 for c in recent if c.get("response_ok") == 0)
                    rate = fails / len(recent)
                    if rate > 0.6:
                        decision = Decision("block", "high_error_rate",
                            f"近10条命令错误率 {rate:.0%}，疑似会话异常")
                    elif rate > 0.4 and decision.action == "allow":
                        decision = Decision("warn", "elevated_error_rate",
                            f"近10条命令错误率 {rate:.0%}")
            except Exception:
                pass

        # Rule 4: 深夜时段标记（北京时间 02:00–06:00）
        bj_hour = datetime.fromtimestamp(now_wall, tz=BEIJING).hour
        if 2 <= bj_hour < 6 and decision.action == "allow":
            decision = Decision("warn", "late_night_operation",
                f"北京时间 {bj_hour:02d}:xx 深夜操作")

        # 写决策日志（fire-and-forget，不阻塞主流程）
        try:
            await db.log_execution_decision(
                session_id=session_id,
                agent_id=agent_id or "",
                tool_name=tool_name or "",
                path=path,
                action=decision.action,
                reason=decision.reason,
                detail=decision.detail,
                extra_delay_ms=decision.extra_delay_ms,
            )
        except Exception:
            pass

        return decision


execution_guard = ExecutionGuard()
