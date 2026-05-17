"""
proxy_pool.py: 代理 IP 池管理。

从环境变量加载代理列表，按策略为每个会话分配代理。支持运行时动态增删。

配置（环境变量）:
  PROXY_POOL               逗号分隔的代理列表
                           支持格式: http://ip:port、https://ip:port、socks5://ip:port、socks4://ip:port
                           示例: http://1.2.3.4:8080,socks5://5.6.7.8:1080
  PROXY_ASSIGN_STRATEGY    分配策略（默认 round_robin）
                           round_robin  轮询（默认）
                           random       随机
                           sticky       粘性（同 browser_id 尽量复用同一代理）
"""
from __future__ import annotations

import os
import random as _random
import threading
from dataclasses import dataclass, field


@dataclass
class _ProxyEntry:
    url: str
    assigned_sessions: set[str] = field(default_factory=set)

    @property
    def in_use(self) -> int:
        return len(self.assigned_sessions)


class ProxyPool:
    def __init__(self) -> None:
        self._proxies: list[_ProxyEntry] = []
        self._lock = threading.Lock()
        self._strategy = os.environ.get("PROXY_ASSIGN_STRATEGY", "round_robin").lower()
        self._rr_index = 0
        # sticky 模式: browser_id → proxy_url
        self._sticky: dict[str, str] = {}
        # 当前分配: session_id → proxy_url
        self._assigned: dict[str, str] = {}

        pool_env = os.environ.get("PROXY_POOL", "").strip()
        if pool_env:
            for p in pool_env.split(","):
                p = p.strip()
                if p:
                    self._proxies.append(_ProxyEntry(url=p))

    # ── 内部工具 ───────────────────────────────────────────────────────────

    def _find(self, url: str) -> _ProxyEntry | None:
        for e in self._proxies:
            if e.url == url:
                return e
        return None

    def _pick(self) -> _ProxyEntry | None:
        """按策略选一个代理（不含 sticky 逻辑）。"""
        if not self._proxies:
            return None
        if self._strategy == "random":
            return _random.choice(self._proxies)
        # round_robin（默认）
        entry = self._proxies[self._rr_index % len(self._proxies)]
        self._rr_index += 1
        return entry

    # ── 公共 API ───────────────────────────────────────────────────────────

    def assign(self, session_id: str, browser_id: str = "") -> str:
        """
        为会话自动分配代理，返回代理 URL。
        无代理池时返回空字符串（直连）。
        """
        with self._lock:
            if not self._proxies:
                return ""

            # sticky 策略：同 browser_id 尽量复用上次分配的代理
            if self._strategy == "sticky" and browser_id:
                cached = self._sticky.get(browser_id, "")
                if cached:
                    entry = self._find(cached)
                    if entry:
                        entry.assigned_sessions.add(session_id)
                        self._assigned[session_id] = entry.url
                        return entry.url

            entry = self._pick()
            if entry is None:
                return ""

            entry.assigned_sessions.add(session_id)
            self._assigned[session_id] = entry.url
            if browser_id:
                self._sticky[browser_id] = entry.url
            return entry.url

    def release(self, session_id: str) -> None:
        """释放会话的代理分配（会话断开时调用）。"""
        with self._lock:
            url = self._assigned.pop(session_id, None)
            if url:
                entry = self._find(url)
                if entry:
                    entry.assigned_sessions.discard(session_id)

    def override(self, session_id: str, proxy_url: str) -> None:
        """
        手动为指定会话设置代理（覆盖自动分配）。
        proxy_url 为空时清除代理（直连）。
        若代理不在池中，临时加入池。
        """
        with self._lock:
            old_url = self._assigned.pop(session_id, None)
            if old_url:
                entry = self._find(old_url)
                if entry:
                    entry.assigned_sessions.discard(session_id)

            if proxy_url:
                entry = self._find(proxy_url)
                if not entry:
                    entry = _ProxyEntry(url=proxy_url)
                    self._proxies.append(entry)
                entry.assigned_sessions.add(session_id)
                self._assigned[session_id] = proxy_url

    def get(self, session_id: str) -> str:
        """获取会话当前分配的代理（未分配返回空字符串）。"""
        return self._assigned.get(session_id, "")

    def add(self, proxy_url: str) -> bool:
        """动态添加代理到池，已存在返回 False。"""
        with self._lock:
            if self._find(proxy_url):
                return False
            self._proxies.append(_ProxyEntry(url=proxy_url))
            return True

    def remove(self, proxy_url: str) -> bool:
        """
        动态从池中移除代理，返回是否成功。
        已分配该代理的会话不受影响，直到重新分配。
        """
        with self._lock:
            entry = self._find(proxy_url)
            if not entry:
                return False
            self._proxies.remove(entry)
            return True

    def list_all(self) -> list[dict]:
        """列出所有代理及使用情况。"""
        with self._lock:
            return [
                {
                    "url": e.url,
                    "in_use": e.in_use,
                    "sessions": list(e.assigned_sessions),
                }
                for e in self._proxies
            ]

    @property
    def size(self) -> int:
        return len(self._proxies)

    @property
    def strategy(self) -> str:
        return self._strategy


# 单例
proxy_pool = ProxyPool()
