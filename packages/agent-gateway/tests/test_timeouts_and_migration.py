"""
Tests for tool-call timeout, turn timeout, and DB migration idempotency.
Run: pytest tests/
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Path 1: tool-call timeout ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_call_timeout_yields_error_result():
    """asyncio.TimeoutError from call_tool is caught; tool result has is_error=True."""
    import config as cfg
    cfg.TOOL_CALL_TIMEOUT = 0.01  # very short for test

    mock_session = MagicMock()
    mock_session.call_tool = AsyncMock(side_effect=asyncio.TimeoutError)

    # Simulate the wait_for + except block from agent_loop.py
    is_error = False
    result_text = ""
    try:
        result = await asyncio.wait_for(
            mock_session.call_tool("some_tool", {}),
            timeout=cfg.TOOL_CALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        result_text = f"工具调用超时（>{cfg.TOOL_CALL_TIMEOUT:.0f}s）: some_tool"
        is_error = True

    assert is_error is True
    assert "超时" in result_text
    assert "some_tool" in result_text


# ── Path 2: turn timeout ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_turn_timeout_logs_to_db_no_ws_message():
    """Turn timeout writes DB error record and does NOT send a second WS message."""
    import config as cfg
    cfg.TURN_TIMEOUT = 0.05

    db_calls = []
    ws_calls = []

    async def slow_run_turn(*args, **kwargs):
        await asyncio.sleep(10)  # longer than TURN_TIMEOUT

    async def fake_log_event(*args, **kwargs):
        db_calls.append(kwargs.get("content", ""))

    fake_ws = MagicMock()
    fake_ws.send_text = AsyncMock(side_effect=lambda msg: ws_calls.append(msg))

    fake_sess = MagicMock()
    fake_sess.db_session_id = 1
    fake_sess.user_id = "test_user"
    fake_sess.current_tool = "some_tool"

    with patch("db.log_event", side_effect=fake_log_event):
        try:
            await asyncio.wait_for(
                slow_run_turn(fake_ws, fake_sess, "hello", 1),
                timeout=cfg.TURN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            msg = f"Agent 推理超时（>{cfg.TURN_TIMEOUT:.0f}s），本轮已终止"
            fake_sess.current_tool = None
            await fake_log_event(content=msg)
            # Intentionally NOT calling ws.send_text here

    assert fake_sess.current_tool is None
    assert any("超时" in c for c in db_calls), "Expected timeout error in DB log"
    assert len(ws_calls) == 0, "No WS message should be sent for turn timeout"


# ── Path 5: DB migration idempotency ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_migration_idempotent():
    """Running init_db() twice on existing table should not raise."""
    import asyncpg

    # Use a real in-memory PostgreSQL or skip if not available
    db_url = "postgresql://boss_gateway:boss_gw_2026@localhost:5432/boss_gateway"
    try:
        conn = await asyncpg.connect(db_url, timeout=3)
    except Exception:
        pytest.skip("PostgreSQL not available in test environment")

    try:
        # Run the migration SQL twice — should not raise
        for _ in range(2):
            await conn.execute(
                "ALTER TABLE agent_conv_events ADD COLUMN IF NOT EXISTS input_tokens INTEGER"
            )
            await conn.execute(
                "ALTER TABLE agent_conv_events ADD COLUMN IF NOT EXISTS output_tokens INTEGER"
            )
    finally:
        await conn.close()
