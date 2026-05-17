"""
quota_tracker.py: 每会话每日配额追踪，防止超出 Boss直聘 账号操作限制。

配额类型与默认上限（可通过环境变量覆盖）：
  candidate_contact   主动沟通候选人（招聘方→求职者）  BOSS_LIMIT_CANDIDATE_CONTACT  默认 20
  job_application     投递工作岗位（求职者→招聘方）    BOSS_LIMIT_JOB_APPLICATION     默认 100

设计要点：
  - 日界按北京时间（UTC+8）计算，跨日自动重置
  - 服务重启后当日计数从 0 开始（不持久化，设计上保守）
  - check_or_raise() 在发送命令前调用；increment() 在命令成功返回后调用
  - 每次 logout 重置对应 session 的计数
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

BEIJING_TZ = timezone(timedelta(hours=8))


class QuotaExceededError(RuntimeError):
    """配额超限异常，携带详细配额信息。"""

    def __init__(self, quota_type: str, used: int, limit: int) -> None:
        self.quota_type = quota_type
        self.used = used
        self.limit = limit
        type_label = {
            "candidate_contact": "主动沟通候选人",
            "job_application": "投递工作岗位",
        }.get(quota_type, quota_type)
        super().__init__(
            f"每日配额已达上限 [{type_label}]: 今日已用 {used}/{limit}。"
            f"明日（北京时间零点）自动重置，或通过环境变量 "
            f"BOSS_LIMIT_{quota_type.upper()} 调高上限。"
        )

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "quota_exceeded": True,
            "quota_type": self.quota_type,
            "used": self.used,
            "limit": self.limit,
            "message": str(self),
        }


@dataclass
class _DailyQuota:
    date_str: str                               # YYYY-MM-DD Beijing time
    counts: dict[str, int] = field(default_factory=dict)


class QuotaTracker:
    """每会话每日配额追踪器（内存，重启清零）。

    Phase 3 改造:配额类型现在由 register_quota_type() 注册,不再 hardcode。
    历史 Boss 配额(candidate_contact / job_application)在模块底部预注册,
    新平台/新配额类型从各自的 bootstrap 模块调 register_quota_type() 接入。
    """

    def __init__(self) -> None:
        self._data: dict[str, _DailyQuota] = {}
        # 运行时配额上限(由 register_quota_type 填充)
        self._limits: dict[str, int] = {}
        # 配额类型 → 环境变量名(供 admin 排错查覆盖来源)
        self._env_var_of: dict[str, str] = {}
        # 配额类型 → 关联 platform(可空,用于按平台过滤展示)
        self._platform_of: dict[str, str] = {}

    def register_quota_type(
        self,
        quota_type: str,
        default_limit: int,
        *,
        env_var: str | None = None,
        platform: str = "",
    ) -> None:
        """注册一个配额类型。env_var 给定时优先读环境变量覆盖。
        重复注册同一 quota_type 会覆盖(后注册者赢),便于运行时调整测试。"""
        limit = default_limit
        if env_var:
            try:
                limit = int(os.getenv(env_var, str(default_limit)))
            except (TypeError, ValueError):
                limit = default_limit
        self._limits[quota_type] = limit
        if env_var:
            self._env_var_of[quota_type] = env_var
        if platform:
            self._platform_of[quota_type] = platform

    # ── 内部 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _today() -> str:
        return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    def _ensure(self, session_id: str) -> _DailyQuota:
        """获取会话的当日配额记录，跨日自动重置。"""
        today = self._today()
        entry = self._data.get(session_id)
        if entry is None or entry.date_str != today:
            entry = _DailyQuota(date_str=today)
            self._data[session_id] = entry
        return entry

    # ── 公开 API ─────────────────────────────────────────────────────────────

    def get_status(self, session_id: str, quota_type: str) -> dict:
        """返回单项配额状态，不改变任何计数。"""
        entry = self._ensure(session_id)
        used = entry.counts.get(quota_type, 0)
        limit = self._limits.get(quota_type, 999_999)
        return {
            "quota_type": quota_type,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "exceeded": used >= limit,
            "date": entry.date_str,
        }

    def check_or_raise(self, session_id: str, quota_type: str) -> None:
        """超限时抛出 QuotaExceededError，否则静默返回。在发送命令前调用。"""
        status = self.get_status(session_id, quota_type)
        if status["exceeded"]:
            raise QuotaExceededError(quota_type, status["used"], status["limit"])

    def increment(self, session_id: str, quota_type: str) -> dict:
        """消费一次配额（命令成功返回后调用），返回最新状态。"""
        entry = self._ensure(session_id)
        entry.counts[quota_type] = entry.counts.get(quota_type, 0) + 1
        return self.get_status(session_id, quota_type)

    def reset_session(self, session_id: str) -> None:
        """logout 时重置该会话所有计数（账号切换后重新开始）。"""
        self._data.pop(session_id, None)

    def get_all_status(self, session_id: str) -> dict[str, dict]:
        """返回所有配额类型的状态。"""
        return {qt: self.get_status(session_id, qt) for qt in self._limits}

    def set_limit(self, quota_type: str, limit: int) -> None:
        """运行时动态调整上限（通过 admin API 调用）。"""
        if limit < 0:
            raise ValueError(f"limit 不能为负数，当前值: {limit}")
        if quota_type not in self._limits:
            raise ValueError(f"未知 quota_type: {quota_type}，可选: {list(self._limits)}")
        self._limits[quota_type] = limit

    def get_limits(self) -> dict[str, int]:
        """返回当前所有配额类型的上限（只读快照）。"""
        return dict(self._limits)

    def list_all(self) -> dict[str, Any]:
        """返回所有会话的配额状态，用于 admin 监控。"""
        return {
            sid: self.get_all_status(sid)
            for sid in self._data
        }


# 单例
quota_tracker = QuotaTracker()

# Boss-side quotas — 历史默认。新平台 / 新配额类型应在各自模块导入时调
# `quota_tracker.register_quota_type(...)` 注册,不要再扩这一处 hardcode。
quota_tracker.register_quota_type(
    "candidate_contact", default_limit=20,
    env_var="BOSS_LIMIT_CANDIDATE_CONTACT", platform="boss",
)
quota_tracker.register_quota_type(
    "job_application", default_limit=100,
    env_var="BOSS_LIMIT_JOB_APPLICATION", platform="boss",
)
