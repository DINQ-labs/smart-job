"""tests/test_steps_common.py — common.py 工具函数(跨平台共享)单测。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.steps.common import extract_list_from_raw, throttled_iter


class ExtractListFromRawTests(unittest.TestCase):
    def test_boss_nested(self):
        r = {"raw": {"zpData": {"jobList": [{"t": "A"}, {"t": "B"}]}}}
        self.assertEqual(
            extract_list_from_raw(r, keys=("jobList", "jobs"), nested_in=("zpData",)),
            [{"t": "A"}, {"t": "B"}],
        )

    def test_linkedin_top_level(self):
        r = {"raw": {"elements": [{"urn": "X"}]}}
        self.assertEqual(
            extract_list_from_raw(r, keys=("elements",), nested_in=("data",)),
            [{"urn": "X"}],
        )

    def test_linkedin_nested(self):
        r = {"raw": {"data": {"elements": [{"urn": "Y"}]}}}
        self.assertEqual(
            extract_list_from_raw(r, keys=("elements",), nested_in=("data",)),
            [{"urn": "Y"}],
        )

    def test_no_raw_wrapper(self):
        # result 自己就是 raw(没有 'raw' wrapper)
        r = {"jobs": [{"id": 1}]}
        self.assertEqual(
            extract_list_from_raw(r, keys=("jobs",)),
            [{"id": 1}],
        )

    def test_first_match_wins(self):
        # nested 优先于顶层
        r = {"raw": {"data": {"elements": [{"x": "nested"}]},
                     "elements": [{"x": "top"}]}}
        self.assertEqual(
            extract_list_from_raw(r, keys=("elements",), nested_in=("data",)),
            [{"x": "nested"}],
        )

    def test_keys_priority_order(self):
        # keys 列表是优先级:第一个找到就返
        r = {"raw": {"jobs": [{"a": 1}], "list": [{"b": 2}]}}
        self.assertEqual(
            extract_list_from_raw(r, keys=("jobs", "list")),
            [{"a": 1}],
        )

    def test_empty_raw(self):
        self.assertEqual(
            extract_list_from_raw({"raw": {}}, keys=("jobs",)),
            [],
        )

    def test_no_match(self):
        r = {"raw": {"unknownField": [{"x": 1}]}}
        self.assertEqual(extract_list_from_raw(r, keys=("jobs",)), [])

    def test_non_dict_input(self):
        self.assertEqual(extract_list_from_raw(None, keys=("jobs",)), [])
        self.assertEqual(extract_list_from_raw("str", keys=("jobs",)), [])
        self.assertEqual(extract_list_from_raw([], keys=("jobs",)), [])

    def test_value_not_list(self):
        # key 命中但值不是 list → 跳过该 key 继续找
        r = {"raw": {"jobs": "not a list", "list": [{"x": 1}]}}
        self.assertEqual(
            extract_list_from_raw(r, keys=("jobs", "list")),
            [{"x": 1}],
        )


class ThrottledIterTests(unittest.TestCase):
    def test_yields_all_items(self):
        async def collect():
            out = []
            async for x in throttled_iter([1, 2, 3], interval_sec=0.0):
                out.append(x)
            return out
        out = asyncio.get_event_loop().run_until_complete(collect())
        self.assertEqual(out, [1, 2, 3])

    def test_first_item_no_wait(self):
        # 第一个不等,直接 yield
        async def first_arrives_fast():
            import time
            t0 = time.monotonic()
            async for x in throttled_iter([1, 2, 3], interval_sec=0.5):
                if x == 1:
                    elapsed = time.monotonic() - t0
                    return elapsed
        elapsed = asyncio.get_event_loop().run_until_complete(first_arrives_fast())
        self.assertLess(elapsed, 0.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)
