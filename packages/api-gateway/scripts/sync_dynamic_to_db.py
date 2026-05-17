"""dynamic-commands/ → gateway 推送（Phase 1b）。

读取所有 yaml + chains，构建 config_update payload，
POST 到 gateway 的 /admin/extension/config/push。

Usage:
    python scripts/sync_dynamic_to_db.py \\
        --gateway https://testapi.dinq.me \\
        --token  $ADMIN_PASSWORD \\
        [--dry-run]               # 只打印 payload，不推送

退出码：
    0  推送成功
    1  lint 失败 / 网络失败 / gateway 拒绝
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_THIS = Path(__file__).resolve()
_GW_ROOT = _THIS.parent.parent

# 让 lint 复用这个脚本里的代码
sys.path.insert(0, str(_GW_ROOT))
from scripts.validate_dynamic import lint_root, collect_files


def _read_yaml(p: Path) -> dict:
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _command_for_push(cmd: dict) -> dict:
    """从 yaml 摘出 push payload 需要的字段。

    yaml 里有 metadata / mcp 等运维字段，扩展端不关心；只发协议必需的部分给扩展。
    """
    out = {
        "path": cmd["path"],
        "description": cmd.get("description") or "",
        "params": (cmd.get("mcp") or {}).get("params") or [],
        "requires": cmd.get("requires"),
        "produces": cmd.get("produces"),
        "requestBuilder": cmd["requestBuilder"],
    }
    return out


def build_payload(root: Path, version: str, notes: str = "",
                  source_ref: str = "") -> tuple[dict, list[Path]]:
    """从 yaml 目录构造 config_update payload。返回 (payload, source_files)。"""
    cmd_files, chain_files = collect_files(root)

    chains: dict[str, Any] = {}
    for f in chain_files:
        d = _read_yaml(f)
        ns = d.get("namespace")
        if not ns:
            raise ValueError(f"{f}: chains/*.yaml 缺 namespace")
        chains[ns] = d

    # 跳过已 promoted 的（promoted_at 非空）—— 它们已成为静态命令，不再推
    cmds: list[dict] = []
    skipped_promoted: list[str] = []
    sources: list[Path] = []
    for f in cmd_files:
        d = _read_yaml(f)
        meta = d.get("metadata") or {}
        if meta.get("promoted_at"):
            skipped_promoted.append(d.get("path") or str(f))
            continue
        if meta.get("retired_at"):
            skipped_promoted.append(d.get("path") or str(f))
            continue
        cmds.append(_command_for_push(d))
        sources.append(f)

    if skipped_promoted:
        print(f"[sync] 跳过已 promoted/retired ({len(skipped_promoted)}): "
              f"{', '.join(skipped_promoted)}", file=sys.stderr)

    payload = {
        "version": version,
        "chains": chains,
        "dynamic_commands": cmds,
        "notes": notes,
        "source_ref": source_ref,
    }
    return payload, sources


def _git_short_sha(repo_dir: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir, text=True,
        ).strip()
    except Exception:
        return ""


def _default_version() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="sync dynamic-commands/ → gateway")
    p.add_argument("--gateway", help="gateway URL（含 https）；可用 GATEWAY_URL env")
    p.add_argument("--token", help="ADMIN_PASSWORD；可用 ADMIN_PASSWORD env")
    p.add_argument("--root", default=str(_GW_ROOT / "dynamic-commands"),
                   help="dynamic-commands/ 目录，默认 sibling of scripts/")
    p.add_argument("--version", default=None,
                   help="config version；默认用 ISO 时间戳 + git short sha")
    p.add_argument("--notes", default="", help="notes 字段（说明本次推送原因）")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印 payload + lint 结果，不推送")
    p.add_argument("--skip-lint", action="store_true",
                   help="跳过 lint（不建议，仅排障）")
    args = p.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: root 目录不存在: {root}", file=sys.stderr)
        return 1

    # 1. lint
    if not args.skip_lint:
        res = lint_root(root)
        for w in res.warnings:
            print(f"⚠ WARN  {w}", file=sys.stderr)
        if not res.ok:
            for e in res.errors:
                print(f"✗ ERROR {e}", file=sys.stderr)
            print(f"\nlint 失败 ({len(res.errors)} 个 error)，拒绝推送", file=sys.stderr)
            return 1

    # 2. 构造 payload
    sha = _git_short_sha(_GW_ROOT.parent)
    version = args.version or f"{_default_version()}-{sha}" if sha else (args.version or _default_version())
    source_ref = sha or "manual"
    try:
        payload, sources = build_payload(root, version, notes=args.notes,
                                         source_ref=source_ref)
    except Exception as e:
        print(f"ERROR: 构造 payload 失败: {e}", file=sys.stderr)
        return 1

    print(f"[sync] version: {version}", file=sys.stderr)
    print(f"[sync] commands: {len(payload['dynamic_commands'])}", file=sys.stderr)
    print(f"[sync] chains:   {len(payload['chains'])}", file=sys.stderr)
    print(f"[sync] sources:  {len(sources)}", file=sys.stderr)

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # 3. 推送
    gw = args.gateway or os.environ.get("GATEWAY_URL")
    token = args.token or os.environ.get("ADMIN_PASSWORD")
    if not gw or not token:
        print("ERROR: --gateway/--token 必填（或设 GATEWAY_URL/ADMIN_PASSWORD env）",
              file=sys.stderr)
        return 1

    try:
        import urllib.request, urllib.error
    except Exception:
        print("ERROR: 需要 stdlib urllib", file=sys.stderr)
        return 1

    url = f"{gw.rstrip('/')}/admin/extension/config/push"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Admin-Password": token,            # 与 _auth_required 约定
            "Cookie": f"admin_token={token}",     # 如果 cookie 路径还在用
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            print(body)
            data = json.loads(body)
            return 0 if data.get("ok") else 1
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: 网络失败 {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
