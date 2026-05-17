#!/usr/bin/env python3
"""
boss_cli.py: 命令行工具，直接调用网关命令函数，无需 LLM 意图识别。

环境变量:
  BOSS_GATEWAY_URL   网关 HTTP 地址（默认 http://127.0.0.1:8767）
  BOSS_SESSION_ID    默认 session_id（可被 --session 覆盖）

使用示例:
  python boss_cli.py sessions
  python boss_cli.py --session <id> status
  python boss_cli.py --session <id> search "Python工程师" --city 101010100
  python boss_cli.py --session <id> detail <encrypt_job_id>
  python boss_cli.py --session <id> chat <encrypt_job_id>
  python boss_cli.py --session <id> send <encrypt_job_id> "你好，我对这个职位很感兴趣"
  python boss_cli.py --session <id> history <encrypt_job_id>
  python boss_cli.py --session <id> logout
  python boss_cli.py --session <id> tokens
  python boss_cli.py --session <id> quota
  python boss_cli.py --session <id> search-candidates "Python工程师"
  python boss_cli.py --session <id> candidate-detail <encrypt_uid>
  python boss_cli.py --session <id> contact-candidate <encrypt_uid>
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

GATEWAY_URL = os.environ.get("BOSS_GATEWAY_URL", "http://127.0.0.1:8767")


def call_cli(tool: str, params: dict) -> Any:
    """调用网关 POST /cli 端点。"""
    url = f"{GATEWAY_URL}/cli"
    data = json.dumps({"tool": tool, "params": params}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}: {body[:200]}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"连接失败: {e.reason}。请确认网关已启动: python server.py --http"}


def print_result(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="boss_cli",
        description="Boss直聘 网关命令行工具（直接调用，无需 LLM）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--session", "-s",
        default="",
        metavar="SESSION_ID",
        help="指定扩展 session_id（默认使用 BOSS_SESSION_ID 环境变量或自动选择）",
    )
    p.add_argument(
        "--gateway", "-g",
        default=GATEWAY_URL,
        metavar="URL",
        help=f"网关地址（默认 {GATEWAY_URL}，可用 BOSS_GATEWAY_URL 环境变量设置）",
    )

    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    # sessions
    sub.add_parser("sessions", help="列出所有扩展会话")

    # agents
    sub.add_parser("agents", help="列出所有 AI Agent 连接")

    # stats
    sub.add_parser("stats", help="查看今日统计（命令数/耗时/错误率）")

    # status
    sub.add_parser("status", help="检查登录状态")

    # login
    sub.add_parser("login", help="打开登录页并截取二维码（约 15 秒）")

    # logout
    sub.add_parser("logout", help="退出当前账号（清除 cookies）")

    # tokens
    sub.add_parser("tokens", help="查看完整令牌链（调试用）")

    # set-proxy
    sp = sub.add_parser("set-proxy", help="设置代理（留空则清除，直连）")
    sp.add_argument("proxy_url", nargs="?", default="",
                    help="代理地址，如 http://1.2.3.4:8080 或 socks5://1.2.3.4:1080")

    # search
    sp = sub.add_parser("search", help="搜索职位")
    sp.add_argument("keyword", help="搜索关键词，如 \"Python工程师\"")
    sp.add_argument("--city", type=int, default=101010100,
                    metavar="CODE",
                    help="城市代码（默认北京101010100，上海101020100，深圳101280600）")
    sp.add_argument("--page", type=int, default=1, help="页码（默认1）")

    # detail
    sp = sub.add_parser("detail", help="获取职位详情（需先 search）")
    sp.add_argument("encrypt_job_id", help="职位加密ID（来自 search 结果的 encryptJobId）")

    # chat
    sp = sub.add_parser("chat", help="向 Boss 发起聊天（打招呼）")
    sp.add_argument("encrypt_job_id", help="职位加密ID")

    # send
    sp = sub.add_parser("send", help="向 Boss 发送消息（需先 chat）")
    sp.add_argument("encrypt_job_id", help="职位加密ID")
    sp.add_argument("message", help="要发送的消息内容")

    # history
    sp = sub.add_parser("history", help="拉取聊天历史（需先 chat）")
    sp.add_argument("encrypt_job_id", help="职位加密ID")
    sp.add_argument("--max-msg-id", default="", help="分页游标（首次为空）")

    # quota
    sub.add_parser("quota", help="查看今日配额使用情况（投递/沟通次数）")

    # search-candidates
    sp = sub.add_parser("search-candidates", help="搜索候选人（招聘方功能）")
    sp.add_argument("keyword", help="搜索关键词，如 \"Python工程师\"")
    sp.add_argument("--city", type=int, default=101010100, metavar="CODE",
                    help="城市代码（默认北京101010100）")
    sp.add_argument("--page", type=int, default=1, help="页码（默认1）")

    # candidate-detail
    sp = sub.add_parser("candidate-detail", help="获取候选人详情（招聘方功能）")
    sp.add_argument("encrypt_uid", help="候选人加密用户ID（来自搜索结果）")

    # contact-candidate
    sp = sub.add_parser("contact-candidate", help="主动沟通候选人（招聘方功能，受每日配额限制）")
    sp.add_argument("encrypt_uid", help="候选人加密用户ID")
    sp.add_argument("--job-id", default="", help="关联职位ID（可选）")

    return p


def main() -> int:
    p = build_parser()
    args = p.parse_args()

    # 全局设置
    global GATEWAY_URL
    GATEWAY_URL = args.gateway

    if not args.cmd:
        p.print_help()
        return 1

    # session_id：--session > 环境变量
    session_id = args.session or os.environ.get("BOSS_SESSION_ID", "")

    def base_params() -> dict:
        return {"session_id": session_id} if session_id else {}

    if args.cmd == "sessions":
        result = call_cli("boss_list_sessions", {})
        print_result(result)

    elif args.cmd == "agents":
        result = call_cli("boss_list_agents", {})
        print_result(result)

    elif args.cmd == "stats":
        # 直接请求 /admin/stats
        url = f"{GATEWAY_URL}/admin/stats"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                print_result(json.loads(resp.read()))
        except Exception as e:
            print_result({"ok": False, "error": str(e)})

    elif args.cmd == "status":
        result = call_cli("boss_check_login", base_params())
        print_result(result)

    elif args.cmd == "login":
        result = call_cli("boss_login", base_params())
        print_result(result)
        if result.get("ok") and result.get("result", {}).get("login_url"):
            url = result["result"]["login_url"]
            print(f"\n请在浏览器打开扫码: {url}", file=sys.stderr)

    elif args.cmd == "logout":
        result = call_cli("boss_logout", base_params())
        print_result(result)

    elif args.cmd == "tokens":
        result = call_cli("boss_get_tokens", base_params())
        print_result(result)

    elif args.cmd == "set-proxy":
        params = {**base_params(), "proxy_url": args.proxy_url}
        result = call_cli("boss_set_proxy", params)
        print_result(result)

    elif args.cmd == "search":
        params = {**base_params(), "keyword": args.keyword, "city": args.city, "page": args.page}
        result = call_cli("boss_search_jobs", params)
        print_result(result)

    elif args.cmd == "detail":
        params = {**base_params(), "encrypt_job_id": args.encrypt_job_id}
        result = call_cli("boss_get_job_detail", params)
        print_result(result)

    elif args.cmd == "chat":
        params = {**base_params(), "encrypt_job_id": args.encrypt_job_id}
        result = call_cli("boss_start_chat", params)
        print_result(result)

    elif args.cmd == "send":
        params = {**base_params(), "encrypt_job_id": args.encrypt_job_id, "content": args.message}
        result = call_cli("boss_send_message", params)
        print_result(result)

    elif args.cmd == "history":
        params = {**base_params(), "encrypt_job_id": args.encrypt_job_id,
                  "max_msg_id": args.max_msg_id}
        result = call_cli("boss_get_chat_history", params)
        print_result(result)

    elif args.cmd == "quota":
        result = call_cli("boss_get_quota_status", base_params())
        print_result(result)

    elif args.cmd == "search-candidates":
        params = {**base_params(), "keyword": args.keyword, "city": args.city, "page": args.page}
        result = call_cli("boss_search_candidates", params)
        print_result(result)

    elif args.cmd == "candidate-detail":
        params = {**base_params(), "encrypt_uid": args.encrypt_uid}
        result = call_cli("boss_get_candidate_detail", params)
        print_result(result)

    elif args.cmd == "contact-candidate":
        params = {**base_params(), "encrypt_uid": args.encrypt_uid, "job_id": args.job_id}
        result = call_cli("boss_contact_candidate", params)
        print_result(result)
        # 配额超限时给出友好提示
        if isinstance(result, dict):
            inner = result.get("result", {}) or {}
            if inner.get("quota_exceeded"):
                print(f"\n[配额提示] {inner.get('message', '')}", file=sys.stderr)

    else:
        p.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
