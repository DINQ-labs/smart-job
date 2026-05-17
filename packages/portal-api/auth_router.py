"""认证端点。

错误码风格：HTTP 状态 + JSON body `{ "error": "<key>", ... }`。
扩展端做 i18n 映射；本服务不返回最终面向用户的中文。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

import config
import db
import deps
import email_sender
import jwt_service
import password as password_mod
import verification as ver
from schemas import (
    AuthOut,
    ChangePasswordReq,
    DeleteAccountConfirmReq,
    DeleteAccountReq,
    LoginReq,
    LogoutReq,
    PasswordResetConfirmReq,
    PasswordResetConfirmResp,
    PasswordResetReq,
    PasswordResetVerifyReq,
    RefreshReq,
    RegisterReq,
    RegisterResp,
    ResendCodeReq,
    ResendCodeResp,
    TokensOut,
    UpdateProfileReq,
    UserOut,
    VerifyEmailReq,
)

log = logging.getLogger("job-portal-api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(row: Any) -> UserOut:
    return UserOut(
        id=row["id"],
        email=row["email"],
        email_verified_at=row["email_verified_at"].isoformat()
        if row["email_verified_at"]
        else None,
        name=row["name"],
        image=row["image"],
        locale=row["locale"],
        created_at=row["created_at"].isoformat(),
    )


def _event_meta(request: Request) -> dict[str, Any]:
    """deps.client_meta 拿 ip_hash + user_agent;走 nginx 经 X-Forwarded-For."""
    m = deps.client_meta(request)
    return {"ip_hash": m["ip_hash"], "user_agent": m["user_agent"]}


async def _issue_token_pair(
    *,
    user_id: str,
    email: str,
    request: Request,
    client: str | None,
) -> tuple[TokensOut, str]:
    """颁发一对 access/refresh，返回 tokens 和新 refresh row id（refresh 旋转用）。"""
    access, access_exp = jwt_service.sign_access_token(sub=user_id, email=email)
    plain_refresh, refresh_hash = jwt_service.new_refresh_token()
    refresh_expires_at = datetime.now(timezone.utc) + timedelta(
        days=config.REFRESH_TTL_DAYS
    )
    meta = deps.client_meta(request)
    rec = await db.insert_refresh_token(
        user_id=user_id,
        token_hash=refresh_hash,
        expires_at=refresh_expires_at,
        user_agent=meta["user_agent"],
        ip_hash=meta["ip_hash"],
        client=client,
    )
    tokens = TokensOut(
        access_token=access,
        refresh_token=plain_refresh,
        access_expires_at=access_exp,
        refresh_expires_at=int(refresh_expires_at.timestamp()),
    )
    return tokens, rec["id"]


# ──────────────────────────────────────────────────────────────────────────
# 注册
# ──────────────────────────────────────────────────────────────────────────


@router.post("/register", response_model=RegisterResp, status_code=200)
async def register(body: RegisterReq) -> RegisterResp:
    email = body.email.lower()
    existing = await db.get_user_by_email(email)
    if existing and existing["email_verified_at"]:
        raise HTTPException(409, detail={"error": "email_already_registered"})

    pwd_hash = password_mod.hash_password(body.password)
    if existing:
        # 用户曾注册但未验证：覆盖密码 hash，方便用户改密重发
        await db.update_user_password(existing["id"], pwd_hash)
        user_id = existing["id"]
    else:
        user = await db.create_user(email=email, password_hash=pwd_hash)
        user_id = user["id"]

    cooldown = await ver.check_resend_cooldown(email, ver.Purpose.SIGNUP)
    if not cooldown.ok:
        raise HTTPException(
            429,
            detail={
                "error": "cooldown",
                "retry_after_seconds": cooldown.retry_after_seconds,
            },
        )

    issued = await ver.issue_code(
        email=email, user_id=user_id, purpose=ver.Purpose.SIGNUP
    )
    try:
        send = email_sender.send_verification_code(
            to=email, code=issued.plain_code, purpose="signup"
        )
    except RuntimeError as e:
        log.error("email send failed: %s", e)
        raise HTTPException(502, detail={"error": "email_send_failed"})

    return RegisterResp(
        verification_id=issued.verification_id,
        email=email,
        expires_at=issued.expires_at.isoformat(),
        dev_hint=(
            "code_in_server_console"
            if send.get("provider") == "dev_console"
            else None
        ),
    )


# ──────────────────────────────────────────────────────────────────────────
# 验证邮箱
# ──────────────────────────────────────────────────────────────────────────


@router.post("/verify-email", response_model=AuthOut)
async def verify_email(body: VerifyEmailReq, request: Request) -> AuthOut:
    result = await ver.verify_code(
        verification_id=body.verification_id,
        code=body.code,
        expected_purpose=ver.Purpose.SIGNUP,
    )
    if result.outcome != "ok":
        detail: dict[str, Any] = {"error": result.outcome}
        if result.attempts_left is not None:
            detail["attempts_left"] = result.attempts_left
        raise HTTPException(400, detail=detail)

    if not result.user_id:
        raise HTTPException(400, detail={"error": "no_user_bound"})

    user = await db.mark_email_verified(result.user_id)

    tokens, _ = await _issue_token_pair(
        user_id=user["id"], email=user["email"], request=request, client="extension"
    )
    return AuthOut(user=_user_out(user), tokens=tokens)


# ──────────────────────────────────────────────────────────────────────────
# 重发码
# ──────────────────────────────────────────────────────────────────────────


@router.post("/resend-code", response_model=ResendCodeResp)
async def resend_code(body: ResendCodeReq) -> ResendCodeResp:
    email = body.email.lower()
    user = await db.get_user_by_email(email)

    # 防枚举：用户不存在 / 已验证 → 都返回 sent=False 但 200。客户端无法区分。
    if not user or user["email_verified_at"]:
        return ResendCodeResp(sent=False)

    cooldown = await ver.check_resend_cooldown(email, ver.Purpose.SIGNUP)
    if not cooldown.ok:
        raise HTTPException(
            429,
            detail={
                "error": "cooldown",
                "retry_after_seconds": cooldown.retry_after_seconds,
            },
        )

    issued = await ver.issue_code(
        email=email, user_id=user["id"], purpose=ver.Purpose.SIGNUP
    )
    try:
        send = email_sender.send_verification_code(
            to=email, code=issued.plain_code, purpose="signup"
        )
    except RuntimeError:
        raise HTTPException(502, detail={"error": "email_send_failed"})

    return ResendCodeResp(
        sent=True,
        verification_id=issued.verification_id,
        expires_at=issued.expires_at.isoformat(),
        dev_hint=(
            "code_in_server_console"
            if send.get("provider") == "dev_console"
            else None
        ),
    )


# ──────────────────────────────────────────────────────────────────────────
# 登录
# ──────────────────────────────────────────────────────────────────────────


@router.post("/login", response_model=AuthOut)
async def login(body: LoginReq, request: Request) -> AuthOut:
    email = body.email.lower()
    user = await db.get_user_by_email(email)
    if not user or not user["password_hash"]:
        raise HTTPException(401, detail={"error": "invalid_credentials"})
    if not password_mod.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, detail={"error": "invalid_credentials"})
    if not user["email_verified_at"]:
        raise HTTPException(
            403, detail={"error": "email_not_verified", "email": user["email"]}
        )

    tokens, _ = await _issue_token_pair(
        user_id=user["id"],
        email=user["email"],
        request=request,
        client=body.client or "extension",
    )
    return AuthOut(user=_user_out(user), tokens=tokens)


# ──────────────────────────────────────────────────────────────────────────
# Refresh：旋转 + 旧 refresh 立即失效（reuse detection 通过 replaced_by 追溯）
# ──────────────────────────────────────────────────────────────────────────


@router.post("/refresh", response_model=AuthOut)
async def refresh(body: RefreshReq, request: Request) -> AuthOut:
    token_hash = jwt_service.hash_refresh_token(body.refresh_token)
    rec = await db.find_refresh_token(token_hash)
    if rec is None:
        raise HTTPException(401, detail={"error": "invalid_refresh"})
    if rec["revoked_at"] is not None:
        # Reuse detected：被撤销的 refresh 又被拿来用。撤销该用户全部 refresh 作为防御。
        log.warning(
            "refresh reuse detected user=%s revoked_at=%s", rec["user_id"], rec["revoked_at"]
        )
        await db.revoke_all_user_tokens(rec["user_id"])
        raise HTTPException(401, detail={"error": "refresh_revoked"})
    if rec["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(401, detail={"error": "refresh_expired"})

    user = await db.get_user_by_id(rec["user_id"])
    if user is None:
        raise HTTPException(401, detail={"error": "user_missing"})

    # 颁新对 + 撤销旧的，记录被谁替换（reuse detection 用）
    tokens, new_id = await _issue_token_pair(
        user_id=user["id"],
        email=user["email"],
        request=request,
        client=rec["client"],
    )
    await db.revoke_refresh_token(rec["id"], replaced_by=new_id)

    return AuthOut(user=_user_out(user), tokens=tokens)


# ──────────────────────────────────────────────────────────────────────────
# Logout：撤销指定 refresh（或全部设备）
# ──────────────────────────────────────────────────────────────────────────


@router.post("/logout")
async def logout(
    body: LogoutReq,
    user: Annotated[deps.CurrentUser, Depends(deps.current_user)],
) -> dict[str, Any]:
    if body.all_devices:
        await db.revoke_all_user_tokens(user.id)
        return {"ok": True, "revoked": "all"}

    if body.refresh_token:
        token_hash = jwt_service.hash_refresh_token(body.refresh_token)
        rec = await db.find_refresh_token(token_hash)
        if rec and rec["user_id"] == user.id and rec["revoked_at"] is None:
            await db.revoke_refresh_token(rec["id"])
            return {"ok": True, "revoked": "current"}
        return {"ok": True, "revoked": "none"}

    # 既没指定 refresh 也没全设备：noop。一般 logout 应把 refresh 传上来。
    return {"ok": True, "revoked": "none"}


# ──────────────────────────────────────────────────────────────────────────
# Me
# ──────────────────────────────────────────────────────────────────────────


@router.get("/me", response_model=UserOut)
async def me(
    user: Annotated[deps.CurrentUser, Depends(deps.current_user)],
) -> UserOut:
    row = await db.get_user_by_id(user.id)
    if row is None:
        raise HTTPException(404, detail={"error": "user_not_found"})
    return _user_out(row)


# ──────────────────────────────────────────────────────────────────────────
# Password Reset
# ──────────────────────────────────────────────────────────────────────────


@router.post("/password-reset")
async def request_password_reset(body: PasswordResetReq) -> ResendCodeResp:
    """请求密码重置，发送验证码到邮箱。防枚举：无论用户是否存在都返回 200。"""
    email = body.email.lower()
    user = await db.get_user_by_email(email)

    # 防枚举：用户不存在 → 返回 sent=False 但 200
    if not user or not user["password_hash"]:
        return ResendCodeResp(sent=False)

    cooldown = await ver.check_resend_cooldown(email, ver.Purpose.PASSWORD_RESET)
    if not cooldown.ok:
        raise HTTPException(
            429,
            detail={
                "error": "cooldown",
                "retry_after_seconds": cooldown.retry_after_seconds,
            },
        )

    issued = await ver.issue_code(
        email=email, user_id=user["id"], purpose=ver.Purpose.PASSWORD_RESET
    )
    try:
        send = email_sender.send_verification_code(
            to=email, code=issued.plain_code, purpose="password_reset"
        )
    except RuntimeError:
        raise HTTPException(502, detail={"error": "email_send_failed"})

    return ResendCodeResp(
        sent=True,
        verification_id=issued.verification_id,
        expires_at=issued.expires_at.isoformat(),
        dev_hint=(
            "code_in_server_console"
            if send.get("provider") == "dev_console"
            else None
        ),
    )


@router.post("/password-reset/verify")
async def verify_password_reset_code(body: PasswordResetVerifyReq) -> dict[str, Any]:
    """验证密码重置码，但不执行重置（用于前端 UX："验证通过，请输入新密码"）。"""
    result = await ver.verify_code(
        verification_id=body.verification_id,
        code=body.code,
        expected_purpose=ver.Purpose.PASSWORD_RESET,
    )
    if result.outcome != "ok":
        detail: dict[str, Any] = {"error": result.outcome}
        if result.attempts_left is not None:
            detail["attempts_left"] = result.attempts_left
        raise HTTPException(400, detail=detail)

    # 返回一个临时 token，前端用它调用 confirm 接口（避免前端同时传 verification_id + code）
    import secrets

    temp_token = secrets.token_urlsafe(32)
    # TODO: 把 temp_token → verification_id 的映射存 Redis，TTL 与 code 过期时间一致
    # 简化版：直接让前端传 verification_id + code，不用 temp_token
    return {"ok": True, "verified": True}


@router.post("/password-reset/confirm", response_model=PasswordResetConfirmResp)
async def confirm_password_reset(body: PasswordResetConfirmReq) -> PasswordResetConfirmResp:
    """验证码 + 新密码，完成密码重置。"""
    result = await ver.verify_code(
        verification_id=body.verification_id,
        code=body.code,
        expected_purpose=ver.Purpose.PASSWORD_RESET,
    )
    if result.outcome != "ok":
        detail: dict[str, Any] = {"error": result.outcome}
        if result.attempts_left is not None:
            detail["attempts_left"] = result.attempts_left
        raise HTTPException(400, detail=detail)

    if not result.user_id:
        raise HTTPException(400, detail={"error": "no_user_bound"})

    pwd_hash = password_mod.hash_password(body.new_password)
    await db.update_user_password(result.user_id, pwd_hash)

    # 撤销该用户所有 refresh token（强制所有设备重新登录）
    await db.revoke_all_user_tokens(result.user_id)
    await db.log_auth_event(
        user_id=result.user_id,
        event_type="password_reset_completed",
        metadata={"method": "email_code"},
    )

    return PasswordResetConfirmResp(ok=True)


# ──────────────────────────────────────────────────────────────────────────
# Profile Update
# ──────────────────────────────────────────────────────────────────────────


@router.patch("/me", response_model=UserOut)
async def update_profile(
    body: UpdateProfileReq,
    user: Annotated[deps.CurrentUser, Depends(deps.current_user)],
) -> UserOut:
    """更新用户资料（name, image, locale）。至少传一个字段。"""
    if body.name is None and body.image is None and body.locale is None:
        raise HTTPException(400, detail={"error": "no_fields"})

    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.image is not None:
        updates["image"] = body.image
    if body.locale is not None:
        updates["locale"] = body.locale

    row = await db.update_user_profile(user.id, **updates)
    if row is None:
        raise HTTPException(404, detail={"error": "user_not_found"})
    return _user_out(row)


@router.post("/me/change-password")
async def change_password(
    body: ChangePasswordReq,
    user: Annotated[deps.CurrentUser, Depends(deps.current_user)],
) -> dict[str, Any]:
    """已登录用户修改密码（需旧密码验证）。"""
    row = await db.get_user_by_id(user.id)
    if row is None:
        raise HTTPException(404, detail={"error": "user_not_found"})
    if not row["password_hash"]:
        raise HTTPException(400, detail={"error": "no_password_set"})

    if not password_mod.verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(401, detail={"error": "invalid_old_password"})

    pwd_hash = password_mod.hash_password(body.new_password)
    await db.update_user_password(user.id, pwd_hash)

    # 撤销所有 refresh token（安全策略：改密后强制重新登录）
    await db.revoke_all_user_tokens(user.id)
    await db.log_auth_event(
        user_id=user.id,
        event_type="password_changed",
        metadata={"method": "authenticated"},
    )

    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
# Account Deletion (Soft Delete)
# ──────────────────────────────────────────────────────────────────────────


@router.post("/me/delete")
async def request_account_deletion(
    body: DeleteAccountReq,
    user: Annotated[deps.CurrentUser, Depends(deps.current_user)],
) -> ResendCodeResp:
    """请求账户删除，发送确认码到注册邮箱（安全检查：需邮箱匹配）。"""
    row = await db.get_user_by_id(user.id)
    if row is None:
        raise HTTPException(404, detail={"error": "user_not_found"})

    if row["email"].lower() != body.email.lower():
        # 防枚举：返回成功但实际不发信
        return ResendCodeResp(sent=False)

    cooldown = await ver.check_resend_cooldown(row["email"], ver.Purpose.EMAIL_CHANGE)
    if not cooldown.ok:
        raise HTTPException(
            429,
            detail={
                "error": "cooldown",
                "retry_after_seconds": cooldown.retry_after_seconds,
            },
        )

    issued = await ver.issue_code(
        email=row["email"], user_id=user.id, purpose=ver.Purpose.EMAIL_CHANGE
    )
    try:
        send = email_sender.send_verification_code(
            to=row["email"], code=issued.plain_code, purpose="account_deletion"
        )
    except RuntimeError:
        raise HTTPException(502, detail={"error": "email_send_failed"})

    return ResendCodeResp(
        sent=True,
        verification_id=issued.verification_id,
        expires_at=issued.expires_at.isoformat(),
        dev_hint=(
            "code_in_server_console"
            if send.get("provider") == "dev_console"
            else None
        ),
    )


@router.post("/me/delete/confirm")
async def confirm_account_deletion(
    body: DeleteAccountConfirmReq,
    user: Annotated[deps.CurrentUser, Depends(deps.current_user)],
) -> dict[str, Any]:
    """确认删除账户（软删除：deleted_at = NOW）。不可逆！"""
    result = await ver.verify_code(
        verification_id=body.verification_id,
        code=body.code,
        expected_purpose=ver.Purpose.EMAIL_CHANGE,
    )
    if result.outcome != "ok":
        detail: dict[str, Any] = {"error": result.outcome}
        if result.attempts_left is not None:
            detail["attempts_left"] = result.attempts_left
        raise HTTPException(400, detail=detail)

    if result.user_id != user.id:
        raise HTTPException(403, detail={"error": "user_mismatch"})

    await db.soft_delete_user(user.id)
    # 撤销所有 token
    await db.revoke_all_user_tokens(user.id)
    await db.log_auth_event(
        user_id=user.id,
        event_type="account_deleted",
        metadata={"method": "user_initiated"},
    )

    return {"ok": True, "deleted": True}
