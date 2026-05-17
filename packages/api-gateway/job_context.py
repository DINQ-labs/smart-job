"""
JobContext: 维护每个职位的 securityId 令牌链状态。
令牌链: listSecurityId → detailSecurityId → chatSecurityId

JobContextStore 是网关侧的镜像，真实令牌由扩展的 TokenStore 持有，
网关通过命令响应同步最新状态。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


TOKEN_TTL_SEC = 5 * 60  # 5分钟


@dataclass
class JobContext:
    encrypt_job_id: str
    list_security_id: str = ""
    detail_security_id: str = ""
    chat_security_id: str = ""
    boss_id: str = ""
    job_name: str = ""
    company_name: str = ""
    lid: str = ""          # 搜索结果中的 lid（如 "9ifueAwdS6H.search.1"），详情请求需要
    updated_at: float = field(default_factory=time.time)

    def is_stale(self, ttl: float = TOKEN_TTL_SEC) -> bool:
        return time.time() - self.updated_at > ttl

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "encrypt_job_id": self.encrypt_job_id,
            "list_security_id": self.list_security_id[:20] + "..." if len(self.list_security_id) > 20 else self.list_security_id,
            "detail_security_id": self.detail_security_id[:20] + "..." if len(self.detail_security_id) > 20 else self.detail_security_id,
            "chat_security_id": self.chat_security_id[:20] + "..." if len(self.chat_security_id) > 20 else self.chat_security_id,
            "boss_id": self.boss_id,
            "job_name": self.job_name,
            "company_name": self.company_name,
            "stale": self.is_stale(),
            "age_sec": int(time.time() - self.updated_at),
        }


class JobContextStore:
    """网关侧职位令牌链存储（内存）。"""

    def __init__(self) -> None:
        self._jobs: dict[str, JobContext] = {}

    def upsert_from_job_list(self, job_list: list[dict]) -> None:
        """从搜索结果更新 listSecurityId。"""
        for job in job_list:
            jid = job.get("encryptJobId") or job.get("jobId")
            sid = job.get("securityId", "")
            if not jid:
                continue
            ctx = self._jobs.get(jid)
            if ctx is None:
                ctx = JobContext(encrypt_job_id=jid)
                self._jobs[jid] = ctx
            if sid:
                ctx.list_security_id = sid
            ctx.boss_id = job.get("encryptBossId") or job.get("bossId") or ctx.boss_id
            ctx.job_name = job.get("jobName", ctx.job_name)
            ctx.company_name = job.get("brandName", ctx.company_name)
            if job.get("lid"):
                ctx.lid = job["lid"]
            ctx.touch()

    def update_detail(self, encrypt_job_id: str, security_id: str, boss_id: str = "") -> None:
        ctx = self._ensure(encrypt_job_id)
        ctx.detail_security_id = security_id
        if boss_id:
            ctx.boss_id = boss_id
        ctx.touch()

    def update_chat(self, encrypt_job_id: str, security_id: str) -> None:
        ctx = self._ensure(encrypt_job_id)
        ctx.chat_security_id = security_id
        ctx.touch()

    def get(self, encrypt_job_id: str) -> Optional[JobContext]:
        return self._jobs.get(encrypt_job_id)

    def get_list_security_id(self, encrypt_job_id: str) -> Optional[str]:
        ctx = self._jobs.get(encrypt_job_id)
        return ctx.list_security_id if ctx and ctx.list_security_id else None

    def get_detail_security_id(self, encrypt_job_id: str) -> Optional[str]:
        ctx = self._jobs.get(encrypt_job_id)
        return ctx.detail_security_id if ctx and ctx.detail_security_id else None

    def get_chat_security_id(self, encrypt_job_id: str) -> Optional[str]:
        ctx = self._jobs.get(encrypt_job_id)
        return ctx.chat_security_id if ctx and ctx.chat_security_id else None

    def list_all(self) -> list[dict]:
        return [ctx.to_dict() for ctx in self._jobs.values()]

    def _ensure(self, encrypt_job_id: str) -> JobContext:
        if encrypt_job_id not in self._jobs:
            self._jobs[encrypt_job_id] = JobContext(encrypt_job_id=encrypt_job_id)
        return self._jobs[encrypt_job_id]

    def evict_stale(self, ttl: float = TOKEN_TTL_SEC) -> int:
        """清理过期令牌，返回清理数量。"""
        stale = [jid for jid, ctx in self._jobs.items() if ctx.is_stale(ttl)]
        for jid in stale:
            del self._jobs[jid]
        return len(stale)


# 单例
job_store = JobContextStore()
