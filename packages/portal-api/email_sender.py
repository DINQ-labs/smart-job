"""Resend 邮件发送。

无 RESEND_API_KEY 时回退到 stdout，方便没邮箱的开发环境闭环测试。
"""
from __future__ import annotations

import logging
from typing import Any

import config

log = logging.getLogger("job-portal-api.email")

_client: Any = None  # resend.Client；延迟导入，无 key 时不强依赖

if config.RESEND_API_KEY:
    import resend as _resend  # type: ignore

    _resend.api_key = config.RESEND_API_KEY
    _client = _resend


def send_verification_code(*, to: str, code: str, purpose: str) -> dict[str, Any]:
    """purpose ∈ {signup, password_reset, email_change}；目前模板复用一套。"""
    expires_in_min = config.CODE_TTL_MINUTES

    if _client is None:
        # 开发模式：把码打到 stdout，让没有 Resend 的环境也能走完闭环
        log.warning(
            "[dev:verification] code=%s email=%s expires_in=%dm purpose=%s",
            code,
            to,
            expires_in_min,
            purpose,
        )
        return {"provider": "dev_console", "id": "dev-noop"}

    try:
        resp = _client.Emails.send(
            {
                "from": config.RESEND_FROM,
                "to": to,
                "subject": f"DingQ 邮箱验证码 {code}",
                "html": _render_html(code=code, expires_in_min=expires_in_min),
                "text": _render_text(code=code, expires_in_min=expires_in_min),
            }
        )
        return {"provider": "resend", "id": resp.get("id", "")}
    except Exception as e:
        log.exception("resend send failed")
        raise RuntimeError(f"email_send_failed: {e}") from e


def _render_text(*, code: str, expires_in_min: int) -> str:
    return (
        f"你的 DingQ 验证码：{code}\n"
        f"{expires_in_min} 分钟内有效。\n\n"
        "如果不是你本人操作请忽略此邮件。\n\n"
        "—— DingQ 团队"
    )


def _render_html(*, code: str, expires_in_min: int) -> str:
    # 极简模板：黑底头 + 蓝色验证码块。inline CSS 兼容主流邮件客户端。
    # 严格按 ../DESIGN.md §1：slate-900 / slate-50 / blue 50/200/600 / 600 字重 / 无渐变。
    return f"""<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8" /><title>DingQ 验证码</title></head>
  <body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:40px 16px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
          <tr><td style="background:#0f172a;padding:24px;color:#ffffff;font-weight:700;font-size:18px;">DingQ</td></tr>
          <tr><td style="padding:32px 28px 12px;color:#334155;font-weight:600;font-size:15px;line-height:1.6;">
            你正在使用 DingQ 账号操作，请使用下方验证码完成验证：
          </td></tr>
          <tr><td align="center" style="padding:8px 28px 28px;">
            <div style="display:inline-block;padding:18px 28px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;color:#2563eb;font-weight:800;font-size:32px;letter-spacing:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{code}</div>
          </td></tr>
          <tr><td style="padding:0 28px 28px;color:#64748b;font-weight:600;font-size:13px;line-height:1.6;">
            验证码 {expires_in_min} 分钟内有效。<br />
            如果不是你本人操作，请忽略此邮件，账号不会被创建或变更。
          </td></tr>
          <tr><td style="padding:18px 28px;background:#f1f5f9;color:#94a3b8;font-weight:600;font-size:12px;border-top:1px solid #e2e8f0;">
            —— DingQ 团队 · job.joyhouse.chat
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
