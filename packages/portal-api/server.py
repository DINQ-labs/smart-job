"""FastAPI 入口。

启动顺序（lifespan）：
1) 加载/生成 JWT 密钥
2) 建 asyncpg pool + CREATE SCHEMA / TABLE IF NOT EXISTS
3) 开启 HTTP 监听
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import admin_router
import auth_router
import config
import db
import jwt_service
import oauth_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("job-portal-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting %s v%s on port %d (env=%s)", config.SERVICE_NAME, config.VERSION, config.PORT, config.APP_ENV)
    jwt_service.init_keys()
    await db.init_pool()
    if config.SEED_DEFAULT_USER:
        try:
            await db.seed_default_user()
        except Exception:
            log.exception("默认账号播种失败(不阻断启动)")
    if config.DEV_FIXED_CODE:
        log.info("开发态固定验证码已启用: %s —— 注册/找回密码统一用此码(生产环境自动禁用)",
                 config.DEV_FIXED_CODE)
    log.info("ready")
    try:
        yield
    finally:
        await db.close_pool()
        log.info("stopped")


app = FastAPI(
    title=config.SERVICE_NAME,
    version=config.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if not config.IS_PRODUCTION else None,
    redoc_url=None,
    openapi_url="/openapi.json" if not config.IS_PRODUCTION else None,
)


# ── CORS ────────────────────────────────────────────────────────────────
# 扩展场景：origin 形如 `chrome-extension://abcd1234...`
# 配置 origins 列表时显式写；开发模式允许通配。
if config.CORS_ORIGINS:
    cors_origins = config.CORS_ORIGINS
    cors_regex = None
else:
    cors_origins = []
    # 开发：所有 chrome-extension 起源 + localhost 任意端口 + 127.0.0.1 + 主站
    cors_regex = (
        r"^(chrome-extension://[a-z0-9]+|"
        r"https?://localhost(:\d+)?|"
        r"https?://127\.0\.0\.1(:\d+)?|"
        r"https://(.*\.)?joyhouse\.chat)$"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=cors_regex,
    allow_credentials=False,  # 扩展用 Bearer，不用 cookie
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


# ── 错误处理 ───────────────────────────────────────────────────────────────
# 统一错误形状：{ error: "<key>", ... }，避免 FastAPI 默认 { detail: ... } 嵌套。


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    issues = [
        {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "invalid_input", "issues": issues},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # 我们在路由里 raise HTTPException(status, detail={"error": "xxx", ...})；
    # 这里把 detail 直接当 body 返回（不再 {"detail": {...}}）。
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code, content={"error": str(exc.detail)}
    )


# ── 路由 ─────────────────────────────────────────────────────────────────

app.include_router(auth_router.router)
app.include_router(oauth_router.router)
app.include_router(admin_router.router)


@app.get("/.well-known/jwks.json")
async def jwks() -> dict[str, Any]:
    """两个 gateway 拉这个验签。建议它们缓存 10 分钟。"""
    return jwt_service.jwks_document()


@app.get("/health")
async def health() -> dict[str, Any]:
    """健康检查：尝试 SELECT 1 验证 DB 连通。"""
    db_ok = False
    try:
        async with db.get_pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "ok": db_ok,
        "service": config.SERVICE_NAME,
        "version": config.VERSION,
        "env": config.APP_ENV,
        "db": "up" if db_ok else "down",
    }


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": config.SERVICE_NAME,
        "version": config.VERSION,
        "endpoints": [
            "POST /auth/register",
            "POST /auth/verify-email",
            "POST /auth/resend-code",
            "POST /auth/login",
            "POST /auth/refresh",
            "POST /auth/logout",
            "GET  /auth/me",
            "POST /auth/password-reset",
            "POST /auth/password-reset/verify",
            "POST /auth/password-reset/confirm",
            "PATCH /auth/me",
            "POST /auth/me/change-password",
            "POST /auth/me/delete",
            "POST /auth/me/delete/confirm",
            "GET  /auth/oauth/{provider}/auth-url",
            "GET  /auth/oauth/{provider}/callback",
            "GET  /admin/users",
            "GET  /admin/users/{id}",
            "PATCH /admin/users/{id}/role",
            "PATCH /admin/users/{id}/disabled",
            "GET  /admin/users/{id}/identities",
            "DELETE /admin/users/{id}/identities/{identity_id}",
            "GET  /admin/users/{id}/events",
            "GET  /admin/users/{id}/tokens",
            "DELETE /admin/tokens/{id}",
            "DELETE /admin/users/{id}/tokens",
            "GET  /.well-known/jwks.json",
            "GET  /health",
        ],
    }
