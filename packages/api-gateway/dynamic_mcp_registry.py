"""Phase 2: 把 yaml 里的 dynamic_commands 注册成 FastMCP tool（运行时）。

工作方式：
  - 启动时 server.py 调 set_mcp(mcp) + apply_latest_from_db(mcp)，把 DB 里
    最新一条 ext_dynamic_config 里的 dynamic_commands 全部注册成 MCP tool。
  - admin push endpoint 成功广播给扩展之后，再调 apply_config(cmds)，让
    本进程的 MCP tools/list 也立刻刷新（无需重启网关）。

注册细节：
  - 用 fastmcp.tools.Tool.from_function + mcp.add_tool（FastMCP ≥ 3.x）
  - 给 wrapper 函数动态构造 inspect.Signature，让 FastMCP 能从签名生成
    JSON schema（agent 工具发现走 MCP tools/list，schema 从这儿来）
  - 调用链：MCP wrapper → _run_boss_tool → cmd_dynamic_dispatch → ext WS

不做的：
  - 模板渲染：扩展端 request-builder.js 已实现，gateway 只透传参数 dict
  - linkedin / indeed 站点：等 dynamic-commands/{linkedin,indeed}/ 下出现
    第一条 yaml 时再扩；目前所有 yaml 都在 boss/ 下
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from fastmcp import Context

from commands import cmd_dynamic_dispatch
from server_helpers import _run_boss_tool

log = logging.getLogger(__name__)

# yaml params.type → Python 类型
_PARAM_TYPE_MAP: dict[str, type] = {
    "str": str, "string": str,
    "int": int, "integer": int,
    "bool": bool, "boolean": bool,
    "float": float, "number": float,
}

# {tool_name: {"path": ..., "site": ...}} —— clear 时知道要摘哪些
_registered: dict[str, dict[str, str]] = {}

# FastMCP 实例由 server.py 启动时 set_mcp() 注入；http_routes 推送后用
_mcp: Any = None


def set_mcp(mcp: Any) -> None:
    """server.py:_startup 里调一次。"""
    global _mcp
    _mcp = mcp


def _require_mcp() -> Any:
    if _mcp is None:
        raise RuntimeError(
            "dynamic_mcp_registry: mcp 实例未注入（server.py 启动时应调 set_mcp）"
        )
    return _mcp


def _coerce_default(default: Any, py_type: type) -> Any:
    if default is not None:
        # 如果 yaml 给了 default，原样返回（int 就 int、str 就 str）
        return default
    if py_type is int:
        return 0
    if py_type is float:
        return 0.0
    if py_type is bool:
        return False
    return ""


def _normalize_params(raw_params: Any) -> list[dict]:
    """规整 yaml 里的 mcp.params。每项是 {name, type(py), default, required, desc}。"""
    out: list[dict] = []
    for p in (raw_params or []):
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        if not name or not isinstance(name, str):
            continue
        type_str = (p.get("type") or "str").lower()
        py_type = _PARAM_TYPE_MAP.get(type_str, str)
        required = bool(p.get("required"))
        default = None if required else _coerce_default(p.get("default"), py_type)
        out.append({
            "name": name,
            "type": py_type,
            "default": default,
            "required": required,
            "desc": (p.get("description") or "").strip(),
        })
    return out


def _build_handler(cmd_def: dict, mcp_name: str, py_params: list[dict]) -> Callable:
    """构造一个签名暴露所有参数的 async wrapper，转交给 _run_boss_tool。"""
    path: str = cmd_def["path"]
    rb: dict = cmd_def.get("requestBuilder") or {}
    method = (rb.get("method") or "GET").upper()
    description = (cmd_def.get("description") or "").strip() or path

    # 内核：被 _run_boss_tool 注入 session_id / agent_id 后调用
    async def _core(session_id: str = "", agent_id: str = "", **kwargs):
        return await cmd_dynamic_dispatch(
            ext_path=path,
            method=method,
            cmd_kwargs=kwargs,
            tool_name=mcp_name,
            session_id=session_id,
            agent_id=agent_id,
        )

    async def _public(ctx: Context, session_id: str = "",
                      app_user_id: str = "", **kwargs):
        return await _run_boss_tool(
            ctx, session_id, app_user_id, _core, **kwargs,
        )

    parameters = [
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          annotation=Context),
        inspect.Parameter("session_id", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          default="", annotation=str),
        inspect.Parameter("app_user_id", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          default="", annotation=str),
    ]
    annotations: dict[str, Any] = {
        "ctx": Context, "session_id": str, "app_user_id": str, "return": str,
    }
    for p in py_params:
        if p["required"]:
            parameters.append(inspect.Parameter(
                p["name"], inspect.Parameter.KEYWORD_ONLY,
                annotation=p["type"],
            ))
        else:
            parameters.append(inspect.Parameter(
                p["name"], inspect.Parameter.KEYWORD_ONLY,
                default=p["default"], annotation=p["type"],
            ))
        annotations[p["name"]] = p["type"]

    # FastMCP/pydantic 既看 __signature__ 又看 __annotations__；两边都要塞
    _public.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    _public.__annotations__ = annotations
    _public.__name__ = mcp_name
    _public.__qualname__ = mcp_name
    _public.__doc__ = description
    return _public


def register_dynamic_mcp_tool(cmd_def: dict) -> str | None:
    """注册（或替换同名）一个 dynamic 命令为 MCP tool。返回工具名或 None。"""
    mcp = _require_mcp()
    mcp_block = cmd_def.get("mcp") or {}
    name = mcp_block.get("name")
    path = cmd_def.get("path") or ""
    if not name or not isinstance(name, str):
        log.warning("[dynamic-mcp] 跳过 path=%s：mcp.name 缺失或非字符串", path)
        return None
    rb = cmd_def.get("requestBuilder") or {}
    if not rb.get("url"):
        log.warning("[dynamic-mcp] 跳过 %s：requestBuilder.url 为空", name)
        return None

    py_params = _normalize_params(mcp_block.get("params"))
    handler = _build_handler(cmd_def, name, py_params)
    desc = (cmd_def.get("description") or "").strip() or path

    # 同名旧 tool 先摘掉（替换语义）
    _safe_remove(mcp, name)

    # 用真 FastMCP 时走 Tool.from_function；测试里 mcp 是 MagicMock 时直接 add_tool
    try:
        from fastmcp.tools import Tool  # type: ignore
        tool = Tool.from_function(handler, name=name, description=desc)
        mcp.add_tool(tool)
    except Exception:
        # 兜底：装饰器形式（@mcp.tool(name=..., description=...)）
        mcp.tool(name=name, description=desc)(handler)

    site = path.split("/", 1)[0] if "/" in path else ""
    _registered[name] = {"path": path, "site": site}
    log.info("[dynamic-mcp] 注册 %s ← %s（%d 参数）",
             name, path, len(py_params))
    return name


def _safe_remove(mcp: Any, name: str) -> None:
    # FastMCP ≥ 3.x 推 local_provider.remove_tool；老路径 mcp.remove_tool 也兼容
    try:
        provider = getattr(mcp, "local_provider", None)
        if provider is not None and hasattr(provider, "remove_tool"):
            provider.remove_tool(name)
            return
        mcp.remove_tool(name)
    except Exception:
        # 没注册过 / 已被摘 / 实现不支持 都吞掉，不打断 apply_config
        pass


def clear_dynamic_mcp_tools() -> int:
    """全清。返回清掉数量。"""
    mcp = _require_mcp()
    n = 0
    for name in list(_registered.keys()):
        _safe_remove(mcp, name)
        _registered.pop(name, None)
        n += 1
    return n


def list_registered() -> list[dict[str, str]]:
    """[{name, path, site}, ...]，给 admin 页面用。"""
    return [
        {"name": n, "path": v["path"], "site": v["site"]}
        for n, v in sorted(_registered.items())
    ]


def apply_config(cmds: list[dict] | None) -> dict[str, Any]:
    """全量替换：清旧 → 注册新。返回 {added, failed, skipped}。"""
    clear_dynamic_mcp_tools()
    added: list[str] = []
    failed: list[dict] = []
    skipped: list[str] = []
    for c in (cmds or []):
        if not isinstance(c, dict):
            continue
        try:
            n = register_dynamic_mcp_tool(c)
            if n:
                added.append(n)
            else:
                skipped.append(c.get("path") or "?")
        except Exception as e:
            failed.append({"path": c.get("path"), "error": str(e)})
            log.exception("[dynamic-mcp] 注册失败 path=%s", c.get("path"))
    return {"added": added, "failed": failed, "skipped": skipped}


async def apply_latest_from_db() -> dict[str, Any]:
    """启动时调用：拉 DB 最新一条 dynamic_config → apply_config。"""
    import db
    try:
        latest = await db.get_latest_dynamic_config()
    except Exception as e:
        log.warning("[dynamic-mcp] 拉最新配置失败（不阻断启动）: %s", e)
        return {"added": [], "failed": [], "skipped": []}
    cmds = (latest or {}).get("dynamic_commands") or []
    return apply_config(cmds)
