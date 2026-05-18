"""Portal access token 校验 —— api-gateway 侧 RS256 验签（扩展 WS 握手用）。

与 agent-gateway/portal_auth.py 同源：access token 由 job-portal-api 用 RSA 私钥
签发；本模块拉 portal 的 `/.well-known/jwks.json`，把公钥按 kid 缓存，之后 verify
纯本地、零网络（仅 portal 轮换密钥、kid 未命中时偶发重拉一次）。

扩展 WS 握手 query 里的 `token=` 若是 portal access token（JWT），用
`verify_token()` 验签并取 `sub` 作为可信 user_id —— 见 server_helpers.authenticate_extension。

环境变量（留空即用默认值，方便 docker-compose 用 ${VAR:-} 透传）：
  PORTAL_JWKS_URL  JWKS 端点。docker 栈内应指向 http://portal-api:8771/.well-known/jwks.json
  JWT_ISSUER       期望的 iss
  JWT_AUDIENCE     期望的 aud
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm

log = logging.getLogger(__name__)

# os.getenv(...) or "默认" —— 区别于 os.getenv(k, "默认")：环境变量被设成空字符串
# （docker-compose 的 ${VAR:-} 常见情况）时也回退到默认值，而非取到空串。
JWKS_URL = os.getenv("PORTAL_JWKS_URL") or "https://api.job.joyhouse.chat/.well-known/jwks.json"
JWT_ISSUER = os.getenv("JWT_ISSUER") or "https://api.job.joyhouse.chat"
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE") or "job-portal"
_JWKS_TIMEOUT = 8

_keys: dict[str, object] = {}   # kid -> RSA 公钥对象


class AuthError(Exception):
    """token 缺失 / 验签失败 / 过期。"""


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


def verify_token(token: str) -> dict:
    """验签 + 校 iss/aud/exp/typ。成功返回 claims；失败抛 AuthError。

    同步函数 —— 验签是纯 CPU + 偶发一次 JWKS 重拉，调用方应用
    `asyncio.to_thread(verify_token, ...)` 包裹，避免阻塞事件循环。
    """
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
