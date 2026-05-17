"""F3 缓存 TTL / 过期职位强制校验

三个 list_cached_jobs MCP 工具（boss/linkedin/indeed）以及底层
db.list_cached_jobs 的参数透传 + SQL 片段生成契约。

db 在 conftest 里是 MagicMock，我们只验证：
  1. 工具层把新参数 fresh_within_days / include_expired 正确传给 db.list_cached_jobs
  2. db.list_cached_jobs 在有真 pool 的情况下，会把两个过滤拼进 WHERE（用 asyncpg pool 的 fetch 捕获 SQL）

这不是一个真实 PG 的集成测试 —— conftest 的 db 是 MagicMock，所以第二条用
独立的 patch 把真 list_cached_jobs 暴露出来测 SQL 生成。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import server  # noqa: F401  conftest bootstrap


class TestMcpToolParamForwarding:
    """MCP 工具 → db.list_cached_jobs 的参数透传契约"""

    @pytest.mark.asyncio
    async def test_boss_list_cached_jobs_forwards_new_filters(self):
        """boss_list_cached_jobs 把 fresh_within_days / include_expired 透传到 db。"""
        import mcp_tools_boss
        # 重新登记一份 MCP，捕获 boss_list_cached_jobs 注册的真实闭包
        fake_mcp = MagicMock()
        tool_fns: dict = {}
        fake_mcp.tool = lambda **_: (lambda fn: (tool_fns.__setitem__(fn.__name__, fn), fn)[1])
        mcp_tools_boss.register(fake_mcp)

        tool = tool_fns["boss_list_cached_jobs"]
        mock_list = AsyncMock(return_value=[])
        with patch.object(mcp_tools_boss.db, "list_cached_jobs", mock_list):
            await tool(MagicMock(),
                       keyword="python", fresh_within_days=7, include_expired=False)

        mock_list.assert_awaited_once()
        kw = mock_list.call_args.kwargs
        assert kw["fresh_within_days"] == 7
        assert kw["include_expired"] is False
        assert kw["keyword"] == "python"

    @pytest.mark.asyncio
    async def test_zero_fresh_within_days_maps_to_none(self):
        """fresh_within_days=0（默认）→ 传 None 给 db，避免无意义的 WHERE 片段。"""
        import mcp_tools_boss
        fake_mcp = MagicMock()
        tool_fns: dict = {}
        fake_mcp.tool = lambda **_: (lambda fn: (tool_fns.__setitem__(fn.__name__, fn), fn)[1])
        mcp_tools_boss.register(fake_mcp)

        mock_list = AsyncMock(return_value=[])
        with patch.object(mcp_tools_boss.db, "list_cached_jobs", mock_list):
            await tool_fns["boss_list_cached_jobs"](MagicMock())

        assert mock_list.call_args.kwargs["fresh_within_days"] is None

    @pytest.mark.asyncio
    async def test_linkedin_and_indeed_mirror_boss_contract(self):
        """三平台工具对新参数的透传行为一致（避免以后漂移）。"""
        import mcp_tools_linkedin, mcp_tools_indeed
        for mod in (mcp_tools_linkedin, mcp_tools_indeed):
            fake_mcp = MagicMock()
            tool_fns: dict = {}
            fake_mcp.tool = lambda **_: (lambda fn: (tool_fns.__setitem__(fn.__name__, fn), fn)[1])
            mod.register(fake_mcp)

            tool_name = f"{mod.__name__.split('_')[-1]}_list_cached_jobs"
            tool = tool_fns[tool_name]
            mock_list = AsyncMock(return_value=[])
            with patch.object(mod.db, "list_cached_jobs", mock_list):
                await tool(MagicMock(), fresh_within_days=30, include_expired=True)
            kw = mock_list.call_args.kwargs
            assert kw["fresh_within_days"] == 30, tool_name
            assert kw["include_expired"] is True, tool_name


class TestDbSqlGeneration:
    """db.list_cached_jobs: 验证 WHERE / args 的生成。

    不依赖真 PG：patch _get_pg_pool 返回一个带可捕获 fetch 的 mock，然后断言
    传给 fetch 的 SQL 片段包含预期条件。
    """

    @pytest.mark.asyncio
    async def test_default_excludes_expired(self):
        """include_expired=False（默认）→ SQL 里必须带 expires_at 过滤。"""
        # 临时取消 conftest 对 db 的全局 mock，跑真 list_cached_jobs 函数
        import importlib, sys
        sys.modules.pop("db", None)
        db = importlib.import_module("db")

        fake_pool = MagicMock()
        captured: dict = {}

        async def _fetch(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        fake_pool.fetch = _fetch

        async def _get_pool():
            return fake_pool

        with patch.object(db, "_get_pg_pool", _get_pool):
            await db.list_cached_jobs(platform="boss")

        assert "expires_at IS NULL OR" in captured["sql"]
        assert "make_interval" not in captured["sql"]  # 无 fresh_within_days 时不拼

    @pytest.mark.asyncio
    async def test_fresh_within_days_adds_interval(self):
        import importlib, sys
        sys.modules.pop("db", None)
        db = importlib.import_module("db")

        fake_pool = MagicMock()
        captured: dict = {}

        async def _fetch(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        fake_pool.fetch = _fetch

        async def _get_pool():
            return fake_pool

        with patch.object(db, "_get_pg_pool", _get_pool):
            await db.list_cached_jobs(platform="boss", fresh_within_days=14)

        assert "make_interval(days =>" in captured["sql"]
        assert 14 in captured["args"]  # 参数被绑定

    @pytest.mark.asyncio
    async def test_include_expired_true_skips_filter(self):
        import importlib, sys
        sys.modules.pop("db", None)
        db = importlib.import_module("db")

        fake_pool = MagicMock()
        captured: dict = {}

        async def _fetch(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        fake_pool.fetch = _fetch

        async def _get_pool():
            return fake_pool

        with patch.object(db, "_get_pg_pool", _get_pool):
            await db.list_cached_jobs(platform="boss", include_expired=True)

        # expires_at 仍在 SELECT 列里；关键是 WHERE 中的 "expires_at IS NULL OR"
        # 过滤片段不应出现
        assert "expires_at IS NULL OR" not in captured["sql"]

    @pytest.mark.asyncio
    async def test_zero_or_negative_fresh_ignored(self):
        """fresh_within_days=0 / -1 → 不拼 interval 片段（防御性）"""
        import importlib, sys
        sys.modules.pop("db", None)
        db = importlib.import_module("db")

        fake_pool = MagicMock()
        captured: dict = {}

        async def _fetch(sql, *args):
            captured["sql"] = sql
            return []

        fake_pool.fetch = _fetch

        async def _get_pool():
            return fake_pool

        with patch.object(db, "_get_pg_pool", _get_pool):
            await db.list_cached_jobs(platform="boss", fresh_within_days=0)
        assert "make_interval" not in captured["sql"]

        with patch.object(db, "_get_pg_pool", _get_pool):
            await db.list_cached_jobs(platform="boss", fresh_within_days=-5)
        assert "make_interval" not in captured["sql"]
