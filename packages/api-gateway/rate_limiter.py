"""
rate_limiter.py: 操作间隔控制,防止平台频率限制触发风控。
随机延迟策略(per op_type):chat 3-8s / search 1-3s / detail 3-7.5s / default 0.5-1.5s。

Phase 3 改造:每个 op_type 的延迟范围由 register_op_type() 注册,不再 hardcode if-elif。
新平台/新 op_type 从各自的 bootstrap 模块调 register_op_type() 接入;调用方 wait()
传 (op_type, platform) 时,优先查 platform-specific override,落回 op_type 默认。
"""
from __future__ import annotations

import asyncio
import random
import time


# (min_sec, max_sec) — 默认 op_type 延迟分布。每条都是 random.uniform 区间。
# 加新 op_type 用 RateLimiter.register_op_type;新平台对某 op_type 想自定义,
# 用 RateLimiter.register_platform_override(platform, op_type, (min,max))。
_DEFAULT_DELAYS: dict[str, tuple[float, float]] = {
    "chat":    (3.0, 8.0),
    "search":  (1.0, 3.0),
    "detail":  (3.0, 7.5),  # boss_get_job_detail 敏感,批量分析第 5 个职位会触发 code=37
    "default": (0.5, 1.5),
}


class RateLimiter:
    """简单的随机延迟速率限制器。op_type 延迟可注册 + per-platform 覆盖。"""

    def __init__(self) -> None:
        self._last_op: dict[str, float] = {}
        # op_type → (min_sec, max_sec)
        self._delays: dict[str, tuple[float, float]] = dict(_DEFAULT_DELAYS)
        # (platform, op_type) → (min_sec, max_sec) — 优先于 _delays
        self._platform_overrides: dict[tuple[str, str], tuple[float, float]] = {}

    def register_op_type(self, op_type: str, min_sec: float, max_sec: float) -> None:
        if max_sec < min_sec or min_sec < 0:
            raise ValueError(f"invalid range: ({min_sec}, {max_sec})")
        self._delays[op_type] = (min_sec, max_sec)

    def register_platform_override(
        self, platform: str, op_type: str, min_sec: float, max_sec: float,
    ) -> None:
        if max_sec < min_sec or min_sec < 0:
            raise ValueError(f"invalid range: ({min_sec}, {max_sec})")
        self._platform_overrides[(platform, op_type)] = (min_sec, max_sec)

    async def wait(self, op_type: str = "default", platform: str = "") -> None:
        """等待该 op_type(可选 platform 覆盖)的随机延迟后返回。"""
        # _last_op 按 (platform, op_type) 隔离,避免 boss 的 search 节奏锁住 linkedin 的 search
        key = f"{platform}:{op_type}" if platform else op_type
        delay = self._get_delay(op_type, platform)
        last = self._last_op.get(key, 0.0)
        elapsed = time.monotonic() - last
        remaining = delay - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_op[key] = time.monotonic()

    def _get_delay(self, op_type: str, platform: str = "") -> float:
        # 1. platform-specific override 优先
        if platform:
            rng = self._platform_overrides.get((platform, op_type))
            if rng is not None:
                return random.uniform(*rng)
        # 2. op_type 默认
        rng = self._delays.get(op_type) or self._delays["default"]
        return random.uniform(*rng)

    def reset(self, op_type: str | None = None) -> None:
        if op_type:
            # 删所有 platform × op_type 的 last_op key,以及无 platform 的 op_type key
            for k in [k for k in self._last_op if k == op_type or k.endswith(f":{op_type}")]:
                self._last_op.pop(k, None)
        else:
            self._last_op.clear()


# 全局单例
rate_limiter = RateLimiter()
