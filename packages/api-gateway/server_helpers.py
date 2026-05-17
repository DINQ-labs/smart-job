"""
server_helpers.py — job-api-gateway 共享工具函数。

所有 MCP tool 模块和 HTTP route 模块共用的工具函数、常量、认证逻辑。
"""
from __future__ import annotations

from dotenv import load_dotenv  # type: ignore
load_dotenv()

import asyncio
import contextvars
import inspect
import json
import logging
import os
from typing import Any

from fastmcp import Context
from starlette.requests import Request
from starlette.responses import JSONResponse

from ext_client import has_any_ext_connected
from session_store import session_store
from agent_tracker import agent_tracker
from browser_pool import browser_pool
import admin_broadcaster as ab

log = logging.getLogger(__name__)

# ── 请求级 DINQ user_id ────────────────────────────────────────────────────
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_user_id", default="")
# Phase 2: agent role(jobseeker/recruiter)— 同 user 同时连两个 ext 时,
# _resolve_and_bind 据此选对应 ext_kind 的 session,避免 ambiguous error。
_current_role: contextvars.ContextVar[str] = contextvars.ContextVar("current_role", default="")

# ── Admin 认证 ─────────────────────────────────────────────────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
COOKIE_NAME = "boss_admin_token"
_admin_tokens: set[str] = set()

# ── 扩展 WebSocket 连接 token ──────────────────────────────────────────────
EXT_TOKEN = os.environ.get("EXT_TOKEN", "")

# ── 内网代理校验 ──────────────────────────────────────────────────────────
_REQUIRE_INTERNAL_AUTH = os.environ.get("REQUIRE_INTERNAL_AUTH", "").lower() == "true"
_TRUSTED_PROXY_IPS: set[str] = {
    ip.strip()
    for ip in os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if ip.strip()
}


def _require_internal(request: Request):
    """校验请求来自受信任的内网代理。"""
    if not _REQUIRE_INTERNAL_AUTH:
        return None
    client_ip = (request.client.host if request.client else "")
    if client_ip not in _TRUSTED_PROXY_IPS:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    if request.headers.get("x-authenticated") != "true":
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _ok(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


# PROD 模式下屏蔽内部异常栈的开关。默认 OFF（屏蔽），开发环境设 API_DEBUG=1
# 查完整错误用于排障。
_API_DEBUG = os.environ.get("API_DEBUG", "").lower() in ("1", "true", "yes")

_PROD_ERR_MSG = "操作失败，请稍后重试。"

# 内部错误特征：str(Exception) 里出现这些词大概率是堆栈/模块路径/DB 内部信息，
# 不应暴露给 Agent / 终端用户。业务手写的错误消息（如 "session_id 必填"）不会
# 命中这些特征，正常透传。
_SYS_ERROR_MARKERS = (
    "Traceback",
    'File "/',
    "asyncio.",
    "psycopg",
    "asyncpg",
    "aiohttp",
    "urllib",
    "KeyError: ",
    "AttributeError: ",
    "ConnectionRefusedError",
    "TimeoutError:",
)


# 审核补丁 #D：按异常类型识别系统错误，比字符串匹配更准。调用方把 Exception
# 实例直接传给 _err()（而不是 str(e)）就能命中这一层。
def _collect_sys_error_types() -> tuple[type, ...]:
    import asyncio
    types: list[type] = [asyncio.TimeoutError, ConnectionError, OSError]
    try:
        import asyncpg  # type: ignore
        types.append(asyncpg.PostgresError)
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass
    try:
        import psycopg  # type: ignore
        types.append(psycopg.Error)
    except Exception as _e:
        log.debug("silently swallowed: %s", _e)
        pass
    return tuple(types)


_SYS_ERROR_TYPES: tuple[type, ...] = _collect_sys_error_types()


# 业务级错误前缀豁免：以这些开头的消息一定是业务手写错误，即使字符串里偶然
# 撞上 marker 也不过滤。避免类似 "Invalid KeyError: ..." 这种业务消息误伤。
_BUSINESS_PREFIXES = (
    "参数 ", "参数",
    "未登录", "未登陆",
    "配额",
    "session_id", "agent_id",
    "扩展未连接", "扩展",
    "请先",
    "缺少", "无效", "非法",
    "Invalid ", "Missing ", "Unauthorized",
)


def _looks_like_business_msg(s: str) -> bool:
    s_strip = s.lstrip()
    return any(s_strip.startswith(p) for p in _BUSINESS_PREFIXES)


def _err(msg: "str | BaseException", force_safe: bool = False) -> str:
    """统一错误响应。非 DEBUG 模式 + 检测到系统级错误时吐通用消息。

    识别顺序：
      1. force_safe=True → 无条件屏蔽（极敏感场景）
      2. 传入 Exception 实例 + 类型命中 _SYS_ERROR_TYPES → 屏蔽（最可靠）
      3. 业务级前缀（"参数"、"未登录" 等）→ 永远透传，即使字符串撞上 marker
      4. 字符串含 _SYS_ERROR_MARKERS → 屏蔽（字符串兜底，给旧 _err(str(e)) 用）

    完整原始消息（无论是否屏蔽）都写 warning 日志供运维排障。
    """
    if isinstance(msg, BaseException):
        exc: BaseException | None = msg
        msg_str = str(exc)
    else:
        exc = None
        msg_str = str(msg)

    if force_safe:
        is_system = True
    elif exc is not None and isinstance(exc, _SYS_ERROR_TYPES):
        is_system = True
    elif _looks_like_business_msg(msg_str):
        is_system = False
    else:
        is_system = any(m in msg_str for m in _SYS_ERROR_MARKERS)

    should_hide = force_safe or (not _API_DEBUG and is_system)
    if should_hide:
        try:
            log.warning("[_err hidden in prod mode] %s", msg_str[:500])
        except Exception as _e:
            log.debug("silently swallowed: %s", _e)
            pass
        return json.dumps({"ok": False, "error": _PROD_ERR_MSG}, ensure_ascii=False)
    return json.dumps({"ok": False, "error": msg_str}, ensure_ascii=False)

def _ext_connected(session_id: str = "") -> bool:
    if session_id:
        return session_store.get(session_id) is not None
    return has_any_ext_connected("bosszp")

def _get_agent_id(ctx: Context) -> str:
    """从 MCP Context 中安全地提取 agent_id。"""
    try:
        return agent_tracker.get_current_agent_id(ctx.request)
    except AttributeError:
        return ""

def _get_caller_user_id(ctx: Context) -> str:
    """从 MCP Context 获取调用者的 dinQ 系统 user_id。"""
    aid = _get_agent_id(ctx)
    uid = agent_tracker.get_user_id(aid) if aid else ""
    if uid:
        return uid
    return _current_user_id.get()

def _no_ext_msg() -> str:
    return _err("扩展未连接。请加载 job-api-ext 扩展（popup 显示已连接网关）。")


# ── B2 平台工具骨架化 ───────────────────────────────────────────────────────
# mcp_tools_*.py 里绝大多数 tool 走同一个四步骨架：
#   1. 从 ctx 提取 agent_id
#   2. 解析 session_id（Boss 走 _resolve_and_bind；LinkedIn/Indeed 走 _default_*）
#   3. 调用对应 cmd_*（通常 session_id + agent_id 为关键字参数）
#   4. try/except 包成 _ok / _err
# 下面两个 helper 把骨架抽出，让每个 tool 从 ~8 行压成 1 行。

_NEEDS_SITE_CACHE: dict = {}


def _cmd_needs_site(cmd_fn) -> bool:
    """探测 cmd_fn 是否接受 site kwarg(结果按 fn 缓存,避免热路径反射开销)。"""
    if cmd_fn in _NEEDS_SITE_CACHE:
        return _NEEDS_SITE_CACHE[cmd_fn]
    try:
        needs = "site" in inspect.signature(cmd_fn).parameters
    except (ValueError, TypeError):
        needs = False
    _NEEDS_SITE_CACHE[cmd_fn] = needs
    return needs


async def _run_boss_tool(
    ctx: "Context",
    session_id: str,
    app_user_id: str,
    cmd_fn,
    *,
    site: str = "",
    pass_agent_id: bool = True,
    **cmd_kwargs,
) -> str:
    """Boss MCP tool 骨架：_resolve_and_bind + cmd 分发 + 统一响应包装。

    pass_agent_id=False 用于极少数不接受 agent_id 参数的 cmd。
    cmd_kwargs 透传给 cmd_fn —— 所有业务参数（如 encrypt_job_id、keyword）
    都通过 kwargs 传，避免跟 session_id 次序冲突。
    """
    aid = _get_agent_id(ctx)
    try:
        sid = await _resolve_and_bind(aid, session_id, app_user_id, site=site)
    except RuntimeError as e:
        return _err(str(e))
    # 部分 cmd(如 cmd_navigate_to)的签名是 (*, site, url, ...) 同时需要 site
    # kwarg。本 helper 之前把 site 吃掉只用作 _resolve_and_bind 路由,导致
    # `linkedin_navigate_to` 调用时 cmd_navigate_to 永远报 missing 'site'。
    # 用 _cmd_needs_site() 缓存探测结果,避免每次 MCP 调用都跑 inspect.signature()。
    if site and _cmd_needs_site(cmd_fn) and "site" not in cmd_kwargs:
        cmd_kwargs["site"] = site
    try:
        if pass_agent_id:
            return _ok(await cmd_fn(session_id=sid, agent_id=aid, **cmd_kwargs))
        return _ok(await cmd_fn(session_id=sid, **cmd_kwargs))
    except Exception as e:
        return _err(str(e))


def _parse_json_arg(raw: Any, field_name: str, default: Any = None) -> Any:
    """解析 MCP 工具传入的 JSON 字符串参数，统一三类语义：
      - 空字符串 / None → 返回 default
      - 已是 dict/list 等 Python 对象 → 原样透传（Agent 偶尔直接塞对象的容错）
      - 非空字符串 → json.loads；失败抛 ValueError，由调用方包成 _err

    这样 mcp_tools_*.py 里"profile_data / actions / state_payload / urns"这类
    JSON 参数不再需要每个工具各写一遍 import json + try/except。
    """
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception as e:
        raise ValueError(f"{field_name} JSON 解析失败: {e}") from e


async def _run_site_tool(
    session_id: str,
    default_session_fn,
    cmd_fn,
    *cmd_args,
    no_session_hint: "str | Callable[[], str]" = "",
    **cmd_kwargs,
) -> str:
    """LinkedIn / Indeed MCP tool 骨架：session_id 或 _default_*_session()
    兜底 + 统一响应包装。cmd_fn 第一位正参数为 session_id（与 li_cmd / ind_cmd
    惯例一致），后续位/关键字参数透传。

    当 session_id 为空且 default_session_fn 也返回 "" 时（通常因为 DINQ 未登录
    或该用户未连接对应平台的扩展），提前返回明确错误 —— 避免把无意义的
    "session '' 不存在" 暴露给 Agent。

    `no_session_hint` 可传 str 或 callable;callable 仅在 sid 为空时调用,允许
    根据失败时的 session_store 状态生成精细化诊断(避免成功路径上的浪费计算)。
    """
    try:
        sid = session_id or default_session_fn()
        if not sid:
            hint = no_session_hint() if callable(no_session_hint) else no_session_hint
            return _err(
                hint
                or "未找到匹配的扩展会话，请确认已登录 DINQ 且浏览器扩展已连接网关。"
            )
        return _ok(await cmd_fn(sid, *cmd_args, **cmd_kwargs))
    except Exception as e:
        return _err(str(e))

async def _resolve_and_bind(agent_id: str, session_id: str = "", app_user_id: str = "",
                            site: str = "") -> str:
    """
    解析并强制绑定 agent → 扩展会话。
    规则：
      - agent 已绑定其他 session → 报错，不允许切换
      - agent 未绑定 + 指定 session_id/app_user_id → 绑定到指定会话
      - agent 未绑定 + 未指定 + 只有一个活跃会话 → 自动绑定
      - agent 未绑定 + 未指定 + 多个活跃会话 → 报错，必须指定
    site: 可选站点过滤（'boss'/'linkedin'/'indeed'），用于按 site_users[site] 查找
    """
    # 1. 检查已有绑定
    if agent_id:
        bound = agent_tracker.get_bound_session(agent_id)
        if bound:
            if session_id and session_id != bound:
                raise RuntimeError(
                    f"Agent 已绑定到会话 {bound[:16]}，不允许切换。"
                    f"若需使用其他会话，请重新启动 Agent 并指定 --session {session_id[:16]}。"
                )
            return bound  # 直接用已绑定会话

    # 2. 解析 sid — 级联 fallback：①显式指定 → ②绑定表 → ③DINQ身份自动匹配
    sid = ""

    # 优先级 ①: 显式 session_id
    if session_id:
        sid = session_id

    # 优先级 ②: 按 app_user_id 查绑定表 / 浏览器池
    if not sid and app_user_id:
        # 先按站点能力匹配（linkedin/indeed 等多站点场景）
        if site:
            entry = session_store.find_by_site(site, app_user_id)
            if entry is not None:
                sid = entry.session_id
        if not sid:
            sid = await browser_pool.get_session_for_user(app_user_id)
        if not sid:
            sid = session_store.get_session_for_app_user(app_user_id) or ""

    # 优先级 ③: 按 DINQ user_id 精确匹配（多租户安全）
    # agent_id 在 MCP tool handler 中为空（ctx.request 是 CallToolRequest，无 HTTP headers），
    # 所以 agent_tracker 查询拿不到身份，必须从 _current_user_id contextvar 兜底，
    # 这与 _get_caller_user_id() 的身份识别路径保持一致。
    if not sid:
        caller_uid = (agent_tracker.get_user_id(agent_id) if agent_id else "") or _current_user_id.get()
        # 🔒 多租户安全：无 caller_uid 时拒绝 fallback 到任意已连接 session，
        # 避免把别人的扩展 session 当作当前请求的默认会话。
        if not caller_uid:
            raise RuntimeError(
                "无法识别当前用户身份（请先登录 DINQ 账号），无法定位你的扩展会话。"
            )
        all_active = [s for s in session_store.list_all() if s["status"] == "connected"]
        active = [s for s in all_active if s["user_id"] == caller_uid]
        if len(active) == 0:
            raise RuntimeError(
                "没有找到你的扩展会话，请确认浏览器已加载 job-api-ext 扩展并在 Popup 里显示「已连接网关」。"
            )
        # Phase 2: 同 user 可能同时连了 jobseeker + recruiter 两个 ext。
        # 先按 caller 当前 role(_current_role contextvar)过滤,
        # 让 jobseeker agent 自动用 jobseeker ext 的 session,反之亦然。
        caller_role = _current_role.get()
        if len(active) > 1 and caller_role:
            kind_filtered = [s for s in active if s.get("ext_kind") == caller_role]
            if kind_filtered:
                log.info(
                    "[resolve] role-filter: user=%s role=%s 把 %d 个 session 过滤到 %d 个",
                    caller_uid[:16], caller_role, len(active), len(kind_filtered),
                )
                active = kind_filtered
        if len(active) > 1:
            # E1 会话亲和：Agent 可预先通过 set_active_session 指定"这个平台默认用哪个会话",
            # 避免每次调用都强制传 session_id。
            platform = (site or "boss").lower()
            pinned = session_store.get_active_session(caller_uid, platform)
            if pinned and any(s["session_id"] == pinned for s in active):
                log.info(
                    "[resolve] 使用 active session: user=%s platform=%s sid=%s",
                    caller_uid[:16], platform, pinned[:16],
                )
                sid = pinned
            else:
                ids = ", ".join(s["session_id"][:16] for s in active)
                raise RuntimeError(
                    f"当前 DINQ 账号下有 {len(active)} 个扩展会话([{ids}]),"
                    f"请明确指定 session_id 或 app_user_id,或先调用 set_active_session "
                    f"把当前平台默认绑定到其中一个。"
                )
        else:
            sid = active[0]["session_id"]

    # 3. 验证 session 存在
    entry = session_store.get(sid)
    if entry is None:
        raise RuntimeError(f"session {sid[:16]} 不存在或已断开，请重新连接扩展。")

    # 4. 强制绑定（bind_session 内部检查冲突）
    if agent_id:
        agent_tracker.bind_session(agent_id, sid)  # 冲突时抛 RuntimeError
        account_name = entry.account_name or ""
        if account_name:
            agent_tracker.set_bound_account(agent_id, account_name)
        # 同步 user_id：agent 已知的 dinQ user_id → SessionEntry
        # 与上方第③优先级过滤保持对称：agent_tracker 查不到时从 contextvar 兜底。
        # 当前 if agent_id: 分支因 JSON-RPC 层 bug 暂不可达，此处为防御性对齐，
        # 避免未来修复 agent_id 传递后遗漏身份回填。
        caller_uid = agent_tracker.get_user_id(agent_id) or _current_user_id.get()
        if caller_uid and not entry.user_id:
            entry.user_id = caller_uid
        asyncio.create_task(ab.admin_broadcaster.broadcast({
            "event": "agent_bound",
            "agent_id": agent_id,
            "session_id": sid,
            "account_name": account_name,
            "user_id": agent_tracker.get_user_id(agent_id),
        }))

    return sid


async def _http_resolve_session(session_id: str, app_user_id: str) -> str:
    """HTTP 层 session 解析：pool 模式唯一路径。
    - session_id 直接传：验证存在后直接使用（兼容直接指定）
    - app_user_id：必须已通过 pool.acquire() 分配浏览器
    - 两者均空：单会话兜底（仅开发用）
    找不到则抛 RuntimeError。
    """
    if session_id:
        if session_store.get(session_id) is None:
            raise RuntimeError(f"session 不存在或已断开: {session_id[:16]}")
        return session_id
    if app_user_id:
        sid = await browser_pool.get_session_for_user(app_user_id) or ""
        if not sid:
            raise RuntimeError(
                f"用户 {app_user_id!r} 尚未分配浏览器，"
                "请先调用 POST /api/pool/acquire"
            )
        return sid
    # 两者均空：仅允许恰好一个活跃会话（开发场景）
    active = [s for s in session_store.list_all() if s["status"] == "connected"]
    if len(active) == 0:
        raise RuntimeError("没有已连接的扩展")
    if len(active) > 1:
        ids = ", ".join(s["session_id"][:16] for s in active)
        raise RuntimeError(f"多会话模式必须指定 session_id 或 app_user_id。可用: [{ids}]")
    return active[0]["session_id"]


def _auth_required(request: Request):
    """Admin 鉴权。优先级：
      1. Go gateway 注入的 X-User-Role: admin（内网代理模式）
      2. 原有 ADMIN_PASSWORD Cookie（直连 / 开发模式）
    ADMIN_PASSWORD 未设置且未启用内网鉴权时直接放行。
    """
    # Go gateway 已注入 admin 角色 → 放行
    if (request.headers.get("x-authenticated") == "true"
            and request.headers.get("x-user-role") == "admin"):
        return None
    if not ADMIN_PASSWORD:
        return None
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or token not in _admin_tokens:
        return JSONResponse(
            {"ok": False, "error": "未授权，请先登录", "code": "UNAUTHORIZED"},
            status_code=401,
        )
    return None
