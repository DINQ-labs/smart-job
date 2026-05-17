"""Portal access token 校验 —— gateway 侧独立 RS256 验签。

access token 由 job-portal-api 用 RSA 私钥签发。本模块启动时拉 portal 的
`/.well-known/jwks.json`，把公钥按 kid 缓存；之后 verify 纯本地、零网络
（仅 portal 轮换密钥、kid 未命中时偶发重拉一次）。

用法：autofill 路由 handler 加 `@portal_auth.require_user` 装饰器 —— 校验
通过则 user_id（JWT sub）注入 request.state.user_id；失败回 401。
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import urllib.request

import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)

# 默认值对齐 portal 生产实际签发参数 —— 注意 portal 生产 .env 把 issuer 覆盖成了
# api.job.joyhouse.chat（config.py 里的默认是 job.joyhouse.chat，别被它误导）。
# env 可覆盖；换环境时务必与该环境 portal 的 JWT_ISSUER/JWT_AUDIENCE 对齐。
JWKS_URL = os.getenv(
    "PORTAL_JWKS_URL", "https://api.job.joyhouse.chat/.well-known/jwks.json"
)
JWT_ISSUER = os.getenv("JWT_ISSUER", "https://api.job.joyhouse.chat")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "job-portal")
_JWKS_TIMEOUT = 8

_keys: dict[str, object] = {}   # kid -> RSA 公钥对象


class AuthError(Exception):
    """token 缺失 / 验签失败 / 过期 —— require_user 据此回 401。"""


def _fetch_jwks() -> dict[str, object]:
    """同步拉 JWKS 并解析成 {kid: 公钥}。"""
    with urllib.request.urlopen(JWKS_URL, timeout=_JWKS_TIMEOUT) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    out: dict[str, object] = {}
    for jwk in doc.get("keys", []):
        kid = jwk.get("kid")
        if kid:
            out[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
    if not out:
        raise RuntimeError("JWKS 文档无可用密钥")
    return out


def load_jwks() -> None:
    """启动预热。server.py 经 asyncio.to_thread 调用；失败不致命 ——
    首个请求会在 _public_key 里重试拉取。"""
    global _keys
    _keys = _fetch_jwks()
    log.info("portal JWKS 已加载：%d 个密钥", len(_keys))


def _public_key(token: str):
    """按 token header 的 kid 取公钥；未命中则重拉一次 JWKS（应对密钥轮换）。"""
    try:
        kid = pyjwt.get_unverified_header(token).get("kid")
    except pyjwt.PyJWTError as e:
        raise AuthError("invalid_token") from e
    if kid and kid in _keys:
        return _keys[kid]
    try:
        _keys.update(_fetch_jwks())
    except Exception as e:
        raise AuthError("jwks_unavailable") from e
    if kid and kid in _keys:
        return _keys[kid]
    raise AuthError("unknown_kid")


def _verify(token: str) -> dict:
    """验签 + 校 iss/aud/exp/typ。成功返回 claims；失败抛 AuthError。"""
    key = _public_key(token)
    try:
        claims = pyjwt.decode(
            token, key, algorithms=["RS256"],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except pyjwt.ExpiredSignatureError as e:
        raise AuthError("token_expired") from e
    except pyjwt.PyJWTError as e:
        raise AuthError("invalid_token") from e
    if claims.get("typ") != "access":
        raise AuthError("wrong_token_type")
    if not claims.get("sub"):
        raise AuthError("missing_sub")
    return claims


def _bearer(request: Request) -> str:
    authz = request.headers.get("authorization", "")
    if authz[:7].lower() != "bearer ":
        return ""
    return authz[7:].strip()


async def authenticate(request: Request) -> str:
    """取 Authorization 头的 access token、验签，返回 user_id（JWT sub）。
    失败抛 AuthError。验签纯 CPU + 偶发一次 JWKS 重拉 —— 放线程池不阻塞事件循环。"""
    token = _bearer(request)
    if not token:
        raise AuthError("missing_token")
    claims = await asyncio.to_thread(_verify, token)
    return claims["sub"]


def require_user(handler):
    """autofill 路由装饰器：先验 token，user_id（JWT sub）注入
    request.state.user_id；校验失败回 401。被装饰 handler 内用
    request.state.user_id 取用户，不再信任请求体里的 user_id。"""
    @functools.wraps(handler)
    async def wrapped(request: Request) -> JSONResponse:
        try:
            request.state.user_id = await authenticate(request)
        except AuthError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=401)
        return await handler(request)
    return wrapped
