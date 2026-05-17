"""OAuth 登录端点（Google / 其他提供商）。

流程：
1. 前端 GET /auth/oauth/{provider} → 重定向到 provider 授权页
2. 用户授权后，provider 回调 GET /auth/oauth/{provider}/callback?code=xxx
3. 后端交换 code → access_token，获取用户信息
4. 查找或创建 app_users + auth_identities 记录
5. 颁发 access/refresh token，返回 AuthOut

多账户锚点：
- Google 用 sub（subject）作为唯一锚点
- 微信用 union_id（跨应用）作为锚点，open_id 仅单应用
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta, timezone
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import config
import db
import deps
import jwt_service

log = logging.getLogger("job-portal-api.oauth")

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])


# ──────────────────────────────────────────────────────────────────────────
# Provider 配置
# ──────────────────────────────────────────────────────────────────────────


class OAuthProvider:
    def __init__(
        self,
        name: str,
        client_id: str,
        client_secret: str,
        auth_url: str,
        token_url: str,
        userinfo_url: str,
        scope: str,
    ):
        self.name = name
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_url = auth_url
        self.token_url = token_url
        self.userinfo_url = userinfo_url
        self.scope = scope

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """构造授权 URL，带 state 防 CSRF。"""
        from urllib.parse import urlencode

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "response_type": "code",
            "state": state,
        }
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """用 code 换 access_token。"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.token_url,
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_userinfo(self, access_token: str) -> dict[str, Any]:
        """获取用户信息。"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()


def _get_providers() -> dict[str, OAuthProvider]:
    """从环境变量加载 provider 配置。"""
    providers: dict[str, OAuthProvider] = {}

    # Google OAuth 2.0
    google_client_id = config._opt("GOOGLE_CLIENT_ID", "")
    google_client_secret = config._opt("GOOGLE_CLIENT_SECRET", "")
    if google_client_id and google_client_secret:
        providers["google"] = OAuthProvider(
            name="google",
            client_id=google_client_id,
            client_secret=google_client_secret,
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            userinfo_url="https://www.googleapis.com/oauth2/v2/userinfo",
            scope="openid email profile",
        )

    return providers


_providers: dict[str, OAuthProvider] = _get_providers()


def get_provider(name: str) -> OAuthProvider:
    if name not in _providers:
        raise HTTPException(400, detail={"error": "unsupported_provider"})
    return _providers[name]


# ──────────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────────


class OAuthUrlOut(BaseModel):
    auth_url: str
    state: str


class OAuthCallbackReq(BaseModel):
    code: str
    state: str


# ──────────────────────────────────────────────────────────────────────────
# State 管理（防 CSRF）
# ──────────────────────────────────────────────────────────────────────────


_states: dict[str, tuple[str, int]] = {}  # state → (provider, created_at_ts)


def _generate_state(provider: str) -> str:
    """生成随机 state，存内存。生产环境应改用 Redis。"""
    state = uuid.uuid4().hex + uuid.uuid4().hex
    _states[state] = (provider, int(__import__("time").time()))
    return state


def _verify_state(state: str) -> str | None:
    """验证 state，返回 provider 名。5 分钟有效期。"""
    import time

    data = _states.pop(state, None)
    if not data:
        return None
    provider, created_at = data
    if time.time() - created_at > 300:  # 5 分钟
        return None
    return provider


# ──────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────


@router.get("/{provider}/auth-url", response_model=OAuthUrlOut)
async def get_oauth_url(provider: str, request: Request) -> OAuthUrlOut:
    """获取 OAuth 授权 URL（前端用此 URL 做服务端重定向或弹窗）。"""
    p = get_provider(provider)

    # 构造 callback URL（基于当前 host）
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    redirect_uri = f"{scheme}://{host}/auth/oauth/{provider}/callback"

    state = _generate_state(provider)
    auth_url = p.get_auth_url(redirect_uri, state)

    return OAuthUrlOut(auth_url=auth_url, state=state)


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
) -> dict[str, Any]:
    """OAuth 提供商回调端点。完成登录流程，返回 AuthOut。"""
    # 验证 state
    real_provider = _verify_state(state)
    if real_provider != provider:
        raise HTTPException(400, detail={"error": "invalid_state"})

    p = get_provider(provider)

    # 构造 redirect_uri（需与 auth_url 时一致）
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    redirect_uri = f"{scheme}://{host}/auth/oauth/{provider}/callback"

    # 交换 code
    try:
        token_resp = await p.exchange_code(code, redirect_uri)
        access_token = token_resp.get("access_token")
        if not access_token:
            raise HTTPException(500, detail={"error": "no_access_token"})
    except httpx.HTTPStatusError as e:
        log.error("oauth exchange failed: %s", e.response.text)
        raise HTTPException(502, detail={"error": "oauth_exchange_failed"}) from e

    # 获取用户信息
    try:
        userinfo = await p.get_userinfo(access_token)
    except httpx.HTTPStatusError as e:
        log.error("oauth userinfo failed: %s", e.response.text)
        raise HTTPException(502, detail={"error": "oauth_userinfo_failed"}) from e

    # Google 返回 {id, email, verified_email, name, picture}
    sub = userinfo.get("id")
    if not sub:
        raise HTTPException(500, detail={"error": "no_user_id"})

    email = userinfo.get("email", "")
    email_verified = userinfo.get("verified_email", False)
    name = userinfo.get("name")
    picture = userinfo.get("picture")

    # 查找已有 identity
    identity = await db.find_identity(provider, sub)

    if identity:
        # 已有用户，直接登录
        user = await db.get_user_by_id(identity["user_id"])
        if not user:
            raise HTTPException(500, detail={"error": "user_missing"})
    else:
        # 新用户，创建账户 + identity
        if email:
            existing = await db.get_user_by_email(email)
            if existing:
                # 邮箱已被注册，需要绑定（TODO: 前端引导用户先登录再链接 OAuth）
                raise HTTPException(
                    409,
                    detail={
                        "error": "email_already_registered",
                        "email": email,
                        "hint": "请先用密码登录，再到设置里绑定 OAuth",
                    },
                )
        # 创建新用户
        user = await db.create_user(
            email=email if email_verified else None,
            name=name,
            image=picture,
            locale="zh-CN",
        )
        identity = await db.link_identity(
            user_id=user["id"],
            kind="oauth",
            provider=provider,
            subject=sub,
            email_at_link=email,
            name_at_link=name,
            image_at_link=picture,
        )

    # 更新 identity 信息（可能变更了 name/picture）
    await db.link_identity(
        user_id=user["id"],
        kind="oauth",
        provider=provider,
        subject=sub,
        email_at_link=email,
        name_at_link=name,
        image_at_link=picture,
    )
    await db.touch_identity_used(identity["id"])

    # 颁发 token
    access, access_exp = jwt_service.sign_access_token(
        sub=user["id"], email=user["email"] or ""
    )
    plain_refresh, refresh_hash = jwt_service.new_refresh_token()
    refresh_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=config.REFRESH_TTL_DAYS)
    )
    meta = deps.client_meta(request)
    rec = await db.insert_refresh_token(
        user_id=user["id"],
        token_hash=refresh_hash,
        expires_at=refresh_expires_at,
        user_agent=meta["user_agent"],
        ip_hash=meta["ip_hash"],
        client="extension",
    )

    await db.touch_user_login(user["id"])
    await db.log_auth_event(
        user_id=user["id"],
        event_type="login",
        provider=provider,
        ip_hash=meta["ip_hash"],
        user_agent=meta["user_agent"],
    )

    return {
        "user": {
            "id": user["id"],
            "email": user["email"],
            "email_verified_at": user["email_verified_at"].isoformat()
            if user["email_verified_at"]
            else None,
            "name": user["name"],
            "image": user["image"],
            "locale": user["locale"],
            "created_at": user["created_at"].isoformat(),
        },
        "tokens": {
            "access_token": access,
            "refresh_token": plain_refresh,
            "token_type": "Bearer",
            "access_expires_at": access_exp,
            "refresh_expires_at": int(refresh_expires_at.timestamp()),
        },
    }
