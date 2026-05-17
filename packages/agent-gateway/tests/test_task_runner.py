"""TaskRunner 单测 — mock db 模块,验证 retry / skip / user_action / abort 闭环。

跑:cd job-agent-gateway && python -m pytest tests/test_task_runner.py -v
或:python tests/test_task_runner.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# audit P2 fix:从跨服务路径解耦,加 repo-root 让 job_common 可见
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


# ── 在 import engine 前 patch db ──
class _FakeDB:
    """所有 DAO 方法变成 no-op,记录最后一次 status & fields。"""
    def __init__(self):
        self.last_status: str | None = None
        self.last_fields: dict = {}
        self.heartbeat_count = 0
        self.task_row = {"status": "pending", "paused_at_idx": None}

    async def update_task_status(self, task_id, status, **fields):
        self.last_status = status
        self.last_fields = fields
        self.task_row["status"] = status
        if "paused_at_idx" in fields:
            self.task_row["paused_at_idx"] = fields["paused_at_idx"]

    async def heartbeat_task(self, task_id):
        self.heartbeat_count += 1

    async def get_task(self, task_id):
        return dict(self.task_row)


_fake_db = _FakeDB()
sys.modules["db"] = _fake_db  # 让 from tasks.engine import * 拿到这个 fake

from job_common.risk_signals import RiskControlSignal
from tasks.registry import TaskTemplate, TaskStep
from tasks.engine import TaskRunner


class TaskRunnerTests(unittest.TestCase):
    def setUp(self):
        global _fake_db
        _fake_db = _FakeDB()
        # 重新指 db
        import tasks.engine as engine_mod
        engine_mod.db = _fake_db
        # 缩短 retry 等待时间(测试不要等 30s)
        for sig in ["boss:code_37", "boss:rate_limit_415"]:
            from job_common.risk_signals import RISK_SIGNALS
            if sig in RISK_SIGNALS:
                RISK_SIGNALS[sig]["wait_sec"] = 0  # 改成 0 立即重试

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    # ── 1. happy path: 1 step 跑成功 ───────────────────────────
    def test_happy_path_single_step(self):
        async def step_fn(_task, ctx):
            ctx["result_summary"] = "all done"
            return {}
        tpl = TaskTemplate(id="test:happy", role="jobseeker", title="happy", description="", steps=[
            TaskStep(id="run", title="跑一下", fn=step_fn, iter_items=False),
        ])
        runner = TaskRunner(task_id=1, template=tpl)
        self._run(runner.run())
        self.assertEqual(_fake_db.last_status, "completed")
        self.assertEqual(_fake_db.last_fields.get("result_summary"), "all done")

    # ── 2. auto_retry 风控:第 1 次抛 code_37,第 2 次成功 ──────
    def test_auto_retry_succeeds_after_retry(self):
        n_calls = {"v": 0}
        async def step_fn(_task, ctx):
            n_calls["v"] += 1
            if n_calls["v"] == 1:
                raise RiskControlSignal("boss:code_37")
            return {}
        tpl = TaskTemplate(id="test:retry", role="jobseeker", title="retry", description="", steps=[
            TaskStep(id="run", title="跑一下", fn=step_fn),
        ])
        self._run(TaskRunner(task_id=2, template=tpl).run())
        self.assertEqual(_fake_db.last_status, "completed")
        self.assertEqual(n_calls["v"], 2)  # retry 1 次后成功

    # ── 3. auto_retry 重试用尽 → 降级 skip,继续下一个 item ─────
    def test_auto_retry_exhausted_falls_back_to_skip(self):
        async def step_fn(_task, ctx):
            raise RiskControlSignal("boss:code_37")  # 永远抛
        tpl = TaskTemplate(id="test:exhausted", role="jobseeker", title="x", description="", steps=[
            TaskStep(id="iter", title="对每项跑", fn=step_fn, iter_items=True),
        ])
        runner = TaskRunner(task_id=3, template=tpl)
        # iter 3 个 item
        self._run(runner.run({"items": [{"id":1}, {"id":2}, {"id":3}]}))
        self.assertEqual(_fake_db.last_status, "completed")
        # 3 个全 skip
        skipped = _fake_db.last_fields.get("skipped", [])
        self.assertEqual(len(skipped), 3)

    # ── 4. skip_item 信号:不重试,直接 skip 当前 item ─────────
    def test_skip_item_signal(self):
        n = {"v": 0}
        async def step_fn(_task, ctx):
            n["v"] += 1
            if ctx["_item_idx"] == 1:
                raise RiskControlSignal("boss:quota_exceeded")
            return {}
        tpl = TaskTemplate(id="test:skip", role="jobseeker", title="x", description="", steps=[
            TaskStep(id="iter", title="x", fn=step_fn, iter_items=True),
        ])
        runner = TaskRunner(task_id=4, template=tpl)
        self._run(runner.run({"items": [{"id":1}, {"id":2}, {"id":3}]}))
        # 应该跑了 3 次(每个 item 1 次,中间那个直接 skip 不 retry)
        self.assertEqual(n["v"], 3)
        skipped = _fake_db.last_fields.get("skipped", [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["idx"], 1)

    # ── 5. abort 信号 → 整任务 failed ──────────────────────────
    def test_abort_signal_fails_task(self):
        async def step_fn(_task, ctx):
            raise RiskControlSignal("kicked")
        tpl = TaskTemplate(id="test:abort", role="jobseeker", title="x", description="", steps=[
            TaskStep(id="run", title="x", fn=step_fn),
        ])
        self._run(TaskRunner(task_id=5, template=tpl).run())
        self.assertEqual(_fake_db.last_status, "failed")

    # ── 6. cancel 信号:中途 cancel,任务进 cancelled ──────────
    def test_cancel_mid_run(self):
        n = {"v": 0}
        runner_ref = {"r": None}

        async def step_fn(_task, ctx):
            n["v"] += 1
            # 第 2 个 item 触发取消
            if ctx["_item_idx"] == 1:
                runner_ref["r"].cancel()
            await asyncio.sleep(0.01)
            return {}

        tpl = TaskTemplate(id="test:cancel", role="jobseeker", title="x", description="", steps=[
            TaskStep(id="iter", title="x", fn=step_fn, iter_items=True),
        ])
        runner = TaskRunner(task_id=6, template=tpl)
        runner_ref["r"] = runner
        self._run(runner.run({"items": [{"id":1}, {"id":2}, {"id":3}]}))
        self.assertEqual(_fake_db.last_status, "cancelled")
        # 取消后第 3 个 item 不应被处理
        self.assertLess(n["v"], 3)

    # ── 7. user_action 信号 + resume 闭环 ───────────────────────
    def test_user_action_pause_and_resume(self):
        seen_first = {"v": False}
        async def step_fn(_task, ctx):
            if not seen_first["v"]:
                seen_first["v"] = True
                raise RiskControlSignal("boss:captcha")
            return {}
        tpl = TaskTemplate(id="test:userpause", role="jobseeker", title="x", description="", steps=[
            TaskStep(id="run", title="x", fn=step_fn),
        ])
        runner = TaskRunner(task_id=7, template=tpl)

        async def driver():
            run_task = asyncio.create_task(runner.run())
            # 等 runner 进入 paused 状态
            for _ in range(50):
                await asyncio.sleep(0.02)
                if _fake_db.task_row["status"] == "paused_user_action":
                    break
            # 触发 resume
            runner.resume()
            await run_task
        self._run(driver())
        self.assertEqual(_fake_db.last_status, "completed")


def main():
    """支持 `python tests/test_task_runner.py` 直接跑(不需要 pytest)。"""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TaskRunnerTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
