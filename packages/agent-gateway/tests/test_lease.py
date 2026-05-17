"""tasks/lease 集成单测 — fakeredis mock,验证 acquire/renew/release/signal 逻辑。

跑:cd job-agent-gateway && python tests/test_lease.py
或:python -m pytest tests/test_lease.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis.aioredis as fakeredis_aio

import redis_client
from tasks import lease


def _async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeRedisManager:
    """用一个 fakeredis 实例替换 redis_client._client。"""

    def __init__(self):
        self._fake = fakeredis_aio.FakeRedis(decode_responses=True)

    def install(self):
        redis_client._client = self._fake

    def teardown(self):
        redis_client._client = None


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.mgr = _FakeRedisManager()
        self.mgr.install()
        # 清掉前次 test 残留
        _async(self.mgr._fake.flushdb())

    def tearDown(self):
        self.mgr.teardown()

    # ── 1. acquire:同一 task,第二次抢失败 ────────────────────
    def test_acquire_is_exclusive(self):
        instance_a = "agent-gw-A"
        instance_b = "agent-gw-B"

        # 用 INSTANCE_ID monkey-patch 模拟两个进程
        lease.INSTANCE_ID = instance_a
        ok_a = _async(lease.acquire_lease(1001))
        self.assertTrue(ok_a)

        lease.INSTANCE_ID = instance_b
        ok_b = _async(lease.acquire_lease(1001))
        self.assertFalse(ok_b)

        # 持有者还是 A
        holder = _async(lease.get_lease_holder(1001))
        self.assertEqual(holder, instance_a)

    # ── 2. release:A 释放后 B 能抢到 ───────────────────────
    def test_release_then_other_acquires(self):
        lease.INSTANCE_ID = "agent-gw-A"
        self.assertTrue(_async(lease.acquire_lease(1002)))
        _async(lease.release_lease(1002))

        # release 后 holder 没了
        holder = _async(lease.get_lease_holder(1002))
        self.assertIsNone(holder)

        lease.INSTANCE_ID = "agent-gw-B"
        self.assertTrue(_async(lease.acquire_lease(1002)))

    # ── 3. release 校验:B 不能误删 A 的 lease ─────────────
    def test_release_only_owns_my_lease(self):
        lease.INSTANCE_ID = "agent-gw-A"
        self.assertTrue(_async(lease.acquire_lease(1003)))

        # B 误调 release(value 不匹配 → Lua 不删)
        lease.INSTANCE_ID = "agent-gw-B"
        _async(lease.release_lease(1003))

        # 持有者仍是 A
        holder = _async(lease.get_lease_holder(1003))
        self.assertEqual(holder, "agent-gw-A")

    # ── 4. renew 校验:value 不匹配返 False ─────────────────
    def test_renew_value_must_match(self):
        lease.INSTANCE_ID = "agent-gw-A"
        self.assertTrue(_async(lease.acquire_lease(1004)))

        # B 续约同 task → 返 False(value 不匹配)
        lease.INSTANCE_ID = "agent-gw-B"
        ok = _async(lease.renew_lease(1004))
        self.assertFalse(ok)

        # A 续约 → 返 True
        lease.INSTANCE_ID = "agent-gw-A"
        ok = _async(lease.renew_lease(1004))
        self.assertTrue(ok)

    # ── 5. 接管路径:A 没及时续约,TTL 过期 → B 抢占 ────────
    def test_takeover_after_lease_expiry(self):
        lease.INSTANCE_ID = "agent-gw-A"
        self.assertTrue(_async(lease.acquire_lease(1005)))

        # 模拟 60s 过去:fakeredis 直接 DEL key 模拟过期
        _async(self.mgr._fake.delete(lease._lease_key(1005)))

        # B 抢占成功
        lease.INSTANCE_ID = "agent-gw-B"
        self.assertTrue(_async(lease.acquire_lease(1005)))

        # A 此时尝试续约 → 必须失败(value 不匹配,key 现在是 B 的)
        lease.INSTANCE_ID = "agent-gw-A"
        self.assertFalse(_async(lease.renew_lease(1005)))

    # ── 6. cancel signal:任意进程都能写,持有者读到 ──────────
    def test_cancel_signal_cross_instance(self):
        # 实例 A 写 cancel signal
        lease.INSTANCE_ID = "agent-gw-A"
        _async(lease.signal_cancel(1006))

        # 实例 B 读到 cancel signal
        lease.INSTANCE_ID = "agent-gw-B"
        self.assertTrue(_async(lease.check_cancel(1006)))

        # B 调 clear
        _async(lease.clear_cancel(1006))
        self.assertFalse(_async(lease.check_cancel(1006)))

    # ── 7. resume signal:GETDEL 消费一次性语义 ────────────
    def test_resume_signal_consume_once(self):
        lease.INSTANCE_ID = "agent-gw-A"
        _async(lease.signal_resume(1007))

        # 第一次 check 返 True
        self.assertTrue(_async(lease.check_resume(1007)))
        # 第二次 check 返 False(GETDEL 已消费)
        self.assertFalse(_async(lease.check_resume(1007)))

    # ── 8. Redis 不在时 graceful degrade ──────────────────
    def test_graceful_degrade_when_no_redis(self):
        self.mgr.teardown()  # 关掉 fake redis,redis_client._client = None

        # acquire 默认放行(单实例 fallback)
        self.assertTrue(_async(lease.acquire_lease(2001)))
        # renew 默认放行
        self.assertTrue(_async(lease.renew_lease(2001)))
        # cancel signal 不报错(no-op)
        _async(lease.signal_cancel(2001))
        # check 默认 False(没有信号源)
        self.assertFalse(_async(lease.check_cancel(2001)))


def main():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(LeaseTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
