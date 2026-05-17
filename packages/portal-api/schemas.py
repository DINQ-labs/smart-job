"""Pydantic 请求/响应模型。

约定：
- 入参 *Req，出参 *Resp；
- 错误统一走 `error: str` + 可选附加字段（`attempts_left` / `retry_after_seconds` 等），
  这样扩展端可以做 i18n 文案映射。
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


_PWD_HAS_LETTER = re.compile(r"[A-Za-z]")
_PWD_HAS_DIGIT = re.compile(r"\d")
_CODE_RE = re.compile(r"^\d{6}$")


class _PasswordMixin:
    @classmethod
    def _validate_password(cls, v: str) -> str:
        if not (8 <= len(v) <= 72):
            raise ValueError("密码长度 8-72")
        if not _PWD_HAS_LETTER.search(v):
            raise ValueError("密码至少 1 个字母")
        if not _PWD_HAS_DIGIT.search(v):
            raise ValueError("密码至少 1 个数字")
        return v


# ──────────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────────


class RegisterReq(BaseModel, _PasswordMixin):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    # 客户端类型，写入 refresh_tokens.client。extension/web/cli。
    client: Optional[str] = Field(default="extension", max_length=32)

    @field_validator("password")
    @classmethod
    def _pwd(cls, v: str) -> str:
        return cls._validate_password(v)


class RegisterResp(BaseModel):
    verification_id: str
    email: EmailStr
    expires_at: str
    dev_hint: Optional[str] = None


class VerifyEmailReq(BaseModel):
    verification_id: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError("code 必须是 6 位数字")
        return v


class ResendCodeReq(BaseModel):
    email: EmailStr


class ResendCodeResp(BaseModel):
    sent: bool
    verification_id: Optional[str] = None
    expires_at: Optional[str] = None
    dev_hint: Optional[str] = None


class LoginReq(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)
    client: Optional[str] = Field(default="extension", max_length=32)


class RefreshReq(BaseModel):
    refresh_token: str = Field(..., min_length=1, max_length=512)


class LogoutReq(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=1, max_length=512)
    all_devices: bool = False


# ──────────────────────────────────────────────────────────────────────────
# Password Reset
# ──────────────────────────────────────────────────────────────────────────


class PasswordResetReq(BaseModel):
    email: EmailStr


class PasswordResetVerifyReq(BaseModel):
    verification_id: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError("code 必须是 6 位数字")
        return v


class PasswordResetConfirmReq(BaseModel):
    verification_id: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=72)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError("code 必须是 6 位数字")
        return v

    @field_validator("new_password")
    @classmethod
    def _pwd(cls, v: str) -> str:
        return cls._validate_password(v)


class PasswordResetConfirmResp(BaseModel):
    ok: bool


# ──────────────────────────────────────────────────────────────────────────
# Profile Update
# ──────────────────────────────────────────────────────────────────────────


class UpdateProfileReq(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    image: Optional[str] = Field(default=None, max_length=512)
    locale: Optional[str] = Field(default=None, max_length=16)


class ChangePasswordReq(BaseModel, _PasswordMixin):
    old_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def _pwd(cls, v: str) -> str:
        return cls._validate_password(v)


# ──────────────────────────────────────────────────────────────────────────
# Account Deletion
# ──────────────────────────────────────────────────────────────────────────


class DeleteAccountReq(BaseModel):
    """请求账户删除，返回 verification_id 发确认邮件"""
    email: EmailStr


class DeleteAccountConfirmReq(BaseModel):
    """确认删除，需 verification_id + code"""
    verification_id: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError("code 必须是 6 位数字")
        return v


# ──────────────────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────────────────


class UserOut(BaseModel):
    id: str
    email: EmailStr
    email_verified_at: Optional[str]
    name: Optional[str]
    image: Optional[str]
    locale: str
    created_at: str


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    access_expires_at: int  # unix seconds
    refresh_expires_at: int  # unix seconds


class AuthOut(BaseModel):
    user: UserOut
    tokens: TokensOut
