"""
oauth_linkedin.py: LinkedIn OAuth2 认证流程。

Step 1: build_authorization_url(state) → 重定向用户到 LinkedIn 授权页面
Step 2: exchange_code_for_token(code)  → 换取 access_token
Step 3: fetch_profile(access_token)    → 获取用户 LinkedIn Profile
Step 4: store_oauth_token(...)         → 写入 account_cookies（platform='linkedin_oauth'）

Token 存储：复用 account_cookies 表，零新表。
"""
from __future__ import annotations

import json
import os
import secrets
import uuid
from urllib.parse import urlencode

import httpx

import db

_CLIENT_ID     = os.environ.get("LINKEDIN_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
_REDIRECT_URI  = os.environ.get(
    "LINKEDIN_REDIRECT_URI", "http://127.0.0.1:8767/oauth/linkedin/callback"
)

_AUTH_URL    = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL   = "https://www.linkedin.com/oauth/v2/accessToken"
_PROFILE_URL = "https://api.linkedin.com/v2/me"


def build_authorization_url(state: str = "") -> str:
    """Step 1: 构建 LinkedIn OAuth2 授权 URL。"""
    if not state:
        state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "state": state,
        "scope": "r_liteprofile r_emailaddress w_member_social",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict:
    """Step 2: 用 authorization code 换取 access_token。"""
    data = {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  _REDIRECT_URI,
        "client_id":     _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(_TOKEN_URL, data=data)
        resp.raise_for_status()
        return resp.json()


async def fetch_profile(access_token: str) -> dict:
    """Step 3: 用 access_token 获取用户 LinkedIn Profile。"""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(_PROFILE_URL, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def store_oauth_token(
    app_user_id: str,
    token_data: dict,
    profile: dict,
) -> str:
    """
    Step 4: 将 OAuth token 存入 account_cookies 表（platform='linkedin_oauth'）。
    返回 cookie_id（account_cookies.id）。
    """
    member_id    = profile.get("id", "")
    first_name   = profile.get("localizedFirstName") or ""
    last_name    = profile.get("localizedLastName") or ""
    account_name = f"{first_name} {last_name}".strip() or member_id

    cookies_json = json.dumps(
        {
            "access_token": token_data.get("access_token", ""),
            "expires_in":   token_data.get("expires_in", 0),
            "token_type":   token_data.get("token_type", "Bearer"),
            "profile":      profile,
        },
        ensure_ascii=False,
    )

    cookie_id = await db.save_account_cookies(
        browser_id=member_id,
        session_id="",
        account_name=account_name,
        user_id=app_user_id,
        platform="linkedin_oauth",
        cookies_json=cookies_json,
    )
    return cookie_id
