"""平台命令共享基座：extension 响应解包 + 会话必存在的命令发送。

供 commands.py (Boss) / linkedin_commands.py / indeed_commands.py 复用，避免
`_unwrap` 与 `_send` 在各平台文件间重复定义。

不适用于 indeed_employer_commands.py —— 它使用不同的响应信封（`result` 字段而非
`ok/code/data`），且其 `_send` 带 `agent_id` 透传。
"""
from __future__ import annotations

from typing import Any

from ext_client import send_command_to
from session_store import session_store


def unwrap_ext_envelope(result: Any) -> Any:
    """从扩展标准响应封装 `{ ok, code, message, data }` 中提取 data。

    非标准格式（包括 `ok=False`、缺 `code` 或缺 `data` 的响应）原样返回，
    由调用方决定如何处理。
    """
    if (isinstance(result, dict)
            and result.get("ok") is True
            and "code" in result
            and "data" in result):
        return result["data"]
    return result


async def send_platform_command(
    session_id: str,
    method: str,
    path: str,
    body: Any = None,
    tool_name: str = "",
    platform_label: str = "Platform",
) -> Any:
    """通过 Extension 会话发送命令并自动拆 extOk 信封。

    会话不存在时抛 RuntimeError 并带平台标签，便于调用方定位问题。
    适用于 LinkedIn / Indeed 这类"会话必须已存在且已登录"的平台命令。
    """
    if session_store.get(session_id) is not None:
        raw = await send_command_to(session_id, method, path, body, tool_name=tool_name)
        return unwrap_ext_envelope(raw)
    raise RuntimeError(
        f"{platform_label} session {session_id[:16]} 不存在。请确认扩展已连接。"
    )
