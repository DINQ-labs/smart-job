"""tasks/steps/sprint2.py — Sprint 2 新增 step 集合。

为 3 个新模板提供步骤实现，按惯例后续可重组到 tasks/steps/{boss,linkedin,indeed}.py。
集中放此文件便于一次性 review；不破坏既有 step 文件。

新 step:
    # C3: batch_apply
    step_apply_boss          — 通过 boss_start_chat + boss_send_message 完成投递（Boss 没直接 apply）
    step_apply_linkedin      — 优先 linkedin_check_applied 跳过已投，再 linkedin_apply_job
    step_apply_indeed        — 同上，indeed_check_applied + indeed_apply_job
    step_summarize_applied   — 投递结果报告

    # C4: track_applications
    step_list_apps_indeed    — indeed_list_applied_jobs（Indeed 唯一有该 API 的平台）
    step_list_apps_boss      — Boss/LinkedIn fallback：返回空 + 占位说明
    step_summarize_tracked   — 投递追踪报告

    # C5: bulk_analyze_candidates
    step_fetch_candidate_detail   — iter：拉单个 candidate 详情
    step_evaluate_candidate       — iter：基于 detail 给 match_score（用启发式 + 后续可接 LLM）
    step_summarize_candidate_analysis — 候选人分析报告
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from tasks.steps.common import ensure_mcp, throttled_iter

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# C3: batch_apply 步骤
# ════════════════════════════════════════════════════════════════════════════

async def step_apply_boss(_task, ctx: dict) -> dict:
    """Boss 投递 = 主动发招呼。对 ctx['_item'] 调 boss_start_chat + boss_send_message。

    iter step，由 engine 逐项触发。失败的项 raise → engine 走 retry/skip 策略。"""
    item = ctx["_item"]
    job_id = item.get("encryptJobId") or item.get("jobId") or item.get("id") or ""
    if not job_id:
        return {}
    security_id = item.get("securityId") or item.get("listSecurityId") or ""
    mcp = await ensure_mcp(ctx)

    # 1. 启动会话
    chat_resp = await mcp.call(
        "boss_start_chat",
        {"encrypt_job_id": job_id, "security_id": security_id},
    )
    encrypt_boss_id = (chat_resp.get("encryptBossId") or chat_resp.get("encrypt_user_id")
                       or chat_resp.get("encrypt_uid") or "")
    if not encrypt_boss_id:
        log.info("[boss:apply] job=%s start_chat 失败，跳过", job_id)
        return {"_skip": True, "reason": "no_chat_target"}

    # 2. 发招呼语（PRD 后续可接 agent-gw modes/apply.py 生成的 LLM 招呼文案，MVP 用模板）
    greeting = ctx.get("apply_greeting") or ctx.get("inputs", {}).get("greeting") or _default_boss_greeting(item)
    send_resp = await mcp.call(
        "boss_send_message",
        {"encrypt_boss_id": encrypt_boss_id, "encrypt_job_id": job_id, "content": greeting},
    )

    applied = ctx.setdefault("applied", [])
    applied.append({
        "item": item,
        "job_id": job_id,
        "platform": "boss",
        "status": "sent" if send_resp.get("ok", True) else "failed",
        "method": "chat",
        "match_score": item.get("match_score"),
    })
    return {"applied": applied}


def _default_boss_greeting(item: dict) -> str:
    """无 LLM 时的兜底招呼语。后续 modes/apply.py 集成后替换。"""
    job_name = item.get("jobName") or item.get("title") or "贵司职位"
    return f"您好！我对「{job_name}」很感兴趣，希望进一步沟通，期待您的回复。"


async def step_apply_linkedin(_task, ctx: dict) -> dict:
    """LinkedIn 投递。优先用 linkedin_check_applied 跳过已投，再 linkedin_apply_job。"""
    item = ctx["_item"]
    job_id = item.get("jobId") or item.get("entityUrn") or item.get("id") or ""
    if not job_id:
        return {}
    mcp = await ensure_mcp(ctx)

    try:
        check = await mcp.call("linkedin_check_applied", {"job_id": job_id})
        if isinstance(check, dict) and check.get("applied"):
            applied = ctx.setdefault("applied", [])
            applied.append({"item": item, "job_id": job_id, "platform": "linkedin",
                            "status": "already_applied", "match_score": item.get("match_score")})
            return {"_skip": True, "reason": "already_applied"}
    except Exception as e:
        log.debug("[linkedin:apply] check_applied failed (continuing): %s", e)

    resp = await mcp.call("linkedin_apply_job", {"job_id": job_id, "profile_data": ""})
    success = isinstance(resp, dict) and (resp.get("ok") or resp.get("applied"))
    applied = ctx.setdefault("applied", [])
    applied.append({
        "item": item,
        "job_id": job_id,
        "platform": "linkedin",
        "status": "sent" if success else "failed",
        "method": "easy_apply",
        "match_score": item.get("match_score"),
        "unresolved_fields": resp.get("unresolved_fields") if isinstance(resp, dict) else None,
    })
    return {"applied": applied}


async def step_apply_indeed(_task, ctx: dict) -> dict:
    """Indeed 投递。indeed_check_applied → indeed_apply_job。"""
    item = ctx["_item"]
    job_id = item.get("jk") or item.get("jobkey") or item.get("id") or ""
    if not job_id:
        return {}
    mcp = await ensure_mcp(ctx)

    try:
        check = await mcp.call("indeed_check_applied", {"job_id": job_id})
        if isinstance(check, dict) and check.get("applied"):
            applied = ctx.setdefault("applied", [])
            applied.append({"item": item, "job_id": job_id, "platform": "indeed",
                            "status": "already_applied", "match_score": item.get("match_score")})
            return {"_skip": True, "reason": "already_applied"}
    except Exception as e:
        log.debug("[indeed:apply] check_applied failed (continuing): %s", e)

    resp = await mcp.call("indeed_apply_job", {"job_id": job_id, "profile_data": ""})
    success = isinstance(resp, dict) and (resp.get("ok") or resp.get("applied"))
    applied = ctx.setdefault("applied", [])
    applied.append({
        "item": item,
        "job_id": job_id,
        "platform": "indeed",
        "status": "sent" if success else "failed",
        "method": "quick_apply",
        "match_score": item.get("match_score"),
    })
    return {"applied": applied}


async def step_summarize_applied(_task, ctx: dict) -> dict:
    """投递结果汇总 → result_summary + artifacts.applied。"""
    applied: list[dict] = ctx.get("applied") or []
    sent = sum(1 for a in applied if a.get("status") == "sent")
    already = sum(1 for a in applied if a.get("status") == "already_applied")
    failed = sum(1 for a in applied if a.get("status") == "failed")

    lines = [
        f"## 投递完成 ✅",
        f"",
        f"- ✅ 成功投递：**{sent}** 个",
        f"- ⚪ 已投过自动跳过：**{already}** 个",
        f"- ❌ 失败：**{failed}** 个",
        f"",
        f"投递总数：{len(applied)}",
    ]
    if failed:
        lines.append("\n> 失败项可在任务详情查看具体原因（可能是风控/表单字段缺失）。")

    artifacts = ctx.setdefault("_artifacts", {})
    artifacts["applied"] = applied
    artifacts["counts"] = {"sent": sent, "already_applied": already, "failed": failed}
    summary = "\n".join(lines)
    ctx["result_summary"] = summary
    return {"result_summary": summary}


# ════════════════════════════════════════════════════════════════════════════
# C4: track_applications 步骤
# ════════════════════════════════════════════════════════════════════════════

async def step_list_apps_indeed(_task, ctx: dict) -> dict:
    """Indeed 调 indeed_list_applied_jobs 拿历史投递。"""
    mcp = await ensure_mcp(ctx)
    limit = int(ctx.get("inputs", {}).get("limit") or 50)
    resp = await mcp.call("indeed_list_applied_jobs", {"limit": limit})
    rows = []
    if isinstance(resp, dict):
        rows = resp.get("items") or resp.get("applications") or resp.get("rows") or []
    log.info("[indeed:list_applications] → %d apps", len(rows))
    return {"items": rows, "applications": rows}


async def step_list_apps_boss(_task, ctx: dict) -> dict:
    """Boss 暂无 list_my_applications API（Sprint 后续接入）。
    fallback：列空 + 在 result_summary 加说明。"""
    log.info("[boss:list_applications] Boss API 暂未支持，返回空列表")
    return {"items": [], "applications": [], "_unsupported": True}


async def step_list_apps_linkedin(_task, ctx: dict) -> dict:
    """LinkedIn 暂无直接列表 API；可未来用 GraphQL 拉。当前返回空。"""
    log.info("[linkedin:list_applications] LinkedIn API 暂未支持，返回空列表")
    return {"items": [], "applications": [], "_unsupported": True}


async def step_summarize_tracked(_task, ctx: dict) -> dict:
    """投递追踪报告。按 status 分桶（sent/read/responded/interviewing/rejected）。"""
    apps = ctx.get("applications") or []
    unsupported = ctx.get("_unsupported", False)
    buckets: dict[str, int] = {}
    for a in apps:
        s = (a.get("status") or "unknown").lower()
        # 归一化 Indeed status → PRD 5 态
        if s in ("submitted", "sent"):
            s = "sent"
        elif s in ("read", "viewed"):
            s = "read"
        elif s in ("responded", "messaged"):
            s = "responded"
        elif s in ("interview", "interviewing", "phone_screen"):
            s = "interviewing"
        elif s in ("rejected", "declined"):
            s = "rejected"
        buckets[s] = buckets.get(s, 0) + 1

    lines = [
        f"## 投递进度追踪 📊",
        "",
        f"投递总数：**{len(apps)}**",
        "",
    ]
    for label, count in sorted(buckets.items(), key=lambda x: -x[1]):
        lines.append(f"- {label}: **{count}**")
    if unsupported:
        lines.append("")
        lines.append("> 注：当前平台未提供「我的投递」API，仅 Indeed 可追踪到完整历史。")

    artifacts = ctx.setdefault("_artifacts", {})
    artifacts["applications"] = apps
    artifacts["status_counts"] = buckets
    summary = "\n".join(lines)
    ctx["result_summary"] = summary
    return {"result_summary": summary}


# ════════════════════════════════════════════════════════════════════════════
# C5: bulk_analyze_candidates 步骤
# ════════════════════════════════════════════════════════════════════════════

async def step_fetch_candidate_detail(_task, ctx: dict) -> dict:
    """对 ctx['_item']（来自 step_search_candidates）拉详情。

    平台路由：boss → boss_get_candidate_detail；linkedin → linkedin_get_profile；
    indeed → step 暂不实现（Indeed Employer Resume DB 模式不太一样）。"""
    item = ctx["_item"]
    platform = ctx.get("platform", "boss")
    mcp = await ensure_mcp(ctx)
    detail = {}
    try:
        if platform == "boss":
            security_id = item.get("securityId") or ""
            encrypt_uid = item.get("encryptUid") or item.get("uid") or ""
            if security_id:
                detail = await mcp.call(
                    "boss_get_candidate_detail",
                    {"security_id": security_id, "encrypt_uid": encrypt_uid},
                )
        elif platform == "linkedin":
            public_id = item.get("publicId") or item.get("public_id") or ""
            if public_id:
                detail = await mcp.call("linkedin_get_profile", {"public_id": public_id})
    except Exception as e:
        log.warning("[%s:candidate_detail] fetch failed: %s", platform, e)

    details = ctx.setdefault("details", [])
    details.append({"item": item, "detail": detail or {}})
    return {"details": details}


async def step_evaluate_candidate(_task, ctx: dict) -> dict:
    """对 ctx['_item'] 给一个 match_score（0-100）。

    MVP 启发式：基于 work_year / degree / skill 命中关键词数。
    后续 sprint 替换为 LLM evaluate（参 modes/interview.py 招聘端框架）。"""
    item = ctx["_item"]
    keyword = (ctx.get("inputs", {}).get("keyword") or "").lower()
    details = ctx.get("details") or []
    # 找最近 push 的一条 detail
    detail = {}
    for d in reversed(details):
        if d.get("item") is item:
            detail = d.get("detail") or {}
            break

    score = 50  # 基础分
    # 工作年限：每多 1 年 +3，上限 +15
    yrs = _coerce_int(detail.get("work_year") or item.get("workYear") or item.get("experience"))
    if yrs:
        score += min(yrs * 3, 15)
    # 学历加分
    deg = (str(detail.get("degree") or item.get("degree") or "")).strip()
    if deg in ("博士", "PhD", "Doctor"):
        score += 12
    elif deg in ("硕士", "Master", "MS"):
        score += 8
    elif deg in ("本科", "Bachelor", "BS"):
        score += 5
    # 关键词命中（skill / position 列表）
    skills = " ".join(str(x) for x in (detail.get("skills") or item.get("skills") or []))
    if keyword and keyword in (skills + str(detail) + str(item)).lower():
        score += 10
    score = max(0, min(score, 100))

    evaluated = ctx.setdefault("evaluated", [])
    evaluated.append({
        "item": item,
        "detail": detail,
        "match_score": score,
        "_reasons": {"work_year": yrs, "degree": deg, "keyword_hit": bool(keyword and keyword in skills.lower())},
    })
    item["match_score"] = score  # 让 notifier._bucket_match_scores 拿得到
    return {"evaluated": evaluated}


async def step_summarize_candidate_analysis(_task, ctx: dict) -> dict:
    """候选人分析报告。按 match_score 排序输出 top 10 + 高/中/低分布。"""
    evaluated: list[dict] = ctx.get("evaluated") or []
    ranked = sorted(evaluated, key=lambda x: x.get("match_score", 0), reverse=True)
    top = ranked[:10]

    high = sum(1 for e in evaluated if e.get("match_score", 0) >= 80)
    mid = sum(1 for e in evaluated if 60 <= e.get("match_score", 0) < 80)
    low = len(evaluated) - high - mid

    lines = [
        "## 候选人分析完成 ✅",
        "",
        f"分析候选人总数：**{len(evaluated)}**",
        f"- 🟢 高匹配（≥80）：**{high}**",
        f"- 🟡 中匹配（60-79）：**{mid}**",
        f"- 🔴 低匹配（<60）：**{low}**",
        "",
        "### Top 10",
    ]
    for i, e in enumerate(top, 1):
        item = e.get("item") or {}
        name = item.get("name") or item.get("geek_name") or item.get("publicId") or "—"
        lines.append(f"{i}. **{name}** · 匹配分 {e.get('match_score', 0)}")

    artifacts = ctx.setdefault("_artifacts", {})
    artifacts["candidates"] = [{
        "name": e.get("item", {}).get("name") or e.get("item", {}).get("geek_name") or "",
        "match_score": e.get("match_score", 0),
        "platform": ctx.get("platform", ""),
        "raw": e.get("item"),
    } for e in ranked]
    artifacts["counts"] = {"high": high, "mid": mid, "low": low, "total": len(evaluated)}
    summary = "\n".join(lines)
    ctx["result_summary"] = summary
    return {"result_summary": summary}


# ── 工具函数 ────────────────────────────────────────────────────────────────

def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        # "5年" / "5 years" / "5"
        import re
        m = re.search(r"\d+", v)
        if m:
            return int(m.group(0))
    return None
