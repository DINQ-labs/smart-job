"""
risk_signals.py — 长任务风控信号注册 + 处置策略 (Phase A)

跑 batch 任务时 Boss/LinkedIn/Indeed 的风控信号要被识别 + 按策略处置:

| action | 含义 | TaskRunner 行为 |
|---|---|---|
| auto_retry  | 平台抽风性限速,等会儿就好 | sleep wait_sec → 重试同一 item;最多 max_retries 次 |
| user_action | 需要用户在网页人工操作    | 暂停任务 + emit task_paused_user_action SSE → 等用户点"继续" |
| skip_item   | 该 item 不能做但其它能做  | 跳过当前 item,记录 reason,继续下一个 |
| abort       | 致命,整任务不能继续      | 任务标记 failed |

每个 signal 还带:
  guide:    用户介入时显给用户的中文文案
  open_url: paused_user_action 时引导用户打开的 URL (一键跳)
  reason:   skip_item 时记录在 task.skipped 里给用户看为啥跳
"""
from __future__ import annotations

# ── 信号 → 处置策略 ──────────────────────────────────────────────────────────
RISK_SIGNALS: dict[str, dict] = {

    # ────────────────────────────────────────────────────────────
    # auto_retry: 平台抽风,等一会儿就好
    # ────────────────────────────────────────────────────────────

    "boss:code_37": {
        "action": "auto_retry",
        "wait_sec": 30,
        "max_retries": 2,
        "label": "Boss 检测到环境异常",
    },
    "boss:rate_limit_415": {
        "action": "auto_retry",
        "wait_sec": 60,
        "max_retries": 2,
        "label": "Boss 频次限制(415)",
    },
    "boss:rate_limit_24": {
        "action": "auto_retry",
        "wait_sec": 60,
        "max_retries": 2,
        "label": "Boss 频次限制(24)",
    },
    "linkedin:429": {
        "action": "auto_retry",
        "wait_sec": 90,
        "max_retries": 1,
        "label": "LinkedIn 频次限制",
    },
    "indeed:cloudflare_503": {
        "action": "auto_retry",
        "wait_sec": 120,
        "max_retries": 1,
        "label": "Indeed 网关临时拥塞",
    },

    # ────────────────────────────────────────────────────────────
    # user_action: 需要用户在网页上人工操作
    # ────────────────────────────────────────────────────────────

    "boss:captcha": {
        "action": "user_action",
        "guide": "Boss 检测到滑块验证。请打开 zhipin.com 完成滑块,然后回来点「我已完成,继续」。",
        "open_url": "https://www.zhipin.com/",
        "label": "需要完成 Boss 滑块验证",
    },
    "boss:logged_out": {
        "action": "user_action",
        "guide": "Boss 登录失效,需要重新扫码登录。请打开登录页扫码后回来点继续。",
        "open_url": "https://www.zhipin.com/web/user/?ka=header-login",
        "label": "Boss 登录失效",
    },
    "linkedin:region_block": {
        "action": "user_action",
        "guide": "LinkedIn 在当前网络不可访问。请切到境外网络(VPN/代理)后回来点继续。",
        "open_url": "",
        "label": "需要切到境外网络",
    },
    "linkedin:trust_intervention": {
        "action": "user_action",
        "guide": "LinkedIn 要求验证你的身份。请到 linkedin.com 完成验证后回来点继续。",
        "open_url": "https://www.linkedin.com/",
        "label": "需要在 LinkedIn 验证身份",
    },
    "linkedin:logged_out": {
        "action": "user_action",
        "guide": "LinkedIn 登录失效,请重新登录后回来点继续。",
        "open_url": "https://www.linkedin.com/login",
        "label": "LinkedIn 登录失效",
    },
    "indeed:cloudflare_challenge": {
        "action": "user_action",
        "guide": "Indeed 要求人机验证。请打开 indeed.com 完成验证后回来点继续。",
        "open_url": "https://www.indeed.com/",
        "label": "需要完成 Indeed 人机验证",
    },
    "indeed:logged_out": {
        "action": "user_action",
        "guide": "Indeed 登录失效,请重新登录后回来点继续。",
        "open_url": "https://www.indeed.com/account/login",
        "label": "Indeed 登录失效",
    },

    # ────────────────────────────────────────────────────────────
    # skip_item: 该 item 不能做但其它能做
    # ────────────────────────────────────────────────────────────

    "boss:quota_exceeded": {
        "action": "skip_item",
        "reason": "今日配额已满",
        "label": "Boss 今日配额已满",
    },
    "linkedin:inmail_quota": {
        "action": "skip_item",
        "reason": "LinkedIn InMail 已耗尽",
        "label": "LinkedIn InMail 配额已满",
    },
    "boss:candidate_blocked": {
        "action": "skip_item",
        "reason": "候选人已拉黑/已联系",
        "label": "候选人不可达",
    },
    "boss:exchange_blocked": {
        "action": "skip_item",
        "reason": "Boss 交换简历预检拒绝(alert_type)",
        "label": "Boss 交换简历预检失败",
    },

    # ────────────────────────────────────────────────────────────
    # abort: 致命,整任务停
    # ────────────────────────────────────────────────────────────

    "kicked": {
        "action": "abort",
        "reason": "扩展会话被踢(同账号别处登录)",
        "label": "扩展被踢下线",
    },
    "ext_disconnected": {
        "action": "abort",
        "reason": "扩展未连接控制台",
        "label": "扩展未连接",
    },

    # ────────────────────────────────────────────────────────────
    # 兜底:detect_risk_signal 没识别出来 + 工具异常时用此信号
    # 给 1 次重试机会(应对 transient 网络抖动),仍失败再 skip
    # 不能用 abort 否则单次 timeout 就炸整个 task
    # ────────────────────────────────────────────────────────────
    "generic:unknown": {
        "action": "auto_retry",
        "wait_sec": 5,
        "max_retries": 1,
        "label": "未知错误(兜底重试)",
    },
}


class RiskControlSignal(Exception):
    """工具调用检测到风控信号时抛出。

    TaskRunner 会按 RISK_SIGNALS[signal_id]['action'] 处置;
    LLM 路径(非 task)会被 agent_loop 截获 → 转成 action_buttons 给用户提示。

    Attributes:
        signal_id:     RISK_SIGNALS 的 key,如 'boss:code_37'
        original_text: 原始错误文本(便于日志 + LLM 兜底解释)
        platform:      'boss' | 'linkedin' | 'indeed' | '' (由 caller 设置或从 signal_id prefix 推断)
    """

    def __init__(self, signal_id: str, original_text: str = "", platform: str = ""):
        if signal_id not in RISK_SIGNALS:
            # 未知信号 → 退化成 ext_disconnected 让 abort,不死循环
            signal_id = "ext_disconnected"
        self.signal_id = signal_id
        self.original_text = original_text
        # platform 缺省时从 signal_id prefix 提取(boss:xxx → boss)
        self.platform = platform or (signal_id.split(":", 1)[0]
                                     if ":" in signal_id else "")
        self.spec = RISK_SIGNALS[signal_id]
        super().__init__(f"[{signal_id}] {self.spec.get('label', signal_id)} :: {original_text}")


def get_strategy(signal_id: str) -> dict:
    """便捷 lookup,未知 signal_id 返回 abort 兜底策略。"""
    return RISK_SIGNALS.get(signal_id) or RISK_SIGNALS["ext_disconnected"]


def is_user_action(signal_id: str) -> bool:
    """该信号是否需要用户介入(暂停任务等用户点继续)。"""
    return get_strategy(signal_id).get("action") == "user_action"


def is_auto_retry(signal_id: str) -> bool:
    return get_strategy(signal_id).get("action") == "auto_retry"


def is_skip_item(signal_id: str) -> bool:
    return get_strategy(signal_id).get("action") == "skip_item"


def is_abort(signal_id: str) -> bool:
    return get_strategy(signal_id).get("action") == "abort"
