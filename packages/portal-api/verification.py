"""6 位邮件验证码：生成 / 持久化 / 校验 / 重发冷却。

业务参数集中在 config（CODE_TTL_MINUTES / CODE_MAX_ATTEMPTS / CODE_RESEND_COOLDOWN_SECONDS）。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

import bcrypt

import config
import db


class Purpose(str, Enum):
    SIGNUP = "signup"
    PASSWORD_RESET = "password_reset"
    EMAIL_CHANGE = "email_change"


def generate_code() -> str:
    """6 位数字验证码。

    开发态(config.DEV_FIXED_CODE 非空)返回固定码,免去查邮箱 —— 生产环境该值
    强制为空,回落到 `secrets` CSPRNG 随机码(非 `random`,避免可预测性)。
    """
    if config.DEV_FIXED_CODE:
        return config.DEV_FIXED_CODE
    return str(secrets.randbelow(900_000) + 100_000)


def _hash_code(code: str) -> str:
    return bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


def _check_code(code: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


@dataclass
class IssuedCode:
    verification_id: str
    plain_code: str
    expires_at: datetime


async def issue_code(
    *, email: str, user_id: str | None, purpose: Purpose
) -> IssuedCode:
    code = generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.CODE_TTL_MINUTES)
    rec = await db.create_verification(
        email=email,
        user_id=user_id,
        code_hash=_hash_code(code),
        purpose=purpose.value,
        expires_at=expires_at,
        max_attempts=config.CODE_MAX_ATTEMPTS,
    )
    return IssuedCode(
        verification_id=rec["id"],
        plain_code=code,
        expires_at=rec["expires_at"],
    )


VerifyOutcome = Literal[
    "ok",
    "not_found",
    "already_used",
    "expired",
    "too_many_attempts",
    "wrong_code",
    "wrong_purpose",
]


@dataclass
class VerifyResult:
    outcome: VerifyOutcome
    user_id: str | None = None
    email: str | None = None
    attempts_left: int | None = None


async def verify_code(
    *, verification_id: str, code: str, expected_purpose: Purpose
) -> VerifyResult:
    rec = await db.get_verification(verification_id)
    if rec is None:
        return VerifyResult(outcome="not_found")
    if rec["consumed_at"] is not None:
        return VerifyResult(outcome="already_used")
    if rec["expires_at"] < datetime.now(timezone.utc):
        return VerifyResult(outcome="expired")
    if rec["attempts"] >= rec["max_attempts"]:
        return VerifyResult(outcome="too_many_attempts")
    if rec["purpose"] != expected_purpose.value:
        return VerifyResult(outcome="wrong_purpose")

    if not _check_code(code, rec["code_hash"]):
        bumped = await db.increment_verification_attempts(verification_id)
        return VerifyResult(
            outcome="wrong_code",
            attempts_left=max(0, bumped["max_attempts"] - bumped["attempts"]),
        )

    await db.consume_verification(verification_id)
    return VerifyResult(
        outcome="ok",
        user_id=rec["user_id"],
        email=rec["email"],
    )


@dataclass
class ResendCheck:
    ok: bool
    retry_after_seconds: int = 0


async def check_resend_cooldown(email: str, purpose: Purpose) -> ResendCheck:
    """看上一条同 purpose 的验证码距今多久；< 冷却窗口则拒绝。"""
    last = await db.latest_verification(email, purpose.value)
    if last is None:
        return ResendCheck(ok=True)
    elapsed = (datetime.now(timezone.utc) - last["created_at"]).total_seconds()
    if elapsed < config.CODE_RESEND_COOLDOWN_SECONDS:
        return ResendCheck(
            ok=False,
            retry_after_seconds=int(config.CODE_RESEND_COOLDOWN_SECONDS - elapsed) + 1,
        )
    return ResendCheck(ok=True)
