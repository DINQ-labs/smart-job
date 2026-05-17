"""tasks/steps/boss.py — Boss直聘专属 step。

Boss 业务流程特征:
  - 求职:搜索职位 → 拉详情(boss_get_job_detail)→ 评分
  - 招聘:搜候选人 → 发招呼(boss_contact_candidate)→ 等回复 → 拉简历

Step 命名约定:`step_boss_*`,被 templates 编排引用。
"""
from __future__ import annotations

import logging

from tasks.steps.common import (
    ensure_mcp, extract_list_from_raw, fetch_detail_with_fallback,
    paginated_search,
)
from tasks.steps._geo import boss_city_code

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#                          求职(jobseeker)
# ═══════════════════════════════════════════════════════════════════

async def step_search_jobs(_task, ctx: dict) -> dict:
    """根据 inputs.keyword + desired_count 自动翻页搜职位。
    空 keyword 退化用 boss_get_recommend_jobs(只抓首页推荐池)。"""
    inputs = ctx.get("inputs") or {}
    keyword = (inputs.get("keyword") or "").strip()
    desired_count = max(1, min(int(inputs.get("desired_count") or 30), 100))

    mcp = await ensure_mcp(ctx)
    if not keyword:
        # 推荐池只用首页(没翻页 API 概念)
        result = await mcp.call("boss_get_recommend_jobs", {"page": 1})
        items = _extract_jobs(result)[:desired_count]
        log.info("[boss:search_jobs] (推荐池) → %d jobs", len(items))
        return {"items": items, "search_keyword": "（推荐池）"}

    city = boss_city_code(inputs.get("city"))
    items, pages = await paginated_search(
        mcp, "boss_search_jobs",
        {"keyword": keyword, "city": city},
        desired_count,
        extract_fn=_extract_jobs,
        item_id_fn=lambda it: it.get("encryptJobId") or it.get("jobId") or "",
    )
    log.info("[boss:search_jobs] kw=%s → %d jobs (跑了 %d 页, 目标 %d)",
             keyword, len(items), pages, desired_count)
    return {"items": items, "search_keyword": keyword}


async def step_fetch_job_detail(_task, ctx: dict) -> dict:
    """iter:对每个 item 调 boss_get_job_detail。

    通用错误降级:detail 不可用时 detail={},score/summarize 用 search item 字段兜底。
    真风控信号继续 raise 给 engine 走 retry/skip/pause 协议。"""
    item = ctx["_item"]
    job_id = item.get("encryptJobId") or item.get("jobId") or item.get("id") or ""
    if not job_id:
        return {}
    list_security_id = item.get("securityId") or item.get("listSecurityId") or ""
    mcp = await ensure_mcp(ctx)
    detail = await fetch_detail_with_fallback(
        mcp, "boss_get_job_detail",
        {"encrypt_job_id": job_id, "list_security_id": list_security_id},
        item_label=item.get("jobName") or job_id,
    )
    details = ctx.setdefault("details", [])
    details.append({
        "item": item,
        "detail": detail or {},
        "_fetch_failed": detail is None,
    })
    return {"details": details}


# ═══════════════════════════════════════════════════════════════════
#                          招聘(recruiter)
# ═══════════════════════════════════════════════════════════════════

async def step_search_candidates(_task, ctx: dict) -> dict:
    """根据 inputs.job_keyword 搜候选人。"""
    inputs = ctx.get("inputs") or {}
    keyword = (inputs.get("job_keyword") or "").strip()
    if not keyword:
        return {"items": [], "_error": "job_keyword 必填"}
    city = boss_city_code(inputs.get("city"))
    count = min(int(inputs.get("contact_count") or 10), 30)

    mcp = await ensure_mcp(ctx)
    result = await mcp.call("boss_search_candidates", {
        "keyword": keyword, "city": city, "page": 1,
    })
    candidates = _extract_candidates(result)
    items = candidates[:count]
    log.info("[boss:search_candidates] kw=%s → %d cands (取前 %d)",
             keyword, len(candidates), len(items))
    return {"items": items, "search_keyword": keyword}


async def step_contact_candidate(_task, ctx: dict) -> dict:
    """iter:对每个候选人 boss_contact_candidate 发招呼。"""
    item = ctx["_item"]
    geek_id = (item.get("encryptGeekId") or item.get("geekId")
               or item.get("encryptCandidateId") or item.get("id") or "")
    if not geek_id:
        return {}
    security_id = item.get("securityId") or item.get("listSecurityId") or ""
    job_id = item.get("encryptJobId") or item.get("jobId") or ""
    inputs = ctx.get("inputs") or {}
    greeting = (inputs.get("greeting_template") or "").strip()

    args = {
        "encrypt_geek_id": geek_id,
        "security_id": security_id,
        "encrypt_job_id": job_id,
    }
    if greeting:
        args["msg"] = greeting

    mcp = await ensure_mcp(ctx)
    result = await mcp.call("boss_contact_candidate", args)
    contacted = ctx.setdefault("contacted", [])
    contacted.append({
        "geek_id": geek_id,
        "name": item.get("geekName") or item.get("name") or "",
        "result": _short_result(result),
    })
    return {"contacted": contacted}


# ═══════════════════════════════════════════════════════════════════
#                          字段提取(Boss raw 响应解析)
# ═══════════════════════════════════════════════════════════════════

def _extract_jobs(result: dict) -> list[dict]:
    """boss 职位列表通常在 raw.zpData.jobList。"""
    return extract_list_from_raw(
        result,
        keys=("jobList", "jobs", "list", "results"),
        nested_in=("zpData", "data"),
    )


def _extract_candidates(result: dict) -> list[dict]:
    """boss 候选人列表通常在 raw.zpData.geekList。"""
    return extract_list_from_raw(
        result,
        keys=("geekList", "candidates", "list", "results"),
        nested_in=("zpData", "data"),
    )


def _short_result(result: dict) -> str:
    if not isinstance(result, dict):
        return "OK"
    if result.get("_text"):
        return str(result["_text"])[:30]
    raw = result.get("raw") if "raw" in result else result
    if isinstance(raw, dict):
        msg = raw.get("message") or raw.get("zpMessage") or ""
        if msg:
            return str(msg)[:30]
    return "已发送"
