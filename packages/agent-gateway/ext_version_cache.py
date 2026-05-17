"""Extension version cache (Phase 2: per-ext kind).

前端 onboarding 需要知道扩展的"最新可用版本 / 最低兼容版本 / 下载地址"。
这些信息的 source of truth 在 job-api-gateway 的 GET /ext/version?ext=X 里。

Phase 2 改动:job-seeker-ext / job-recruiter-ext 各自独立版本号,所以 cache 也按
ext_kind 分两份缓存,SSE connected 事件按当前 session.role 推对应那份。

每 60s per kind 从 api-gateway fetch 一次。cache miss 时阻塞拉一次。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

import config

log = logging.getLogger(__name__)

_TTL_SEC = 60.0
_HTTP_TIMEOUT_SEC = 3.0

_DEFAULT: dict[str, Any] = {
    "ext": "",
    "version": "unknown",
    "commit": "",
    "min_compatible": "",
    "download_url": "",
    "chrome_store_url": "",
}

# per-kind cache: { 'jobseeker': {...}, 'recruiter': {...} }
_cache: dict[str, dict[str, Any]] = {}
_cached_at: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}


def _kind_lock(kind: str) -> asyncio.Lock:
    if kind not in _locks:
        _locks[kind] = asyncio.Lock()
    return _locks[kind]


async def _fetch_once(kind: str) -> dict[str, Any]:
    url = f"{config.JOB_API_GATEWAY_URL.rstrip('/')}/ext/version"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        r = await client.get(url, params={"ext": kind})
        r.raise_for_status()
        data = r.json()
    # 过滤只保留已知字段,避免意外透传
    out = {k: data.get(k, v) for k, v in _DEFAULT.items()}
    out["ext"] = kind
    return out


async def get_ext_version(kind: str = "jobseeker", force: bool = False) -> dict[str, Any]:
    """返回 {ext, version, commit, min_compatible, download_url, chrome_store_url}。

    kind: 'jobseeker' | 'recruiter' (默认 jobseeker 向下兼容老调用)
    TTL 60s per kind;cache miss 阻塞拉一次(有锁防并发风暴);fetch 失败不抛,
    保留上次值或默认值,降级日志。
    """
    if kind not in ("jobseeker", "recruiter"):
        kind = "jobseeker"

    now = time.monotonic()
    cached = _cache.get(kind)
    cached_at = _cached_at.get(kind, 0.0)
    if cached and not force and (now - cached_at) < _TTL_SEC:
        return dict(cached)

    async with _kind_lock(kind):
        # 双检,被其它协程刚刷过就走开
        now = time.monotonic()
        cached = _cache.get(kind)
        cached_at = _cached_at.get(kind, 0.0)
        if cached and not force and (now - cached_at) < _TTL_SEC:
            return dict(cached)

        try:
            data = await _fetch_once(kind)
            _cache[kind] = data
            _cached_at[kind] = time.monotonic()
            return dict(data)
        except Exception as e:
            log.warning("[ext_version] fetch failed kind=%s: %s", kind, e)
            # 失败 → 返回上次缓存(若有)或默认
            return dict(cached) if cached else {**_DEFAULT, "ext": kind}
