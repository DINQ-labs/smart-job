"""tasks/steps/linkedin.py — LinkedIn 专属 step。

业务流程特征:
  jobseeker:跟 Boss 概念一致(搜 → 拉详情 → 评分),只是工具名 + 字段不同
  recruiter:跟 Boss 不同 — 用 connection degree + connect/inmail 决定联系方式
            1°/2° 用 linkedin_connect(免费,300 字符 note)
            3° 跳过(需要 Premium InMail)→ skip_item

工具(由 job-api-gateway/linkedin_commands.py 提供):
  linkedin_search_jobs(keywords, geo_location_id, page, page_size)
  linkedin_get_job_detail(job_id)
  linkedin_search_candidates(keywords, start, count)
  linkedin_get_connection_degree(member_urn, public_id)
  linkedin_connect(member_urn, message)
  linkedin_send_message(member_id, text)
"""
from __future__ import annotations

import logging

from tasks.steps.common import (
    ensure_mcp, extract_list_from_raw, fetch_detail_with_fallback,
    paginated_search,
)
from tasks.steps._geo import linkedin_geo_urn

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#                          求职(jobseeker)
# ═══════════════════════════════════════════════════════════════════

async def step_search_jobs(_task, ctx: dict) -> dict:
    """根据 inputs.keyword + desired_count 自动翻页搜 LinkedIn 职位。"""
    inputs = ctx.get("inputs") or {}
    keyword = (inputs.get("keyword") or "").strip()
    if not keyword:
        return {"items": [], "_error": "keyword 必填(LinkedIn 不支持空搜索)"}
    desired_count = max(1, min(int(inputs.get("desired_count") or 30), 100))
    geo_location_id = (
        linkedin_geo_urn(inputs.get("country"))
        or (inputs.get("geo_location_id") or "").strip()
    )

    mcp = await ensure_mcp(ctx)
    items, pages = await paginated_search(
        mcp, "linkedin_search_jobs",
        {
            "keywords": keyword,
            "geo_location_id": geo_location_id,
            "page_size": 25,  # LinkedIn 单页上限通常 25,后端按 page 翻
        },
        desired_count,
        extract_fn=_extract_jobs,
        item_id_fn=lambda it: (it.get("jobPostingUrn") or it.get("entityUrn")
                               or it.get("jobId") or it.get("id") or ""),
    )
    log.info("[linkedin:search_jobs] kw=%s country=%s → %d jobs (跑了 %d 页, 目标 %d)",
             keyword, inputs.get("country") or "(any)", len(items), pages, desired_count)
    return {"items": items, "search_keyword": keyword}


async def step_fetch_job_detail(_task, ctx: dict) -> dict:
    """iter:对每个 LinkedIn job 调 linkedin_get_job_detail。

    通用错误降级:detail 不可用时 detail={},score/summarize 用 search item 字段兜底。
    真风控信号继续 raise 给 engine 走 retry/skip/pause 协议。"""
    item = ctx["_item"]
    # LinkedIn job 的 ID 字段通常在 jobPostingUrn / entityUrn 里
    job_id = (item.get("jobPostingUrn") or item.get("entityUrn")
              or item.get("jobId") or item.get("id") or "")
    if not job_id:
        return {}
    # entityUrn 可能形如 'urn:li:fsd_jobPosting:1234' — 提数字部分
    if isinstance(job_id, str) and ":" in job_id:
        job_id = job_id.rsplit(":", 1)[-1]

    mcp = await ensure_mcp(ctx)
    detail = await fetch_detail_with_fallback(
        mcp, "linkedin_get_job_detail", {"job_id": job_id},
        item_label=item.get("title") or job_id,
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
    """搜 LinkedIn 用户作为候选人池(普通 LinkedIn 模式,非 Recruiter Talent)。"""
    inputs = ctx.get("inputs") or {}
    keyword = (inputs.get("job_keyword") or "").strip()
    if not keyword:
        return {"items": [], "_error": "job_keyword 必填"}
    count = min(int(inputs.get("contact_count") or 10), 30)

    mcp = await ensure_mcp(ctx)
    result = await mcp.call("linkedin_search_candidates", {
        "keywords": keyword,
        "start": 0,
        "count": count,
    })
    candidates = _extract_candidates(result)
    items = candidates[:count]
    log.info("[linkedin:search_candidates] kw=%s → %d profiles (取前 %d)",
             keyword, len(candidates), len(items))
    return {"items": items, "search_keyword": keyword}


def _normalize_degree(v) -> str:
    """LinkedIn distance 字段规范化。
    可能值:'DISTANCE_1' / 'DISTANCE_2' / 'DISTANCE_3' / 'OUT_OF_NETWORK' / 'SELF'
            / 'F1' / 'F2' / 'F3' / 'FIRST' / '1' / 1 / None
    返回:'1' / '2' / '3' / 'OUT' / 'SELF' / 'UNKNOWN'
    """
    s = str(v or "").upper().strip()
    if not s or s == "NONE":               return "UNKNOWN"
    if "SELF" in s:                          return "SELF"
    if "OUT_OF_NETWORK" in s or s == "OUT":  return "OUT"
    if "_1" in s or s == "1" or s == "F1" or "FIRST" in s:   return "1"
    if "_2" in s or s == "2" or s == "F2" or "SECOND" in s:  return "2"
    if "_3" in s or s == "3" or s == "F3" or "THIRD" in s:   return "3"
    if "_4" in s or s == "4":                return "4"
    return "UNKNOWN"


async def step_check_degree(_task, ctx: dict) -> dict:
    """iter:查询每个候选人的 connection degree,标到 item['_degree']。
    1° → linkedin_send_message(已是好友,直接发);
    2° → linkedin_connect 加好友 note;
    3°+/OUT/UNKNOWN → skip(需 Premium 才能 InMail)。"""
    item = ctx["_item"]
    member_urn = item.get("memberUrn") or item.get("urn") or item.get("entityUrn") or ""
    public_id = item.get("publicIdentifier") or item.get("publicId") or ""
    if not member_urn and not public_id:
        item["_degree"] = "UNKNOWN"
        return {}
    mcp = await ensure_mcp(ctx)
    result = await mcp.call("linkedin_get_connection_degree", {
        "member_urn": member_urn,
        "public_id": public_id,
    })
    raw = result.get("raw") if isinstance(result, dict) else {}
    raw_degree = (
        result.get("degree")
        or (raw.get("degree") if isinstance(raw, dict) else None)
        or (raw.get("distance", {}).get("value") if isinstance(raw, dict) else None)
    )
    item["_degree"] = _normalize_degree(raw_degree)
    return {}


async def step_invite_or_skip(_task, ctx: dict) -> dict:
    """iter:按 degree 选合适的联系方式。
    1° → linkedin_send_message(已是好友,直接发消息)
    2° → linkedin_connect with note(发好友邀请,300 字符上限)
    3°+/OUT/UNKNOWN/SELF → skip"""
    item = ctx["_item"]
    degree = item.get("_degree", "UNKNOWN")
    inputs = ctx.get("inputs") or {}
    greeting = (inputs.get("greeting_template") or "").strip()
    member_urn = item.get("memberUrn") or item.get("urn") or item.get("entityUrn") or ""
    name = (item.get("firstName", "") + " " + item.get("lastName", "")).strip() or item.get("name") or ""

    # 1°/2° 才走联系路径,其他都 skip
    if degree not in ("1", "2"):
        skipped = ctx.setdefault("_skipped", [])
        reason_map = {
            "3":       "3° 关系需 Premium InMail,跳过",
            "4":       "4°+ 关系需 Premium InMail,跳过",
            "OUT":     "Out of network,无法直接联系",
            "SELF":    "这是当前登录用户本人",
            "UNKNOWN": "无法确定关系度数,跳过保守",
        }
        skipped.append({
            "idx": ctx.get("_item_idx", 0),
            "signal": f"linkedin:degree_{degree.lower()}",
            "reason": reason_map.get(degree, f"degree={degree} 不支持"),
            "item_summary": name[:40],
        })
        return {}

    if not member_urn:
        return {}

    mcp = await ensure_mcp(ctx)
    contacted = ctx.setdefault("contacted", [])

    if degree == "1":
        # 已是好友,直接发消息(不限字符)
        if not greeting:
            greeting = f"Hi {item.get('firstName', '')}, 想和你聊聊一个机会"
        result = await mcp.call("linkedin_send_message", {
            "member_id": member_urn,
            "text": greeting,
        })
        contacted.append({
            "geek_id": member_urn, "name": name,
            "result": "已发消息(1°)",
        })
    else:
        # 2°:发好友邀请 + 可选 note
        args = {"member_urn": member_urn}
        if greeting:
            args["message"] = greeting[:300]   # connect note 上限 300 字符
        result = await mcp.call("linkedin_connect", args)
        contacted.append({
            "geek_id": member_urn, "name": name,
            "result": "邀请已发(2°)",
        })

    return {"contacted": contacted}


# ═══════════════════════════════════════════════════════════════════
#                          字段提取
# ═══════════════════════════════════════════════════════════════════

def _extract_jobs(result: dict) -> list[dict]:
    """LinkedIn Voyager API 通常返 elements 数组(顶层或 raw.data.elements)。"""
    return extract_list_from_raw(
        result,
        keys=("elements", "jobs", "results", "list"),
        nested_in=("data",),
    )


def _extract_candidates(result: dict) -> list[dict]:
    """LinkedIn 用户列表(同样 Voyager elements 格式)。"""
    return extract_list_from_raw(
        result,
        keys=("elements", "people", "results", "profiles", "list"),
        nested_in=("data",),
    )
