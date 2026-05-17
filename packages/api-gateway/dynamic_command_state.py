"""Phase 1b: 内存里缓存"当前已推送的动态命令 path 集合"。

来源：
  - 进程启动时由 server.py 调 hydrate_from_db() 从 ext_dynamic_config 最新一行加载
  - admin push endpoint 成功广播后调 update_paths() 替换全集
  - 兜底：fallback 永远是 False（不识别为 dynamic），不会误判 static 命令

用途：
  - ext_client.send_command_to 调度时打 is_dynamic 标记到 command_log
  - admin 命令日志页可按 is_dynamic 过滤监控

线程安全：单 asyncio 事件循环 + 简单 set，set() 替换是原子操作够用。
"""
from __future__ import annotations

_dynamic_paths: set[str] = set()


def update_paths(paths: list[str] | set[str]) -> None:
    """全量替换。clearDynamicCommands 语义。"""
    global _dynamic_paths
    _dynamic_paths = set(paths or [])


def is_dynamic_path(path: str) -> bool:
    return bool(path) and path in _dynamic_paths


def list_paths() -> list[str]:
    return sorted(_dynamic_paths)


async def hydrate_from_db() -> int:
    """启动时把最新一条配置里的命令 path 同步进来。"""
    import db
    try:
        latest = await db.get_latest_dynamic_config()
    except Exception:
        return 0
    if not latest:
        return 0
    cmds = latest.get("dynamic_commands") or []
    paths = [c.get("path") for c in cmds if isinstance(c, dict) and c.get("path")]
    update_paths(paths)
    return len(paths)
