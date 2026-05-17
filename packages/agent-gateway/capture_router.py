"""
API 抓包 REST 端点 — 接收扩展推送的抓包数据 + 管理后台查看/分析。

端点路径兼容 api-analyzer-server（扩展无需改动即可对接）。
LLM 分析逻辑从 api-analyzer-server/server.py 移植。
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from urllib.parse import quote, urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse

import capture_db
import config

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Extension-facing endpoints (compatible with api-recorder-v2)
# ══════════════════════════════════════════════════════════════════════════════


async def capture_health(request: Request) -> JSONResponse:
    """GET /api/health"""
    count = await capture_db.session_count()
    return JSONResponse({"status": "ok", "sessions": count})


async def capture_session_start(request: Request) -> JSONResponse:
    """POST /api/session/start — {session_id, tab_url}"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    sid = body.get("session_id", "").strip()
    if not sid:
        return JSONResponse({"error": "session_id required"}, status_code=400)

    await capture_db.upsert_session(sid, body.get("tab_url", ""), body.get("session_name", ""))
    return JSONResponse({"status": "ok", "session_id": sid})


async def receive_capture(request: Request) -> JSONResponse:
    """POST /api/capture — {session_id, tab_url, capture}"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    sid = body.get("session_id", "").strip()
    if not sid:
        return JSONResponse({"error": "session_id required"}, status_code=400)

    capture = body.get("capture")
    if not capture or not isinstance(capture, dict):
        return JSONResponse({"error": "capture required"}, status_code=400)

    count = await capture_db.add_capture(sid, body.get("tab_url", ""), capture)

    # 简要日志
    req = capture.get("request", {})
    resp = capture.get("response") or {}
    method = req.get("method", "?")
    url = req.get("url", "?")
    status = resp.get("status", "?")
    log.debug("[capture %s #%d] %s %s %s", sid[:12], count, method, status, url[:80])

    return JSONResponse({"status": "ok", "count": count})


# ══════════════════════════════════════════════════════════════════════════════
# Admin-facing endpoints (for job-api-admin)
# ══════════════════════════════════════════════════════════════════════════════


async def list_capture_sessions(request: Request) -> JSONResponse:
    """GET /api/sessions?limit=50&offset=0"""
    limit = min(int(request.query_params.get("limit", 50)), 200)
    offset = int(request.query_params.get("offset", 0))
    sessions = await capture_db.list_sessions(limit, offset)
    return JSONResponse({"sessions": sessions, "limit": limit, "offset": offset})


async def get_capture_session(request: Request) -> JSONResponse:
    """GET /api/sessions/{session_id}"""
    sid = request.path_params["session_id"]
    session = await capture_db.get_session(sid)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)
    return JSONResponse(session)


async def get_capture_requests(request: Request) -> JSONResponse:
    """GET /api/sessions/{session_id}/requests?limit=100&offset=0"""
    sid = request.path_params["session_id"]
    limit = min(int(request.query_params.get("limit", 100)), 500)
    offset = int(request.query_params.get("offset", 0))
    total, reqs = await capture_db.get_requests(sid, limit, offset)
    return JSONResponse({"total": total, "requests": reqs})


async def delete_capture_session(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{session_id}"""
    sid = request.path_params["session_id"]
    deleted = await capture_db.delete_session(sid)
    return JSONResponse({"status": "ok", "deleted": deleted})


async def analyze_capture_session(request: Request) -> JSONResponse:
    """POST /api/sessions/{session_id}/analyze — 触发 LLM 分析。"""
    sid = request.path_params["session_id"]
    session = await capture_db.get_session(sid)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)

    reqs = await capture_db.get_all_requests(sid)
    if not reqs:
        return JSONResponse({"analysis": "No requests captured yet.", "session_id": sid})

    # 标记 analyzing
    await capture_db.update_analysis(sid, "analyzing")

    try:
        summary = prepare_request_summary(reqs)
        analysis = await call_llm(summary)
        model_used = config.MODEL
        await capture_db.update_analysis(sid, "done", analysis, model_used)
        return JSONResponse({"analysis": analysis, "session_id": sid, "model": model_used})
    except Exception as e:
        log.error("Capture analysis failed for %s: %s", sid, e)
        await capture_db.update_analysis(sid, "failed")
        return JSONResponse({"error": str(e), "session_id": sid}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
# LLM Analysis — ported from api-analyzer-server/server.py
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是一位资深的 Web API 逆向工程专家。

## 背景

我们有一套系统通过 Chrome 扩展 (job-seeker-ext) 在浏览器内执行 API 调用，
再通过 MCP 协议暴露给 AI Agent 进行调度。架构如下：

```
AI Agent (job-agent-gateway)
  ↓ MCP tool call
job-api-gateway (FastMCP server)
  ↓ WebSocket command {method, path, body}
job-seeker-ext (Chrome Extension, 在浏览器 MAIN world 执行, 自动携带 cookie/fingerprint)
  ↓ fetch()
目标网站 API
```

扩展中的命令通过 `defineCommand()` 注册：
```javascript
defineCommand({
  path: 'namespace/action',        // 命令路径
  description: '...',
  requires: { chain, stage, keyParam },  // 需要的前置 token
  produces: { chain, stage },            // 产出的 token
  handler: async (params) => {
    const raw = await executeSiteApi(url, options);
    tokenStore.setChainToken(...);       // 存储安全 token
    return extOk({ raw, ... });
  }
});
```

Token Chain 定义调用间的依赖（如 搜索→详情→沟通 需要逐步获取 securityId）：
```javascript
defineChain({
  namespace: 'jobs', entityKey: 'encryptJobId',
  stages: [
    { name: 'list',   field: 'listSecurityId' },
    { name: 'detail', field: 'detailSecurityId', requires: 'list' },
    { name: 'chat',   field: 'chatSecurityId',   requires: 'detail' },
  ]
});
```

gateway 侧用 FastMCP 暴露 tool：
```python
@mcp.tool()
async def namespace_action(ctx: Context, param1: str, ..., session_id: str = "") -> str:
    sid = await _resolve_and_bind(...)
    return _ok(await cmd_action(param1, session_id=sid))
```

你的任务是分析浏览器插件捕获的 HTTP 流量，输出可直接用于扩展和 gateway 开发的结构化分析报告。

## 输出格式

### 1. API 端点清单

按功能分组列出所有发现的 API 端点：

| 方法 | URL 路径模式 | 用途 | 关键请求参数 | 关键响应字段 | 备注 |
|------|-------------|------|-------------|-------------|------|

对每个端点标注：
- Content-Type（form-urlencoded / json / query string）
- 哪些参数是必需的，哪些是可选的
- 响应中的关键业务字段和安全 token 字段（如 securityId, encryptId 等）

### 2. Token 链与安全机制

分析请求间的 token 依赖关系：
- 哪些 API 返回安全 token（securityId, seed, fpSeed, token 等）
- 这些 token 在后续哪些请求中被消费
- token 的字段名、来源路径（如 `zpData.securityId`）、有效期推测
- 画出 token 流转的 DAG（如: 搜索→详情→沟通）

输出建议的 Token Chain 定义：
```javascript
defineChain({
  namespace: '...',
  entityKey: '...',
  stages: [...]
});
```

### 3. 认证与 Cookie

- 关键 Cookie 名称及用途推测
- 是否有 CSRF token（来源 header/cookie/response）
- 是否有设备指纹参数（fp, clientId, did 等）
- 登录/鉴权流程的调用链

### 4. 业务流程链

识别完整的业务操作流程（按调用顺序），例如：

**流程: 搜索职位并查看详情**
1. `POST /wapi/zpgeek/search/joblist.json` → 获取 jobList + listSecurityId
2. `GET /wapi/zpgeek/job/detail.json?securityId=xxx` → 获取详情 + detailSecurityId
3. ...

标注每步产出和消费的 token。

### 5. 扩展命令定义建议

对每个值得实现的 API，输出完整的 `defineCommand()` 代码：

```javascript
// 文件: commands/xxx.js
defineCommand({
  path: 'namespace/action_name',
  description: '中文描述',
  requires: { chain: '...', stage: '...', keyParam: '...' },  // 如果有前置依赖
  produces: { chain: '...', stage: '...' },                    // 如果产出 token
  handler: async ({ param1, param2 = defaultValue } = {}) => {
    if (!param1) throw new Error('param1 required');

    // API 调用 — executeSiteApi 会在 MAIN world 执行，自动携带 cookie
    const raw = await executeSiteApi('/api/path', {
      method: 'POST',
      headers: { 'Content-Type': '...' },
      body: ...,
    });

    // 存储 token（如果有）
    if (raw?.zpData?.securityId) {
      tokenStore.setChainToken('namespace', entityKey, 'fieldName', raw.zpData.securityId);
    }

    return extOk({ raw, ... });
  },
});
```

### 6. MCP Tool 定义建议

对应每个扩展命令，输出 gateway 侧的 MCP tool 定义：

```python
# 文件: server.py (新增 tool)
@mcp.tool()
async def namespace_action_name(
    ctx: Context,
    param1: str,
    param2: int = 10,
    session_id: str = "",
    app_user_id: str = "",
) -> str:
    \"\"\"
    中文功能描述。

    参数:
      param1: 参数说明
      param2: 参数说明（默认 10）

    返回: { field1: ..., field2: ... }
    \"\"\"
    aid = _get_agent_id(ctx)
    try:
        sid = await _resolve_and_bind(aid, session_id, app_user_id)
    except RuntimeError as e:
        return _err(str(e))
    try:
        return _ok(await cmd_action_name(param1=param1, param2=param2, session_id=sid, agent_id=aid))
    except Exception as e:
        return _err(str(e))
```

以及对应的 commands.py 函数：
```python
async def cmd_action_name(param1: str, ..., session_id: str = "", agent_id: str = "") -> dict:
    result = await send_command_to(session_id, "POST", "namespace/action_name", {...}, tool_name="namespace_action_name", agent_id=agent_id)
    return _unwrap(result)
```

### 7. 反爬与注意事项

- 频率限制（观察到的 rate limit 特征）
- 加密/签名参数（哪些需要在扩展 MAIN world 中生成而非模拟）
- 请求头中的特殊字段（自定义 header, referer 策略等）
- 对实现的建议：哪些可以直接 fetch，哪些需要特殊处理

请用中文回答。分析要专业、精确，代码要完整可用。"""


def prepare_request_summary(requests: list[dict]) -> str:
    """对抓包数据分组归纳，生成 LLM 分析用的摘要文本。"""
    groups: dict[str, list[dict]] = defaultdict(list)

    for req_data in requests:
        method = req_data.get("method", "GET")
        url = req_data.get("url", "")
        pattern = normalize_url(url)
        key = f"{method} {pattern}"
        groups[key].append(req_data)

    lines = [
        "=" * 60,
        "CAPTURED API TRAFFIC SUMMARY",
        f"Total requests: {len(requests)}",
        f"Unique endpoints: {len(groups)}",
        "=" * 60,
    ]

    for key, items in sorted(groups.items(), key=lambda x: -len(x[1])):
        lines.append("")
        lines.append(f"--- {key} ({len(items)} calls) ---")

        sample = items[0]
        lines.append(f"  Example URL: {sample.get('url', '')[:200]}")
        lines.append(f"  Status: {sample.get('response_status', '?')}")

        # Auth headers
        headers = sample.get("request_headers") or {}
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except (json.JSONDecodeError, TypeError):
                headers = {}
        auth_headers = extract_auth_headers(headers)
        if auth_headers:
            lines.append(f"  Auth Headers: {json.dumps(auth_headers, ensure_ascii=False)}")

        # Request body
        body = sample.get("request_body")
        if body:
            if len(body) > 500:
                body = body[:500] + "...[truncated]"
            lines.append(f"  Request Body: {body}")

        # Response body sample
        resp_body = sample.get("response_body")
        if resp_body and resp_body != "null":
            resp_preview = _truncate(resp_body, 1000)
            try:
                parsed = json.loads(resp_preview)
                if isinstance(parsed, dict):
                    structure = describe_json_structure(parsed)
                    lines.append(f"  Response Structure: {json.dumps(structure, ensure_ascii=False, indent=2)}")
                else:
                    lines.append(f"  Response Body: {resp_preview[:300]}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"  Response Body: {resp_preview[:300]}")

        # URL variations
        if len(items) > 1:
            urls = set(i.get("url", "") for i in items[:5])
            if len(urls) > 1:
                lines.append(f"  URL variations ({min(5, len(items))} samples):")
                for u in list(urls)[:3]:
                    lines.append(f"    - {u[:150]}")

    return "\n".join(lines)


def normalize_url(url: str) -> str:
    """Replace dynamic segments in URL with placeholders."""
    try:
        parsed = urlparse(url)
        path = parsed.path
        path = re.sub(r'/\d{4,}', '/{id}', path)
        path = re.sub(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/{uuid}', path)
        path = re.sub(r'/[0-9a-f]{16,}', '/{hash}', path)
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    except Exception:
        return url


def extract_auth_headers(headers: dict) -> dict:
    """Extract authentication-related headers."""
    auth_keys = {
        'cookie', 'authorization', 'x-csrf-token', 'x-token',
        'zp-token', 'x-requested-with', 'x-zp-client-id',
    }
    result = {}
    for k, v in headers.items():
        if k.lower() in auth_keys:
            if k.lower() == 'cookie':
                cookie_names = [c.split('=')[0].strip() for c in str(v).split(';') if '=' in c]
                result[k] = f"[cookies: {', '.join(cookie_names)}]"
            else:
                result[k] = str(v)[:100] + ('...' if len(str(v)) > 100 else '')
    return result


def describe_json_structure(obj: Any, depth: int = 0, max_depth: int = 3) -> Any:
    """Describe JSON structure (keys + types) without exposing all values."""
    if depth >= max_depth:
        return "..."
    if isinstance(obj, dict):
        result = {}
        for k, v in list(obj.items())[:20]:
            if isinstance(v, dict):
                result[k] = describe_json_structure(v, depth + 1, max_depth)
            elif isinstance(v, list):
                if v:
                    first = v[0]
                    if isinstance(first, dict):
                        result[k] = [describe_json_structure(first, depth + 1, max_depth)]
                    else:
                        result[k] = [type(first).__name__]
                else:
                    result[k] = []
            else:
                val_str = str(v)
                if len(val_str) <= 50:
                    result[k] = f"({type(v).__name__}) {val_str}"
                else:
                    result[k] = f"({type(v).__name__}) {val_str[:30]}..."
        return result
    return type(obj).__name__


def _truncate(text: str | None, max_len: int) -> str:
    if not text:
        return ""
    text = str(text)
    return text[:max_len] + "...[truncated]" if len(text) > max_len else text


# ── LLM call ─────────────────────────────────────────────────────────────────

from typing import Any
from anthropic import AsyncAnthropic


async def call_llm(summary: str) -> str:
    """调用 LLM 分析抓包摘要。复用 job-agent-gateway 的 Anthropic/OpenRouter 配置。"""
    client_kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_BASE_URL:
        client_kwargs["base_url"] = config.ANTHROPIC_BASE_URL
    client = AsyncAnthropic(**client_kwargs)

    response = await client.messages.create(
        model=config.MODEL,
        max_tokens=4096,
        temperature=0.3,
        system=_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"以下是从浏览器捕获的 API 流量摘要，请分析：\n\n{summary}"},
        ],
    )
    return response.content[0].text if response.content else ""


# ══════════════════════════════════════════════════════════════════════════════
# Claude Code 实现 — 服务端触发 claude CLI
# ══════════════════════════════════════════════════════════════════════════════

from starlette.responses import Response, StreamingResponse

_TARGET_DIRS = {
    "job-api-gateway": "/opt/job-api-gateway",
    "job-agent-gateway": "/opt/job-agent-gateway",
}


async def download_capture_raw(request: Request):
    """GET /api/sessions/{session_id}/download — 下载原始抓包数据 JSON。"""
    sid = request.path_params["session_id"]
    session = await capture_db.get_session(sid)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)

    reqs = await capture_db.get_all_requests(sid)
    payload = {
        "session_id": session["id"],
        "name": session.get("name", ""),
        "tab_url": session.get("tab_url", ""),
        "request_count": session.get("request_count", len(reqs)),
        "created_at": session.get("created_at"),
        "requests": reqs,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    name_part = session.get("name", "").replace(" ", "_")[:30] or sid[:12]
    filename = f"capture_{name_part}.json"
    # RFC 5987: filename* for non-ASCII, filename for ASCII fallback
    ascii_fallback = f"capture_{sid[:12]}.json"
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": cd},
    )


async def download_capture_analysis(request: Request):
    """GET /api/sessions/{session_id}/download-analysis — 下载 LLM 分析结果 Markdown。"""
    sid = request.path_params["session_id"]
    session = await capture_db.get_session(sid)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)

    if session.get("analysis_status") != "done" or not session.get("analysis_text"):
        return JSONResponse({"error": "analysis not available"}, status_code=404)

    name_part = session.get("name", "").replace(" ", "_")[:30] or sid[:12]
    header = (
        f"# API Capture Analysis: {session.get('name') or sid}\n\n"
        f"- **Session ID:** {sid}\n"
        f"- **Tab URL:** {session.get('tab_url', '')}\n"
        f"- **Requests:** {session.get('request_count', 0)}\n"
        f"- **Model:** {session.get('analysis_model', 'unknown')}\n"
        f"- **Analyzed at:** {session.get('analyzed_at', '')}\n\n---\n\n"
    )
    content = header + session["analysis_text"]
    filename = f"analysis_{name_part}.md"
    ascii_fallback = f"analysis_{sid[:12]}.md"
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": cd},
    )


async def implement_capture_session(request: Request):
    """POST /api/sessions/{session_id}/implement — 服务端触发 Claude Code 实现。SSE 流式返回输出。"""
    sid = request.path_params["session_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    instructions = body.get("instructions", "").strip()
    target = body.get("target", "job-api-gateway")

    session = await capture_db.get_session(sid)
    if not session:
        return JSONResponse({"error": "session not found"}, status_code=404)
    if session.get("analysis_status") != "done":
        return JSONResponse({"error": "analysis not done yet"}, status_code=400)

    analysis_text = session.get("analysis_text", "")
    session_name = session.get("name", "")

    target_dir = _TARGET_DIRS.get(target)
    if not target_dir:
        return JSONResponse({"error": f"unknown target: {target}"}, status_code=400)

    prompt = f"""根据以下 API 抓包分析结果，为项目实现新的命令和工具。

## 抓包目的
{session_name}

## 用户指令
{instructions}

## 抓包分析结果
{analysis_text}

## 目标项目
当前工作目录为 {target_dir}，请直接修改代码文件。
- 如果目标是 job-api-gateway：在 commands.py 添加 cmd_* 函数，在 server.py 添加 @mcp.tool()
- 如果目标是 job-agent-gateway：在对应模块中添加功能
"""

    import asyncio as _asyncio

    async def event_stream():
        try:
            process = await _asyncio.create_subprocess_exec(
                "claude", "-p", prompt, "--output-format", "text",
                cwd=target_dir,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.STDOUT,
            )
            async for line in process.stdout:
                text = line.decode("utf-8", errors="replace")
                yield f"data: {json.dumps({'type': 'output', 'text': text}, ensure_ascii=False)}\n\n"
            await process.wait()
            yield f"data: {json.dumps({'type': 'done', 'exit_code': process.returncode})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
