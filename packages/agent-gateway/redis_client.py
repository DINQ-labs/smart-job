"""
Async Redis client — singleton with graceful degradation.

All operations catch exceptions and return fallback values so that
Redis unavailability never breaks the main agent loop.
Pattern follows dinq-probe/infra/redis_cache.py.

Phase 7 additions:
  - Session store: save/load messages + meta for cross-worker SSE recovery
  - Messages compression: recent 20 full, older truncated to 200 chars
  - Distributed turn counter for multi-worker concurrency limiting
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

import config

log = logging.getLogger(__name__)

_client: Optional[aioredis.Redis] = None


async def init() -> None:
    """Connect to Redis. Safe to call multiple times (idempotent)."""
    global _client
    if _client is not None:
        return
    try:
        _client = aioredis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        await _client.ping()
        log.info("Redis connected: %s", config.REDIS_URL)
    except Exception as exc:
        log.warning("Redis connection failed (will operate without Redis): %s", exc)
        _client = None


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


def get_client() -> Optional[aioredis.Redis]:
    """Return the Redis client or None if unavailable."""
    return _client


# ── Convenience helpers (graceful degradation) ────────────────────────────────

async def get(key: str) -> Optional[str]:
    if _client is None:
        return None
    try:
        return await _client.get(key)
    except Exception:
        log.warning("Redis GET failed for %s", key, exc_info=True)
        return None


async def set(key: str, value: str, ttl: int = 0) -> None:
    if _client is None:
        return
    try:
        if ttl > 0:
            await _client.setex(key, ttl, value)
        else:
            await _client.set(key, value)
    except Exception:
        log.warning("Redis SET failed for %s", key, exc_info=True)


async def delete(key: str) -> None:
    if _client is None:
        return
    try:
        await _client.delete(key)
    except Exception:
        log.warning("Redis DELETE failed for %s", key, exc_info=True)


async def incr(key: str) -> Optional[int]:
    if _client is None:
        return None
    try:
        return await _client.incr(key)
    except Exception:
        log.warning("Redis INCR failed for %s", key, exc_info=True)
        return None


async def decr(key: str) -> Optional[int]:
    if _client is None:
        return None
    try:
        return await _client.decr(key)
    except Exception:
        log.warning("Redis DECR failed for %s", key, exc_info=True)
        return None


# ── Session store (Phase 7) ──────────────────────────────────────────────────

_SESS_MSG_PREFIX = "agent:sess:"
_SESS_META_PREFIX = "agent:meta:"
_SESSION_TTL = 7200  # 2 hours

# Fields persisted to Redis meta hash
_META_FIELDS = (
    "current_mode", "platform", "role_type", "entry_type", "user_tier",
    "ext_session_id", "app_user_id", "stable_browser_id",
    "search_session_id", "turn_index", "db_session_id", "title_set",
    "last_turn_tool_usage",
)


def _block_to_dict(block) -> dict:
    """Convert a content block to a plain dict (handles Pydantic models)."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    if hasattr(block, "dict") and callable(block.dict):
        return block.dict()
    # Fallback: should not reach here after agent_loop fix, but guard anyway
    return block


def _is_corrupted_content(content) -> bool:
    """Return True if a list content has any string element (Pydantic→str corruption)."""
    if not isinstance(content, list):
        return False
    return any(isinstance(b, str) for b in content)


def _compress_messages(messages: list[dict], recent_full: int = 20) -> list[dict]:
    """Compress older messages for Redis storage.

    - Last `recent_full` messages: kept in full
    - Older messages: content truncated to 200 chars
    - Pydantic content blocks are converted to plain dicts before serialisation
    """
    if len(messages) <= recent_full:
        return messages

    cutoff = len(messages) - recent_full
    compressed = []
    for msg in messages[:cutoff]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str):
            compressed.append({"role": role, "content": content[:200]})
        elif isinstance(content, list):
            truncated = []
            for block in content:
                b = _block_to_dict(block)
                if isinstance(b, dict):
                    b = dict(b)
                    if "content" in b and isinstance(b["content"], str):
                        b["content"] = b["content"][:200]
                    if "text" in b and isinstance(b["text"], str):
                        b["text"] = b["text"][:200]
                truncated.append(b)
            compressed.append({"role": role, "content": truncated})
        else:
            compressed.append({"role": role, "content": content})

    # Append full recent messages (also normalise any Pydantic blocks)
    for msg in messages[cutoff:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            compressed.append({"role": role, "content": [_block_to_dict(b) for b in content]})
        else:
            compressed.append({"role": role, "content": content})
    return compressed


def _session_keys(user_id: str, role: str, platform: str) -> tuple[str, str]:
    """生成 (msg_key, meta_key) 三元组复合 key — 不同 (user, role, platform) 互不覆盖。"""
    suffix = f"{user_id}:{role}:{platform}"
    return _SESS_MSG_PREFIX + suffix, _SESS_META_PREFIX + suffix


async def save_session(user_id: str, role: str, platform: str,
                       messages: list[dict], meta: dict[str, Any]) -> None:
    """Persist per-platform SSE session to Redis (fire-and-forget safe)."""
    if _client is None:
        return
    try:
        msg_key, meta_key = _session_keys(user_id, role, platform)

        # Compress messages before storing
        compressed = _compress_messages(messages)
        msg_json = json.dumps(compressed, ensure_ascii=False, default=str)

        pipe = _client.pipeline()
        pipe.setex(msg_key, _SESSION_TTL, msg_json)

        # Store meta as flat hash (only string-safe values)
        meta_clean = {}
        for k, v in meta.items():
            if v is None:
                meta_clean[k] = ""
            elif isinstance(v, bool):
                meta_clean[k] = "1" if v else "0"
            else:
                meta_clean[k] = str(v)
        if meta_clean:
            pipe.delete(meta_key)
            pipe.hset(meta_key, mapping=meta_clean)
            pipe.expire(meta_key, _SESSION_TTL)

        await pipe.execute()
    except Exception:
        log.warning("Redis save_session failed for %s", user_id, exc_info=True)


async def load_session(user_id: str, role: str, platform: str) -> tuple[Optional[list[dict]], Optional[dict[str, str]]]:
    """Load per-platform SSE session from Redis. Returns (messages, meta) or (None, None).

    If loaded messages contain corrupted content (list elements that are strings
    rather than dicts — caused by Pydantic objects being serialised via str()),
    the messages are discarded and (None, meta) is returned so the session
    starts fresh rather than crashing with a 400 on the next API call.
    """
    if _client is None:
        return None, None
    try:
        msg_key, meta_key = _session_keys(user_id, role, platform)

        pipe = _client.pipeline()
        pipe.get(msg_key)
        pipe.hgetall(meta_key)
        msg_json, meta_hash = await pipe.execute()

        messages = json.loads(msg_json) if msg_json else None
        meta = meta_hash if meta_hash else None

        # Guard: discard history if any message has corrupted list-of-strings content
        if messages is not None:
            for m in messages:
                if _is_corrupted_content(m.get("content")):
                    log.warning(
                        "Redis session %s has corrupted content (Pydantic→str), "
                        "discarding history to prevent 400 errors",
                        user_id,
                    )
                    messages = None
                    break

        return messages, meta
    except Exception:
        log.warning("Redis load_session failed for %s", user_id, exc_info=True)
        return None, None


async def delete_session(user_id: str, role: str, platform: str) -> None:
    """Remove per-platform session data from Redis."""
    if _client is None:
        return
    try:
        msg_key, meta_key = _session_keys(user_id, role, platform)
        pipe = _client.pipeline()
        pipe.delete(msg_key)
        pipe.delete(meta_key)
        await pipe.execute()
    except Exception:
        log.warning("Redis delete_session failed for %s/%s/%s", user_id, role, platform, exc_info=True)


async def touch_session(user_id: str, role: str, platform: str) -> None:
    """Refresh TTL on per-platform session keys (keep alive without rewriting)."""
    if _client is None:
        return
    try:
        msg_key, meta_key = _session_keys(user_id, role, platform)
        pipe = _client.pipeline()
        pipe.expire(msg_key, _SESSION_TTL)
        pipe.expire(meta_key, _SESSION_TTL)
        await pipe.execute()
    except Exception:
        pass  # touch is best-effort


# ── Distributed turn counter (Phase 7) ──────────────────────────────────────

_TURN_COUNTER_KEY = "agent:turns:active"
_TURN_COUNTER_TTL = 600  # safety: auto-expire if server crashes


async def acquire_turn_slot(max_turns: int) -> bool:
    """Try to acquire a distributed turn slot. Returns True if acquired."""
    if _client is None:
        return True  # no Redis = fall back to local semaphore
    try:
        current = await _client.incr(_TURN_COUNTER_KEY)
        await _client.expire(_TURN_COUNTER_KEY, _TURN_COUNTER_TTL)
        if current <= max_turns:
            return True
        # Over limit — release immediately
        await _client.decr(_TURN_COUNTER_KEY)
        return False
    except Exception:
        log.warning("Redis acquire_turn_slot failed", exc_info=True)
        return True  # graceful: allow if Redis is down


async def release_turn_slot() -> None:
    """Release a distributed turn slot."""
    if _client is None:
        return
    try:
        val = await _client.decr(_TURN_COUNTER_KEY)
        # Guard against going negative (crashed workers)
        if val is not None and val < 0:
            await _client.set(_TURN_COUNTER_KEY, 0)
    except Exception:
        log.warning("Redis release_turn_slot failed", exc_info=True)
