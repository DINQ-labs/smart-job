"""tasks/steps/common.py — 跨平台共享原子 step + 工具。

step:
  - step_score_by_salary       jobseeker:按薪资上限 sort 取 top N(平台无关)
  - step_summarize_top_jobs    jobseeker:写 result_summary + artifacts.top_jobs
  - step_summarize_pipeline    recruiter:汇总联系结果

工具:
  - ensure_mcp(ctx)            所有 step 共享的 MCP session 创建(ctx['_mcp'] 缓存)
  - extract_list_from_raw      逐级在 raw 响应里查 list 字段(替代每平台重复 _extract_*)
  - throttled_iter             带间隔的循环 helper(避免 Cloudflare 等 burst 风控)
  - paginated_search           循环抓页直到 desired_count(三平台 search_jobs 共用)
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Iterable

from tasks.mcp_client import TaskMcpSession, RiskControlSignal

log = logging.getLogger(__name__)


# ── fetch detail with fallback ─────────────────────────────────────
# iter step 内的"拉详情"调用容错:
#   - 真风控信号(boss:code_37 / boss:captcha 等)→ raise,让 engine 走重试/暂停
#   - 通用错误(超时/网络/字段错 → generic:*)→ 返 None,caller 降级用 search item
# 这样 30 个 detail 调用里某几个失败不影响其它,任务能完整跑完出报告。

async def fetch_detail_with_fallback(
    mcp, tool_name: str, args: dict, *, item_label: str = "",
) -> dict | None:
    """调 detail 工具。返 dict = 成功;返 None = 通用错误降级。
    真风控仍 raise 让 engine 协议处理。"""
    try:
        return await mcp.call(tool_name, args)
    except RiskControlSignal as sig:
        if not sig.signal_id.startswith("generic:"):
            raise  # 真风控,engine 走 retry/skip/pause
        log.warning("[%s] item=%s detail 调用失败(%s)→ 降级用 search 字段: %s",
                    tool_name, item_label[:40] if item_label else "?",
                    sig.signal_id, str(sig.original_text)[:120])
        return None


# ── 通用 raw 响应解析 ─────────────────────────────────────────────
# 三平台 cmd 都返 `{"raw": {...site_specific...}}`,具体 list 字段在不同
# 嵌套深度。每平台自己写一遍 _extract_jobs / _extract_candidates 重复且容易漏。
# 用这个 helper 只需声明字段名即可,新平台只要填两组字符串。

def extract_list_from_raw(
    result: Any,
    *,
    keys: tuple[str, ...] = (),
    nested_in: tuple[str, ...] = (),
) -> list[dict]:
    """在 result.raw(或 result 本身)里找第一个 list-of-dicts 字段。

    keys: 直接在顶层找的 key 名(优先级从前到后)
    nested_in: 在这些 wrapper 字段下面再找 keys(用于 zpData/data/elements wrapper)
    """
    if not isinstance(result, dict):
        return []
    raw = result.get("raw") if "raw" in result else result
    if not isinstance(raw, dict):
        return []
    # 先尝试 nested wrappers(很多平台数据藏在 raw.zpData.* / raw.data.*)
    for outer in nested_in:
        wrapper = raw.get(outer)
        if not isinstance(wrapper, dict):
            continue
        for k in keys:
            v = wrapper.get(k)
            if isinstance(v, list):
                return v
    # 再尝试顶层
    for k in keys:
        v = raw.get(k)
        if isinstance(v, list):
            return v
    return []


# ── 翻页循环搜索(三平台 search_jobs 共用)──────────────────────────
# 业务流程:从 page=1 开始抓,直到 len(items) >= desired_count 或 max_pages 触顶。
# 自动去重(同 job 在多页之间可能重复返回);整页全是已见时早退;每页之间随机 sleep
# 2-4s 避免 burst 触发风控。
async def paginated_search(
    mcp,
    tool_name: str,
    base_args: dict,
    desired_count: int,
    *,
    extract_fn: Callable[[Any], list[dict]],
    item_id_fn: Callable[[dict], str],
    page_arg: str = "page",
    interval_range: tuple[float, float] = (2.0, 4.0),
    max_pages: int = 20,
) -> tuple[list[dict], int]:
    """返 (items, pages_fetched)。items 长度 ≤ desired_count。"""
    items: list[dict] = []
    seen: set[str] = set()
    pages_done = 0
    for page in range(1, max_pages + 1):
        if page > 1:
            await asyncio.sleep(random.uniform(*interval_range))
        args = {**base_args, page_arg: page}
        result = await mcp.call(tool_name, args)
        pages_done = page
        new_items = extract_fn(result)
        if not new_items:
            break
        added = 0
        for it in new_items:
            jid = (item_id_fn(it) or "").strip()
            if jid and jid in seen:
                continue
            if jid:
                seen.add(jid)
            items.append(it)
            added += 1
            if len(items) >= desired_count:
                return items[:desired_count], pages_done
        if added == 0:
            # 整页全是已见(或都没 id 但已经收够),早退
            break
    return items[:desired_count], pages_done


# ── 节流循环(给 step 内部循环用,平摊 burst 风控压力)──────────────
async def throttled_iter(items: Iterable, interval_sec: float = 1.5):
    """对 items yield 每个元素,中间间隔 interval_sec 秒。第一个不等。
    用法:
        async for item in throttled_iter(jobs, interval_sec=2):
            ...
    """
    first = True
    for item in items:
        if not first:
            await asyncio.sleep(interval_sec)
        first = False
        yield item


# ── 通用 MCP session helper ───────────────────────────────────────
# 模板 step 第一次需要调 MCP 时创建 session,后续 step 复用 ctx['_mcp']。
# Session 在 task 结束时由 engine._finalize 兜底 cleanup(ctx['_cleanup'] 列表)。
async def ensure_mcp(ctx: dict) -> TaskMcpSession:
    sess = ctx.get("_mcp")
    if sess is not None:
        return sess
    user_id = ctx.get("user_id") or ""
    role = ctx.get("role") or "jobseeker"
    platform = ctx.get("platform") or "boss"
    sess = TaskMcpSession(
        user_id=user_id, role=role, platform=platform,
        request_id=f"task-{ctx.get('_task_id','')}",
    )
    await sess.__aenter__()
    ctx["_mcp"] = sess
    cleanups = ctx.setdefault("_cleanup", [])
    async def _close():
        await sess.__aexit__(None, None, None)
    cleanups.append(_close)
    return sess


# ── 通用 step:按薪资排序 ─────────────────────────────────────────
async def step_score_by_salary(_task, ctx: dict) -> dict:
    """按薪资从高到低 sort 挑前 N。

    数据来源(降级):
      1. ctx['details'] = [{item, detail}, ...]  — 跑过 fetch_detail step 的有 detail
      2. ctx['items']   = [{...}, ...]            — 没跑 detail 直接用 search 结果

    Boss/LinkedIn/Indeed 的 search 结果通常已经带 salaryDesc / salary 等字段,
    多数场景**不需要**为了排序专门拉详情。不拉详情 = 少 N 次 MCP 调用 + 不容易触发风控。
    """
    details = ctx.get("details")
    if not details:
        items = ctx.get("items") or []
        details = [{"item": it, "detail": {}} for it in items]
    top_n = int(ctx.get("top_n") or ctx.get("inputs", {}).get("top_n") or 5)

    def _sal(d):
        det = d.get("detail") or {}
        item = d.get("item") or {}
        # 多种字段都试 — Boss `salaryDesc` / LinkedIn `salaryRange` / Indeed `salary`
        for src in (det, item):
            for k in ("salary_max", "salary_high", "salary", "salaryDesc", "salaryRange"):
                v = src.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        s = v.replace("k", "K").replace("K", "")
                        if "-" in s:
                            return float(s.split("-")[1].strip())
                    except Exception:
                        pass
        return 0.0

    ranked = sorted(details, key=_sal, reverse=True)[:top_n]
    log.info("[step:score] %d items → top %d", len(details), len(ranked))
    return {"ranked": ranked}


# ── job 字段提取(三平台 first-non-empty)+ 平台 URL 构造 ─────────
# 详情页前端要展示 city / experience / degree / JD 摘要 / "在 XX 查看"链接。
# 这些字段在 search item 和 detail dict 里散落,各平台命名不同;此处统一搜罗。
def _build_top_job_artifact(d: dict, platform: str) -> dict:
    item = d.get("item") or {}
    det = d.get("detail") or {}
    job_id = (
        item.get("encryptJobId") or item.get("jobkey") or item.get("jk")
        or item.get("jobId")     or item.get("entityUrn") or item.get("urn")
        or item.get("id") or ""
    )
    if isinstance(job_id, str) and ":" in job_id:
        # 'urn:li:fsd_jobPosting:1234' → '1234'
        job_id = job_id.rsplit(":", 1)[-1]
    title = (item.get("jobName") or item.get("title") or det.get("title") or "")
    company = (det.get("brandName") or det.get("companyName")
               or item.get("brandName") or item.get("companyName")
               or item.get("company") or "")
    salary = (det.get("salaryDesc") or det.get("salary")
              or item.get("salaryDesc") or item.get("salary") or "")
    city = (det.get("cityName") or det.get("locationName") or det.get("location")
            or item.get("cityName") or item.get("locationName")
            or item.get("location") or item.get("city") or "")
    experience = (det.get("jobExperience") or det.get("experience")
                  or item.get("jobExperience") or item.get("experience") or "")
    degree = (det.get("jobDegree") or det.get("degree") or det.get("education")
              or item.get("jobDegree") or item.get("degree")
              or item.get("education") or "")
    jd_text = (det.get("postDescription") or det.get("description")
               or det.get("descriptionContent") or det.get("jdContent") or "")
    if isinstance(jd_text, dict):
        jd_text = jd_text.get("text") or ""
    jd_excerpt = str(jd_text or "").replace("\n", " ").strip()[:140]
    return {
        "job_id":       job_id,
        "title":        title,
        "company":      company,
        "salary":       salary,
        "city":         city,
        "experience":   experience,
        "degree":       degree,
        "jd_excerpt":   jd_excerpt,
        "platform_url": _build_job_platform_url(platform, job_id),
        "fetch_failed": bool(d.get("_fetch_failed")),
    }


def _build_job_platform_url(platform: str, job_id: str) -> str:
    """三平台官网 job 详情 URL — 前端"在 XX 查看"按钮直接 chrome.tabs.create。"""
    if not job_id:
        return ""
    p = (platform or "").lower()
    if p == "boss":
        return f"https://www.zhipin.com/job_detail/{job_id}.html"
    if p == "linkedin":
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    if p == "indeed":
        return f"https://www.indeed.com/viewjob?jk={job_id}"
    return ""


# ── 通用 step:生成 jobseeker 报告 ────────────────────────────────
async def step_summarize_top_jobs(_task, ctx: dict) -> dict:
    ranked = ctx.get("ranked") or []
    skipped = ctx.get("_skipped") or []
    keyword = ctx.get("search_keyword") or ""
    platform = ctx.get("platform") or ""

    fetched = len(ctx.get("details") or [])
    if not ranked:
        summary = (
            f"没拿到职位详情(关键词:{keyword})。\n"
            f"可能原因:平台没匹配结果,或扩展未连接。\n"
            f"跳过:{len(skipped)} 个"
        )
    else:
        compared = max(fetched, len(ranked))
        lines = [
            f"📌 关键词「{keyword}」比对 {compared} 个职位,"
            f"按薪资精选 Top {len(ranked)}:\n"
        ]
        for i, d in enumerate(ranked, 1):
            item = d.get("item", {})
            det = d.get("detail", {})
            title = (item.get("jobName") or item.get("title")
                     or det.get("title") or "(未知职位)")
            company = (det.get("brandName") or det.get("companyName")
                       or item.get("brandName") or item.get("companyName")
                       or item.get("company") or "")
            salary = (det.get("salaryDesc") or det.get("salary")
                      or item.get("salaryDesc") or item.get("salary") or "")
            lines.append(f"{i}. {title} · {company} · {salary}")
        if skipped:
            lines.append(f"\n⚠ 跳过 {len(skipped)} 个(配额/拉黑/风控)。")
        summary = "\n".join(lines)

    artifacts = ctx.setdefault("_artifacts", {})
    artifacts["top_jobs"] = [_build_top_job_artifact(d, platform) for d in ranked]
    artifacts["fetched_count"] = len(ctx.get("details") or [])
    artifacts["skipped_count"] = len(skipped)
    return {"result_summary": summary}


def _build_candidate_platform_url(platform: str, c: dict) -> str:
    """recruiter contacted 候选人的"打开 Boss/LinkedIn 聊天/主页"链接。"""
    p = (platform or "").lower()
    geek_id = c.get("geek_id") or ""
    if p == "boss" and geek_id:
        return f"https://www.zhipin.com/web/geek/chat?id={geek_id}"
    if p == "linkedin":
        public_id = c.get("public_id") or ""
        if public_id:
            return f"https://www.linkedin.com/in/{public_id}/"
        if geek_id:
            jid = geek_id.rsplit(":", 1)[-1] if ":" in str(geek_id) else geek_id
            return f"https://www.linkedin.com/in/{jid}/"
        return "https://www.linkedin.com/messaging/"
    return ""


# ── 通用 step:生成 recruiter 流水线报告 ─────────────────────────
async def step_summarize_pipeline(_task, ctx: dict) -> dict:
    contacted = ctx.get("contacted") or []
    skipped = ctx.get("_skipped") or []
    keyword = ctx.get("search_keyword") or ""
    platform = ctx.get("platform") or ""

    error = ctx.get("_error")
    if error:
        summary = f"❌ 任务参数错误:{error}"
    elif not contacted:
        summary = (
            f"没找到候选人(关键词:{keyword})。\n"
            f"可能原因:平台没匹配结果,或扩展未连接。\n"
            f"跳过:{len(skipped)} 个"
        )
    else:
        lines = [f"📌 关键词「{keyword}」联系了 {len(contacted)} 个候选人:\n"]
        for i, c in enumerate(contacted, 1):
            lines.append(f"{i}. {c.get('name','(未知)')} — {c.get('result','OK')}")
        if skipped:
            lines.append(f"\n⚠ 跳过 {len(skipped)} 个(配额/拉黑/风控)。")
        summary = "\n".join(lines)

    artifacts = ctx.setdefault("_artifacts", {})
    artifacts["contacted"] = [
        {**c, "platform_url": _build_candidate_platform_url(platform, c)}
        for c in contacted
    ]
    artifacts["contacted_count"] = len(contacted)
    artifacts["skipped_count"] = len(skipped)
    return {"result_summary": summary}
