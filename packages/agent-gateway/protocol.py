"""
消息协议格式 helpers（WebSocket + SSE 共用）。

─── WebSocket 协议 ───────────────────────────────────────────────────────────
前端 → gateway:
  {"type": "user_message", "text": "..."}
  {"type": "abort"}
  {"type": "new_session",  "role_type": "...", "entry_type": "..."}

gateway → 前端（JSON 字符串）:
  {"type": "text_delta",    "delta": "..."}
  {"type": "tool_call",     "tool": "...", "input": {...}}
  {"type": "tool_result",   "tool": "...", "ok": bool, "preview": "..."}
  {"type": "message_end",   "stop_reason": "end_turn"}
  {"type": "error",         "message": "..."}
  {"type": "aborted"}
  {"type": "connected",     "user_id": "...", "history_length": N}
  {"type": "session_ready", "history_length": 0}

─── SSE 协议 ─────────────────────────────────────────────────────────────────
POST /agent/sse?user_id=<id>  Body: {"text": "...", "new_session": false}
Response: text/event-stream，每条事件格式：
  event: <type>
  data: <json>

  （空行分隔；keep-alive comment: ": keepalive"）

事件类型与 WS 相同，另加：
  {"type": "done"} / data: [DONE]  — 流结束标志（OpenAI 兼容）
"""
import json


def connected(user_id: str, history_length: int) -> str:
    return json.dumps({"type": "connected", "user_id": user_id, "history_length": history_length}, ensure_ascii=False)


def text_delta(delta: str) -> str:
    return json.dumps({"type": "text_delta", "delta": delta}, ensure_ascii=False)


def tool_call(tool: str, input_: dict) -> str:
    return json.dumps({"type": "tool_call", "tool": tool, "input": input_}, ensure_ascii=False)


def tool_result(tool: str, ok: bool, preview: str) -> str:
    return json.dumps({"type": "tool_result", "tool": tool, "ok": ok, "preview": preview}, ensure_ascii=False)


def message_end(stop_reason: str) -> str:
    return json.dumps({"type": "message_end", "stop_reason": stop_reason}, ensure_ascii=False)


def error(message: str) -> str:
    return json.dumps({"type": "error", "message": message}, ensure_ascii=False)


def aborted() -> str:
    return json.dumps({"type": "aborted"}, ensure_ascii=False)


def session_ready() -> str:
    return json.dumps({"type": "session_ready", "history_length": 0}, ensure_ascii=False)


# ── SSE 格式化 helpers ─────────────────────────────────────────────────────────

def sse_event(event_type: str, payload) -> str:
    """
    将 payload（dict 或字符串）格式化为标准 SSE 事件。

    输出格式：
        event: <event_type>
        data: <json_or_str>

        （末尾含两个换行，符合 SSE 规范）
    """
    data = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)
    return f"event: {event_type}\ndata: {data}\n\n"


def sse_keepalive() -> str:
    """SSE keep-alive comment，防止代理/浏览器因长时间无数据断连。"""
    return ": keepalive\n\n"


def sse_done() -> str:
    """SSE 流结束标志（兼容 OpenAI stream 格式）。"""
    return "event: done\ndata: [DONE]\n\n"
