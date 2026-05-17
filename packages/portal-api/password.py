"""bcrypt 包装。

为什么 cost=12：在 2026 年硬件上单次 ~150ms，对登录端足够慢、对单机不会被打爆。
"""
from __future__ import annotations

import bcrypt

BCRYPT_COST = 12
# bcrypt 内部限制：超过 72 字节会被截断。提前拒绝避免误以为长密码更安全。
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")
    if len(pw) > MAX_PASSWORD_BYTES:
        raise ValueError("password_too_long")
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=BCRYPT_COST)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        pw = password.encode("utf-8")
        if len(pw) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
