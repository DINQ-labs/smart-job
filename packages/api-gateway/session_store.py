"""
session_store.py: 多会话注册表，每个扩展 WS 连接对应一个 SessionEntry。
每个会话有独立的 job_store 和 rate_limiter，完全隔离。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from geek_context import GeekContextStore
from job_context import JobContextStore
from rate_limiter import RateLimiter


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionEntry:
    session_id: str           # UUID hex（主键）
    browser_id: str           # 发给扩展的 browserId（与 session_id 相同）
    ws: Any                   # WebSocket 实例
    pending: dict[str, asyncio.Future]
    connected_at: str
    job_store: JobContextStore = field(default_factory=JobContextStore)
    geek_store: GeekContextStore = field(default_factory=GeekContextStore)
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    account_name: str = ""
    user_id: str = ""         # dinQ 系统用户 UUID（扩展连接时由网关注入）
    app_user_id: str = ""     # 平台业务用户 ID（Boss/LinkedIn 等，check_login 写入）
    ip_address: str = ""      # 扩展连接来源 IP
    display_id: int = 0       # 人类可读的 1-based 顺序编号（每次启动重新分配）
    ext_name: str = ""        # 扩展标识名(如 bosszp、linkedin 等)
    ext_kind: str = ""        # Phase 2: 双 ext 区分(jobseeker / recruiter,空=老 ext)
    proxy_url: str = ""       # 当前分配的代理（空字符串=直连）
    # 多站点能力：一个扩展可支持多站点（boss / linkedin / indeed），
    # 扩展在 WS 连接后通过 capabilities 消息上报 sites，check_login 后通过
    # site_status 消息上报对应站点的 app_user_id。
    sites: set[str] = field(default_factory=set)
    site_users: dict[str, str] = field(default_factory=dict)  # {site: app_user_id}


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionEntry] = {}
        self._counter: int = 0   # 全局递增，重启清零
        self._user_session_map: dict[str, str] = {}  # app_user_id → session_id（非 pool 模式）
        # E1 会话亲和：per-user 的 active session 偏好
        # {dinq_user_id: {platform: session_id}} —— 多活会话场景下优先选这个
        self._active_sessions: dict[str, dict[str, str]] = {}

    def set_counter(self, value: int) -> None:
        """启动时从 DB 读取最大 display_id，保证重启后续号不重复。"""
        if value > self._counter:
            self._counter = value

    def register(self, ws: Any, pending: dict[str, asyncio.Future],
                 ip_address: str = "", ext_name: str = "",
                 ext_kind: str = "",
                 stable_browser_id: str = "") -> str:
        """注册新会话,返回 session_id。"""
        session_id = uuid.uuid4().hex
        self._counter += 1
        # 若扩展提供了稳定 browser_id,使用它;否则降级为 session_id[:16]
        browser_id = stable_browser_id if stable_browser_id else session_id[:16]
        entry = SessionEntry(
            session_id=session_id,
            browser_id=browser_id,
            ws=ws,
            pending=pending,
            connected_at=_utc_now(),
            ip_address=ip_address,
            display_id=self._counter,
            ext_name=ext_name,
            ext_kind=ext_kind,
        )
        self._sessions[session_id] = entry
        return session_id

    def unregister(self, session_id: str) -> None:
        entry = self._sessions.pop(session_id, None)
        if entry:
            # 取消所有等待中的命令 future，避免调用方永久挂起到超时
            for fut in list(entry.pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError(f"session {session_id[:16]} disconnected"))
            entry.pending.clear()

    def get(self, session_id: str) -> SessionEntry | None:
        return self._sessions.get(session_id)

    def bind_app_user(self, app_user_id: str, session_id: str) -> None:
        """将应用层 user_id 与扩展 session 绑定（非 pool 模式下的直接映射）。"""
        if app_user_id and session_id:
            self._user_session_map[app_user_id] = session_id

    def get_session_for_app_user(self, app_user_id: str) -> str | None:
        """通过应用层 user_id 查询 session_id（非 pool 模式）。"""
        sid = self._user_session_map.get(app_user_id)
        # 验证 session 仍然在线，否则清除过期映射
        if sid and self.get(sid) is None:
            self._user_session_map.pop(app_user_id, None)
            return None
        return sid

    def get_by_browser_id(self, browser_id: str) -> SessionEntry | None:
        """通过稳定 browser_id 查找会话。"""
        for entry in self._sessions.values():
            if entry.browser_id == browser_id:
                return entry
        return None

    def get_sessions_by_user_id(self, user_id: str) -> list[SessionEntry]:
        """查找所有具有指定 dinQ user_id 的在线会话。"""
        return [e for e in self._sessions.values() if e.user_id == user_id]

    def has_any_connected(self, ext_name: str = "") -> bool:
        """检查是否有任意已连接的会话（可按 ext_name 过滤）。"""
        for entry in self._sessions.values():
            if entry.ws is not None:
                if not ext_name or entry.ext_name == ext_name:
                    return True
        return False

    def set_sites(self, session_id: str, sites: list[str] | set[str]) -> None:
        """扩展 capabilities 上报：记录本 session 能处理哪些站点。"""
        entry = self._sessions.get(session_id)
        if entry is not None:
            entry.sites = {s.strip().lower() for s in sites if s and isinstance(s, str)}

    def set_site_user(self, session_id: str, site: str, app_user_id: str) -> None:
        """记录某 session 下指定站点的平台用户 ID（check_login 成功时写入）。"""
        entry = self._sessions.get(session_id)
        if entry is None or not site:
            return
        site_key = site.strip().lower()
        if app_user_id:
            entry.site_users[site_key] = app_user_id
            # Boss 主站点镜像到 app_user_id 字段，保持与旧代码兼容
            if site_key == "boss":
                entry.app_user_id = app_user_id
        else:
            entry.site_users.pop(site_key, None)

    def find_by_site(self, site: str, app_user_id: str = "") -> "SessionEntry | None":
        """按站点能力查找已连接会话。
        - 指定 app_user_id：只返回该站点下 site_users[site]==app_user_id 的会话
        - 未指定：返回第一个连接中且支持该站点的会话（sites 未上报时视为支持全部，兼容旧扩展）
        """
        site_key = (site or "").strip().lower()
        if not site_key:
            return None
        for entry in self._sessions.values():
            if entry.ws is None:
                continue
            # sites 为空视为兼容旧扩展 —— 旧扩展不知道 capabilities 协议，
            # 但它实际支持 boss/linkedin/indeed 三个站点。
            supports = (not entry.sites) or (site_key in entry.sites)
            if not supports:
                continue
            if app_user_id:
                if entry.site_users.get(site_key) == app_user_id:
                    return entry
            else:
                return entry
        return None

    def resolve_session(self, sid: str) -> SessionEntry:
        """
        严格查找指定 session_id，找不到或 sid 为空则抛 RuntimeError。
        调用方必须通过 browser_pool.acquire() / get_session_for_user() 获取 session_id 后显式传入。
        """
        if not sid:
            raise RuntimeError("session_id 必填，请通过 pool.acquire() 获取后显式传入")
        entry = self.get(sid)
        if entry is None:
            raise RuntimeError(f"session_id 不存在或已断开: {sid}")
        return entry

    # ── 会话亲和：E1 ─────────────────────────────────────────────────────────
    # 同一 DINQ 用户可能有多个扩展会话（Chrome + Edge，或多开浏览器）。
    # 没设定亲和时，_resolve_and_bind / _default_*_session 遇到多活会话会报错
    # 要求显式传 session_id。通过 set_active_session 可以让 Agent 一次性指定
    # "这个平台优先用这个 session"，后续调用自动落到它，直到被清除或下线。

    def set_active_session(self, user_id: str, platform: str, session_id: str) -> None:
        """设定 user_id 在 platform 下的 active session。session_id 必须存在且归属该 user。"""
        if not user_id or not platform or not session_id:
            raise ValueError("user_id / platform / session_id 必填")
        entry = self._sessions.get(session_id)
        if entry is None:
            raise RuntimeError(f"session {session_id[:16]} 不存在")
        if entry.user_id and entry.user_id != user_id:
            raise RuntimeError(
                f"session {session_id[:16]} 归属其他用户，不允许设为 active（多租户隔离）"
            )
        self._active_sessions.setdefault(user_id, {})[platform.lower()] = session_id

    def get_active_session(self, user_id: str, platform: str) -> str | None:
        """取 user_id 在 platform 下的 active session_id，若 session 已下线则清理并返回 None。"""
        if not user_id or not platform:
            return None
        sid = (self._active_sessions.get(user_id) or {}).get(platform.lower())
        if not sid:
            return None
        entry = self._sessions.get(sid)
        if entry is None or entry.ws is None:
            # 已下线：惰性清理，避免返回僵尸 session
            self._active_sessions.get(user_id, {}).pop(platform.lower(), None)
            return None
        return sid

    def clear_active_session(self, user_id: str, platform: str | None = None) -> None:
        """清除 user_id 在 platform 下的 active（platform=None 时清全部）。"""
        if not user_id:
            return
        if platform is None:
            self._active_sessions.pop(user_id, None)
        else:
            self._active_sessions.get(user_id, {}).pop(platform.lower(), None)

    def list_active_sessions(self, user_id: str) -> dict[str, str]:
        """返回 user_id 下所有 platform 的 active session（惰性清理已下线的）。"""
        if not user_id:
            return {}
        out: dict[str, str] = {}
        user_map = self._active_sessions.get(user_id, {})
        for platform in list(user_map.keys()):
            sid = self.get_active_session(user_id, platform)
            if sid:
                out[platform] = sid
        return out

    async def force_disconnect(self, session_id: str) -> None:
        """关闭 WS 并注销会话。"""
        entry = self._sessions.get(session_id)
        if entry and entry.ws:
            try:
                await entry.ws.close()
            except Exception:
                pass
        self.unregister(session_id)

    def list_all(self) -> list[dict]:
        return [
            {
                "session_id": e.session_id,
                "browser_id": e.browser_id,
                "connected_at": e.connected_at,
                "account_name": e.account_name,
                "user_id": e.user_id,
                "app_user_id": e.app_user_id,
                "status": "connected" if e.ws is not None else "disconnected",
                "job_store_count": len(e.job_store.list_all()),
                "geek_store_count": len(e.geek_store.list_all()),
                "ip_address": e.ip_address,
                "display_id": e.display_id,
                "ext_name": e.ext_name,
                "ext_kind": e.ext_kind,  # Phase 2: jobseeker / recruiter / 空(老 ext)
                "proxy_url": e.proxy_url,
                "sites": sorted(e.sites),
                "site_users": dict(e.site_users),
            }
            for e in self._sessions.values()
        ]


# 单例
session_store = SessionStore()
