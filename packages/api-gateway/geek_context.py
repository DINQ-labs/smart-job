"""
GeekContext: 维护每个候选人（求职者）的 securityId 令牌链状态（招聘方视角）。

令牌链：
  boss_search_candidates → geekCard.securityId (search_security_id)
      ↓
  boss_geek_info(uid, search_security_id) → data.securityId (detail_security_id)
                                           + data.encryptExpectId
      ↓
  boss_boss_enter(encryptGeekId, encryptJobId, detail_security_id, encryptExpectId)
      ↓
  (聊天会话)
      ↓
  boss_resume_preview_check → authority_id
      ↓
  boss_resume_download(encryptGeekId, authority_id)

GeekContextStore 是网关侧的镜像，真实令牌由扩展的 TokenStore 持有，
网关通过命令响应同步最新状态。每个 SessionEntry 持有一个独立实例。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


TOKEN_TTL_SEC = 5 * 60    # 5 分钟：securityId 有效期（与 job token 一致）
PROFILE_TTL_SEC = 60 * 60  # 1 小时：候选人资料快照有效期


@dataclass
class GeekContext:
    encrypt_geek_id: str       # 主键，来自搜索结果 geekCard.encryptGeekId

    # ── 令牌链（TOKEN_TTL_SEC）─────────────────────────────────────────────
    search_security_id: str = ""   # 搜索结果中的 geekCard.securityId（用于 boss_geek_info）
    detail_security_id: str = ""   # boss_geek_info 返回的新 securityId（用于 bossEnter）
    encrypt_expect_id: str = ""    # boss_geek_info 返回（bossEnter 必填）
    authority_id: str = ""         # boss_resume_preview_check 返回（下载简历用）
    token_updated_at: float = field(default_factory=time.time)

    # ── 稳定标识符（无 TTL）────────────────────────────────────────────────
    uid: str = ""                  # 明文 uid（boss_boss_chat_history 用）
    source_encrypt_job_id: str = ""  # 从哪个职位搜索结果中发现的

    # ── 资料快照（PROFILE_TTL_SEC）────────────────────────────────────────
    name: str = ""
    city: str = ""
    work_year: str = ""
    salary: str = ""
    active_desc: str = ""
    current_work: str = ""         # 如 "百度·产品经理"
    school: str = ""               # 最高学历院校
    degree_name: str = ""
    apply_status: int = -1         # -1=未知, 0=未处理, 1=合适, 2=不合适
    friend_relation_status: int = -1  # -1=未知, 0=陌生人, 1=已沟通
    profile_updated_at: float = field(default_factory=time.time)

    # ── 交互状态（持久，不随 TTL 清理）────────────────────────────────────
    contacted: bool = False        # 是否已调用 boss_boss_enter 进入聊天
    contact_job_id: str = ""       # 进入聊天时使用的职位 ID

    # ──────────────────────────────────────────────────────────────────────

    def is_token_stale(self, ttl: float = TOKEN_TTL_SEC) -> bool:
        return time.time() - self.token_updated_at > ttl

    def is_profile_stale(self, ttl: float = PROFILE_TTL_SEC) -> bool:
        return time.time() - self.profile_updated_at > ttl

    def touch_token(self) -> None:
        self.token_updated_at = time.time()

    def touch_profile(self) -> None:
        self.profile_updated_at = time.time()

    def get_enter_security_id(self) -> str:
        """bossEnter 所需的 securityId：优先用 detail（最新），回退到 search。"""
        return self.detail_security_id or self.search_security_id

    def to_dict(self) -> dict:
        def _trim(s: str) -> str:
            return (s[:20] + "...") if len(s) > 20 else s

        return {
            "encrypt_geek_id": self.encrypt_geek_id,
            "uid": self.uid,
            "name": self.name,
            "city": self.city,
            "work_year": self.work_year,
            "salary": self.salary,
            "active_desc": self.active_desc,
            "current_work": self.current_work,
            "school": self.school,
            "degree_name": self.degree_name,
            "apply_status": self.apply_status,
            "friend_relation_status": self.friend_relation_status,
            "search_security_id": _trim(self.search_security_id),
            "detail_security_id": _trim(self.detail_security_id),
            "encrypt_expect_id": _trim(self.encrypt_expect_id),
            "authority_id": _trim(self.authority_id),
            "source_encrypt_job_id": self.source_encrypt_job_id,
            "contacted": self.contacted,
            "contact_job_id": self.contact_job_id,
            "token_stale": self.is_token_stale(),
            "profile_stale": self.is_profile_stale(),
            "token_age_sec": int(time.time() - self.token_updated_at),
            "profile_age_sec": int(time.time() - self.profile_updated_at),
        }


class GeekContextStore:
    """网关侧候选人令牌链存储（内存，per-session）。"""

    def __init__(self) -> None:
        self._geeks: dict[str, GeekContext] = {}

    # ── 写入方法 ────────────────────────────────────────────────────────────

    def upsert_from_search_list(
        self,
        geeks_list: list[dict],
        source_encrypt_job_id: str = "",
    ) -> None:
        """
        从 cmd_search_candidates 返回的已展平 geeks 列表批量更新。
        每项应含 encryptGeekId / encrypt_geek_id + securityId / security_id。
        同时更新资料快照（name, city, salary 等）。
        """
        now = time.time()
        for g in geeks_list:
            eid = g.get("encryptGeekId") or g.get("encrypt_geek_id")
            if not eid:
                continue
            ctx = self._ensure(eid)
            sid = g.get("securityId") or g.get("security_id", "")
            if sid:
                ctx.search_security_id = sid
                ctx.token_updated_at = now
            if source_encrypt_job_id:
                ctx.source_encrypt_job_id = source_encrypt_job_id
            self._update_profile_from_flat(ctx, g)
            ctx.profile_updated_at = now

    def update_from_geek_info(self, encrypt_geek_id: str, info_data: dict) -> None:
        """
        从 cmd_boss_get_geek_info 的响应更新令牌链。
        info_data 结构（网关返回值）：
          info_data["raw"]["zpData"]["data"]["securityId"]      → detail_security_id
          info_data["raw"]["zpData"]["data"]["encryptExpectId"] → encrypt_expect_id
          info_data["raw"]["zpData"]["data"]["uid"]             → uid
        """
        raw = info_data.get("raw") or {}
        data = (raw.get("zpData") or {}).get("data") or {}
        ctx = self._ensure(encrypt_geek_id)
        if data.get("securityId"):
            ctx.detail_security_id = data["securityId"]
        if data.get("encryptExpectId"):
            ctx.encrypt_expect_id = data["encryptExpectId"]
        if data.get("uid"):
            ctx.uid = str(data["uid"])
        ctx.touch_token()
        self._update_profile_from_detail(ctx, data)
        ctx.touch_profile()

    def update_from_resume_preview(self, encrypt_geek_id: str, authority_id: str) -> None:
        """记录简历预览权限 ID（boss_resume_preview_check 成功后调用）。"""
        if not authority_id:
            return
        ctx = self._ensure(encrypt_geek_id)
        ctx.authority_id = authority_id
        ctx.touch_token()

    def mark_contacted(self, encrypt_geek_id: str, contact_job_id: str = "") -> None:
        """标记已调用 boss_boss_enter 进入聊天会话。"""
        ctx = self._ensure(encrypt_geek_id)
        ctx.contacted = True
        if contact_job_id:
            ctx.contact_job_id = contact_job_id

    # ── 读取方法 ────────────────────────────────────────────────────────────

    def get(self, encrypt_geek_id: str) -> Optional[GeekContext]:
        return self._geeks.get(encrypt_geek_id)

    def get_enter_security_id(self, encrypt_geek_id: str) -> Optional[str]:
        """返回 bossEnter 所需的 securityId（detail 优先，回退 search）。"""
        ctx = self._geeks.get(encrypt_geek_id)
        if ctx is None:
            return None
        sid = ctx.get_enter_security_id()
        return sid or None

    def get_encrypt_expect_id(self, encrypt_geek_id: str) -> Optional[str]:
        ctx = self._geeks.get(encrypt_geek_id)
        return ctx.encrypt_expect_id if ctx and ctx.encrypt_expect_id else None

    def get_authority_id(self, encrypt_geek_id: str) -> Optional[str]:
        ctx = self._geeks.get(encrypt_geek_id)
        return ctx.authority_id if ctx and ctx.authority_id else None

    def list_all(self) -> list[dict]:
        return [ctx.to_dict() for ctx in self._geeks.values()]

    def list_fresh(self, token_ttl: float = TOKEN_TTL_SEC) -> list[dict]:
        """列出令牌仍有效（未过期）的候选人。"""
        return [
            ctx.to_dict()
            for ctx in self._geeks.values()
            if not ctx.is_token_stale(token_ttl)
        ]

    # ── 维护方法 ────────────────────────────────────────────────────────────

    def evict_stale(
        self,
        token_ttl: float = TOKEN_TTL_SEC,
        profile_ttl: float = PROFILE_TTL_SEC,
    ) -> int:
        """
        清理过期条目：令牌 AND 资料都过期才删除。
        已进入聊天（contacted=True）的条目永不清理。
        返回清理数量。
        """
        stale = [
            eid for eid, ctx in self._geeks.items()
            if not ctx.contacted
            and ctx.is_token_stale(token_ttl)
            and ctx.is_profile_stale(profile_ttl)
        ]
        for eid in stale:
            del self._geeks[eid]
        return len(stale)

    # ── 内部工具 ────────────────────────────────────────────────────────────

    def _ensure(self, encrypt_geek_id: str) -> GeekContext:
        if encrypt_geek_id not in self._geeks:
            self._geeks[encrypt_geek_id] = GeekContext(encrypt_geek_id=encrypt_geek_id)
        return self._geeks[encrypt_geek_id]

    @staticmethod
    def _update_profile_from_flat(ctx: GeekContext, g: dict) -> None:
        """从 search 结果已展平字段更新快照（camelCase 键）。"""
        if g.get("name"):
            ctx.name = g["name"]
        city = g.get("city")
        if city:
            ctx.city = city if isinstance(city, str) else str(city)
        if g.get("workYear"):
            ctx.work_year = g["workYear"]
        if g.get("salary"):
            ctx.salary = g["salary"]
        if g.get("activeDesc"):
            ctx.active_desc = g["activeDesc"]
        if g.get("currentWork"):
            ctx.current_work = g["currentWork"]
        if g.get("school"):
            ctx.school = g["school"]
        if "applyStatus" in g and g["applyStatus"] is not None:
            ctx.apply_status = int(g["applyStatus"])
        if "friendRelationStatus" in g and g["friendRelationStatus"] is not None:
            ctx.friend_relation_status = int(g["friendRelationStatus"])

    @staticmethod
    def _update_profile_from_detail(ctx: GeekContext, data: dict) -> None:
        """从 boss_geek_info 的 zpData.data 对象更新快照。"""
        if data.get("geekName") or data.get("name"):
            ctx.name = data.get("geekName") or data.get("name", "")
        if data.get("activeDesc"):
            ctx.active_desc = data["activeDesc"]
        if data.get("highestDegreeName") or data.get("degreeName"):
            ctx.degree_name = data.get("highestDegreeName") or data.get("degreeName", "")
        gw = data.get("geekWork") or {}
        if isinstance(gw, dict) and gw.get("name"):
            ctx.current_work = gw["name"]
        ge = data.get("geekEdu") or {}
        if isinstance(ge, dict) and ge.get("name"):
            ctx.school = ge["name"]
