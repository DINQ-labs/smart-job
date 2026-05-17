"""
risk_detector.py — 从工具响应识别风控信号 (Phase A)

按优先级匹配:
  1. structured_data 字段(如 boss_code=37, region_blocked=True)
  2. error_text 关键词(中英双语,大小写不敏感)
  3. tool_name 上下文(同样的"403"在 boss vs linkedin 含义不同)

返回:
  RISK_SIGNALS 中的 signal_id 字符串,如 'boss:code_37'
  None 表示工具结果正常或无法识别(走通用错误流)
"""
from __future__ import annotations


# ── 关键词映射(按平台分组,case-insensitive 由 detect_risk_signal 处理) ─
_BOSS_KEYWORDS = [
    # (substring, signal_id)
    ("环境异常", "boss:code_37"),
    ("code 37", "boss:code_37"),
    ("code=37", "boss:code_37"),
    ("code 24", "boss:rate_limit_24"),
    ("code=24", "boss:rate_limit_24"),
    ("code 415", "boss:rate_limit_415"),
    ("code=415", "boss:rate_limit_415"),
    ("操作过于频繁", "boss:rate_limit_415"),
    ("登录失效", "boss:logged_out"),
    ("请重新登录", "boss:logged_out"),
    ("not logged in", "boss:logged_out"),
    ("未登录", "boss:logged_out"),
    ("滑块", "boss:captcha"),
    ("captcha", "boss:captcha"),
    ("verify", "boss:captcha"),
    ("配额", "boss:quota_exceeded"),
    ("quota_exceeded", "boss:quota_exceeded"),
    ("已拉黑", "boss:candidate_blocked"),
    ("blocked", "boss:candidate_blocked"),
]

_LINKEDIN_KEYWORDS = [
    ("region_block", "linkedin:region_block"),
    ("region blocked", "linkedin:region_block"),
    ("地区受限", "linkedin:region_block"),
    ("trust_intervention", "linkedin:trust_intervention"),
    ("verify your identity", "linkedin:trust_intervention"),
    ("身份验证", "linkedin:trust_intervention"),
    ("inmail quota", "linkedin:inmail_quota"),
    ("inmail 已用完", "linkedin:inmail_quota"),
    ("not logged in", "linkedin:logged_out"),
    ("未登录", "linkedin:logged_out"),
    ("status 429", "linkedin:429"),
    ("rate limit", "linkedin:429"),
]

_INDEED_KEYWORDS = [
    ("cloudflare", "indeed:cloudflare_challenge"),
    ("403 forbidden", "indeed:cloudflare_503"),
    ("503", "indeed:cloudflare_503"),
    ("attention required", "indeed:cloudflare_challenge"),
    ("人机验证", "indeed:cloudflare_challenge"),
    ("not logged in", "indeed:logged_out"),
    ("未登录", "indeed:logged_out"),
]

_GENERIC_KEYWORDS = [
    ("kicked", "kicked"),
    ("session disconnected", "ext_disconnected"),
    ("ext disconnected", "ext_disconnected"),
    ("不存在或已断开", "ext_disconnected"),
    ("扩展未连接", "ext_disconnected"),
    ("not connected", "ext_disconnected"),
]


# Dispatch by platform — 加新平台只在这两个 dict 加一条 entry,无需改 detect_risk_signal
# (替代旧 if-elif 链,避免静默"未知平台"漏过任何 signal)
_PLATFORM_KEYWORDS: dict[str, list[tuple[str, str]]] = {
    "boss":     _BOSS_KEYWORDS,
    "linkedin": _LINKEDIN_KEYWORDS,
    "indeed":   _INDEED_KEYWORDS,
}

_LOGGED_OUT_SIGNAL_BY_PLATFORM: dict[str, str] = {
    "boss":     "boss:logged_out",
    "linkedin": "linkedin:logged_out",
    "indeed":   "indeed:logged_out",
}

# Tool-name → platform 映射 — 用 prefix 反推。一开始就要识别新平台时,在
# _PLATFORM_KEYWORDS 加 entry 后,这个函数会自动 pick up(无需改逻辑)。
def _platform_of_tool(tool_name: str) -> str:
    """boss_search_jobs → 'boss',linkedin_xxx → 'linkedin' 等。"""
    if not tool_name:
        return ""
    n = tool_name.lower()
    for plat in _PLATFORM_KEYWORDS:
        if n.startswith(f"{plat}_"):
            return plat
    return ""


def _match_keywords(text_lower: str, table: list[tuple[str, str]]) -> str | None:
    """逐条 substring 检查,第一个 hit 返回 signal_id。"""
    for kw, sig in table:
        if kw.lower() in text_lower:
            return sig
    return None


def detect_risk_signal(
    tool_name: str = "",
    structured_data: dict | None = None,
    error_text: str = "",
) -> str | None:
    """从工具响应判断是否触发风控,返回 signal_id 或 None。

    优先级:
      1. structured_data 强信号(明确字段)
      2. error_text 关键词(按平台优先,再 generic)
    """
    # ─ 1. structured_data 强信号 ─
    if isinstance(structured_data, dict):
        # Boss 平台
        bc = structured_data.get("boss_code")
        if bc == 37:
            return "boss:code_37"
        if bc == 24:
            return "boss:rate_limit_24"
        if bc == 415:
            return "boss:rate_limit_415"
        # 通用 quota 字段
        if structured_data.get("quota_exceeded"):
            return "boss:quota_exceeded"
        # LinkedIn region/trust
        if structured_data.get("region_blocked"):
            return "linkedin:region_block"
        if structured_data.get("trust_intervention"):
            return "linkedin:trust_intervention"
        # Boss 候选人拉黑(boss_check_reply_block / boss_exchange_test 返回)
        if structured_data.get("blocked") is True:
            # alert_type 是 exchange 预检的,reply_block 是回复拦截
            if "alert_type" in structured_data or "exchange" in (tool_name or "").lower():
                return "boss:exchange_blocked"
            return "boss:candidate_blocked"
        # logged_in=false 是登录态丢失 — 用 dispatch 替代 if-elif,加新平台只改 dict
        if structured_data.get("logged_in") is False:
            platform = _platform_of_tool(tool_name)
            sig_id = _LOGGED_OUT_SIGNAL_BY_PLATFORM.get(platform)
            if sig_id:
                return sig_id

    # ─ 2. error_text 关键词 ─
    if not error_text:
        return None
    txt = str(error_text).lower()
    platform = _platform_of_tool(tool_name)

    # 平台关键词表 — dispatch by manifest;加新平台只在 _PLATFORM_KEYWORDS 加一条
    table = _PLATFORM_KEYWORDS.get(platform)
    if table:
        sig = _match_keywords(txt, table)
        if sig:
            return sig

    # 跨平台 generic(扩展断连等)
    sig = _match_keywords(txt, _GENERIC_KEYWORDS)
    if sig:
        return sig

    # 平台未知,挨所有表 best-effort 试一下
    if not platform:
        for tbl in _PLATFORM_KEYWORDS.values():
            sig = _match_keywords(txt, tbl)
            if sig:
                return sig

    return None
