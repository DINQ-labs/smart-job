"""dynamic command → static command 升级脚本（Phase 2）。

功能：
  - 读 dynamic-commands/<site>/<cmd>.yaml
  - 启发式判断哪些命令该 promote（运行天数、调用次数、错误率）
  - 生成三段 stub 代码：
    1. job-seeker-ext/commands/<site>.js 的 defineCommand
    2. job-api-gateway/commands.py 的 cmd_xxx 函数
    3. job-api-gateway/mcp_tools_<site>.py 的 @mcp.tool 包装
  - 标记 yaml 的 metadata.promoted_at 字段（--apply）

Usage:
    # dry-run：扫描所有 yaml + 打印 ready 列表
    python scripts/promote_to_static.py

    # 接调用统计（Phase 2.5：暂不实现，占位）
    python scripts/promote_to_static.py --gateway https://... --token $PASS

    # 只对单条命令生成 stub（不评估稳定性）
    python scripts/promote_to_static.py --codegen boss/test_ping
    python scripts/promote_to_static.py --codegen boss/test_ping --write /tmp/promote

    # 改完代码 + 部署后：标 promoted_at（下次 sync 自动从 push payload 删）
    python scripts/promote_to_static.py --apply boss/test_ping
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

_THIS = Path(__file__).resolve()
_GW_ROOT = _THIS.parent.parent

# Phase 1b 启发式（保守值；上 prod 后视实际调整）
_MIN_DAYS_STABLE = 30
_MIN_CALLS = 100
_MAX_ERROR_RATE = 0.02     # 2% 错误率算稳定


def _parse_iso_date(value: Any) -> date | None:
    """yaml 把 `2026-04-26` 自动解析成 date 对象；也兼容字符串。"""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.split("T")[0])
    except Exception:
        return None


def _days_since(value: Any) -> int | None:
    d = _parse_iso_date(value)
    if not d:
        return None
    return (date.today() - d).days


def _read_yaml(p: Path) -> dict:
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(p: Path, data: dict) -> None:
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _list_active_yamls(root: Path) -> list[Path]:
    out: list[Path] = []
    for site in ("boss", "linkedin", "indeed"):
        d = root / site
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            data = _read_yaml(f)
            meta = data.get("metadata") or {}
            if meta.get("promoted_at") or meta.get("retired_at"):
                continue
            out.append(f)
    return out


def _fetch_command_stats(gateway: str, token: str, path: str) -> dict:
    """从 gateway 拉单条命令的调用统计。Phase 1b 阶段直接读 command_log。

    Phase 2 计划：admin endpoint /admin/command-stats?path=...
    暂时占位返回 None，让 promote 走"凭年龄"判断，不阻断流程。
    """
    return {}  # TODO Phase 2


def _evaluate(yaml_path: Path, stats: dict) -> tuple[bool, list[str]]:
    """返回 (该升, 评估说明列表)。"""
    data = _read_yaml(yaml_path)
    meta = data.get("metadata") or {}
    notes: list[str] = []

    days = _days_since(meta.get("added_at"))
    if days is None:
        notes.append("⚠ metadata.added_at 缺失，无法评估天数 — 拒绝 promote")
        return False, notes
    notes.append(f"运行 {days} 天")
    if days < _MIN_DAYS_STABLE:
        notes.append(f"  → 不足 {_MIN_DAYS_STABLE} 天，建议继续 dynamic 跑一段")
        return False, notes

    if stats:
        calls = stats.get("calls", 0)
        errors = stats.get("errors", 0)
        rate = errors / calls if calls else 0
        notes.append(f"调用 {calls} 次（错误率 {rate*100:.2f}%）")
        if calls < _MIN_CALLS:
            notes.append(f"  → 不足 {_MIN_CALLS} 次调用，样本太少")
            return False, notes
        if rate > _MAX_ERROR_RATE:
            notes.append(f"  → 错误率 > {_MAX_ERROR_RATE*100}%，先排障")
            return False, notes
    else:
        notes.append("  → 无调用统计（Phase 2 接 admin stats endpoint），仅按天数判断")

    return True, notes


def _cmd_fn_name(path: str) -> str:
    return "cmd_" + path.replace("/", "_").replace("-", "_")


def _site_from_path(path: str) -> str:
    """`boss/foo` → 'boss'，未知前缀回 'boss'。"""
    head = (path or "").split("/", 1)[0]
    return head if head in ("boss", "linkedin", "indeed") else "boss"


_PARAM_PY_TYPE = {
    "str": "str", "string": "str",
    "int": "int", "integer": "int",
    "bool": "bool", "boolean": "bool",
    "float": "float", "number": "float",
}
_PARAM_DEFAULT_REPR = {
    "str": '""', "int": "0", "bool": "False", "float": "0.0",
}


def _py_param_signature(params: list[dict]) -> str:
    """给 cmd_fn / mcp tool 生成 'k1: str = "", k2: int = 0' 这样的片段。"""
    parts: list[str] = []
    for p in params or []:
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        if not name:
            continue
        py_type = _PARAM_PY_TYPE.get((p.get("type") or "str").lower(), "str")
        if p.get("required"):
            parts.append(f"{name}: {py_type}")
        else:
            default = p.get("default")
            if default is None:
                default_repr = _PARAM_DEFAULT_REPR.get(py_type, '""')
            else:
                default_repr = repr(default)
            parts.append(f"{name}: {py_type} = {default_repr}")
    return ", ".join(parts)


def _gen_ext_handler(data: dict) -> str:
    path = data["path"]
    desc_first = (data.get("description") or "").splitlines()[0]
    rb = data.get("requestBuilder") or {}
    method = (rb.get("method") or "GET").upper()
    url = rb.get("url") or ""
    body_tpl = rb.get("body")
    content_type = rb.get("contentType") or ""

    body_block = ""
    if body_tpl and method != "GET":
        body_json = json.dumps(body_tpl, ensure_ascii=False, indent=2)
        if content_type == "form":
            body_block = (
                f"    const params = {body_json};\n"
                f"    const resolved = renderTemplateObj(params, body);\n"
                f"    const requestBody = new URLSearchParams(resolved).toString();\n"
            )
        else:
            body_block = (
                f"    const params = {body_json};\n"
                f"    const resolved = renderTemplateObj(params, body);\n"
                f"    const requestBody = JSON.stringify(resolved);\n"
            )
    headers = ""
    if content_type == "form":
        headers = "      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },\n"
    elif body_tpl:
        headers = "      headers: { 'Content-Type': 'application/json; charset=UTF-8' },\n"

    return (
        f"// 由 promote_to_static.py 从 dynamic-commands/{path}.yaml 生成\n"
        f"// TODO: review + 移到对应站点的 commands/<site>.js / commands/<group>.js\n"
        f"defineCommand({{\n"
        f"  path: {json.dumps(path)},\n"
        f"  description: {json.dumps(desc_first, ensure_ascii=False)},\n"
        f"  handler: async (body = {{}}) => {{\n"
        f"{body_block}"
        f"    const raw = await executeBossApi({json.dumps(url)}, {{\n"
        f"      method: {json.dumps(method)},\n"
        f"{headers}"
        f"      body: {'requestBody' if body_tpl and method != 'GET' else 'undefined'},\n"
        f"    }});\n"
        f"    return extOk({{ raw }});\n"
        f"  }},\n"
        f"}});\n"
    )


def _gen_cmd_fn(data: dict) -> str:
    path = data["path"]
    fn = _cmd_fn_name(path)
    rb = data.get("requestBuilder") or {}
    method = (rb.get("method") or "GET").upper()
    mcp_block = data.get("mcp") or {}
    mcp_name = mcp_block.get("name") or fn
    params = mcp_block.get("params") or []
    sig = _py_param_signature(params)
    sig_full = (sig + ", " if sig else "") + 'session_id: str | None = None, agent_id: str = ""'
    body_keys = ", ".join(
        f'"{p["name"]}": {p["name"]}'
        for p in params
        if isinstance(p, dict) and p.get("name")
    )
    body_dict = "{" + body_keys + "}" if body_keys else "{}"
    desc = (data.get("description") or "").splitlines()[0] or path

    return (
        f"async def {fn}(\n"
        f"    {sig_full},\n"
        f") -> dict:\n"
        f"    \"\"\"{desc}\n\n"
        f"    由 promote_to_static.py 从 dynamic-commands/{path}.yaml 生成。\n"
        f"    \"\"\"\n"
        f"    entry = session_store.resolve_session(session_id)\n"
        f"    await entry.rate_limiter.wait(\"default\")\n"
        f"    result = await send_command_to(\n"
        f"        entry.session_id, {json.dumps(method)}, {json.dumps(path)},\n"
        f"        {body_dict},\n"
        f"        timeout_ms=20000, tool_name={json.dumps(mcp_name)}, agent_id=agent_id,\n"
        f"    )\n"
        f"    return _unwrap(result)\n"
    )


def _gen_mcp_tool(data: dict) -> str:
    path = data["path"]
    cmd_fn = _cmd_fn_name(path)
    mcp_block = data.get("mcp") or {}
    mcp_name = mcp_block.get("name") or cmd_fn
    desc = (data.get("description") or "").splitlines()[0] or path
    params = mcp_block.get("params") or []
    sig = _py_param_signature(params)
    sig_full = 'ctx: Context, session_id: str = "", app_user_id: str = ""' + (", " + sig if sig else "")
    pass_kwargs = ", ".join(
        f"{p['name']}={p['name']}"
        for p in params
        if isinstance(p, dict) and p.get("name")
    )
    cmd_kwargs_block = (", " + pass_kwargs) if pass_kwargs else ""

    return (
        f"@mcp.tool()\n"
        f"async def {mcp_name}({sig_full}) -> str:\n"
        f"    \"\"\"{desc}\"\"\"\n"
        f"    return await _run_boss_tool(\n"
        f"        ctx, session_id, app_user_id, {cmd_fn}{cmd_kwargs_block},\n"
        f"    )\n"
    )


def _print_pr_description(data: dict) -> None:
    path = data.get("path") or "?"
    mcp_name = (data.get("mcp") or {}).get("name") or "?"
    print("─" * 70)
    print(f"# Promote `{path}` → static")
    print(f"**MCP tool name:** `{mcp_name}`")
    print()
    print("## 1. job-seeker-ext/commands/<site>.js  ── defineCommand stub")
    print("```js")
    print(_gen_ext_handler(data), end="")
    print("```")
    print()
    print("## 2. job-api-gateway/commands.py  ── 静态命令函数")
    print("```python")
    print(_gen_cmd_fn(data), end="")
    print("```")
    print()
    print(f"## 3. job-api-gateway/mcp_tools_{_site_from_path(path)}.py  ── @mcp.tool")
    print("```python")
    print(_gen_mcp_tool(data), end="")
    print("```")
    print()
    print("## 完成后")
    print(f"- `python scripts/promote_to_static.py --apply {path}` 标记 `metadata.promoted_at`")
    print(f"- 重新 `npm run build` 扩展 + 部署网关")
    print(f"- 下次 sync_dynamic_to_db.py 跑时这条命令自动从 push payload 里消失")
    print("─" * 70)


def _write_patches(data: dict, out_dir: Path) -> list[Path]:
    """把三段 stub 写到 out_dir/<path-slug>/ 下。返回写入文件列表。"""
    path = data["path"]
    slug = path.replace("/", "_")
    site = _site_from_path(path)
    target = out_dir / slug
    target.mkdir(parents=True, exist_ok=True)
    files: list[tuple[Path, str]] = [
        (target / f"01_ext_command_{site}.js", _gen_ext_handler(data)),
        (target / "02_commands_py_fn.py",       _gen_cmd_fn(data)),
        (target / f"03_mcp_tool_{site}.py",    _gen_mcp_tool(data)),
    ]
    written: list[Path] = []
    for fp, content in files:
        fp.write_text(content, encoding="utf-8")
        written.append(fp)
    return written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="dynamic → static promote helper")
    p.add_argument("--root", default=str(_GW_ROOT / "dynamic-commands"))
    p.add_argument("--gateway", help="可选：拉调用统计的 gateway URL")
    p.add_argument("--token", help="ADMIN_PASSWORD")
    p.add_argument("--apply", metavar="PATH",
                   help="把 path 对应 yaml 的 metadata.promoted_at 标为今天")
    p.add_argument("--codegen", metavar="PATH",
                   help="只对该 path 跑 codegen，不评估稳定性")
    p.add_argument("--write", metavar="DIR",
                   help="把生成的三段 stub 写到 DIR/<slug>/ 下；缺省则只 stdout")
    args = p.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: root 目录不存在: {root}", file=sys.stderr)
        return 1

    if args.apply:
        target_path = args.apply
        for f in _list_active_yamls(root):
            data = _read_yaml(f)
            if data.get("path") == target_path:
                meta = data.setdefault("metadata", {})
                meta["promoted_at"] = date.today().isoformat()
                _write_yaml(f, data)
                print(f"✓ 标记 promoted_at = {meta['promoted_at']}: {f}")
                return 0
        print(f"ERROR: 找不到 path={target_path} 的 yaml", file=sys.stderr)
        return 1

    if args.codegen:
        target_path = args.codegen
        # codegen 也允许已 promoted 的 yaml（没坏处，可重新生成 stub）
        all_files = []
        for site in ("boss", "linkedin", "indeed"):
            d = root / site
            if d.is_dir():
                all_files.extend(sorted(d.glob("*.yaml")))
        for f in all_files:
            data = _read_yaml(f)
            if data.get("path") != target_path:
                continue
            _print_pr_description(data)
            if args.write:
                out = Path(args.write).expanduser().resolve()
                written = _write_patches(data, out)
                print(f"\n✓ 已写入 {len(written)} 个 stub 到 {out}/{target_path.replace('/', '_')}/")
                for w in written:
                    print(f"   - {w}")
            return 0
        print(f"ERROR: 找不到 path={target_path} 的 yaml", file=sys.stderr)
        return 1

    # dry-run：扫描 + 评估 + 打印建议
    candidates: list[tuple[Path, dict, list[str]]] = []
    for f in _list_active_yamls(root):
        data = _read_yaml(f)
        path = data.get("path") or "?"
        stats = {}
        if args.gateway and args.token:
            stats = _fetch_command_stats(args.gateway, args.token, path)
        ok, notes = _evaluate(f, stats)
        marker = "✓ READY" if ok else "  (skip)"
        print(f"{marker}  {path:<40} ({f.name})")
        for n in notes:
            print(f"           {n}")
        if ok:
            candidates.append((f, data, notes))

    if not candidates:
        print("\n没有符合 promote 条件的命令")
        return 0

    print(f"\n=== {len(candidates)} 个候选 promote ===\n")
    for _, data, _ in candidates:
        _print_pr_description(data)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
