"""RS256 JWT 签发 + 校验 + JWKS 公钥发布。

设计点：
- 启动时从 `config.JWT_PRIVATE_KEY_PATH` 读 PEM；不存在则生成 RSA-2048 并落盘（0600）。
- kid 默认从公钥 SHA-256 派生前 12 位（可被 env 覆盖）。
- 公开两个 API：`sign_access_token(sub, **claims)` / `verify_access_token(token) -> claims`。
- JWKS 输出符合 RFC 7517：{ keys: [{kty, kid, use, alg, n, e}] }。

为什么不用对称 HS256：网关需要独立验签，公开公钥是最自然的解耦方式；同 DB 也能省一次 RPC。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
)

import config

log = logging.getLogger("job-portal-api.jwt")


@dataclass(frozen=True)
class KeyMaterial:
    private_pem: bytes
    public_pem: bytes
    private_key: RSAPrivateKey
    public_key: RSAPublicKey
    kid: str


_keys: KeyMaterial | None = None


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _derive_kid(public_pem: bytes) -> str:
    if config.JWT_KEY_ID:
        return config.JWT_KEY_ID
    return hashlib.sha256(public_pem).hexdigest()[:12]


def _load_or_generate() -> KeyMaterial:
    path = config.JWT_PRIVATE_KEY_PATH
    if path.exists():
        with open(path, "rb") as f:
            private_pem = f.read()
        private_key = serialization.load_pem_private_key(
            private_pem, password=None, backend=default_backend()
        )
        if not isinstance(private_key, RSAPrivateKey):
            raise RuntimeError(f"{path}: 不是 RSA 私钥")
        log.info("loaded existing JWT private key from %s", path)
    else:
        if config.IS_PRODUCTION:
            raise RuntimeError(
                f"生产环境拒绝自动生成密钥；请预置 {path}（chmod 600）"
            )
        log.warning("no JWT key at %s — generating RSA-2048 (dev only)", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with open(path, "wb") as f:
            f.write(private_pem)
        os.chmod(path, 0o600)

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    kid = _derive_kid(public_pem)
    return KeyMaterial(
        private_pem=private_pem,
        public_pem=public_pem,
        private_key=private_key,
        public_key=public_key,
        kid=kid,
    )


def init_keys() -> None:
    global _keys
    _keys = _load_or_generate()
    log.info("JWT kid=%s", _keys.kid)


def _km() -> KeyMaterial:
    if _keys is None:
        raise RuntimeError("JWT keys not initialized")
    return _keys


# ──────────────────────────────────────────────────────────────────────────
# 签发
# ──────────────────────────────────────────────────────────────────────────


def sign_access_token(*, sub: str, email: str, extra: dict[str, Any] | None = None) -> tuple[str, int]:
    """返回 (jwt_string, expiry_unix_seconds)。"""
    km = _km()
    now = int(time.time())
    exp = now + config.ACCESS_TTL_MIN * 60
    payload: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "iss": config.JWT_ISSUER,
        "aud": config.JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": exp,
        "jti": secrets.token_urlsafe(12),
        "typ": "access",
    }
    if extra:
        payload.update(extra)
    token = pyjwt.encode(
        payload,
        km.private_pem,
        algorithm="RS256",
        headers={"kid": km.kid, "typ": "JWT"},
    )
    return token, exp


# ──────────────────────────────────────────────────────────────────────────
# 校验
# ──────────────────────────────────────────────────────────────────────────


def verify_access_token(token: str) -> dict[str, Any]:
    """验签 + 校验 iss/aud/exp/nbf。失败抛 pyjwt.PyJWTError 子类。"""
    km = _km()
    return pyjwt.decode(
        token,
        km.public_pem,
        algorithms=["RS256"],
        audience=config.JWT_AUDIENCE,
        issuer=config.JWT_ISSUER,
        options={"require": ["exp", "iat", "sub"]},
    )


# ──────────────────────────────────────────────────────────────────────────
# JWKS（gateway 拉这个验签）
# ──────────────────────────────────────────────────────────────────────────


def jwks_document() -> dict[str, Any]:
    km = _km()
    public_numbers = km.public_key.public_numbers()
    # 把 modulus / exponent 编成 base64url 无填充（RFC 7517）
    n_bytes = public_numbers.n.to_bytes(
        (public_numbers.n.bit_length() + 7) // 8, "big"
    )
    e_bytes = public_numbers.e.to_bytes(
        (public_numbers.e.bit_length() + 7) // 8, "big"
    )
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": km.kid,
                "use": "sig",
                "alg": "RS256",
                "n": _b64url(n_bytes),
                "e": _b64url(e_bytes),
            }
        ]
    }


# ──────────────────────────────────────────────────────────────────────────
# Refresh token：不是 JWT，是不透明随机字符串 + 服务端哈希存储
# ──────────────────────────────────────────────────────────────────────────


def new_refresh_token() -> tuple[str, str]:
    """返回 (plain_token, sha256_hex)。plain 发给客户端，hex 入库做唯一索引。"""
    plain = secrets.token_urlsafe(48)
    digest = hashlib.sha256(plain.encode("ascii")).hexdigest()
    return plain, digest


def hash_refresh_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("ascii")).hexdigest()
