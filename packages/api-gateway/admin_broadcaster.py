"""
admin_broadcaster.py: Admin WebSocket 实时事件广播器。
fire-and-forget：每个 client 异常独立捕获，不影响其他。
"""
from __future__ import annotations

import json
from typing import Any

from starlette.websockets import WebSocket


class AdminBroadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def subscribe(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    async def unsubscribe(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """广播事件到所有已订阅的 admin WS 客户端。"""
        if not self._clients:
            return
        text = json.dumps(event, ensure_ascii=False)
        dead = set()
        for client in list(self._clients):
            try:
                await client.send_text(text)
            except Exception:
                dead.add(client)
        self._clients -= dead


# 单例
admin_broadcaster = AdminBroadcaster()
