"""
form_template_enrich.py — 抓包富化（K5）。

定期取 form_template_captures 里 processed=false 的记录，把请求摘要喂给 LLM，
推断站点表单期望收集的字段（canonical ident + profile_key），merge 进 form_templates。
不在 /autofill/match 热路径里跑。

设计文档：autofill-ext/docs/form-knowledge-base.md §6
"""
from __future__ import annotations

import asyncio
import json
import logging

from anthropic import AsyncAnthropic

import config
import form_template_db
from resume_parser import _parse_llm_response

log = logging.getLogger(__name__)

_ENRICH_INTERVAL = 600   # 秒

_KNOWN_PROFILE_KEYS = [
    "fullName", "firstName", "lastName", "email", "phone", "city", "state",
    "country", "address", "zipCode", "linkedin", "github", "website",
    "currentCompany", "currentTitle", "experienceYears", "expectedSalary",
    "school", "major", "degree", "skills",
]

_SYSTEM = (
    "你是表单逆向分析助手。给定一个网页申请/注册流程抓到的若干 HTTP 请求"
    "（提交 payload、站点返回的表单 schema 等），推断这个站点表单期望收集哪些字段。\n"
    "每个字段给出：\n"
    "- ident：字段稳定标识，用请求 payload 里的真实字段名（如 first_name / email）。\n"
    "- profile_key：对应用户档案的哪个键，从给定列表里选，选不出填 null。\n"
    "- required：能判断则给出是否必填。\n"
    "只返回 JSON，不要解释、不要 markdown。"
)


async def _llm_infer(requests: list) -> list:
    client = AsyncAnthropic(api_key=config.API_KEY, base_url=config.BASE_URL or None)
    slim = []
    for r in requests[:40]:
        slim.append({
            "method": r.get("method"),
            "url": r.get("url"),
            "req_body": (r.get("req_body") or "")[:2000],
            "resp_mime": r.get("resp_mime"),
            "resp_body": (r.get("resp_body") or "")[:2000],
        })
    user_prompt = (
        "【已知 profile_key 列表】\n"
        + json.dumps(_KNOWN_PROFILE_KEYS, ensure_ascii=False)
        + "\n\n【抓到的 HTTP 请求】\n"
        + json.dumps(slim, ensure_ascii=False, indent=2)
        + '\n\n输出 JSON：'
        + '{"fields":[{"ident":"first_name","profile_key":"firstName","required":true}]}'
    )
    resp = await client.messages.create(
        model=config.MODEL,
        max_tokens=2048,
        temperature=0,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if not resp.content:
        return []
    parsed = _parse_llm_response((resp.content[0].text or "").strip())
    return parsed.get("fields") or []


async def process_capture(cap: dict) -> int:
    """处理单条抓包：LLM 反推字段 → merge 进模板。返回并入的字段数。

    不论成败都标 processed，避免脏数据反复卡队列。
    """
    merged = 0
    try:
        if cap.get("template_id") and cap.get("requests"):
            fields = await _llm_infer(cap["requests"])
            if fields:
                merged = await form_template_db.merge_enriched_fields(
                    cap["template_id"], fields)
                log.info("enrich: capture=%s template=%s merged=%d",
                         cap.get("id"), cap.get("template_id"), merged)
    except Exception as e:
        log.warning("enrich capture=%s failed: %s", cap.get("id"), e)
    finally:
        try:
            await form_template_db.mark_capture_processed(cap["id"])
        except Exception:
            pass
    return merged


async def process_pending_captures(limit: int = 5) -> int:
    """处理一批未处理抓包，返回处理条数。"""
    captures = await form_template_db.fetch_pending_captures(limit=limit)
    for cap in captures:
        await process_capture(cap)
    return len(captures)


async def enrichment_loop() -> None:
    """server.py _startup 里 asyncio.create_task 起这个常驻循环。"""
    log.info("form-template enrichment loop started (interval=%ds)", _ENRICH_INTERVAL)
    while True:
        try:
            await asyncio.sleep(_ENRICH_INTERVAL)
            n = await process_pending_captures(limit=5)
            if n:
                log.info("enrichment tick: processed %d captures", n)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("enrichment loop tick error: %s", e)
