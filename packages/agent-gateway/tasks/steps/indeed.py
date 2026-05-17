"""tasks/steps/indeed.py — Indeed 专属 step。

业务流程特征:
  jobseeker:跟 Boss/LinkedIn 概念一致(搜 → 拉详情 → 评分),工具名+字段不同
  recruiter:**业务模式根本不同** — 不是"主动联系候选人",
            是"候选人 apply 后 inbox triage"(批量处理收件箱)。
            模板叫 `recruiter:inbox_triage`,跟 hiring_pipeline 是不同的逻辑模板。

工具(job-api-gateway/indeed_commands.py + indeed_employer_commands.py):
  jobseeker:
    indeed_search_jobs(keywords, location, page, country, ...)
    indeed_get_job_detail(job_id, country)
  recruiter (inbox_triage):
    indeed_employer_list_jobs(limit)              — 列雇主在招职位
    indeed_employer_search_candidates(employer_job_id, dispositions, limit, ...)
                                                  — 列某 job 的候选人(NEW/PENDING/...)
    indeed_employer_get_candidate(submission_uuid|legacy_id)
                                                  — 候选人详情(自带 _preview 字段)
    indeed_employer_download_resume(legacy_id)    — 拉简历文件(future)
    indeed_employer_update_candidate_status       — 更新状态(future)
"""
from __future__ import annotations

import logging

from tasks.steps.common import (
    ensure_mcp,
    extract_list_from_raw,
    throttled_iter,
    fetch_detail_with_fallback,
    paginated_search,
)
from tasks.steps._geo import indeed_country

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#                          求职(jobseeker)
# ═══════════════════════════════════════════════════════════════════

async def step_search_jobs(_task, ctx: dict) -> dict:
    """根据 inputs.keyword + desired_count 自动翻页搜 Indeed 职位。"""
    inputs = ctx.get("inputs") or {}
    keyword = (inputs.get("keyword") or "").strip()
    if not keyword:
        return {"items": [], "_error": "keyword 必填(Indeed 不支持空搜索)"}
    desired_count = max(1, min(int(inputs.get("desired_count") or 30), 100))
    location = (inputs.get("location") or "").strip()
    country = indeed_country(inputs.get("country"))

    mcp = await ensure_mcp(ctx)
    # Indeed Cloudflare 对 burst 比 LinkedIn/Boss 更敏感,翻页间隔加宽到 3-5s
    items, pages = await paginated_search(
        mcp, "indeed_search_jobs",
        {
            "keywords": keyword,
            "location": location,
            "country": country,
            "page_size": 15,
        },
        desired_count,
        extract_fn=_extract_jobs,
        item_id_fn=lambda it: (it.get("jobkey") or it.get("jobId")
                               or it.get("jk") or it.get("id") or ""),
        interval_range=(3.0, 5.0),
    )
    log.info("[indeed:search_jobs] kw=%s loc=%s country=%s → %d jobs (跑了 %d 页, 目标 %d)",
             keyword, location or "(any)", country, len(items), pages, desired_count)
    return {"items": items, "search_keyword": keyword}


async def step_fetch_job_detail(_task, ctx: dict) -> dict:
    """iter:对每个 Indeed job 调 indeed_get_job_detail。

    通用错误降级:detail 不可用时 detail={},score/summarize 用 search item 字段兜底。
    真风控信号继续 raise 给 engine 走 retry/skip/pause 协议。"""
    item = ctx["_item"]
    inputs = ctx.get("inputs") or {}
    country = indeed_country(inputs.get("country"))

    # Indeed job 主键是 jobkey(jk)
    job_id = item.get("jobkey") or item.get("jobId") or item.get("jk") or item.get("id") or ""
    if not job_id:
        return {}

    mcp = await ensure_mcp(ctx)
    detail = await fetch_detail_with_fallback(
        mcp, "indeed_get_job_detail",
        {"job_id": job_id, "country": country},
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
#                  招聘(recruiter)— Inbox Triage
# ═══════════════════════════════════════════════════════════════════

async def step_list_pending_candidates(_task, ctx: dict) -> dict:
    """列雇主所有在招 job + 平铺 NEW/PENDING 状态的候选人到 ctx['items']。

    inputs:
      max_per_job   每个 job 最多看几个候选人(default 5)
      max_total     总上限(default 30)
      dispositions  状态过滤(default 'NEW,PENDING')
    """
    inputs = ctx.get("inputs") or {}
    max_per_job = min(int(inputs.get("max_per_job") or 5), 20)
    max_total = min(int(inputs.get("max_total") or 30), 100)
    dispositions = (inputs.get("dispositions") or "NEW,PENDING").strip()

    mcp = await ensure_mcp(ctx)
    jobs_result = await mcp.call("indeed_employer_list_jobs", {"limit": 50})
    jobs = _extract_employer_jobs(jobs_result)
    if not jobs:
        return {"items": [], "search_keyword": "(雇主 inbox)", "_jobs_count": 0}

    candidates: list[dict] = []
    jobs_with_pending = 0
    # Indeed Cloudflare 对 burst 请求很敏感:每次 search 之间间隔 2s,
    # 50 个 job 整体跑 ~100s,远低于 step 内 cancel/lease 续约的 30s × 多次 tick。
    async for job in throttled_iter(jobs, interval_sec=2.0):
        if len(candidates) >= max_total:
            break
        job_id = (job.get("employer_job_id") or job.get("jobId")
                  or job.get("id") or job.get("job_key") or "")
        if not job_id:
            continue
        cands_result = await mcp.call("indeed_employer_search_candidates", {
            "employer_job_id": job_id,
            "dispositions": dispositions,
            "limit": max_per_job,
        })
        cands = _extract_employer_candidates(cands_result)
        if cands:
            jobs_with_pending += 1
        for c in cands:
            # 在每个 candidate 上带 job context,后续 summarize 按 job 分组
            c["_job_id"] = job_id
            c["_job_title"] = (job.get("title") or job.get("jobTitle")
                               or job.get("name") or "")
            candidates.append(c)
            if len(candidates) >= max_total:
                break

    log.info("[indeed:inbox] %d jobs, %d 有 pending → 共 %d 候选人",
             len(jobs), jobs_with_pending, len(candidates))
    return {
        "items": candidates,
        "search_keyword": f"(雇主 inbox · {dispositions})",
        "_jobs_count": len(jobs),
        "_jobs_with_pending": jobs_with_pending,
    }


async def step_fetch_candidate_preview(_task, ctx: dict) -> dict:
    """iter:对每个候选人 indeed_employer_get_candidate 拿 detail (含 _preview)。"""
    item = ctx["_item"]
    submission_uuid = (item.get("submission_uuid") or item.get("submissionUuid")
                        or item.get("uuid") or "")
    legacy_id = (item.get("legacy_id") or item.get("legacyId")
                  or item.get("candidate_id") or "")
    if not submission_uuid and not legacy_id:
        return {}

    mcp = await ensure_mcp(ctx)
    args: dict = {}
    if submission_uuid:
        args["submission_uuid"] = submission_uuid
    if legacy_id:
        args["legacy_id"] = legacy_id
    detail = await mcp.call("indeed_employer_get_candidate", args)
    previews = ctx.setdefault("previews", [])
    previews.append({"item": item, "detail": detail})
    return {"previews": previews}


async def step_summarize_inbox(_task, ctx: dict) -> dict:
    """生成 inbox 报告:按 job 分组列出待处理候选人。"""
    previews = ctx.get("previews") or []
    skipped = ctx.get("_skipped") or []
    jobs_count = ctx.get("_jobs_count", 0)
    jobs_with_pending = ctx.get("_jobs_with_pending", 0)

    if not previews:
        if jobs_count == 0:
            summary = "❌ 没找到任何在招 job。请确认在 employers.indeed.com 已发布职位。"
        else:
            summary = f"📭 没有 NEW/PENDING 候选人。共扫描 {jobs_count} 个 job,inbox 都已清空。"
    else:
        # 按 job 分组
        by_job: dict[str, list] = {}
        for p in previews:
            jt = (p.get("item") or {}).get("_job_title") or "(unknown job)"
            by_job.setdefault(jt, []).append(p)
        lines = [f"📥 共 {len(previews)} 个待处理候选人,跨 {len(by_job)} 个职位:\n"]
        for jt, ps in by_job.items():
            lines.append(f"\n▸ {jt}({len(ps)} 人):")
            for p in ps:
                detail = p.get("detail") or {}
                preview = detail.get("_preview") or {}
                item = p.get("item") or {}
                name = (preview.get("name") or item.get("name")
                        or item.get("candidateName") or "(unknown)")
                role = preview.get("current_role") or ""
                years = preview.get("years")
                meta_parts = []
                if role: meta_parts.append(role)
                if years: meta_parts.append(f"{years} 年经验")
                meta = (' · ' + ' · '.join(meta_parts)) if meta_parts else ''
                lines.append(f"  - {name}{meta}")
        if skipped:
            lines.append(f"\n⚠ 跳过 {len(skipped)} 个。")
        summary = "\n".join(lines)

    artifacts = ctx.setdefault("_artifacts", {})
    artifacts["candidates"] = [_build_indeed_candidate_artifact(p) for p in previews]
    artifacts["total_pending"] = len(previews)
    artifacts["jobs_scanned"] = jobs_count
    artifacts["jobs_with_pending"] = jobs_with_pending
    artifacts["skipped_count"] = len(skipped)
    return {"result_summary": summary}


def _build_indeed_candidate_artifact(p: dict) -> dict:
    """Indeed inbox_triage 单个候选人的展示 artifact,带 platform_url 跳转。"""
    item = p.get("item") or {}
    detail = p.get("detail") or {}
    preview = detail.get("_preview") or {}
    submission_uuid = (item.get("submission_uuid") or item.get("submissionUuid")
                        or item.get("uuid") or "")
    legacy_id = (item.get("legacy_id") or item.get("legacyId")
                  or item.get("candidate_id") or "")
    employer_job_id = item.get("_job_id") or ""
    # Indeed Employer 候选人详情页(带 employer_job + submission)— 没有 employer_job
    # 时 fallback 到 candidates 列表页
    if employer_job_id and submission_uuid:
        platform_url = (
            f"https://employers.indeed.com/c/p/candidates/{employer_job_id}/"
            f"submissions/{submission_uuid}"
        )
    else:
        platform_url = "https://employers.indeed.com/p/candidates"
    return {
        "name":            preview.get("name") or item.get("name") or item.get("candidateName"),
        "current_role":    preview.get("current_role"),
        "years":           preview.get("years"),
        "job_title":       item.get("_job_title"),
        "employer_job_id": employer_job_id,
        "submission_uuid": submission_uuid,
        "legacy_id":       legacy_id,
        "platform_url":    platform_url,
    }


# ═══════════════════════════════════════════════════════════════════
#                          字段提取
# ═══════════════════════════════════════════════════════════════════

def _extract_employer_jobs(result: dict) -> list[dict]:
    """Indeed Employer 在招 job 列表。"""
    return extract_list_from_raw(
        result,
        keys=("jobs", "results", "list", "elements"),
        nested_in=("data", "metaData"),
    )


def _extract_employer_candidates(result: dict) -> list[dict]:
    """Indeed Employer 某 job 下的候选人 submissions。"""
    return extract_list_from_raw(
        result,
        keys=("candidates", "submissions", "results", "list", "elements"),
        nested_in=("data", "metaData"),
    )


# ═══════════════════════════════════════════════════════════════════
#                          字段提取
# ═══════════════════════════════════════════════════════════════════

def _extract_jobs(result: dict) -> list[dict]:
    """Indeed 职位列表(历史字段位置多变,直接顶层或 wrapper 里都试)。"""
    return extract_list_from_raw(
        result,
        keys=("jobs", "results", "list", "elements", "jobsList"),
        nested_in=("data", "metaData", "zpData"),
    )
