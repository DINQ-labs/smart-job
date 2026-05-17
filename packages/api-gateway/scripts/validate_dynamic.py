"""dynamic-commands/ yaml 校验器（Phase 1b）。

用法：
    python scripts/validate_dynamic.py [--root <dir>]

退出码：
    0  全部通过（可有 warning）
    1  存在 error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 静态命令 / 静态 chain：动态命令不能撞这些，因为扩展端 registry.js 会拒
# 这是从扩展源码 grep 出来的快照。如果以后改了，这个列表要同步。
_STATIC_EXT_PATHS: set[str] = {
    # boss 静态命令（job-seeker-ext/commands/jobs.js + candidates.js + session.js + misc.js）
    "boss/check_login", "boss/login", "boss/capture_qr", "boss/wait_for_login",
    "boss/init_session", "boss/get_user_info", "boss/get_session_status",
    "boss/get_tokens", "boss/logout",
    "boss/search_jobs", "boss/get_job_detail", "boss/start_chat",
    "boss/send_message", "boss/get_chat_history",
    "boss/get_recommend_jobs", "boss/get_job_card", "boss/get_job_history",
    "boss/get_friend_list", "boss/geek_filter_by_label", "boss/geek_get_boss_data",
    "boss/get_ws_endpoints", "boss/msg_history_pull", "boss/get_geek_job",
    "boss/get_resume_baseinfo", "boss/get_resume_expect", "boss/get_resume_status",
    "boss/get_deliver_list", "boss/get_interview_data",
    "boss/list_my_jobs", "boss/refresh_my_jobs",
    "boss/list_interacted_geeks", "boss/contact_list", "boss/view_geek_detail",
    "boss/search_candidates", "boss/get_candidate_detail", "boss/contact_candidate",
    "boss/geek_info", "boss/boss_enter", "boss/boss_chat_history",
    "boss/auto_suggest", "boss/rec_geek_list", "boss/mark_geek_interest",
    "boss/list_geek_interests", "boss/filter_label",
    "boss/resume_preview_check", "boss/resume_download",
    "boss/accept_exchange", "boss/recruiter_chat_list",
    "boss/get_quick_replies",
    "boss/chatted_jobs",
    # linkedin / indeed 静态命令名空间
    # 不在这里穷举，只做 boss/* 黑名单（站点有侧重）
}

_STATIC_MCP_NAMES: set[str] = {
    "boss_check_login", "boss_login", "boss_capture_qr", "boss_wait_for_login",
    "boss_init_session", "boss_search_jobs", "boss_get_job_detail",
    "boss_start_chat", "boss_send_message", "boss_get_chat_history",
    "boss_geek_filter_by_label", "boss_get_friend_list",
    "boss_geek_get_boss_data", "boss_recruiter_chat_list",
    "boss_filter_by_label", "boss_list_interacted_geeks",
    "boss_search_candidates", "boss_contact_candidate",
    "boss_list_cached_jobs", "boss_get_cached_job",
    "boss_list_cached_chats", "boss_list_cached_geeks",
    "boss_list_cached_recruiter_chats",
    # 不穷举；命中的会报 error，未命中且 lint 没拒就靠运行时拦
}

_BUILTIN_CHAIN_NAMESPACES: set[str] = {
    "jobs", "geeks", "linkedin_people", "linkedin_jobs", "indeed_jobs",
}

# 允许的 host（防止动态命令把 token 发去黑产 host）
_HOST_WHITELIST: set[str] = {
    "zhipin.com", "linkedin.com", "indeed.com",
}

_PATH_RE = re.compile(r"^(boss|linkedin|indeed)/[a-z][a-z0-9_]+$")
_MCP_NAME_RE = re.compile(r"^(boss|linkedin|indeed)_[a-z][a-z0-9_]+$")
_VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
_VALID_EXTRACTOR_TYPES = {"jsonpath", "jsonpath_list"}


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "LintResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def _err(res: LintResult, file: Path | str, msg: str) -> None:
    res.errors.append(f"[{file}] {msg}")


def _warn(res: LintResult, file: Path | str, msg: str) -> None:
    res.warnings.append(f"[{file}] {msg}")


def _validate_url_host(url: str) -> str | None:
    """返回错误描述或 None。允许相对路径 (/wapi/...) 和白名单 host。"""
    if not isinstance(url, str) or not url:
        return "url 必填且为字符串"
    if url.startswith("/"):
        return None
    # 绝对 URL：检查 host 在白名单
    m = re.match(r"^https?://([^/]+)/", url)
    if not m:
        return f"url 必须以 / 或 http(s):// 开头：{url!r}"
    host = m.group(1).lower().split(":")[0]
    for w in _HOST_WHITELIST:
        if host == w or host.endswith("." + w):
            return None
    return f"url host {host!r} 不在白名单 {sorted(_HOST_WHITELIST)}"


def _collect_template_vars(s: Any) -> set[str]:
    """从字符串模板 (`{{body.x}}` 等) 抽取所有 body.* 变量名。"""
    found: set[str] = set()
    if isinstance(s, str):
        for m in re.finditer(r"\{\{\s*([^}]+?)\s*\}\}", s):
            expr = m.group(1).strip()
            if expr.startswith("body."):
                found.add(expr.split(".", 1)[1].split(".")[0])
    elif isinstance(s, dict):
        for v in s.values():
            found |= _collect_template_vars(v)
    elif isinstance(s, list):
        for v in s:
            found |= _collect_template_vars(v)
    return found


def validate_command(cmd: dict, file: Path | str,
                     known_chain_namespaces: set[str]) -> LintResult:
    res = LintResult()

    # path
    path = cmd.get("path")
    if not isinstance(path, str) or not _PATH_RE.match(path):
        _err(res, file, f"path 必填且匹配 {_PATH_RE.pattern}: {path!r}")
        return res
    if path in _STATIC_EXT_PATHS:
        _err(res, file, f"path {path!r} 撞静态命令，扩展端 registry.js 会拒")

    # description
    desc = cmd.get("description")
    if not isinstance(desc, str) or len(desc.strip()) < 10:
        _err(res, file, "description 必填且 ≥10 字符")

    # mcp
    mcp = cmd.get("mcp", {})
    if mcp:
        if not isinstance(mcp, dict):
            _err(res, file, "mcp 必须是对象")
        else:
            mcp_name = mcp.get("name")
            if not isinstance(mcp_name, str) or not _MCP_NAME_RE.match(mcp_name):
                _err(res, file, f"mcp.name 必填且匹配 {_MCP_NAME_RE.pattern}: {mcp_name!r}")
            elif mcp_name in _STATIC_MCP_NAMES:
                _err(res, file, f"mcp.name {mcp_name!r} 撞静态 MCP tool（Phase 2 启用后会冲突）")
            mcp_params = mcp.get("params", [])
            if not isinstance(mcp_params, list):
                _err(res, file, "mcp.params 必须是数组")

    # requestBuilder
    rb = cmd.get("requestBuilder")
    if not isinstance(rb, dict):
        _err(res, file, "requestBuilder 必填且为对象")
    else:
        method = (rb.get("method") or "").upper()
        if method not in _VALID_METHODS:
            _err(res, file, f"requestBuilder.method 非法 {method!r}（允许 {sorted(_VALID_METHODS)}）")
        url_err = _validate_url_host(rb.get("url"))
        if url_err:
            _err(res, file, f"requestBuilder.{url_err}")
        ct = rb.get("contentType")
        if ct is not None and ct not in ("form", "json", "application/x-www-form-urlencoded", "application/json"):
            _warn(res, file, f"requestBuilder.contentType 非常见值 {ct!r}")

    # body 模板变量必须在 mcp.params 里声明（防 typo）
    if isinstance(rb, dict) and isinstance(mcp, dict):
        declared_params = {
            (p.get("name") if isinstance(p, dict) else None)
            for p in (mcp.get("params") or [])
        } - {None}
        used = _collect_template_vars(rb)
        missing = used - declared_params
        if missing:
            _warn(res, file,
                  f"requestBuilder body 引用了未声明的 params: {sorted(missing)}")

    # requires / produces chain ref
    for kind in ("requires", "produces"):
        v = cmd.get(kind)
        if v is None:
            continue
        items = v if isinstance(v, list) else [v]
        for item in items:
            if not isinstance(item, dict):
                _err(res, file, f"{kind} 必须是对象或对象数组")
                continue
            ns = item.get("chain")
            if not ns:
                _err(res, file, f"{kind}.chain 必填")
            elif ns not in known_chain_namespaces:
                _err(res, file, f"{kind}.chain {ns!r} 引用了未知链")
            stage = item.get("stage")
            if not stage or not isinstance(stage, str):
                _err(res, file, f"{kind}.stage 必填字符串")
            if kind == "produces":
                ext = item.get("extractor")
                if not isinstance(ext, dict):
                    _err(res, file, "produces.extractor 必填对象")
                else:
                    et = ext.get("type")
                    if et not in _VALID_EXTRACTOR_TYPES and not (
                        isinstance(et, str) and et.startswith("named:")
                    ):
                        _err(res, file, f"produces.extractor.type 非法 {et!r}")

    # metadata
    meta = cmd.get("metadata", {})
    if not isinstance(meta, dict) or not meta.get("owner"):
        _warn(res, file, "metadata.owner 缺失（运维归属不明）")
    return res


def validate_chain(chain_def: dict, file: Path | str) -> LintResult:
    res = LintResult()
    ns = chain_def.get("namespace")
    if not ns or not isinstance(ns, str):
        _err(res, file, "namespace 必填字符串")
        return res
    if ns in _BUILTIN_CHAIN_NAMESPACES:
        _err(res, file, f"namespace {ns!r} 撞内置链，扩展端 config-sync.js 会拒")
    entity = chain_def.get("entityKey")
    if not entity:
        _err(res, file, "entityKey 必填")
    stages = chain_def.get("stages")
    if not isinstance(stages, list) or not stages:
        _err(res, file, "stages 必填且为非空数组")
        return res
    stage_names: set[str] = set()
    for i, s in enumerate(stages):
        if not isinstance(s, dict):
            _err(res, file, f"stages[{i}] 必须是对象")
            continue
        n = s.get("name")
        if not n:
            _err(res, file, f"stages[{i}].name 必填")
        elif n in stage_names:
            _err(res, file, f"stages[{i}].name {n!r} 重复")
        else:
            stage_names.add(n)
        if not s.get("field"):
            _err(res, file, f"stages[{i}].field 必填")
        req = s.get("requires")
        if req and req not in stage_names:
            _err(res, file, f"stages[{i}].requires {req!r} 引用了后定义的 stage（顺序错）")
    return res


def load_yaml(path: Path) -> tuple[Any, str | None]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f), None
    except Exception as e:
        return None, str(e)


def collect_files(root: Path) -> tuple[list[Path], list[Path]]:
    """返回 (command_yamls, chain_yamls)。"""
    cmds, chains = [], []
    for site in ("boss", "linkedin", "indeed"):
        d = root / site
        if d.is_dir():
            cmds.extend(sorted(d.glob("*.yaml")))
            cmds.extend(sorted(d.glob("*.yml")))
    cd = root / "chains"
    if cd.is_dir():
        chains.extend(sorted(cd.glob("*.yaml")))
        chains.extend(sorted(cd.glob("*.yml")))
    return cmds, chains


def lint_root(root: Path) -> LintResult:
    res = LintResult()
    cmds_files, chains_files = collect_files(root)

    # 先 lint 链 + 收集 namespace
    known_ns: set[str] = set(_BUILTIN_CHAIN_NAMESPACES)
    for f in chains_files:
        data, err = load_yaml(f)
        if err:
            _err(res, f, f"yaml 解析失败: {err}")
            continue
        if not isinstance(data, dict):
            _err(res, f, "顶层必须是 mapping/dict")
            continue
        res.merge(validate_chain(data, f))
        ns = data.get("namespace")
        if isinstance(ns, str):
            known_ns.add(ns)

    # 然后 lint 命令，命令引用链时已知 namespace 集合
    seen_paths: dict[str, str] = {}        # path -> file
    seen_mcp: dict[str, str] = {}
    for f in cmds_files:
        data, err = load_yaml(f)
        if err:
            _err(res, f, f"yaml 解析失败: {err}")
            continue
        if not isinstance(data, dict):
            _err(res, f, "顶层必须是 mapping/dict")
            continue
        res.merge(validate_command(data, f, known_ns))
        # 跨文件唯一性
        p = data.get("path")
        if isinstance(p, str):
            if p in seen_paths:
                _err(res, f, f"path {p!r} 与 {seen_paths[p]} 重复")
            else:
                seen_paths[p] = str(f)
        mcp = data.get("mcp") or {}
        mname = mcp.get("name") if isinstance(mcp, dict) else None
        if isinstance(mname, str):
            if mname in seen_mcp:
                _err(res, f, f"mcp.name {mname!r} 与 {seen_mcp[mname]} 重复")
            else:
                seen_mcp[mname] = str(f)

    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dynamic-commands/ yaml lint")
    parser.add_argument("--root", default=None,
                        help="dynamic-commands/ 根目录；默认是脚本所在仓库的 dynamic-commands/")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent / "dynamic-commands"
    if not root.is_dir():
        print(f"ERROR: root 目录不存在: {root}", file=sys.stderr)
        return 1

    res = lint_root(root)

    if args.json:
        print(json.dumps({
            "ok": res.ok,
            "errors": res.errors,
            "warnings": res.warnings,
        }, ensure_ascii=False, indent=2))
    else:
        for w in res.warnings:
            print(f"⚠ WARN  {w}")
        for e in res.errors:
            print(f"✗ ERROR {e}")
        if res.ok:
            print(f"\n✓ OK — {len(res.warnings)} warning(s), 0 error(s)")
        else:
            print(f"\n✗ FAILED — {len(res.errors)} error(s), {len(res.warnings)} warning(s)")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
