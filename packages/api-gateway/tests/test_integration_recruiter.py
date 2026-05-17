"""招聘方工具集成测试 (C2)

覆盖三个新 MCP 工具的命令层契约 —— 它们不走 handle_cli HTTP 分发，只在
MCP 层暴露，因此集成测试直接命中 commands.cmd_* 函数，验证：

  1. cmd_boss_rec_geek_list: 正确调用 send_command_to + _unwrap，
     并把 geeks 列表 upsert 到 entry.geek_store
  2. cmd_boss_mark_geek_interest: 正确映射所有字段到
     db.upsert_recruiter_geek_interest（这是持久化操作，regression 最危险的一项）
  3. cmd_boss_list_geek_interests: 正确把分页/过滤参数透传到
     db.get_recruiter_geek_interests

conftest 已 mock 重型依赖（db / ext_client / fastmcp 等），
在此文件里把 MagicMock 具体方法替换为 AsyncMock 以便可 await。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 触发 conftest bootstrap
import server  # noqa: F401
from session_store import SessionEntry, session_store


def _make_session(sid: str = "sid-rec-1", app_user: str = "recruiter_alice") -> SessionEntry:
    """构造一个最小可工作的 SessionEntry 并注册到全局 session_store。"""
    import asyncio
    from datetime import datetime, timezone

    entry = SessionEntry(
        session_id=sid,
        browser_id=sid,
        ws=MagicMock(),
        pending={},
        connected_at=datetime.now(timezone.utc).isoformat(),
        account_name="Alice HR",
        app_user_id=app_user,
    )
    # rate_limiter.wait 替换成 no-op，避免真实 sleep
    entry.rate_limiter.wait = AsyncMock(return_value=None)
    session_store._sessions[sid] = entry
    return entry


def _cleanup_session(sid: str) -> None:
    session_store._sessions.pop(sid, None)


@pytest.fixture
def recruiter_session():
    entry = _make_session()
    yield entry
    _cleanup_session(entry.session_id)


class TestBossRecGeekList:
    """cmd_boss_rec_geek_list: WS envelope 解包 + geek_store 写入"""

    @pytest.mark.asyncio
    async def test_unwrap_and_geek_store_upsert(self, recruiter_session):
        import commands
        canned = {
            "ok": True, "code": 0, "data": {
                "geeks": [
                    {"encryptGeekId": "eg1", "name": "Bob", "expectPosition": "Python"},
                    {"encryptGeekId": "eg2", "name": "Carol", "expectPosition": "Go"},
                    {"name": "No-ID", "expectPosition": "Java"},  # 无 encryptGeekId 应被过滤
                ],
            },
        }
        with patch.object(commands, "send_command_to",
                          new=AsyncMock(return_value=canned)) as mock_send:
            data = await commands.cmd_boss_rec_geek_list(
                job_id="job1", page=1, filters={"degree": 2},
                session_id=recruiter_session.session_id,
                agent_id="agent-7",
            )

        # 1. WS 调用按 pool 规范发出
        mock_send.assert_awaited_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == recruiter_session.session_id
        assert call_args[0][1] == "GET"
        assert call_args[0][2] == "boss/rec_geek_list"
        body = call_args[0][3]
        assert body["job_id"] == "job1"
        assert body["page"] == 1
        assert body["filters"] == {"degree": 2}
        assert call_args[1]["tool_name"] == "boss_rec_geek_list"
        assert call_args[1]["agent_id"] == "agent-7"

        # 2. _unwrap 拆掉 envelope，geeks 进入返回值
        assert "geeks" in data
        assert len(data["geeks"]) == 3  # 原样返回（过滤只针对 geek_store）

        # 3. 有 encryptGeekId 的两条写入 geek_store（存储后字段名变 snake_case）
        stored = recruiter_session.geek_store.list_all()
        stored_ids = {g.get("encrypt_geek_id") for g in stored}
        assert "eg1" in stored_ids
        assert "eg2" in stored_ids
        assert len(stored) == 2  # 无 encryptGeekId 的条目被过滤

    @pytest.mark.asyncio
    async def test_empty_geeks_does_not_crash(self, recruiter_session):
        import commands
        with patch.object(commands, "send_command_to",
                          new=AsyncMock(return_value={"ok": True, "code": 0, "data": {"geeks": []}})):
            data = await commands.cmd_boss_rec_geek_list(
                job_id="job1", session_id=recruiter_session.session_id,
            )
        assert data["geeks"] == []

    @pytest.mark.asyncio
    async def test_rate_limit_invoked(self, recruiter_session):
        import commands
        with patch.object(commands, "send_command_to",
                          new=AsyncMock(return_value={"ok": True, "code": 0, "data": {}})):
            await commands.cmd_boss_rec_geek_list(
                job_id="j1", session_id=recruiter_session.session_id,
            )
        # 每次搜索都必须经过 search bucket 的速率限制
        recruiter_session.rate_limiter.wait.assert_awaited_once_with("search")

    @pytest.mark.asyncio
    async def test_match_score_batch_upserted(self, recruiter_session):
        """F4：扩展返回 matchScore 时应 fire-and-forget 写入 recruiter_geek_interests。"""
        import commands
        import asyncio
        canned = {
            "ok": True, "code": 0, "data": {
                "geeks": [
                    {"encryptGeekId": "eg1", "matchScore": 87},
                    {"encryptGeekId": "eg2", "matchScore": 62},
                    {"encryptGeekId": "eg3", "matchScore": None},  # 该项不应触发 upsert
                    {"name": "noop", "matchScore": 50},  # 无 encryptGeekId 同样跳过
                ],
            },
        }
        mock_upsert = AsyncMock(return_value=None)
        with patch.object(commands, "send_command_to",
                          new=AsyncMock(return_value=canned)):
            with patch.object(commands.db, "upsert_recruiter_geek_interest", mock_upsert):
                await commands.cmd_boss_rec_geek_list(
                    job_id="job-42", session_id=recruiter_session.session_id,
                )
                # fire-and-forget 是 asyncio.ensure_future；让事件循环跑一轮收尾
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        # 仅两条有分数 + 有 encryptGeekId 的被写入
        assert mock_upsert.await_count == 2
        calls = {c.kwargs["encrypt_geek_id"]: c.kwargs for c in mock_upsert.call_args_list}
        assert calls["eg1"]["match_score"] == 87
        assert calls["eg1"]["encrypt_job_id"] == "job-42"
        assert calls["eg1"]["platform"] == "boss"
        assert calls["eg1"]["app_user_id"] == "recruiter_alice"
        assert calls["eg2"]["match_score"] == 62

    @pytest.mark.asyncio
    async def test_match_score_missing_skips_silently(self, recruiter_session):
        """所有 geek 都没有 matchScore 时不触发任何写入（防御旧版 Boss API）。"""
        import commands
        import asyncio
        canned = {
            "ok": True, "code": 0, "data": {
                "geeks": [
                    {"encryptGeekId": "eg1"},  # 无 matchScore 字段
                    {"encryptGeekId": "eg2", "matchScore": None},
                ],
            },
        }
        mock_upsert = AsyncMock(return_value=None)
        with patch.object(commands, "send_command_to",
                          new=AsyncMock(return_value=canned)):
            with patch.object(commands.db, "upsert_recruiter_geek_interest", mock_upsert):
                await commands.cmd_boss_rec_geek_list(
                    job_id="j1", session_id=recruiter_session.session_id,
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        mock_upsert.assert_not_awaited()


class TestBossMarkGeekInterest:
    """cmd_boss_mark_geek_interest: DB 写入字段映射 —— 最危险的 regression 入口"""

    @pytest.mark.asyncio
    async def test_full_field_mapping_to_db(self, recruiter_session):
        import commands
        mock_upsert = AsyncMock(return_value=None)
        with patch.object(commands.db, "upsert_recruiter_geek_interest", mock_upsert):
            result = await commands.cmd_boss_mark_geek_interest(
                encrypt_geek_id="eg-100",
                encrypt_job_id="job-200",
                interested=True,
                status="contacted",
                match_score=85,
                notes="强匹配 Python 高级",
                geek_name="Bob Chen",
                salary="30-50k",
                city="Shanghai",
                degree="本科",
                work_year="5-10年",
                search_security_id="sec-xyz",
                session_id=recruiter_session.session_id,
            )

        assert result == {"ok": True, "encrypt_geek_id": "eg-100", "interested": True}
        mock_upsert.assert_awaited_once()
        kw = mock_upsert.call_args.kwargs
        assert kw["app_user_id"] == "recruiter_alice"
        assert kw["platform"] == "boss"
        assert kw["encrypt_geek_id"] == "eg-100"
        assert kw["encrypt_job_id"] == "job-200"
        assert kw["geek_name"] == "Bob Chen"
        assert kw["status"] == "contacted"
        assert kw["interested"] is True
        assert kw["match_score"] == 85
        assert kw["notes"] == "强匹配 Python 高级"
        assert kw["search_security_id"] == "sec-xyz"

    @pytest.mark.asyncio
    async def test_db_failure_propagates(self, recruiter_session):
        """DB 写入失败必须传播异常，不能静默返回 ok=True（历史 regression 原点）。"""
        import commands
        mock_upsert = AsyncMock(side_effect=RuntimeError("DB connection lost"))
        with patch.object(commands.db, "upsert_recruiter_geek_interest", mock_upsert):
            with pytest.raises(RuntimeError, match="DB connection lost"):
                await commands.cmd_boss_mark_geek_interest(
                    encrypt_geek_id="eg-100",
                    session_id=recruiter_session.session_id,
                )

    @pytest.mark.asyncio
    async def test_defaults_applied(self, recruiter_session):
        """最小参数：只传 encrypt_geek_id → 其余字段用默认值"""
        import commands
        mock_upsert = AsyncMock(return_value=None)
        with patch.object(commands.db, "upsert_recruiter_geek_interest", mock_upsert):
            await commands.cmd_boss_mark_geek_interest(
                encrypt_geek_id="eg-only",
                session_id=recruiter_session.session_id,
            )
        kw = mock_upsert.call_args.kwargs
        assert kw["status"] == "new"
        assert kw["interested"] is True
        assert kw["match_score"] is None
        assert kw["notes"] == ""
        assert kw["encrypt_job_id"] == ""


class TestBossListGeekInterests:
    """cmd_boss_list_geek_interests: 分页/过滤参数透传"""

    @pytest.mark.asyncio
    async def test_pagination_and_filter_args(self, recruiter_session):
        import commands
        rows = [
            {"encrypt_geek_id": "eg-a", "interested": True},
            {"encrypt_geek_id": "eg-b", "interested": True},
        ]
        mock_get = AsyncMock(return_value=rows)
        with patch.object(commands.db, "get_recruiter_geek_interests", mock_get):
            result = await commands.cmd_boss_list_geek_interests(
                encrypt_job_id="job-200",
                interested_only=True,
                limit=10,
                offset=20,
                session_id=recruiter_session.session_id,
            )
        assert result == {"interests": rows}
        kw = mock_get.call_args.kwargs
        assert kw["app_user_id"] == "recruiter_alice"
        assert kw["encrypt_job_id"] == "job-200"
        assert kw["interested_only"] is True
        assert kw["limit"] == 10
        assert kw["offset"] == 20

    @pytest.mark.asyncio
    async def test_empty_job_id_passes_none(self, recruiter_session):
        """空 encrypt_job_id 应被翻译成 None（DB 层不加 job 过滤条件）"""
        import commands
        mock_get = AsyncMock(return_value=[])
        with patch.object(commands.db, "get_recruiter_geek_interests", mock_get):
            await commands.cmd_boss_list_geek_interests(
                encrypt_job_id="",
                session_id=recruiter_session.session_id,
            )
        kw = mock_get.call_args.kwargs
        assert kw["encrypt_job_id"] is None

    @pytest.mark.asyncio
    async def test_default_pagination(self, recruiter_session):
        import commands
        mock_get = AsyncMock(return_value=[])
        with patch.object(commands.db, "get_recruiter_geek_interests", mock_get):
            await commands.cmd_boss_list_geek_interests(
                session_id=recruiter_session.session_id,
            )
        kw = mock_get.call_args.kwargs
        assert kw["limit"] == 50
        assert kw["offset"] == 0
        assert kw["interested_only"] is False


class TestRecruiterGeekUpsertSqlShape:
    """审核补丁 #C：upsert_recruiter_geek_interest 的 UPDATE 子句必须用
    COALESCE，避免 _batch_upsert_match_scores (fire-and-forget) 写入的
    match_score 被 cmd_boss_mark_geek_interest 默认传的 None 抹掉。"""

    def _make_captured_pool(self) -> tuple[MagicMock, list[tuple[str, tuple]]]:
        """构造一个伪 asyncpg pool：pool.acquire() 返回 async context manager，
        conn.execute 的调用参数被 captured_sql 收集。"""
        captured: list[tuple[str, tuple]] = []

        conn = MagicMock()
        async def _exec(sql: str, *args):
            captured.append((sql, args))
        conn.execute = _exec

        class _AcquireCM:
            async def __aenter__(self_inner):
                return conn
            async def __aexit__(self_inner, *exc):
                return False

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AcquireCM())
        return pool, captured

    def _load_real_db(self):
        """绕过 conftest 对 `db` 的 MagicMock，导入真 db 模块（仅本测试类用）。"""
        import importlib, sys
        saved = sys.modules.pop("db", None)
        try:
            real_db = importlib.import_module("db")
            return real_db, saved
        except BaseException:
            if saved is not None:
                sys.modules["db"] = saved
            raise

    def _restore_db(self, saved):
        import sys
        if saved is not None:
            sys.modules["db"] = saved
        else:
            sys.modules.pop("db", None)

    @pytest.mark.asyncio
    async def test_update_clause_uses_coalesce(self):
        """UPDATE 每个列都应写成 COALESCE(EXCLUDED.col, tbl.col)，
        updated_at 例外（始终刷新）。"""
        real_db, saved = self._load_real_db()
        try:
            backend = real_db._PostgresBackend.__new__(real_db._PostgresBackend)
            pool, captured = self._make_captured_pool()

            async def _fake_get_pool():
                return pool
            backend._get_pool = _fake_get_pool

            await backend.upsert_recruiter_geek_interest(
                app_user_id="hr1", platform="boss",
                encrypt_geek_id="eg", encrypt_job_id="jj",
                match_score=95,
            )

            assert len(captured) == 1
            sql, args = captured[0]
            assert "ON CONFLICT(app_user_id, platform, encrypt_geek_id, encrypt_job_id) DO UPDATE SET" in sql
            assert "match_score=COALESCE(EXCLUDED.match_score, recruiter_geek_interests.match_score)" in sql
            # updated_at 仍然直接覆盖
            assert "updated_at=EXCLUDED.updated_at" in sql
        finally:
            self._restore_db(saved)

    @pytest.mark.asyncio
    async def test_none_fields_filtered_out(self):
        """match_score=None（cmd_boss_mark_geek_interest 默认路径）不应出现
        在 INSERT 列表里，从源头杜绝覆盖已有值的可能。"""
        real_db, saved = self._load_real_db()
        try:
            backend = real_db._PostgresBackend.__new__(real_db._PostgresBackend)
            pool, captured = self._make_captured_pool()

            async def _fake_get_pool():
                return pool
            backend._get_pool = _fake_get_pool

            # 模拟 mark_geek_interest 最小调用：match_score=None（默认）
            await backend.upsert_recruiter_geek_interest(
                app_user_id="hr1", platform="boss",
                encrypt_geek_id="eg", encrypt_job_id="jj",
                interested=True,
                match_score=None,  # 默认值，不应进 SQL
            )

            sql, args = captured[0]
            # match_score 不在 INSERT 列表 → 也不在 UPDATE SET
            assert "match_score" not in sql
            # interested 进了
            assert "interested=COALESCE(EXCLUDED.interested, recruiter_geek_interests.interested)" in sql
        finally:
            self._restore_db(saved)
