"""集中读取环境变量。模块级常量，启动时一次读完。

为什么不延迟读：开发期排错优先 fail-fast；缺关键变量直接启动失败比运行时报错好定位。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 启动时加载 .env / .env.local（如果存在）。生产环境 systemd unit 直接注入 env 也支持。
_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env.local")
load_dotenv(_HERE / ".env")


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"必填环境变量 {name} 未设置；参考 .env.example")
    return v


def _opt(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _opt_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


# ── DB ────────────────────────────────────────────────────────────────────
# 默认本地 Postgres（与 job-api-gateway / job-agent-gateway 同一约定），
# 本地启动开箱即用；生产用 .env 覆盖。
DATABASE_URL = _opt(
    "DATABASE_URL", "postgresql://postgres:password@127.0.0.1:5432/smart_job"
)
DB_SCHEMA = _opt("DB_SCHEMA", "identity")
DB_POOL_MIN = _opt_int("DB_POOL_MIN", 2)
DB_POOL_MAX = _opt_int("DB_POOL_MAX", 10)

# ── HTTP ─────────────────────────────────────────────────────────────────
PORT = _opt_int("PORT", 8771)
APP_ENV = _opt("APP_ENV", "development")
IS_PRODUCTION = APP_ENV == "production"

_cors_raw = _opt("CORS_ORIGINS", "")
CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]

# ── JWT ──────────────────────────────────────────────────────────────────
JWT_PRIVATE_KEY_PATH = Path(
    _opt("JWT_PRIVATE_KEY_PATH", str(_HERE / "keys" / "jwt-private.pem"))
).expanduser()
JWT_KEY_ID = _opt("JWT_KEY_ID", "")  # 空时启动后从公钥派生
JWT_ISSUER = _opt("JWT_ISSUER", "http://localhost:8771")
JWT_AUDIENCE = _opt("JWT_AUDIENCE", "job-portal")
ACCESS_TTL_MIN = _opt_int("ACCESS_TTL_MIN", 15)
REFRESH_TTL_DAYS = _opt_int("REFRESH_TTL_DAYS", 30)

# ── Resend ───────────────────────────────────────────────────────────────
RESEND_API_KEY = _opt("RESEND_API_KEY", "")
RESEND_FROM = _opt("RESEND_FROM", "DingQ <onboarding@resend.dev>")

# ── 验证码策略（业务常量，可被 env 覆盖）──────────────────────────────────
CODE_TTL_MINUTES = _opt_int("CODE_TTL_MINUTES", 10)
CODE_MAX_ATTEMPTS = _opt_int("CODE_MAX_ATTEMPTS", 5)
CODE_RESEND_COOLDOWN_SECONDS = _opt_int("CODE_RESEND_COOLDOWN_SECONDS", 60)

# ── 默认账号播种(开发态)──────────────────────────────────────────────────
# 让浏览器扩展免注册即可登录,且 Claude Code 直连 job-api-gateway MCP 时用同一个
# 已知身份 —— MCP 的 X-User-Id 头与扩展 WS 的 ?user_id= 都对上这个固定 id。
# 弱口令,生产环境(APP_ENV=production)默认关闭;可用 SEED_DEFAULT_USER 显式覆盖。
SEED_DEFAULT_USER = _opt("SEED_DEFAULT_USER", "0" if IS_PRODUCTION else "1") == "1"
DEFAULT_USER_ID = _opt("DEFAULT_USER_ID", "smartjob")
DEFAULT_USER_EMAIL = _opt("DEFAULT_USER_EMAIL", "smartjob@joyhouselabs.com")  # 勿用 .local/.test 等保留域:Pydantic EmailStr 会拒
DEFAULT_USER_PASSWORD = _opt("DEFAULT_USER_PASSWORD", "123456")

# ── 版本号（用于 /health 和 JWT 元信息）────────────────────────────────────
SERVICE_NAME = "job-portal-api"
VERSION = "0.1.0"
