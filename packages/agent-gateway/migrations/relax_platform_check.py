"""relax_platform_check.py — Phase 2D one-shot migration

把 agent_conv_sessions / agent_conv_events 的 platform CHECK 约束
从 enum-style `IN ('boss','linkedin','indeed')` 改成 `length(platform) > 0`。

Why: 加新平台(如 51job / Liepin)不需要 ALTER TABLE,直接用 onboarding 写入新值即可。
输入由前端 onboarding overlay + 后端 platforms_config.PLATFORMS 严格控制,DB 不需要再做
enum 兜底(且 enum 反而会让加平台时 SQL INSERT 失败,静默挂掉)。

Idempotent:每次跑都安全(找到旧约束就 DROP 再 ADD;找不到旧约束就只 ADD;若新约束已存在则跳过)。

Usage:
    cd /opt/job-agent-gateway && venv/bin/python3 migrations/relax_platform_check.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


_TABLES = ("agent_conv_sessions", "agent_conv_events")


async def find_platform_check(pool, table: str) -> str | None:
    """返回 platform 列上 enum-style CHECK 约束的名字(如有);否则 None。"""
    rows = await pool.fetch(
        """SELECT con.conname, pg_get_constraintdef(con.oid) AS def
           FROM pg_constraint con
           JOIN pg_class rel ON rel.oid = con.conrelid
           WHERE rel.relname = $1 AND con.contype = 'c'""",
        table,
    )
    for r in rows:
        d = r["def"] or ""
        # 匹配旧的 enum CHECK(包含 platform IN ('boss', 'linkedin', 'indeed'))
        if "platform" in d.lower() and "boss" in d.lower() and " in " in d.lower():
            return r["conname"]
    return None


async def has_relaxed_check(pool, table: str) -> bool:
    rows = await pool.fetch(
        """SELECT pg_get_constraintdef(con.oid) AS def
           FROM pg_constraint con
           JOIN pg_class rel ON rel.oid = con.conrelid
           WHERE rel.relname = $1 AND con.contype = 'c'""",
        table,
    )
    for r in rows:
        d = (r["def"] or "").lower()
        if "platform" in d and "length(platform)" in d:
            return True
    return False


async def migrate():
    pool = await db._get_pool()
    for table in _TABLES:
        old_name = await find_platform_check(pool, table)
        if await has_relaxed_check(pool, table):
            log.info("[%s] 已有 length(platform)>0 CHECK,跳过", table)
        else:
            new_constraint_name = f"{table}_platform_nonempty_chk"
            await pool.execute(
                f"ALTER TABLE {table} "
                f"ADD CONSTRAINT {new_constraint_name} CHECK (length(platform) > 0)"
            )
            log.info("[%s] 已加 %s", table, new_constraint_name)
        if old_name:
            await pool.execute(f"ALTER TABLE {table} DROP CONSTRAINT {old_name}")
            log.info("[%s] 已删旧 enum CHECK %s", table, old_name)
        else:
            log.info("[%s] 无旧 enum CHECK,无需 DROP", table)


if __name__ == "__main__":
    asyncio.run(migrate())
    log.info("migration done")
