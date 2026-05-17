"""tasks/lease.py — Redis lease 让 TaskRunner 安全跑在多实例 (Phase E+)

每个 task_id 在 Redis 上一把 lease(`task:lease:{id}`,TTL 60s,value=本进程 instance_id)。

工作流:
  1. acquire_lease(task_id)   原子 SET NX EX 60 → 抢成功才跑
  2. renew_lease(task_id)     30s 跑一次,EXPIRE 续 60s;返 False = 已不持有(早过期或被人接走)
  3. release_lease(task_id)   完成时 Lua 校验 value 再 DEL,防误删别人续上的 lease

跨实例的 cancel / resume:
  cancel_signal(task_id)      持有者每个 step boundary 检查
  resume_signal(task_id)      持有者 wait_for_resume 时轮询

graceful degradation:Redis 不在时所有 fn 当作单实例(allow,与现有 redis_client 风格一致)。
"""
from __future__ import annotations

import logging
import os
import uuid

import redis_client

log = logging.getLogger(__name__)

LEASE_TTL_SEC = 60
LEASE_RENEW_INTERVAL_SEC = 30
SIGNAL_TTL_SEC = 300

# 本进程的 instance id (entire process lifetime).读 env 优先,方便部署时手填(k8s POD_NAME)
INSTANCE_ID = os.getenv("INSTANCE_ID") or f"agent-gw-{uuid.uuid4().hex[:8]}"


def _lease_key(task_id: int) -> str:    return f"task:lease:{task_id}"
def _cancel_key(task_id: int) -> str:   return f"task:cancel:{task_id}"
def _resume_key(task_id: int) -> str:   return f"task:resume:{task_id}"


# ── Lease ─────────────────────────────────────────────────────────
# 用 WATCH/MULTI/EXEC 事务保证 GET-then-mutate 原子(等价于 Lua,
# 但 fakeredis 不带 Lua 解释器,事务两边都支持)。

async def acquire_lease(task_id: int) -> bool:
    """抢 lease。True = 拿到,我来跑;False = 别人在跑。
    Redis 不在时返 True(单实例 fallback,跟现有 redis_client 风格一致)。"""
    client = redis_client.get_client()
    if client is None:
        return True
    try:
        ok = await client.set(_lease_key(task_id), INSTANCE_ID, nx=True, ex=LEASE_TTL_SEC)
        return bool(ok)
    except Exception as e:
        log.warning("[lease] acquire failed for task %d: %s", task_id, e)
        return True  # graceful fallback


async def _check_and_mutate(client, key: str, expected_value: str,
                              mutation: str) -> bool:
    """WATCH key → GET → 比较 → MULTI → mutation → EXEC。
    mutation: 'expire' or 'delete'。返回 True 表示原子操作成功。"""
    from redis.exceptions import WatchError
    async with client.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(key)
            cur = await pipe.get(key)
            if cur != expected_value:
                await pipe.unwatch()
                return False
            pipe.multi()
            if mutation == "expire":
                pipe.expire(key, LEASE_TTL_SEC)
            elif mutation == "delete":
                pipe.delete(key)
            else:
                raise ValueError(f"unknown mutation: {mutation}")
            await pipe.execute()
            return True
        except WatchError:
            return False


async def renew_lease(task_id: int) -> bool:
    """续约。True = 续上了,继续干;False = 不再持有(过期 / 别人接走),持有者必须立即停止。"""
    client = redis_client.get_client()
    if client is None:
        return True
    try:
        return await _check_and_mutate(client, _lease_key(task_id), INSTANCE_ID, "expire")
    except Exception as e:
        log.warning("[lease] renew failed for task %d: %s", task_id, e)
        return True  # 容忍单次 redis hiccup


async def release_lease(task_id: int) -> None:
    """完成时释放。校验 value 再删,防误删别人续上的 lease。"""
    client = redis_client.get_client()
    if client is None:
        return
    try:
        await _check_and_mutate(client, _lease_key(task_id), INSTANCE_ID, "delete")
    except Exception as e:
        log.warning("[lease] release failed for task %d: %s", task_id, e)


async def get_lease_holder(task_id: int) -> str | None:
    """返当前持有者 instance_id(或 None=没人持有,任务可能已结束/孤儿)。"""
    client = redis_client.get_client()
    if client is None:
        return None
    try:
        return await client.get(_lease_key(task_id))
    except Exception:
        return None


# ── 跨实例 cancel / resume signal ─────────────────────────────────

async def signal_cancel(task_id: int) -> None:
    client = redis_client.get_client()
    if client is None:
        return
    try:
        await client.set(_cancel_key(task_id), "1", ex=SIGNAL_TTL_SEC)
    except Exception as e:
        log.warning("[lease] signal_cancel failed: %s", e)


async def check_cancel(task_id: int) -> bool:
    client = redis_client.get_client()
    if client is None:
        return False
    try:
        v = await client.get(_cancel_key(task_id))
        return v is not None
    except Exception:
        return False


async def clear_cancel(task_id: int) -> None:
    client = redis_client.get_client()
    if client is None:
        return
    try:
        await client.delete(_cancel_key(task_id))
    except Exception:
        pass


async def signal_resume(task_id: int) -> None:
    client = redis_client.get_client()
    if client is None:
        return
    try:
        await client.set(_resume_key(task_id), "1", ex=SIGNAL_TTL_SEC)
    except Exception as e:
        log.warning("[lease] signal_resume failed: %s", e)


async def check_resume(task_id: int) -> bool:
    """check + 自动清掉(consume-once)。"""
    client = redis_client.get_client()
    if client is None:
        return False
    try:
        # GETDEL 是 Redis 6.2+ 原子 get-and-delete,正合用
        v = await client.getdel(_resume_key(task_id))
        return v is not None
    except Exception:
        # 退回 GET + DEL
        try:
            v = await client.get(_resume_key(task_id))
            if v is not None:
                await client.delete(_resume_key(task_id))
                return True
        except Exception:
            pass
        return False
