"""FastAPI 依赖项：从 Authorization 头解 access token → 当前用户。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import db
import jwt_service

_bearer = HTTPBearer(auto_error=True, scheme_name="Bearer")


@dataclass
class CurrentUser:
    id: str
    email: str
    claims: dict[str, Any]


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> CurrentUser:
    try:
        claims = jwt_service.verify_access_token(creds.credentials)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "token_expired"},
        )
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token"},
        )

    if claims.get("typ") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "wrong_token_type"},
        )

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_sub"},
        )

    # 不每次查 DB（access token 短命 + 已签发 = 一定时间内有效）。
    # 撤销机制走 refresh_tokens 表 + 短 access TTL 即可。
    return CurrentUser(id=sub, email=claims.get("email", ""), claims=claims)


def client_meta(request: Request) -> dict[str, str | None]:
    """从请求里提取 user_agent / ip 摘要，落到 refresh_tokens 元数据。"""
    import hashlib

    ua = request.headers.get("user-agent", "")[:256] or None
    ip_raw = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.headers.get("x-real-ip", "").strip()
        or (request.client.host if request.client else "")
    )
    ip_hash = (
        hashlib.sha256(ip_raw.encode()).hexdigest()[:32] if ip_raw else None
    )
    return {"user_agent": ua, "ip_hash": ip_hash}
