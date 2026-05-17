"""voice_router.py — 讯飞 IAT 语音识别代理。

两条路径:

[POST /voice/asr] 整段模式(已废弃但保留)
  接收前端上传的 16k mono 16bit raw PCM,讯飞 IAT WebSocket 跑完返完整文本。
  适合短录音,UX 是"录完→等→出结果"。

[WebSocket /voice/stream] 实时流式模式(新)
  client → server:
    binary frame      PCM chunk(任意大小,server 切 1280 bytes 帧节流送讯飞)
    text  {"type":"end"}    用户停止录音 → server 发讯飞 last 帧
    text  {"type":"abort"}  立即终止
  server → client:
    text  {"type":"partial", "text", "sn", "is_seg_end", "is_final"}
                       partial 实时识别结果(同 sn 内 text 会被覆盖)
    text  {"type":"error",   "error"}

讯飞 IAT 协议(wss://iat-api.xfyun.cn/v2/iat):
  鉴权: URL query (host/date/authorization) HMAC-SHA256 签名 API_SECRET
  帧:   first (status=0) 带 common+business 配置 → middle (1) → last (2)
  节流: 1280 bytes / 40ms 一帧(讯飞要求,过快会被拒)
  返回: data.result.{sn,ls,ws[].cw[].w}  + status=2 标记终结

环境变量(.env):
  XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET / XFYUN_ASR_URL
"""
from __future__ import annotations

import os
import json
import base64
import hmac
import hashlib
import asyncio
import logging
from email.utils import formatdate
from urllib.parse import urlencode, urlparse

import websockets
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


XFYUN_APP_ID     = os.environ.get("XFYUN_APP_ID", "")
XFYUN_API_KEY    = os.environ.get("XFYUN_API_KEY", "")
XFYUN_API_SECRET = os.environ.get("XFYUN_API_SECRET", "")
XFYUN_ASR_URL    = os.environ.get("XFYUN_ASR_URL", "wss://iat-api.xfyun.cn/v2/iat")


def _build_auth_url() -> str:
    """构造带鉴权 query 的 ws URL。讯飞会校验 HMAC 签名 + 5 分钟内 date。"""
    u = urlparse(XFYUN_ASR_URL)
    host = u.netloc
    path = u.path
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    sign_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(
            XFYUN_API_SECRET.encode("utf-8"),
            sign_origin.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    auth_origin = (
        f'api_key="{XFYUN_API_KEY}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    auth = base64.b64encode(auth_origin.encode("utf-8")).decode("utf-8")
    qs = urlencode({"authorization": auth, "date": date, "host": host})
    return f"{XFYUN_ASR_URL}?{qs}"


async def _xfyun_iat_recognize(pcm_bytes: bytes) -> str:
    """流式发 PCM 给讯飞,并发 sender + receiver,拼接所有结果返回完整文本。

    为什么必须并发:讯飞协议双向流——
      - server 随时推 partial result + status=2 终止信号(vad_eos / 内部 timeout 触发)
      - 如果 sender 只发不收,server 已经 status=2 关闭 session 后,后续帧会被讯飞
        判定 "code 10165 invalid handle",整个 request 失败
    解法:receiver 收到 status=2 / 错误时 set done event,sender 下一帧前 check 立即退出。
    """
    url = _build_auth_url()
    FRAME_SIZE = 1280  # 讯飞推荐:16k × 2byte × 0.04s = 1280 bytes / 帧
    INTERVAL_SEC = 0.04

    pieces: dict[int, str] = {}     # sn → 该段文本(讯飞按句切分)
    done = asyncio.Event()          # receiver 完成时 set,sender 看到立即停发
    receiver_error: list[Exception] = []

    async with websockets.connect(url, max_size=10_000_000, open_timeout=10) as ws:
        async def sender():
            total = len(pcm_bytes)
            for i, off in enumerate(range(0, total, FRAME_SIZE)):
                if done.is_set():
                    return  # receiver 已完成(讯飞主动 status=2 / 错误),不再发
                chunk = pcm_bytes[off:off + FRAME_SIZE]
                is_first = (i == 0)
                is_last = (off + FRAME_SIZE) >= total
                payload: dict = {
                    "data": {
                        "status": 0 if is_first else (2 if is_last else 1),
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    }
                }
                if is_first:
                    payload["common"] = {"app_id": XFYUN_APP_ID}
                    payload["business"] = {
                        "language": "zh_cn",
                        "domain": "iat",
                        "accent": "mandarin",
                        "vad_eos": 5000,    # 5s 静音视为说完(server 主动 close)
                        "ptt": 1,           # 加标点
                    }
                try:
                    await ws.send(json.dumps(payload, ensure_ascii=False))
                except Exception:
                    return  # 连接已关 → 退出,让 receiver 收尾
                await asyncio.sleep(INTERVAL_SEC)

        async def receiver():
            try:
                async for raw in ws:
                    try:
                        resp = json.loads(raw)
                    except Exception as e:
                        log.warning("[xfyun-iat] bad json: %s (%s)", e, str(raw)[:200])
                        continue
                    code = resp.get("code", -1)
                    if code != 0:
                        receiver_error.append(
                            RuntimeError(f"xfyun code={code} msg={resp.get('message')}")
                        )
                        return
                    d = resp.get("data") or {}
                    result = d.get("result") or {}
                    ws_arr = result.get("ws") or []
                    text = "".join(
                        cw.get("w", "")
                        for w in ws_arr
                        for cw in (w.get("cw") or [])
                    )
                    sn = result.get("sn", len(pieces))
                    pieces[sn] = text
                    if d.get("status") == 2:
                        return
            except websockets.exceptions.ConnectionClosed:
                # 讯飞主动 close(vad_eos / timeout)— 已收到的 pieces 仍有效
                pass
            finally:
                done.set()

        await asyncio.gather(sender(), receiver())

    if receiver_error:
        raise receiver_error[0]
    return "".join(pieces[k] for k in sorted(pieces.keys()))


async def voice_asr_handler(request: Request) -> JSONResponse:
    """POST /voice/asr — multipart/form-data with field `file` = 16k PCM bytes."""
    if not XFYUN_APP_ID or not XFYUN_API_KEY or not XFYUN_API_SECRET:
        return JSONResponse(
            {"error": "xfyun not configured(.env XFYUN_APP_ID/API_KEY/API_SECRET)"},
            status_code=503,
        )
    try:
        form = await request.form()
    except Exception as e:
        return JSONResponse({"error": f"bad form: {e}"}, status_code=400)

    f = form.get("file") or form.get("audio")
    if f is None or not hasattr(f, "read"):
        return JSONResponse({"error": "missing file field"}, status_code=400)

    pcm = await f.read()
    if not pcm:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    # 16k × 2byte → 8MB ≈ 250s,超过则拒
    if len(pcm) > 8_000_000:
        return JSONResponse({"error": "audio too large (>8MB)"}, status_code=413)
    # 太短(<0.3s)讯飞会直接报错,先 reject 友好提示
    if len(pcm) < 16000 * 2 * 0.3:
        return JSONResponse({"error": "audio too short"}, status_code=400)

    log.info("[voice] asr request: %d bytes (~%.1fs)", len(pcm), len(pcm) / 32000)
    try:
        text = await _xfyun_iat_recognize(pcm)
    except Exception as e:
        log.exception("[voice] xfyun failed")
        return JSONResponse({"error": f"asr failed: {e}"}, status_code=502)
    log.info("[voice] asr result: %r", text[:80])
    return JSONResponse({"text": text})


# ═══════════════════════════════════════════════════════════════════
#                    实时流式模式 (WebSocket)
# ═══════════════════════════════════════════════════════════════════

async def voice_stream_handler(client_ws: WebSocket) -> None:
    """WebSocket /voice/stream — 实时双向流式语音识别。

    跟整段 /voice/asr 的关键差别:
      - client 边录边推 PCM(任意 chunk 大小)
      - server 把每个 chunk 累积切 1280 bytes 一帧 + 40ms 节流送讯飞
      - 讯飞返 partial 结果 → 立即转发给 client(同 sn 后续 partial 会覆盖前面)
      - vad_eos 触发讯飞主动结束 → 通知 client 一次 is_final=True 后关连接
    """
    await client_ws.accept()

    if not (XFYUN_APP_ID and XFYUN_API_KEY and XFYUN_API_SECRET):
        try:
            await client_ws.send_text(json.dumps({
                "type": "error", "error": "xfyun not configured",
            }))
        finally:
            await client_ws.close()
        return

    log.info("[voice-stream] client connected")
    url = _build_auth_url()
    FRAME = 1280
    INTERVAL = 0.04
    accumulated = bytearray()
    first_sent = False
    xfyun_closed = asyncio.Event()  # 讯飞侧已结束(status=2 / vad_eos / error)

    async def send_xfyun_frame(xf_ws, audio_bytes: bytes, is_last: bool) -> None:
        """构造一帧讯飞 payload 发送。"""
        nonlocal first_sent
        payload: dict = {
            "data": {
                "status": 0 if not first_sent else (2 if is_last else 1),
                "format": "audio/L16;rate=16000",
                "encoding": "raw",
                "audio": base64.b64encode(audio_bytes).decode("ascii"),
            }
        }
        if not first_sent:
            payload["common"] = {"app_id": XFYUN_APP_ID}
            payload["business"] = {
                "language": "zh_cn", "domain": "iat",
                "accent": "mandarin", "vad_eos": 5000, "ptt": 1,
            }
            first_sent = True
        await xf_ws.send(json.dumps(payload))
        await asyncio.sleep(INTERVAL)

    async def flush_to_xfyun(xf_ws, is_end: bool) -> None:
        """把 accumulated 切 FRAME 大小帧送讯飞。is_end=True 时连尾巴一起送。"""
        while len(accumulated) >= FRAME and not xfyun_closed.is_set():
            chunk = bytes(accumulated[:FRAME])
            del accumulated[:FRAME]
            is_last_frame = is_end and not accumulated
            try:
                await send_xfyun_frame(xf_ws, chunk, is_last_frame)
            except websockets.exceptions.ConnectionClosed:
                xfyun_closed.set()
                return
        if is_end and accumulated and not xfyun_closed.is_set():
            chunk = bytes(accumulated)
            accumulated.clear()
            try:
                await send_xfyun_frame(xf_ws, chunk, True)
            except websockets.exceptions.ConnectionClosed:
                xfyun_closed.set()
        elif is_end and first_sent and not xfyun_closed.is_set():
            # 发个空 last 帧表示结束(讯飞需要见到 status=2 才返 final)
            try:
                await send_xfyun_frame(xf_ws, b"", True)
            except websockets.exceptions.ConnectionClosed:
                xfyun_closed.set()

    try:
        async with websockets.connect(url, max_size=10_000_000, open_timeout=10) as xf_ws:

            async def client_to_xfyun() -> None:
                """从 client 接 PCM/控制帧 → 转发讯飞。"""
                try:
                    while not xfyun_closed.is_set():
                        msg = await client_ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if msg.get("type") != "websocket.receive":
                            continue
                        if msg.get("bytes"):
                            accumulated.extend(msg["bytes"])
                            await flush_to_xfyun(xf_ws, is_end=False)
                        elif msg.get("text"):
                            try:
                                ctrl = json.loads(msg["text"])
                            except Exception:
                                continue
                            t = ctrl.get("type")
                            if t == "end":
                                await flush_to_xfyun(xf_ws, is_end=True)
                                return
                            if t == "abort":
                                return
                except WebSocketDisconnect:
                    pass
                # client 断开但已发过 → 补发 last 帧让讯飞收尾
                if first_sent and not xfyun_closed.is_set():
                    try:
                        await flush_to_xfyun(xf_ws, is_end=True)
                    except Exception:
                        pass

            async def xfyun_to_client() -> None:
                """从讯飞接 partial → 转发 client。"""
                try:
                    async for raw in xf_ws:
                        try:
                            resp = json.loads(raw)
                        except Exception:
                            continue
                        code = resp.get("code", -1)
                        if code != 0:
                            await _safe_send(client_ws, {
                                "type": "error",
                                "error": f"xfyun code={code}: {resp.get('message')}",
                            })
                            xfyun_closed.set()
                            return
                        d = resp.get("data") or {}
                        result = d.get("result") or {}
                        ws_arr = result.get("ws") or []
                        text = "".join(
                            cw.get("w", "")
                            for w in ws_arr
                            for cw in (w.get("cw") or [])
                        )
                        is_final = (d.get("status") == 2)
                        await _safe_send(client_ws, {
                            "type": "partial",
                            "text": text,
                            "sn": result.get("sn", 0),
                            "is_seg_end": result.get("ls", False),
                            "is_final": is_final,
                        })
                        if is_final:
                            xfyun_closed.set()
                            return
                except websockets.exceptions.ConnectionClosed:
                    # 讯飞主动 close(vad_eos)→ 给 client 个 is_final 信号
                    await _safe_send(client_ws, {
                        "type": "partial", "text": "", "sn": 0,
                        "is_seg_end": True, "is_final": True,
                    })
                finally:
                    xfyun_closed.set()

            await asyncio.gather(client_to_xfyun(), xfyun_to_client())

    except Exception as e:
        log.exception("[voice-stream] handler failed")
        await _safe_send(client_ws, {"type": "error", "error": str(e)})

    try:
        await client_ws.close()
    except Exception:
        pass
    log.info("[voice-stream] client disconnected")


async def _safe_send(ws: WebSocket, payload: dict) -> bool:
    """send_text wrapped 不让 close 后的 send 把 handler 整死。"""
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False
