"""dynamic_command_state 模块测试。

简单 set 操作 + DB hydrate 路径。
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest

import server  # noqa: F401  conftest bootstrap

# conftest mock 'db'，但 dynamic_command_state 在 hydrate 时调 db.get_latest_dynamic_config
# 所以测试时直接 monkeypatch 替代
sys.modules.pop("dynamic_command_state", None)
import dynamic_command_state as dcs


def test_initially_empty():
    dcs.update_paths([])  # reset
    assert dcs.list_paths() == []
    assert dcs.is_dynamic_path("boss/anything") is False


def test_update_paths_replaces_set():
    dcs.update_paths(["boss/a", "boss/b"])
    assert dcs.is_dynamic_path("boss/a") is True
    assert dcs.is_dynamic_path("boss/b") is True
    assert dcs.is_dynamic_path("boss/c") is False
    # 替换语义：旧的不再是 dynamic
    dcs.update_paths(["boss/c"])
    assert dcs.is_dynamic_path("boss/a") is False
    assert dcs.is_dynamic_path("boss/c") is True


def test_update_paths_handles_none_and_dups():
    dcs.update_paths(None)  # type: ignore[arg-type]
    assert dcs.list_paths() == []
    dcs.update_paths(["boss/x", "boss/x", "boss/y"])
    assert sorted(dcs.list_paths()) == ["boss/x", "boss/y"]


def test_is_dynamic_path_empty_string():
    dcs.update_paths(["boss/x"])
    assert dcs.is_dynamic_path("") is False
    assert dcs.is_dynamic_path(None) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_hydrate_from_db_loads_paths(monkeypatch):
    fake_config = {
        "version": "v1",
        "dynamic_commands": [
            {"path": "boss/foo", "requestBuilder": {"method": "GET", "url": "/x"}},
            {"path": "boss/bar"},
            {"description": "no path skipped"},
            "not a dict skipped",
        ],
    }
    monkeypatch.setattr(dcs, "__name__", dcs.__name__)  # ensure module loaded
    import db
    monkeypatch.setattr(db, "get_latest_dynamic_config",
                        AsyncMock(return_value=fake_config), raising=False)

    dcs.update_paths([])  # reset
    n = await dcs.hydrate_from_db()
    assert n == 2
    assert dcs.is_dynamic_path("boss/foo")
    assert dcs.is_dynamic_path("boss/bar")
    assert not dcs.is_dynamic_path("boss/missing")


@pytest.mark.asyncio
async def test_hydrate_from_db_handles_no_config(monkeypatch):
    import db
    monkeypatch.setattr(db, "get_latest_dynamic_config",
                        AsyncMock(return_value=None), raising=False)
    dcs.update_paths(["leftover"])
    n = await dcs.hydrate_from_db()
    assert n == 0
    # 没有配置时不动 set
    assert dcs.is_dynamic_path("leftover")


@pytest.mark.asyncio
async def test_hydrate_from_db_swallows_errors(monkeypatch):
    import db
    monkeypatch.setattr(db, "get_latest_dynamic_config",
                        AsyncMock(side_effect=RuntimeError("db down")), raising=False)
    dcs.update_paths([])
    n = await dcs.hydrate_from_db()
    assert n == 0
