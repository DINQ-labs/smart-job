"""Admin 端点：用户管理 / 身份管理 / 审计日志.

认证方式：
1. JWT token（普通用户通过 /auth/login 获取）
2. Cookie admin_token（管理后台通过 /admin/login 获取，与 job-api-gateway 共享）
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import config
import db
import jwt_service

log = logging.getLogger("job-portal-api.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

# 与 job-api-gateway 共享的 admin token 存储（生产环境应使用 Redis）
_admin_tokens: set = set()


async def _get_admin_from_cookie(request: Request) -> str | None:
    """从 Cookie 获取 admin token（与 job-api-gateway 共享）。"""
    token = request.cookies.get("admin_token")
    if token and token in _admin_tokens:
        return token
    return None


async def _require_admin(request: Request) -> tuple[str, bool]:
    """
    验证 admin 身份，返回 (admin_id, is_super_admin)。
    支持两种认证方式：
    1. JWT token（普通 Portal 用户，需要 staff/admin 角色）
    2. Cookie admin_token（管理后台专用）
    """
    # 方式 1：Cookie admin_token（管理后台专用）
    cookie_token = await _get_admin_from_cookie(request)
    if cookie_token:
        return "admin", True

    # 方式 2：JWT token
    creds: HTTPAuthorizationCredentials | None = await HTTPBearer(auto_error=False)(request)
    if creds:
        try:
            payload = jwt_service.verify_access_token(creds.credentials)
            if payload.get("typ") == "access":
                user_id = payload.get("sub")
                if user_id:
                    # 检查用户角色
                    row = await db.get_user_by_id(user_id)
                    if row and row["role"] in ("staff", "admin"):
                        is_super = row["role"] == "admin"
                        return user_id, is_super
        except Exception:
            pass

    # ADMIN_PASSWORD 禁用时放行（开发环境）
    if not config._opt("ADMIN_PASSWORD", ""):
        return "admin", True

    raise HTTPException(401, detail={"error": "Not authenticated"})


def _user_out(row: Any) -> dict[str, Any]:
    """序列化用户信息（含 identities_count）。"""
    return {
        "id": row["id"],
        "email": row["email"],
        "email_verified_at": row["email_verified_at"].isoformat() if row["email_verified_at"] else None,
        "phone": row["phone"],
        "phone_verified_at": row["phone_verified_at"].isoformat() if row["phone_verified_at"] else None,
        "name": row["name"],
        "image": row["image"],
        "locale": row["locale"],
        "role": row["role"],
        "disabled_at": row["disabled_at"].isoformat() if row["disabled_at"] else None,
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
        "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
        "metadata": row["metadata"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "identities_count": row.get("identities_count", 0),
    }


# ──────────────────────────────────────────────────────────────────────────
# 用户管理
# ──────────────────────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    request: Request,
    q: str | None = None,
    role: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """列出用户（支持搜索 / 角色过滤）。"""
    await _require_admin(request)
    rows, total = await db.list_users(q=q, role=role, limit=limit, offset=offset)
    return {
        "users": [_user_out(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
) -> dict[str, Any]:
    """获取用户详情。"""
    await _require_admin(request)
    row = await db.get_user_by_id(user_id)
    if not row:
        raise HTTPException(404, detail={"error": "user_not_found"})
    return _user_out(row)


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    role: str,
    request: Request,
) -> dict[str, Any]:
    """修改用户角色（仅 admin 可以修改为 admin）。"""
    admin_id, is_super = await _require_admin(request)

    if not is_super and role == "admin":
        raise HTTPException(403, detail={"error": "only_admin_can_grant_admin"})

    if role not in ("user", "staff", "admin"):
        raise HTTPException(400, detail={"error": "invalid_role"})

    await db.set_user_role(user_id, role)
    await db.log_auth_event(
        user_id=user_id,
        event_type="role_changed",
        metadata={"new_role": role, "changed_by": admin_id},
    )
    return {"ok": True}


@router.patch("/users/{user_id}/disabled")
async def set_user_disabled(
    user_id: str,
    disabled: bool,
    request: Request,
) -> dict[str, Any]:
    """启用/禁用用户。"""
    admin_id, _ = await _require_admin(request)

    await db.set_user_disabled(user_id, disabled)
    await db.log_auth_event(
        user_id=user_id,
        event_type="user_disabled" if disabled else "user_enabled",
        metadata={"disabled": disabled, "changed_by": admin_id},
    )
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
# 身份管理（auth_identities）
# ──────────────────────────────────────────────────────────────────────────


@router.get("/users/{user_id}/identities")
async def list_user_identities(
    user_id: str,
    request: Request,
) -> dict[str, Any]:
    """列出用户的所有登录身份。"""
    await _require_admin(request)
    identities = await db.list_user_identities(user_id)
    return {
        "identities": [db.identity_to_dict(row) for row in identities],
    }


@router.delete("/users/{user_id}/identities/{identity_id}")
async def unlink_identity(
    user_id: str,
    identity_id: str,
    request: Request,
) -> dict[str, Any]:
    """解绑登录身份。"""
    admin_id, _ = await _require_admin(request)

    ok = await db.unlink_identity(identity_id, user_id)
    if not ok:
        raise HTTPException(400, detail={"error": "cannot_unlink_last_identity"})
    await db.log_auth_event(
        user_id=user_id,
        event_type="identity_unlinked",
        metadata={"identity_id": identity_id, "unlinked_by": admin_id},
    )
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
# 审计日志（auth_events）
# ──────────────────────────────────────────────────────────────────────────


@router.get("/users/{user_id}/events")
async def list_user_events(
    request: Request,
    user_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    """列出用户审计日志。"""
    await _require_admin(request)
    events = await db.list_user_events(user_id, limit=limit)
    return {
        "events": [db.event_to_dict(row) for row in events],
    }


# ──────────────────────────────────────────────────────────────────────────
# Refresh Tokens 管理
# ──────────────────────────────────────────────────────────────────────────


@router.get("/users/{user_id}/tokens")
async def list_user_tokens(
    user_id: str,
    request: Request,
) -> dict[str, Any]:
    """列出用户的有效 refresh token（用于设备管理）。"""
    await _require_admin(request)

    sql = f"""
    SELECT * FROM {db._tbl('refresh_tokens')}
    WHERE user_id = $1 AND revoked_at IS NULL AND expires_at > NOW()
    ORDER BY created_at DESC
    """
    rows = []
    async with db.get_pool().acquire() as conn:
        rows = await conn.fetch(sql, user_id)

    tokens = []
    for row in rows:
        tokens.append({
            "id": row["id"],
            "user_id": row["user_id"],
            "expires_at": row["expires_at"].isoformat(),
            "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
            "replaced_by": row["replaced_by"],
            "user_agent": row["user_agent"],
            "ip_hash": row["ip_hash"],
            "client": row["client"],
            "device_id": row["device_id"],
            "device_name": row["device_name"],
            "last_seen_at": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
            "created_at": row["created_at"].isoformat(),
        })
    return {"tokens": tokens}


@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    request: Request,
) -> dict[str, Any]:
    """撤销指定 refresh token（强制下线设备）。"""
    await _require_admin(request)
    await db.revoke_refresh_token(token_id)
    return {"ok": True}


@router.delete("/users/{user_id}/tokens")
async def revoke_all_user_tokens(
    user_id: str,
    request: Request,
) -> dict[str, Any]:
    """撤销用户所有 refresh token（强制下线所有设备）。"""
    admin_id, _ = await _require_admin(request)

    await db.revoke_all_user_tokens(user_id)
    await db.log_auth_event(
        user_id=user_id,
        event_type="all_tokens_revoked",
        metadata={"revoked_by": admin_id},
    )
    return {"ok": True, "revoked": "all"}
