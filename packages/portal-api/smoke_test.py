"""不依赖真实 DB 的冒烟测试。

只验证不需要 DB 的 3 条路径：
- GET /
- GET /.well-known/jwks.json
- POST /auth/register 的入参校验（Pydantic 422 → 400 invalid_input）

完整端到端（注册→验证码→登录→refresh）需要真 Postgres，参考 README §curl 一节手测。
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

# 必填 env 先填一个能 import 的占位
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@127.0.0.1/x")

# 替换 lifespan：不连 DB，只 init JWT 密钥
import jwt_service  # noqa: E402


@asynccontextmanager
async def _no_db_lifespan(app):
    jwt_service.init_keys()
    yield


import server  # noqa: E402

server.app.router.lifespan_context = _no_db_lifespan

from fastapi.testclient import TestClient  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, hint: str = "") -> None:
    mark = "✓" if cond else "✗"
    line = f"{mark} {name}"
    if hint:
        line += f"  ({hint})"
    print(line)
    if not cond:
        failures.append(name)


with TestClient(server.app, raise_server_exceptions=False) as c:
    # 1) /
    r = c.get("/")
    check("GET /", r.status_code == 200 and "endpoints" in r.json(), str(r.status_code))

    # 2) /.well-known/jwks.json
    r = c.get("/.well-known/jwks.json")
    j = r.json()
    keys = j.get("keys", [])
    check(
        "JWKS doc",
        r.status_code == 200 and len(keys) == 1 and keys[0]["kty"] == "RSA",
        f"kid={keys[0].get('kid') if keys else 'none'}",
    )
    check(
        "JWKS has n + e + kid + alg + use",
        bool(keys) and all(k in keys[0] for k in ("n", "e", "kid", "alg", "use")),
    )

    # 3) /auth/register 参数校验
    r = c.post("/auth/register", json={"email": "bad", "password": "short"})
    check("Pydantic 校验 → invalid_input", r.status_code == 400 and r.json().get("error") == "invalid_input", str(r.status_code))

    r = c.post("/auth/register", json={"email": "ok@test.com", "password": "letters_only"})
    body = r.json()
    check("密码缺数字时拒绝", r.status_code == 400 and body.get("error") == "invalid_input", str(body))

    # 4) /auth/me 无 token → 403 (HTTPBearer auto_error)
    r = c.get("/auth/me")
    check("无 Bearer 拒访", r.status_code == 403, str(r.status_code))

    # 5) /auth/me 假 token → 401 invalid_token
    r = c.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    body = r.json()
    check("无效 token → invalid_token", r.status_code == 401 and body.get("error") == "invalid_token", str(body))

    # 6) 自签一个 token，能过 /auth/me 的 JWT 解码（即便 DB 查不到 user）
    tok, _ = jwt_service.sign_access_token(sub="fake-user", email="fake@t.com")
    r = c.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
    # /auth/me 会去查 DB，DB 没起 → 走 RuntimeError → 500
    # 但 JWT 校验过了；这条仅用来验签解码路径。返回 != 401 即 JWT 通过。
    check("自签 token 通过 JWT 验证", r.status_code != 401, f"got {r.status_code}")

print()
if failures:
    print(f"FAIL: {len(failures)} case(s):", failures)
    sys.exit(1)
print("ALL_PASS")
