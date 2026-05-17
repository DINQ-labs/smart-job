"""
Agent 推理循环 — async generator，支持多 MCP Server + Mode 系统。

每次 yield 一条 protocol dict（可直接序列化为 WS 帧）。
messages 列表由 caller 持有并传入，本函数原地追加（多轮对话状态）。

Phase 1: system prompt 从 modes/ 模块组装，支持 mode 检测和工具过滤。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import config
import db
import pricing
import resume_db
from modes import get_mode
from modes.base import compose_system_prompt
from modes.detect import detect_mode, TOOL_MODE_AFFINITY

log = logging.getLogger(__name__)


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得 fallback 到备用模型（过载/限速/余额不足/服务端错误）"""
    from anthropic import APIStatusError
    if isinstance(exc, APIStatusError) and exc.status_code in (402, 429, 500, 502, 503, 529):
        return True
    s = str(exc)
    return "Overloaded" in s or "overloaded" in s

from mcp_manager import mcp_manager


# ── Prompt constants moved to modes/base.py and modes/search.py ──────────────
# SYSTEM_PROMPT, _LINKEDIN_SYSTEM_PROMPT_ADDON, _INDEED_SYSTEM_PROMPT_ADDON,
# _SYSTEM_PROMPT_PROD_ADDON, _EVAL_RULES, _build_system_prompt()
# are now in modes/ modules. compose_system_prompt() replaces _build_system_prompt().


async def _build_resume_summary(user_id: str) -> str:
    """从 resume_db 取解析结果，返回紧凑文本摘要（~300 tokens）。

    本地无简历时，触发一次内部聚合器拉取（fire-once，不等本轮解析完成）。
    本轮仍返回空摘要；下一轮 turn 本地有了 parsed 数据再生成摘要。
    """
    if not user_id:
        return ""
    try:
        row = await resume_db.get_resume_by_user(user_id)
        if not row:
            # 本地完全无记录 → 尝试从聚合器拉默认简历
            try:
                from resume_router import _pull_default_resume_to_local
                rid = await _pull_default_resume_to_local(user_id)
                if rid:
                    log.info(
                        "agent_loop pulled default resume from aggregator "
                        "user=%s resume_id=%d (parsing async; available next turn)",
                        user_id[:8], rid,
                    )
            except Exception as e:
                log.warning("agent_loop aggregator pull failed for %s: %s",
                            user_id[:8], e)
            return ""
        if row.get("parse_status") != "done":
            return ""

        def _parse(v):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return None
            return v

        lines = ["## 我的简历（用于职位匹配参考）"]
        name = row.get("name")
        work_years = row.get("work_years")
        degree = row.get("degree")
        if name or work_years or degree:
            parts = [p for p in [name, f"{work_years}经验" if work_years else None, degree] if p]
            lines.append("基本：" + " | ".join(parts))
        target_positions = _parse(row.get("target_positions")) or []
        if target_positions:
            lines.append("期望职位：" + "、".join(target_positions))
        target_cities = _parse(row.get("target_cities")) or []
        if target_cities:
            lines.append("期望城市：" + "、".join(target_cities))
        if row.get("target_salary_raw"):
            lines.append(f"期望薪资：{row['target_salary_raw']}/月")
        skills = _parse(row.get("skills")) or []
        if skills:
            lines.append("技能：" + "、".join(skills[:15]))
        work_experience = _parse(row.get("work_experience")) or []
        if work_experience:
            lines.append("工作经历：")
            for w in work_experience[:3]:
                if isinstance(w, dict):
                    lines.append(f"  - {w.get('company','')} | {w.get('title','')} | {w.get('duration','')}")
        education = _parse(row.get("education")) or []
        if education and isinstance(education, list):
            edu = education[0] if education else {}
            if isinstance(edu, dict) and edu:
                lines.append(f"教育：{edu.get('school','')} | {edu.get('degree','')} | {edu.get('major','')}")
        return "\n".join(lines)
    except Exception:
        return ""


async def _build_recruiter_persona_summary(user_id: str, platform: str) -> str:
    """读取招聘方在「我」tab 设的候选人画像 (recruiter_preferences),注入 system prompt。

    画像字段(per-platform):
      target_role / must_skills / nice_skills / exp_min / exp_max / degree /
      background_pref / exclude_pref

    用途:LLM 看到"招聘方想找什么样的人",渲染搜候选人 chip 时按画像生成
    [搜 NLP Engineer | SF | 5yr+] 这种推荐;Step 2A 类似 jobseeker 流程。
    无画像时返空串。
    """
    if not user_id:
        return ""
    try:
        import recruiter_db
        data = await recruiter_db.get_preferences(user_id, platform)
        if not data:
            return ""
        target_role = (data.get("target_role") or "").strip()
        must = data.get("must_skills") or []
        nice = data.get("nice_skills") or []
        exp_min = data.get("exp_min")
        exp_max = data.get("exp_max")
        degree = (data.get("degree") or "").strip()
        bg = (data.get("background_pref") or "").strip()
        exclude = (data.get("exclude_pref") or "").strip()
        if not (target_role or must or nice or exp_min is not None or exp_max is not None
                or degree or bg or exclude):
            return ""
        brand = {"boss": "Boss直聘", "linkedin": "LinkedIn", "indeed": "Indeed"}.get(
            platform, platform
        )
        lines = [f"## 候选人画像({brand},招聘方已保存)"]
        if target_role:
            lines.append(f"- 目标职位:{target_role}")
        if must:
            lines.append(f"- 必备技能:{', '.join(must[:10])}")
        if nice:
            lines.append(f"- 加分技能:{', '.join(nice[:10])}")
        if exp_min is not None or exp_max is not None:
            lo = exp_min if exp_min is not None else 0
            hi = exp_max if exp_max is not None else "∞"
            lines.append(f"- 经验年限:{lo}-{hi} 年")
        if degree:
            lines.append(f"- 学历:{degree}")
        if bg:
            lines.append(f"- 背景偏好:{bg}")
        if exclude:
            lines.append(f"- 排除项:{exclude}")
        lines.append(
            "**这是 Step 2A 渲染 3 chip 时第 1 个推荐 chip 的字段来源**。"
            "用户本轮消息以 `搜候选人:...` / `Search candidates: ...` 开头时,按 "
            "'wizard 完成后的搜索消息识别(招聘方)' 段直接搜索;消息给出新条件时以"
            "消息为准,覆盖保存值。"
        )
        return "\n".join(lines)
    except Exception:
        return ""


async def _build_recruiter_templates_summary(user_id: str, platform: str) -> str:
    """读取招聘方在「我」tab 设的招呼模板 (message_templates, role_type='recruiter'),
    注入 system prompt。

    Templates(per-platform):
      greeting  — 打招呼语(首次触达候选人)
      followup  — 跟进话术(用户没回复)
      reject    — 拒绝话术(候选人不合适)

    用途:LLM 在调 *_send_message / *_request_compose 时优先用这些模板填充 draft_text,
    保证一致的语气 + 用户校准过的话术。无模板时返空串。
    """
    if not user_id:
        return ""
    try:
        import recruiter_db
        rows = await recruiter_db.get_templates(user_id, "recruiter", platform)
        if not rows:
            return ""
        # rows: [{kind, content, updated_at}, ...]
        by_kind = {r["kind"]: (r.get("content") or "").strip() for r in rows
                   if r.get("content")}
        if not by_kind:
            return ""
        brand = {"boss": "Boss直聘", "linkedin": "LinkedIn", "indeed": "Indeed"}.get(
            platform, platform
        )
        lines = [f"## 招呼模板({brand},招聘方已保存)"]
        if by_kind.get("greeting"):
            lines.append(f"### 打招呼语(首次触达 - greeting)\n{by_kind['greeting']}")
        if by_kind.get("followup"):
            lines.append(f"### 跟进话术(用户没回复 - followup)\n{by_kind['followup']}")
        if by_kind.get("reject"):
            lines.append(f"### 拒绝话术(候选人不合适 - reject)\n{by_kind['reject']}")
        lines.append(
            "**调消息工具时优先用这些模板**(*_send_message / *_request_compose 的 "
            "draft_text 字段)。模板里的 {name} {position} {company} 占位符按上下文"
            "实际值替换。用户消息显式给出新话术时以消息为准。"
        )
        return "\n".join(lines)
    except Exception:
        return ""


async def _build_preferences_summary(user_id: str, platform: str) -> str:
    """读取用户在当前 platform 已保存的求职偏好,注入 system prompt。

    **架构(2026-04-28 恢复 platform-aware)**:三平台各存各的偏好(DB
    主键 (user_id, platform))。Boss 用 CN 城市 + CNY 月薪、LinkedIn/Indeed
    用 Global 城市 + USD 年薪。本函数按入参 platform 读对应行 —— 不再写死
    'boss',避免 LinkedIn/Indeed agent 注入了 Boss 的 CNY 月薪 + 北京/上海
    偏好导致前端预设错位。

    目的:用户在前端 PreferenceWizard 填过一次后,这些偏好作为 Step 2A 渲染
    `[搜索 X | 城市 | 薪资, 搜索 Y | ..., 自定义搜索]` 3 chip 时**第 1 个推荐
    chip 的字段来源**(无简历偏好时也能给出推荐)。无偏好或读取失败时返回空串。
    """
    if not user_id:
        return ""
    try:
        import preferences_db
        data = await preferences_db.get_user_preferences(user_id, platform=platform)
        if not data:
            return ""
        job_role = (data.get("job_role") or "").strip()
        city = (data.get("city") or "").strip()
        salary = (data.get("salary_range") or "").strip()
        notes = (data.get("notes") or "").strip()
        if not (job_role or city or salary):
            return ""
        brand = {"boss": "Boss直聘", "linkedin": "LinkedIn", "indeed": "Indeed"}.get(
            platform, platform
        )
        lines = [f"## 我的求职偏好（{brand}，用户已保存）"]
        if job_role:
            lines.append(f"- 目标职位：{job_role}")
        if city:
            lines.append(f"- 期望城市：{city}")
        if salary:
            lines.append(f"- 薪资档位：{salary}")
        if notes:
            lines.append(f"- 备注：{notes}")
        lines.append(
            "**这是 Step 2A 渲染 3 chip 时第 1 个推荐 chip 的字段来源**(优先于简历期望)。"
            "**不要**因为有保存偏好就跳过 Step 2A 直接调搜索工具 —— 必须按 modes/search.py "
            "Step 2A 流程渲染 `[搜索 X | 城市 | 薪资, 搜索 Y | ..., 自定义搜索]`,等用户点击。"
            "用户本轮消息以 `搜工作：...` / `Search jobs: ...` 开头时,按 'Wizard 完成后的搜索"
            "消息识别' 段直接搜索;消息显式给出新偏好时以消息为准,覆盖保存值。"
        )
        return "\n".join(lines)
    except Exception:
        return ""


# Platform prefix list 现在从 platforms_config manifest 读 — 加新平台只要改 manifest
from platforms_config import list_platform_prefixes as _list_platform_prefixes
_PLATFORM_PREFIXES = _list_platform_prefixes()


def _filter_tools_for_session(
    tools: list[dict],
    platform: str,
    role_type: str = "",
) -> list[dict]:
    """Per-(role, platform) agent 工具白名单 (B3 + Phase 1 cell)。

    两阶段过滤:
      1. Platform 维度:只留 {platform}_* + 跨平台无前缀工具(如 emit_action_buttons)
      2. Role 维度(Phase 1 新):若 role_type ∈ {jobseeker, recruiter},
         按 cell.tool_deny_patterns 屏蔽 role-错配的工具(jobseeker 不见
         *_search_candidates / *_employer_*;recruiter 不见 *_apply_job 等)。
         共享基础设施工具(如 *_check_login)豁免。

    LLM 上下文从 30+ 缩到 ~10-12,且不会被错 role 工具误调。

    bug fix(2026-05-10):cross-platform leak 排查辅助 —— 输出 INFO 一行
    "platform=X role=Y tools=N",这样 grep journal 能直接看出每次 SSE
    实际拿到的 tool 列表 platform。如果用户截图显示顶栏 Indeed 但工具是
    boss_*,这条 log 就是铁证 SSE platform 参数传错。
    """
    own_prefix = f"{platform}_"
    out = []
    for t in tools:
        name = t["name"]
        # Step 1: platform 过滤
        if name.startswith(own_prefix):
            out.append(t)
            continue
        if any(name.startswith(p) for p in _PLATFORM_PREFIXES):
            continue
        out.append(t)  # 跨平台无前缀

    # Step 2: role 过滤(Phase 1 cell)
    if role_type in ("jobseeker", "recruiter"):
        try:
            from modes.cells import get_cell, cell_blocks_tool
            cell = get_cell(role_type, platform)
            out = [t for t in out if not cell_blocks_tool(cell, t["name"])]
        except Exception as e:
            log.debug("cell tool filter failed (%s); keeping platform-only filter", e)

    # 审计 log:每次工具过滤后输出当前 cell 拿到的工具数 + 关键名字片段
    try:
        names = [t["name"] for t in out]
        # 抽取异常迹象:any tool name 不是当前 platform 前缀 + 不是 shared
        from modes.cells import is_shared_tool
        unexpected = [
            n for n in names
            if not n.startswith(own_prefix) and not is_shared_tool(n)
            and any(n.startswith(p) for p in _PLATFORM_PREFIXES)
        ]
        if unexpected:
            log.warning(
                "[cell-leak] platform=%s role=%s 出现非本 platform 工具 %d 个: %s",
                platform, role_type, len(unexpected), unexpected[:8],
            )
        else:
            log.info(
                "[cell-tools] platform=%s role=%s tools=%d",
                platform, role_type, len(names),
            )
    except Exception:
        pass

    return out


def mcp_tool_to_anthropic(tool) -> dict:
    schema = tool.inputSchema or {"type": "object", "properties": {}}
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": schema,
    }


def tool_result_text(result) -> str:
    parts = []
    for item in result.content:
        if hasattr(item, "text"):
            parts.append(item.text)
        else:
            parts.append(str(item))
    return "\n".join(parts) if parts else "(空结果)"


def _content_to_dicts(content) -> list[dict]:
    """Normalize Anthropic SDK Pydantic response blocks to plain dicts.

    resp.content is a list of Pydantic models (TextBlock, ToolUseBlock, …).
    Storing them as-is in the messages list causes Redis to serialise them
    via json.dumps(default=str) → strings, which the API rejects on the next
    turn with: messages[N].content[0] expected object, received string.
    """
    result = []
    for block in content:
        if isinstance(block, dict):
            result.append(block)
        elif hasattr(block, "model_dump"):
            result.append(block.model_dump())
        elif hasattr(block, "dict") and callable(block.dict):
            result.append(block.dict())
        else:
            result.append(block)
    return result


# 持有 fire-and-forget 任务引用,避免 asyncio 在事件循环关闭时报
# "Task was destroyed but it is pending!" (PEP 524 RuntimeWarning)。
# add_done_callback 自动从集合移除,无内存泄漏。
_BG_FIRE_AND_FORGET_TASKS: set = set()


_CANDIDATE_LIST_TOOLS = {
    "indeed_employer_search_candidates",
    "boss_search_candidates",
}


# Pre-flight 校验:这些工具的 security_id / chatSecurityId 入参不能为空。
# 列表工具返回空时 Agent 偶尔会硬塞空串,后端会抛 "security_id 必填"。
# agent_loop 在调 ext / MCP 之前本地短路,合成引导性 tool_result。
#
# **重要原则(2026-04-28 修订)**:仅当 ext-side handler **没有** fallback
# 自解析机制时,才把工具列入此集合。否则 LLM 正确传空 security_id 期望后端反查
# 时,pre-flight 会过激拦截,把 happy path 杀掉。
#
# **不在此列表的工具(ext 自带 token fallback,空 security_id 合法)**:
#   - boss_get_chat_history —— encrypt_uid 反查 chat_token
#   - boss_start_chat —— 从 tokenStore.getJobTokens(eid).detailSecurityId 取,
#     缺时自动调 get_job_detail 建链(commands/jobs.js:89-107)
#   - boss_get_candidate_detail —— tokenStore.getGeekToken(encrypt_uid)?.securityId
#     fallback(commands/candidates.js:158)
#   - boss_contact_candidate —— 同上(commands/candidates.js:200)
#   - boss_geek_get_boss_data —— 2026-04-28 加了 lookupChatTokenByBoss 反查
#     (geek_filter_by_label stage 2 批量写入 chatSecurityId 后即可命中)
_TOOLS_REQUIRE_SECURITY_ID = {
    # 招聘端互动候选人详情 —— 参数全必填,无 fallback
    "boss_view_geek_detail",
    # 招聘端候选人基础信息(旧)—— candidates.js:239 显式 throw 'security_id 必填'
    "boss_geek_info",
    # 招聘端预检屏蔽 —— 参数全必填(candidates.js:512-513)
    "boss_check_reply_block",
}


async def _intercept_candidate_list(
    result_text: str,
    tool_name: str,
    tool_input: dict,
    user_id: str,
) -> None:
    """拦截搜索候选人结果，预建 DB 记录（只写 meta，不涉及文件）。
    不修改 result_text — Agent 正常看到完整搜索结果。"""
    import candidate_resume_db

    try:
        data = json.loads(result_text)
    except Exception:
        return

    inner = data.get("result", data) if isinstance(data, dict) else data
    if not isinstance(inner, dict):
        return

    # 推断平台和来源职位 ID
    if "indeed" in tool_name:
        platform = "indeed"
        candidates = inner.get("candidates", [])
        source_job_id = tool_input.get("employer_job_id", "")
        for c in candidates:
            cid = c.get("legacyId") or c.get("legacy_id")
            if not cid:
                continue
            meta = {
                "submissionUuid": c.get("submissionUuid"),
                "candidateId": c.get("candidateId"),
                "phone": c.get("phone"),
                "location": c.get("location"),
                "country": c.get("country"),
                "currentTitle": c.get("currentTitle"),
                "currentCompany": c.get("currentCompany"),
                "appliedAt": c.get("appliedAt"),
                "milestone": c.get("milestone"),
                "sourceType": c.get("sourceType"),
                "experience": c.get("experience"),
                "matchHighlights": c.get("matchHighlights"),
            }
            # 去掉 None 值
            meta = {k: v for k, v in meta.items() if v is not None}
            try:
                await candidate_resume_db.upsert_candidate_meta(
                    platform=platform,
                    candidate_id=cid,
                    candidate_name=c.get("name"),
                    candidate_meta=meta,
                    source_job_id=source_job_id,
                    downloaded_by=user_id,
                )
            except Exception as e:
                log.warning("upsert_candidate_meta failed for %s/%s: %s", platform, cid, e)

    elif "boss" in tool_name:
        platform = "boss"
        geeks = inner.get("geeks", [])
        source_job_id = tool_input.get("encrypt_job_id", "")
        for g in geeks:
            cid = g.get("encryptGeekId")
            if not cid:
                continue
            meta = {
                "geekId": g.get("geekId"),
                "securityId": g.get("securityId"),
                "encryptExpectId": g.get("encryptExpectId"),
                "encryptJobId": g.get("encryptJobId"),
                "salary": g.get("salary"),
                "city": g.get("city"),
                "workYear": g.get("workYear"),
                "degree": g.get("degree"),
                "currentWork": g.get("currentWork"),
                "school": g.get("school"),
                "activeDesc": g.get("activeDesc"),
            }
            meta = {k: v for k, v in meta.items() if v is not None}
            try:
                await candidate_resume_db.upsert_candidate_meta(
                    platform=platform,
                    candidate_id=cid,
                    candidate_name=g.get("name"),
                    candidate_meta=meta,
                    source_job_id=source_job_id,
                    downloaded_by=user_id,
                )
            except Exception as e:
                log.warning("upsert_candidate_meta failed for %s/%s: %s", platform, cid, e)

    count = len(candidates if "indeed" in tool_name else geeks if "boss" in tool_name else [])
    if count:
        log.info("Candidate meta saved: %s %d candidates from tool %s", platform, count, tool_name)


async def _intercept_base64_pdf(
    result_text: str,
    tool_name: str,
    tool_input: dict,
    user_id: str,
) -> str:
    """拦截包含 base64_pdf 的工具结果，存储到磁盘 + DB，返回摘要文本。"""
    import base64
    import os
    import time
    import candidate_resume_db
    from candidate_resume_router import CANDIDATE_RESUME_DIR

    try:
        data = json.loads(result_text)
    except Exception:
        return result_text

    # 支持嵌套结构: { result: { base64_pdf: "..." } } 或直接 { base64_pdf: "..." }
    inner = data.get("result", data) if isinstance(data, dict) else data
    if not isinstance(inner, dict):
        return result_text
    b64_raw = inner.get("base64_pdf")
    if not b64_raw or not isinstance(b64_raw, str):
        return result_text

    # 去除 data:xxx;base64, 前缀
    if ";base64," in b64_raw:
        b64_raw = b64_raw.split(";base64,", 1)[1]

    try:
        pdf_bytes = base64.b64decode(b64_raw)
    except Exception as e:
        log.warning("base64_pdf decode failed: %s", e)
        return result_text

    # 推断平台和候选人 ID
    platform = "unknown"
    candidate_id = ""
    if "indeed" in tool_name:
        platform = "indeed"
        candidate_id = tool_input.get("legacy_id", "")
    elif "boss" in tool_name:
        platform = "boss"
        candidate_id = tool_input.get("encrypt_uid", "")
    elif "linkedin" in tool_name:
        platform = "linkedin"
        candidate_id = tool_input.get("profile_id", "")

    if not candidate_id:
        candidate_id = inner.get("candidate_id", "") or f"unknown_{int(time.time())}"

    # 查找搜索时预建的记录（可能有丰富的 meta）
    existing = None
    try:
        existing = await candidate_resume_db.get_candidate_resume(platform, candidate_id, downloaded_by=user_id)
    except Exception:
        pass

    # 候选人姓名：优先用已有记录，其次用 tool 结果
    candidate_name = (
        (existing.get("candidate_name") if existing else None)
        or inner.get("candidate_name", "")
        or inner.get("name", "")
        or ""
    )

    # 保存文件
    save_dir = os.path.join(CANDIDATE_RESUME_DIR, platform, candidate_id)
    os.makedirs(save_dir, exist_ok=True)
    filename = f"{int(time.time())}_resume.pdf"
    abs_path = os.path.join(save_dir, filename)
    rel_path = os.path.join(platform, candidate_id, filename)

    with open(abs_path, "wb") as f:
        f.write(pdf_bytes)

    file_size_kb = len(pdf_bytes) / 1024

    # 存入 DB（upsert_candidate_resume 会保留搜索时写入的 meta）
    try:
        resume_id = await candidate_resume_db.upsert_candidate_resume(
            platform=platform,
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            file_path=rel_path,
            file_size=len(pdf_bytes),
            downloaded_by=user_id,
        )
    except Exception as e:
        log.error("candidate_resume_db.upsert failed: %s", e)
        resume_id = None

    # 后台提取 PDF 文本
    if resume_id:
        asyncio.create_task(_parse_candidate_pdf(resume_id, abs_path))

    # 构建摘要（移除 base64_pdf，保留其他字段）
    inner.pop("base64_pdf", None)
    summary_parts = [
        f"简历已下载并保存 (PDF, {file_size_kb:.0f}KB)。",
        f"候选人: {candidate_name}" if candidate_name else "",
        f"平台: {platform}, ID: {candidate_id}",
    ]

    # 如果有搜索时预建的 meta，附上关键信息
    meta = (existing.get("candidate_meta") if existing else None) or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    meta_parts = []
    if meta.get("phone"):
        meta_parts.append(f"电话: {meta['phone']}")
    if meta.get("location"):
        meta_parts.append(f"地点: {meta['location']}")
    if meta.get("currentTitle"):
        title = meta["currentTitle"]
        if meta.get("currentCompany"):
            title += f" @ {meta['currentCompany']}"
        meta_parts.append(f"当前: {title}")
    if meta.get("currentWork"):
        meta_parts.append(f"当前: {meta['currentWork']}")
    if meta_parts:
        summary_parts.append("已关联信息: " + ", ".join(meta_parts))

    if inner:
        summary_parts.append(f"其他信息: {json.dumps(inner, ensure_ascii=False)[:500]}")

    return "\n".join(p for p in summary_parts if p)


async def _parse_candidate_pdf(resume_id: int, file_path: str) -> None:
    """后台提取候选人 PDF 文本（复用 resume_parser 的提取逻辑）。"""
    import candidate_resume_db
    try:
        from resume_parser import _extract_text_async
        raw_text = await _extract_text_async(file_path, "pdf")
        await candidate_resume_db.update_parse_status(
            resume_id, "done", raw_text=raw_text,
        )
        log.info("Candidate PDF text extracted: resume_id=%d, %d chars", resume_id, len(raw_text))
    except Exception as e:
        log.error("Candidate PDF parse failed: resume_id=%d: %s", resume_id, e)
        await candidate_resume_db.update_parse_status(resume_id, "failed")


import time as _time_mod
import secrets as _secrets_mod


_JOB_TOOLS_BY_PLATFORM: dict[str, set[str]] = {
    "boss":     {"boss_search_jobs", "boss_get_recommend_jobs"},
    "linkedin": {"linkedin_search_jobs"},
    "indeed":   {"indeed_search_jobs"},
}
_ALL_JOB_SEARCH_TOOLS: set[str] = set().union(*_JOB_TOOLS_BY_PLATFORM.values())


def _platform_of_job_tool(tool_name: str) -> str:
    for p, tools in _JOB_TOOLS_BY_PLATFORM.items():
        if tool_name in tools:
            return p
    return ""


def _build_job_list_card(tool_name: str, structured_data: dict) -> dict | None:
    """把搜索类工具的结构化结果压成前端可渲染的 job_list_card 事件。

    三个平台数据结构不同,按 platform 分支抽字段:
      - Boss:     structured_data.raw.zpData.jobList[]  (含 matchRate, scorer 写入)
      - LinkedIn: structured_data.jobs[]  (jobId / title / companyName / location,
                    scorer 写入 match_percent)
      - Indeed:   structured_data.jobs[]  (jobKey / title / company / location / salary,
                    scorer 写入 match_percent)

    三平台都会被 _score_jobs_with_llm 打分（当有简历摘要且非 evaluate 模式时）。
    无简历 / 打分失败时 match_percent=None, 前端自动隐藏匹配度列且不按匹配度排序。
    返回 SSE 事件 dict; 结构不符或无 job 时返回 None。
    """
    platform = _platform_of_job_tool(tool_name)
    if not platform:
        return None

    jobs_out: list[dict] = []
    scanned = 0

    if platform == "boss":
        try:
            zp = (structured_data.get("raw") or {}).get("zpData") or {}
            job_list = zp.get("jobList") or []
            if not isinstance(job_list, list) or not job_list:
                return None
        except Exception:
            return None
        scanned = len(job_list)
        for idx, j in enumerate(job_list[:30], start=1):
            if not isinstance(j, dict):
                continue
            job_id = j.get("encryptJobId") or j.get("jobId") or ""
            if not job_id:
                continue
            mode = ""
            desc = (j.get("jobDescription") or j.get("positionName") or "").lower()
            if "远程" in desc or "remote" in desc:
                mode = "远程"
            elif "混合" in desc or "hybrid" in desc:
                mode = "混合"
            jobs_out.append({
                "index": idx,
                "job_id": str(job_id),
                "match_percent": int(j["matchRate"]) if isinstance(j.get("matchRate"), (int, float)) else None,
                "title": j.get("jobName", ""),
                "company": j.get("brandName", ""),
                "city": j.get("cityName", ""),
                "mode": mode,
                "salary": j.get("salaryDesc", ""),
            })
    else:
        # LinkedIn / Indeed: flat jobs[] at top level of structured_data
        raw_jobs = structured_data.get("jobs") or []
        if not isinstance(raw_jobs, list) or not raw_jobs:
            return None
        scanned = len(raw_jobs)
        # Phase 2: id_field 改读 platforms_config manifest;company_key 暂保留 if-else
        # (manifest 暂未抽 company_key,之后增量加)
        from platforms_config import get_platform as _get_platform
        _p = _get_platform(platform)
        id_key = (_p.id_field if _p else None) or ("jobId" if platform == "linkedin" else "jobKey")
        company_key = "companyName" if platform == "linkedin" else "company"
        for idx, j in enumerate(raw_jobs[:30], start=1):
            if not isinstance(j, dict):
                continue
            jid = j.get(id_key) or ""
            if not jid:
                continue
            mp_raw = j.get("match_percent")
            jobs_out.append({
                "index": idx,
                "job_id": str(jid),
                # _score_jobs_with_llm 有简历时会原地注入 match_percent;
                # 无简历 / 打分失败时保留 None, 前端自动隐藏匹配度列。
                "match_percent": int(mp_raw) if isinstance(mp_raw, (int, float)) else None,
                "title": j.get("title", ""),
                "company": j.get(company_key, ""),
                "city": j.get("location", ""),
                "mode": "",
                # Indeed 的 ext 端已在 _shapeIndeedJob 里抽取 salary 字符串;
                # LinkedIn 目前没有 salary(jobCard 不直接暴露),留空。
                "salary": j.get("salary", "") or "",
            })

    if not jobs_out:
        return None

    # ── 匹配度策略（产品策略 2026-04-25 修订版,2026-04-28 阈值改为 .env 可配）─
    # **不再** 对低匹配职位做后端硬过滤。理由：scorer 对"相近但不完全对位"
    # 的职位（如"产品运营实习生"vs"Product Manager Intern"）容易给中段分（50s），
    # 一刀切会把整张 card 砍没，用户只剩 markdown 文字、没有任何
    # 可点击的下一步入口。
    #
    # 改为分层：
    #   - 后端 card：永远渲染所有有效职位（含低分），让 UI 能给用户提供
    #     checkbox / 详情 / 投递等可点击 action
    #   - agent 文字回复：按 prompt 规则只推荐 match_percent >= 阈值 的；
    #     全部低于阈值时让 agent 提示换搜索词（search.py 里的"匹配度过滤规则"）
    #   - 阈值由 config.MIN_MATCH_PERCENT 控制(默认 50,可在 .env 调),三平台共用
    LOW_MATCH_THRESHOLD = config.MIN_MATCH_PERCENT  # prompt 端"是否推荐"判断阈值

    matched = sum(
        1 for j in jobs_out
        if (j.get("match_percent") or 0) >= LOW_MATCH_THRESHOLD
    )

    card: dict = {
        "type": "job_list_card",
        "card_id": f"jlc_{int(_time_mod.time())}_{_secrets_mod.token_hex(4)}",
        "platform": platform,
        "tool": tool_name,
        "scanned": scanned,
        "matched": matched,
        "jobs": jobs_out,
    }

    # Indeed 求职端 spec 4.3 四动作。Indeed Easy Apply 不自动(每家公司流程
    # 差异巨大,易跳外部 ATS),所以**没有"投递"action** —— 只能"查看详情"
    # 跳转 indeed.com 让用户手动 apply。
    if platform == "indeed":
        card["actions"] = [
            {
                "id": "analyze",
                "label_zh": "分析职位",
                "label_en": "Analyze",
                "send_template_zh": "分析编号 {ids} 的职位",
                "send_template_en": "Analyze jobs {ids}",
                "variant": "secondary",
                "require_selection": True,
            },
            {
                "id": "interview_prep",
                "label_zh": "面试准备",
                "label_en": "Interview prep",
                "send_template_zh": "为编号 {ids} 的职位准备面试",
                "send_template_en": "Prepare interview for jobs {ids}",
                "variant": "secondary",
                "require_selection": True,
            },
            {
                "id": "view_details",
                "label_zh": "查看详情",
                "label_en": "View detail",
                "send_template_zh": "__job_action__:view_details:indeed:{cardId}:{ids}".replace(
                    "{cardId}", card["card_id"]
                ),
                "send_template_en": "__job_action__:view_details:indeed:{cardId}:{ids}".replace(
                    "{cardId}", card["card_id"]
                ),
                "variant": "primary",
                "require_selection": True,
            },
        ]

    # LinkedIn 求职端 spec 4.3 五动作。chip 文字回流到 agent,由 prompt
    # 段"推荐后的追问 chip"决定后续路由。LinkedIn 的"消息 招聘经理"路径会
    # 触发前端先调 linkedin_get_connection_degree,然后弹 LinkedinComposeModal。
    if platform == "linkedin":
        card["actions"] = [
            {
                "id": "analyze",
                "label_zh": "分析职位",
                "label_en": "Analyze",
                "send_template_zh": "分析编号 {ids} 的职位",
                "send_template_en": "Analyze jobs {ids}",
                "variant": "secondary",
                "require_selection": True,
            },
            {
                "id": "interview_prep",
                "label_zh": "面试准备",
                "label_en": "Interview prep",
                "send_template_zh": "为编号 {ids} 的职位准备面试",
                "send_template_en": "Prepare interview for jobs {ids}",
                "variant": "secondary",
                "require_selection": True,
            },
            {
                "id": "view_details",
                "label_zh": "查看详情",
                "label_en": "View detail",
                "send_template_zh": "__job_action__:view_details:linkedin:{cardId}:{ids}".replace(
                    "{cardId}", card["card_id"]
                ),
                "send_template_en": "__job_action__:view_details:linkedin:{cardId}:{ids}".replace(
                    "{cardId}", card["card_id"]
                ),
                "variant": "secondary",
                "require_selection": True,
            },
            {
                "id": "msg_recruiter",
                "label_zh": "消息招聘经理",
                "label_en": "Msg recruiter",
                "send_template_zh": "给编号 {ids} 的职位招聘经理发消息",
                "send_template_en": "Message recruiter for jobs {ids}",
                "variant": "primary",
                "require_selection": True,
            },
        ]

    return card


# ── B6: 招聘方候选人搜索结果富卡片 ────────────────────────────────────────
_CANDIDATE_SEARCH_TOOLS = {
    "boss_search_candidates",
    "linkedin_search_candidates",
    "indeed_employer_search_resumes",
    "indeed_employer_search_candidates",
}


def _platform_of_candidate_tool(tool_name: str) -> str:
    if tool_name.startswith("boss_"):
        return "boss"
    if tool_name.startswith("linkedin_"):
        return "linkedin"
    if tool_name.startswith("indeed_"):
        return "indeed"
    return ""


def _build_candidate_list_card(tool_name: str, structured_data: dict) -> dict | None:
    """把搜索候选人类工具的结构化结果压成 candidate_list_card 事件。

    平台数据形态(初版按观察实现,实际接入后可微调):
      - Boss:     structured_data.raw.zpData.results[]  with encryptGeekId / name /
                  positionName / company / city / experience / education / matchRate
      - LinkedIn: structured_data.candidates[]  with publicIdentifier / name /
                  headline / current_role / current_company / location / connection_degree
      - Indeed search_resumes: structured_data.candidates[]  with submission_id /
                  display_name / current_role / current_company / location / yoe
      - Indeed search_candidates(申请人): structured_data.candidates[] with similar shape

    无简历 / 打分失败时 match_percent=None。三平台默认 actions:
      analyze / message / view_profile。Indeed 招聘端额外有 set_feedback。
    """
    platform = _platform_of_candidate_tool(tool_name)
    if not platform:
        return None

    raw_list: list = []
    if platform == "boss":
        try:
            zp = (structured_data.get("raw") or {}).get("zpData") or {}
            # Boss 搜候选人结果可能在 results / list / items 里,容错读
            raw_list = zp.get("results") or zp.get("list") or zp.get("items") or []
        except Exception:
            return None
    else:
        raw_list = (
            structured_data.get("candidates")
            or structured_data.get("resumes")
            or structured_data.get("submissions")
            or []
        )

    if not isinstance(raw_list, list) or not raw_list:
        return None

    candidates_out: list[dict] = []
    scanned = len(raw_list)
    for idx, c in enumerate(raw_list[:30], start=1):
        if not isinstance(c, dict):
            continue
        # 平台异构 → 容错读多个候选 key
        if platform == "boss":
            cid = c.get("encryptGeekId") or c.get("geekId") or ""
            name = c.get("name") or c.get("nickname") or ""
            current_role = c.get("positionName") or c.get("currentPosition") or ""
            current_company = c.get("company") or c.get("currentCompany") or ""
            city = c.get("city") or c.get("cityName") or ""
            yoe = c.get("experience") or c.get("workYears") or None
            mp_raw = c.get("matchRate") or c.get("match_percent")
            edu = c.get("education") or c.get("degree") or ""
        elif platform == "linkedin":
            cid = c.get("publicIdentifier") or c.get("memberUrn") or c.get("urn") or ""
            name = c.get("name") or c.get("fullName") or ""
            current_role = c.get("headline") or c.get("currentRole") or ""
            current_company = c.get("currentCompany") or c.get("company") or ""
            city = c.get("location") or ""
            yoe = c.get("yearsExperience") or c.get("yoe") or None
            mp_raw = c.get("match_percent")
            edu = c.get("education") or ""
        else:  # indeed
            cid = (c.get("submissionId") or c.get("submission_id")
                   or c.get("candidateKey") or c.get("candidate_key") or "")
            name = c.get("displayName") or c.get("display_name") or c.get("name") or ""
            current_role = (c.get("currentRole") or c.get("current_role")
                            or c.get("title") or "")
            current_company = (c.get("currentCompany") or c.get("current_company")
                               or c.get("company") or "")
            city = c.get("location") or c.get("city") or ""
            yoe = c.get("yoe") or c.get("yearsExperience") or None
            mp_raw = c.get("match_percent")
            edu = c.get("education") or c.get("degree") or ""

        if not cid:
            continue
        candidates_out.append({
            "index": idx,
            "candidate_id": str(cid),
            "name": str(name),
            "current_role": str(current_role),
            "current_company": str(current_company),
            "city": str(city),
            "years_experience": int(yoe) if isinstance(yoe, (int, float)) else None,
            "education": str(edu),
            "match_percent": int(mp_raw) if isinstance(mp_raw, (int, float)) else None,
        })

    if not candidates_out:
        return None

    LOW_MATCH_THRESHOLD = config.MIN_MATCH_PERCENT
    matched = sum(
        1 for c in candidates_out
        if (c.get("match_percent") or 0) >= LOW_MATCH_THRESHOLD
    )

    card: dict = {
        "type": "candidate_list_card",
        "card_id": f"clc_{int(_time_mod.time())}_{_secrets_mod.token_hex(4)}",
        "platform": platform,
        "tool": tool_name,
        "scanned": scanned,
        "matched": matched,
        "candidates": candidates_out,
    }

    # 默认 actions(三平台共用 + Indeed 额外 set_feedback)
    base_actions = [
        {
            "id": "analyze",
            "label_zh": "分析候选人",
            "label_en": "Analyze",
            "send_template_zh": "分析编号 {ids} 的候选人",
            "send_template_en": "Analyze candidates {ids}",
            "variant": "secondary",
            "require_selection": True,
        },
        {
            "id": "view_profile",
            "label_zh": "查看简历",
            "label_en": "View profile",
            "send_template_zh": "查看编号 {ids} 的简历",
            "send_template_en": "View profile of candidates {ids}",
            "variant": "secondary",
            "require_selection": True,
        },
        {
            "id": "message",
            "label_zh": "发送消息",
            "label_en": "Message",
            "send_template_zh": "给编号 {ids} 的候选人发消息",
            "send_template_en": "Message candidates {ids}",
            "variant": "primary",
            "require_selection": True,
        },
    ]
    card["actions"] = base_actions

    if platform == "indeed" and tool_name == "indeed_employer_search_candidates":
        # 申请人 spec 5.4 额外动作:标记反馈(GOOD/FAIR/REJECT)
        card["actions"].append({
            "id": "set_feedback",
            "label_zh": "标记反馈",
            "label_en": "Set feedback",
            "send_template_zh": "为编号 {ids} 的申请人标记反馈",
            "send_template_en": "Set feedback for candidates {ids}",
            "variant": "secondary",
            "require_selection": True,
        })

    return card


_SCORING_FIELD_MAP: dict[str, dict] = {
    "boss": {
        "job_list_path": ("raw", "zpData", "jobList"),  # 嵌套路径
        "id_key": "encryptJobId",
        "title_key": "jobName",
        "city_key": "cityName",
        "salary_key": "salaryDesc",
        "skills_key": "skills",
        "industry_key": "brandIndustry",
        "score_field": "matchRate",
    },
    "linkedin": {
        "job_list_path": ("jobs",),
        "id_key": "jobId",
        "title_key": "title",
        "city_key": "location",
        "salary_key": None,            # LinkedIn search 不带薪资
        "skills_key": None,
        "industry_key": None,
        "score_field": "match_percent",
    },
    "indeed": {
        "job_list_path": ("jobs",),
        "id_key": "jobKey",
        "title_key": "title",
        "city_key": "location",
        "salary_key": "salary",        # v1.3.10 后 SERP 解析已带
        "skills_key": None,
        "industry_key": None,
        "score_field": "match_percent",
    },
}


def _get_job_list_by_path(data: dict, path: tuple) -> list | None:
    """按 path 元组定位 job list（支持嵌套，如 Boss 的 raw.zpData.jobList）。"""
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur if isinstance(cur, list) else None


async def _score_jobs_with_llm(
    client: AsyncAnthropic,
    structured_data: dict,
    resume_summary: str,
    platform: str = "boss",
) -> None:
    """
    用 LLM 对职位列表打匹配分（0-100），原地注入对应平台的 score 字段。
    - Boss:     写入 j["matchRate"]（ 来自 _SCORING_FIELD_MAP）
    - LinkedIn: 写入 j["match_percent"]
    - Indeed:   写入 j["match_percent"]

    失败时静默跳过，不影响主流程。
    """
    spec = _SCORING_FIELD_MAP.get(platform)
    if not spec:
        return

    try:
        job_list = _get_job_list_by_path(structured_data, spec["job_list_path"])
        if not isinstance(job_list, list) or not job_list:
            return
    except Exception:
        return

    id_key = spec["id_key"]
    compact_jobs = []
    for j in job_list[:30]:
        if not isinstance(j, dict):
            continue
        jid = j.get(id_key) or ""
        if not jid:
            continue
        compact = {
            "id": str(jid),
            "title": j.get(spec["title_key"], ""),
            "city": j.get(spec["city_key"], ""),
        }
        if spec["salary_key"] and j.get(spec["salary_key"]):
            compact["salary"] = j.get(spec["salary_key"], "")
        if spec["skills_key"]:
            compact["skills"] = (j.get(spec["skills_key"]) or [])[:5]
        if spec["industry_key"]:
            compact["industry"] = j.get(spec["industry_key"], "")
        compact_jobs.append(compact)

    if not compact_jobs:
        return

    prompt = (
        f"{resume_summary}\n\n"
        "以下是搜索到的职位（JSON）：\n"
        f"{json.dumps(compact_jobs, ensure_ascii=False)}\n\n"
        "请对每个职位与我的简历的匹配度评分（0-100整数），"
        "综合考虑职位名称、城市、薪资、技能要求。\n"
        '只输出 JSON 数组，格式：[{"id": "...", "score": 85}, ...]\n'
        "不要输出任何其他文字。"
    )

    try:
        resp = await client.messages.create(
            model=config.SCORING_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return
        scores: list[dict] = json.loads(m.group())
        score_map = {
            str(item["id"]): max(0, min(100, int(item["score"])))
            for item in scores
            if item.get("id") is not None and isinstance(item.get("score"), (int, float))
        }
        score_field = spec["score_field"]
        for j in job_list:
            jid = str(j.get(id_key) or "")
            if jid in score_map:
                j[score_field] = score_map[jid]
    except Exception as e:
        log.debug("Job scoring skipped (platform=%s): %s", platform, e)




# ── Local tool: update_language_preference ───────────────────────────────────
_LANG_TOOL = {
    "name": "update_language_preference",
    "description": (
        "Update the user's language preference for future responses. "
        "Call this ONLY when the user explicitly requests to change the response language "
        "(e.g., 'please respond in English', '请用中文回答'). "
        "Do NOT call this for routine queries."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "description": "BCP 47 language tag, e.g. 'zh' for Chinese, 'en' for English.",
            }
        },
        "required": ["language"],
    },
}


async def run_agent_turn(
    messages: list[dict],
    user_text: str,
    ext_session_id: str = "",
    app_user_id: str = "",
    user_id: str = "",
    search_session_id: str = "",
    platform: str = "boss",
    user_tier: str = "",
    request_id: str = "",
    current_mode: str = "search",
    role_type: str = "",
    language: str = "",
    last_turn_tool_usage: set[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    执行一轮 agent 推理，yield protocol dict。
    messages 列表会被原地追加（caller 拥有 messages）。

    Phase 2: mode detection 支持 tool_usage_hint（来自上轮 turn_tools 事件）
    + mid-turn mode 升级（search → evaluate → apply 根据本轮 tool 调用实时升级）。
    """
    messages.append({"role": "user", "content": user_text})

    # ── Mode detection ────────────────────────────────────────────────────────
    mode_name = detect_mode(
        user_text, current_mode, role_type,
        tool_usage_hint=last_turn_tool_usage,
    )
    mode_def = get_mode(mode_name)
    if mode_def is None:
        mode_name = "search"
        mode_def = get_mode("search")

    yield {"type": "mode_detected", "mode": mode_name}

    log.info("[agent-turn] user=%s model=%s mode=%s history=%d msg=%s",
             user_id or "-", config.MODEL, mode_name, len(messages) - 1, user_text[:300])

    # Sprint 6 / G3: Credit 预检（pre-call）。BILLING_ENFORCE=1 时强制阻断
    # 余额不足请求；=0 时仅日志，业务继续跑（开发/灰度模式）。
    # check_balance 跨服务调 job-api-gateway:/billing/check —— 不可达时 fail-open。
    if user_id:
        try:
            import billing_client
            chk = await billing_client.check_balance(user_id, model_id=config.MODEL)
            if not chk.get("can_afford") and chk.get("enforce"):
                yield {
                    "type": "insufficient_credit",
                    "cost": chk.get("cost", 0),
                    "balance": chk.get("balance", 0),
                    "plan": chk.get("plan", ""),
                    "model": chk.get("model_id") or config.MODEL,
                    "sku": chk.get("sku", ""),
                }
                # 移除刚 append 的 user message（避免在不扣费情况下污染历史）
                if messages and messages[-1].get("role") == "user":
                    messages.pop()
                return
            if not chk.get("can_afford"):
                log.info("[agent-turn] user=%s 余额不足但 BILLING_ENFORCE=0,继续放行: cost=%s balance=%s",
                         user_id[:8], chk.get("cost"), chk.get("balance"))
        except Exception as e:
            log.warning("[agent-turn] billing check 异常，fail-open: %s", e)

    resume_summary = await _build_resume_summary(user_id)
    preferences_summary = await _build_preferences_summary(user_id, platform)

    # A: recruiter role 时,把候选人画像 + 招呼模板注入 system prompt
    # 拼到 preferences_summary 后面(compose_system_prompt 把 preferences_summary 当成
    # 一段 markdown 附加,不需要新参数 — 招聘方相关段在那段后追加即可)
    if role_type == "recruiter":
        persona = await _build_recruiter_persona_summary(user_id, platform)
        templates = await _build_recruiter_templates_summary(user_id, platform)
        extra_segments = [s for s in (persona, templates) if s]
        if extra_segments:
            if preferences_summary:
                preferences_summary = preferences_summary + "\n\n" + "\n\n".join(extra_segments)
            else:
                preferences_summary = "\n\n".join(extra_segments)

    headers: dict[str, str] = {}
    if user_id:
        headers["x-user-id"] = user_id
    if ext_session_id:
        headers["x-ext-session-id"] = ext_session_id
    if app_user_id:
        headers["x-app-user-id"] = app_user_id
    if user_tier:
        headers["x-user-tier"] = user_tier
    if request_id:
        headers["x-request-id"] = request_id
    # Phase 2: 同 user 可能同时连了 jobseeker + recruiter 两个 ext。带上 role
    # 让 api-gateway _resolve_and_bind 按 ext_kind 选对应 session,不再 ambiguous error。
    if role_type:
        headers["x-user-role"] = role_type

    client_kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_BASE_URL:
        client_kwargs["base_url"] = config.ANTHROPIC_BASE_URL

    async with streamablehttp_client(
        config.BOSS_GATEWAY_MCP,
        headers=headers if headers else None,
        timeout=300.0,
        sse_read_timeout=300.0,
    ) as (read, write, _):
        async with ClientSession(read, write) as boss_session:
            await boss_session.initialize()
            boss_tools_resp = await boss_session.list_tools()
            boss_tool_names = {t.name for t in boss_tools_resp.tools}

            # 合并工具列表：boss（每 turn 新连接）+ 额外 server（启动时持久连接）
            all_tools = [mcp_tool_to_anthropic(t) for t in boss_tools_resp.tools]
            for tool_name in mcp_manager.extra_tools():
                extra_sess, _ = mcp_manager.get(tool_name)
                if extra_sess:
                    # 从 manager 拿工具定义（已在 start() 时缓存到 entry.tools）
                    pass  # 工具定义在下面从 session 拿，此处仅判断存在性

            # 重新从各 extra session 拉一次 tools（定义可能更新）
            seen: set[str] = set()
            for entry in mcp_manager._entries.values():
                if entry.session:
                    try:
                        extra_tools_resp = await entry.session.list_tools()
                        for t in extra_tools_resp.tools:
                            if t.name not in seen:
                                all_tools.append(mcp_tool_to_anthropic(t))
                                seen.add(t.name)
                    except Exception:
                        pass

            # ── Mode-based tool filtering ─────────────────────────────────────
            if mode_def.tool_filter is not None:
                all_tools = mode_def.tool_filter(all_tools)

            # ── Per-platform tool filtering (B3) ─────────────────────────────
            # Each platform-specific agent (Boss/LinkedIn/Indeed) only sees its
            # own platform's tools + 跨平台白名单(resume / preferences / lang)。
            # 防止 LLM 跨平台胡说,prompt 也不需要 if Boss / elif LinkedIn 分支。
            all_tools = _filter_tools_for_session(all_tools, platform=platform, role_type=role_type)
            # Phase 2 audit:旧硬编码 _INDEED_NO_GO 块已迁入 modes/cells.py:
            #   _CELL_JS_INDEED.tool_deny_patterns(jobseeker apply 相关 8 条)
            #   _CELL_RC_INDEED.tool_deny_patterns(employer publish/edit 4 条)
            # _filter_tools_for_session 已经按 cell 过滤,这里不需要二次施加。

            # ── Inject local tools (handled in-process, not routed to MCP) ──
            all_tools.append(_LANG_TOOL)

            client = AsyncAnthropic(**client_kwargs)

            total_input = 0
            total_output = 0
            total_cache_creation = 0
            total_cache_read = 0
            total_cost_usd = 0.0

            # 主模型 + fallback 模型列表
            _models = [config.MODEL]
            if config.FALLBACK_MODEL and config.FALLBACK_MODEL != config.MODEL:
                _models.append(config.FALLBACK_MODEL)

            # ── Phase 2 实时化：本轮 tool_use 轨迹 + mid-turn mode 升级 ─────
            _turn_tools: set[str] = set()
            _inferred_mode: str = mode_name  # turn 起点
            # 漏斗语义：search / recruiter 都算"起点"（同 ladder=0），
            # evaluate 算"评估阶段"，apply 算"动作阶段"。升级只向高走。
            # recruiter role 场景：起点 recruiter (0) → 调 get_candidate_detail 升
            # evaluate (1) → 调 contact_candidate 升 apply (2)，和求职者漏斗对称。
            # 审核补丁 #5：把 recruiter 纳入 ladder 使 recruiter role 也能享受
            # mid-turn mode 升级（之前 _inferred_mode=recruiter 不在 ladder 里
            # → 条件失败 → badge 不升级）。
            _MODE_LADDER = {
                "search": 0,
                "recruiter": 0,
                "evaluate": 1,
                "compare": 1,
                "apply": 2,
                "interview": 2,
            }

            while True:
                await asyncio.sleep(0)

                resp = None
                for _midx, _model in enumerate(_models):
                    _text_yielded = False
                    # 对 fallback 模型指定 OpenRouter provider，避免 anthropic-version 头
                    # 导致 OpenRouter 把请求路由给 Anthropic 提供商
                    _extra_body: dict | None = None
                    if _midx > 0 and "/" in _model:
                        _provider = _model.split("/")[0]
                        _extra_body = {"provider": {"order": [_provider], "allow_fallbacks": False}}
                    # extended thinking 仅对 Claude 主模型开启；fallback 走 OpenRouter
                    # 第三方模型时通常不支持 thinking 参数，直接关掉防 400。
                    _thinking_param: dict | None = None
                    _is_claude_native = _midx == 0 or "/" not in _model
                    if config.ENABLE_THINKING and _is_claude_native:
                        _thinking_param = {
                            "type": "enabled",
                            "budget_tokens": min(config.THINKING_BUDGET_TOKENS,
                                                  max(1024, config.MAX_TOKENS - 1024)),
                        }

                    try:
                        _stream_kwargs = dict(
                            model=_model,
                            max_tokens=config.MAX_TOKENS,
                            system=compose_system_prompt(
                                # B5: per-platform 取精简 prompt(模式有 compose_per_platform 才生效;
                                # 其它 mode 无 composer 时降级到全量 system_prompt)
                                mode_def.get_prompt(platform),
                                platform, resume_summary, config.DEBUG_MODE,
                                language=language,
                                preferences_summary=preferences_summary,
                                role_type=role_type,
                            ),
                            tools=all_tools,
                            messages=messages,
                        )
                        if _extra_body:
                            _stream_kwargs["extra_body"] = _extra_body
                        if _thinking_param:
                            _stream_kwargs["thinking"] = _thinking_param

                        # 累积本轮完整 thinking，stream 收尾后单条 yield 一次给 DB 落库
                        _thinking_buf: list[str] = []

                        async with client.messages.stream(**_stream_kwargs) as stream:
                            # 改成消费完整 stream 事件，按 type 分发：
                            # - content_block_delta + delta.type='thinking_delta' → 流式 reasoning
                            # - content_block_delta + delta.type='text_delta'    → 流式 text（兼容老 text_stream 行为）
                            async for event in stream:
                                etype = getattr(event, "type", None)
                                if etype != "content_block_delta":
                                    continue
                                delta = getattr(event, "delta", None)
                                dtype = getattr(delta, "type", None) if delta else None
                                if dtype == "thinking_delta":
                                    chunk = getattr(delta, "thinking", "") or ""
                                    if chunk:
                                        _thinking_buf.append(chunk)
                                        yield {"type": "thinking_delta", "delta": chunk}
                                elif dtype == "text_delta":
                                    text_chunk = getattr(delta, "text", "") or ""
                                    if text_chunk:
                                        _text_yielded = True
                                        yield {"type": "text_delta", "delta": text_chunk}
                            resp = await stream.get_final_message()

                        # thinking 段一次性 yield 完整文本，给 DB 落档；前端忽略也无害
                        if _thinking_buf:
                            yield {"type": "thinking_done",
                                   "content": "".join(_thinking_buf)}
                        break  # 成功，跳出 fallback 循环
                    except Exception as exc:
                        if (
                            not _text_yielded
                            and _midx < len(_models) - 1
                            and _is_retryable(exc)
                        ):
                            log.warning(
                                "Model %s overloaded, falling back to %s",
                                _model, _models[_midx + 1],
                            )
                            continue
                        raise

                # 累计 token 用量 + 成本（prompt caching: write 1.25×, read 0.10×）
                # cost 计算:**优先用 _model(我们 request 用的名字,确保在 MODEL_PRICING 表里)**,
                # resp.model 作为 fallback。OpenRouter 流式返回的 resp.model 不稳定 — 有时是
                # 简化名(claude-opus-4 vs claude-opus-4-6)或 provider 别名,会导致 normalize
                # 后查不到 pricing → 默认返 0,把 cost 全部抹零。
                usage = getattr(resp, "usage", None)
                in_tok = out_tok = cache_c_tok = cache_r_tok = 0
                cost_usd = 0.0
                if usage:
                    in_tok       = getattr(usage, "input_tokens", 0) or 0
                    out_tok      = getattr(usage, "output_tokens", 0) or 0
                    cache_c_tok  = getattr(usage, "cache_creation_input_tokens", 0) or 0
                    cache_r_tok  = getattr(usage, "cache_read_input_tokens", 0) or 0
                    total_input         += in_tok
                    total_output        += out_tok
                    total_cache_creation += cache_c_tok
                    total_cache_read     += cache_r_tok
                    cost_usd = pricing.compute_cost(
                        _model or getattr(resp, "model", ""),
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cache_creation_input_tokens=cache_c_tok,
                        cache_read_input_tokens=cache_r_tok,
                    )
                    total_cost_usd += cost_usd
                    yield {
                        "type": "token_usage",
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "cache_creation_input_tokens": cache_c_tok,
                        "cache_read_input_tokens": cache_r_tok,
                        "cost_usd": cost_usd,
                        "total_input_tokens": total_input,
                        "total_output_tokens": total_output,
                        "total_cache_creation_input_tokens": total_cache_creation,
                        "total_cache_read_input_tokens": total_cache_read,
                        "total_cost_usd": round(total_cost_usd, 6),
                        "model": getattr(resp, "model", "") or _model,
                    }

                # Sprint 6 / G3: Credit 后扣费（post-call fire-and-forget）
                # 即使 pre-check 已 fail-open，post-call 仍尝试扣 → 让 balance ledger 完整
                # 实际模型用 _model（我们 request 的），resp.model 不稳定（OpenRouter 别名）
                actual_model = _model or getattr(resp, "model", "") or config.MODEL
                if user_id and actual_model:
                    try:
                        import billing_client
                        billing_client.charge_async(
                            user_id, model_id=actual_model,
                            meta={"in_tok": in_tok, "out_tok": out_tok,
                                  "session_id": ext_session_id or "",
                                  "mode": mode_name},
                        )
                    except Exception as e:
                        log.debug("[agent-turn] charge_async dispatch failed: %s", e)

                if resp.stop_reason != "tool_use":
                    messages.append({"role": "assistant", "content": _content_to_dicts(resp.content)})
                    log.info(
                        "[agent-end] user=%s stop=%s in=%d out=%d cache_c=%d cache_r=%d "
                        "total_in=%d total_out=%d total_cost=$%.4f",
                        user_id or "-", resp.stop_reason,
                        in_tok, out_tok, cache_c_tok, cache_r_tok,
                        total_input, total_output, total_cost_usd,
                    )
                    # Phase 2 实时化：turn 结束时把用过的工具快照告诉 sse_router
                    # 持久化到 session.last_turn_tool_usage，下一轮 detect_mode 可用
                    if _turn_tools:
                        yield {"type": "turn_tools", "tools": sorted(_turn_tools)}
                    yield {
                        "type": "message_end",
                        "stop_reason": resp.stop_reason,
                        "total_input_tokens": total_input,
                        "total_output_tokens": total_output,
                        "total_cache_creation_input_tokens": total_cache_creation,
                        "total_cache_read_input_tokens": total_cache_read,
                        "total_cost_usd": round(total_cost_usd, 6),
                    }
                    break

                messages.append({"role": "assistant", "content": _content_to_dicts(resp.content)})
                tool_results = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue

                    tool_input = dict(block.input)

                    # ── 本地工具：update_language_preference（不路由到 MCP）──
                    if block.name == "update_language_preference":
                        new_lang = tool_input.get("language", "")
                        log.info("[lang] user=%s set language=%s", user_id, new_lang)
                        yield {"type": "tool_call", "tool": block.name, "input": tool_input}
                        yield {"type": "_language_updated", "language": new_lang}
                        ack = "Language preference updated." if new_lang.startswith("en") else "语言偏好已更新。"
                        yield {
                            "type": "tool_result",
                            "tool": block.name,
                            "ok": True,
                            "preview": ack,
                        }
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": ack,
                            "is_error": False,
                        })
                        continue

                    # 路由：boss 工具用本 turn 的 boss_session，其余用 mcp_manager 的持久 session
                    if block.name in boss_tool_names:
                        # 透明注入 session_id（优先）或 app_user_id（fallback）
                        # job-api-gateway 的 _resolve_and_bind 两者都能路由到正确的扩展会话
                        if ext_session_id and "session_id" not in tool_input:
                            tool_input = {**tool_input, "session_id": ext_session_id}
                        elif app_user_id and "session_id" not in tool_input and "app_user_id" not in tool_input:
                            tool_input = {**tool_input, "app_user_id": app_user_id}
                        # 透明注入 search_session_id（仅 boss_search_jobs）
                        if block.name == "boss_search_jobs" and search_session_id and "search_session_id" not in tool_input:
                            tool_input = {**tool_input, "search_session_id": search_session_id}
                        target_session = boss_session
                        target_lock = None
                    else:
                        target_session, target_lock = mcp_manager.get(block.name)
                        if target_session is None:
                            target_session = boss_session  # fallback
                            target_lock = None

                    log.info("[tool→] %s input=%s", block.name,
                             json.dumps(tool_input, ensure_ascii=False)[:400])

                    # ── Pre-flight 校验：security_id 缺失短路 ───────────────
                    # 列表为空 / token chain 没建立时,Agent 偶尔会硬塞 security_id=""
                    # 走到 ext + commands.py 后端校验抛 "security_id 必填",浪费 ~400ms
                    # ext round-trip + 污染 error_logs。这里本地直接合成 tool_result,
                    # 用引导性文字让 Agent 学会下一轮换路径。
                    _need_sid = block.name in _TOOLS_REQUIRE_SECURITY_ID
                    if _need_sid and not (tool_input.get("security_id") or "").strip():
                        # 注意:boss_geek_filter_by_label(求职端消息列表)的返回里
                        # **不含 securityId** —— 必须用户先在 Boss 浏览器打开该
                        # 对话让扩展 intercept friend/add 才能拿 chatSecurityId。
                        _is_geek_chat_path = block.name in (
                            "boss_geek_get_boss_data", "boss_get_chat_history",
                        )
                        if _is_geek_chat_path:
                            _hint = (
                                f"{block.name} 拒绝执行:security_id 缺失。\n\n"
                                "求职端消息列表(boss_geek_filter_by_label)**不返回 chatSecurityId**,"
                                "需要扩展先 intercept friend/add 响应进 tokenStore 才能查询。\n\n"
                                "**正确做法**(给用户的提示模板):\n"
                                "> 要查看和 {boss 姓名} 的聊天历史,需要先让扩展捕获本次会话的安全令牌:\n"
                                "> 1. 打开 Boss 直聘的「消息」页面\n"
                                "> 2. 点击和 {boss 姓名} 的对话(扩展会自动捕获 token)\n"
                                "> 3. 回到这里点下面的按钮重新查询\n"
                                "> `[👉 打开 Boss 消息页](https://www.zhipin.com/web/geek/chat), [我已打开过对话，重新查看]`\n\n"
                                "**绝对不要**传空串重试,**也不要**用 encryptFriendId 当 encrypt_job_id 调。"
                            )
                        else:
                            _hint = (
                                f"{block.name} 拒绝执行:security_id 缺失。"
                                "上游列表(boss_search_jobs / boss_search_candidates / "
                                "boss_chatted_jobs / boss_get_candidate_detail 等)的返回项里**没拿到** "
                                "securityId,或 token chain 没建立。"
                                "**不要**传空串重试 —— 改为:"
                                "(1) 若是因列表返回空,告知用户该会话不在 Boss 当前消息列表里"
                                "(可能 Cookie 过期 / 已超 30 天 / Boss 端清了),引导刷新或重登录;"
                                "(2) 若是因没先调列表工具,先调对应列表拿到 securityId 再重试。"
                            )
                        yield {"type": "tool_call", "tool": block.name, "input": tool_input}
                        yield {
                            "type": "tool_result",
                            "tool": block.name,
                            "ok": False,
                            "preview": _hint,
                        }
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": _hint,
                            "is_error": True,
                        })
                        log.info("[tool✗pre] %s blocked: missing security_id", block.name)
                        continue

                    yield {"type": "tool_call", "tool": block.name, "input": tool_input}

                    # ── 观测埋点(MVP 5):每次 mcp tool 调用 → mcp_call_log 一行 ──
                    _mcp_started = time.monotonic()
                    _mcp_success = False
                    _mcp_risk: str | None = None
                    try:
                        if target_lock:
                            async with target_lock:
                                result = await asyncio.wait_for(
                                    target_session.call_tool(block.name, tool_input),
                                    timeout=config.TOOL_CALL_TIMEOUT,
                                )
                        else:
                            result = await asyncio.wait_for(
                                target_session.call_tool(block.name, tool_input),
                                timeout=config.TOOL_CALL_TIMEOUT,
                            )
                        result_text = tool_result_text(result)
                        is_error = getattr(result, "isError", False)
                        _mcp_success = not is_error
                        if is_error:
                            _mcp_risk = "generic:tool_error"
                    except asyncio.TimeoutError:
                        result_text = f"工具调用超时（>{config.TOOL_CALL_TIMEOUT:.0f}s）: {block.name}"
                        is_error = True
                        _mcp_risk = "generic:timeout"
                    except Exception as e:
                        result_text = f"工具调用异常: {type(e).__name__}: {e}"
                        is_error = True
                        _mcp_risk = "generic:unknown"
                    finally:
                        # fire-and-forget:不 await,record_mcp_call 内部失败静默
                        try:
                            asyncio.create_task(db.record_mcp_call(
                                user_id=user_id, role=role_type, platform=platform,
                                tool_name=block.name,
                                op_type=db.infer_mcp_op_type(block.name),
                                duration_ms=int((time.monotonic() - _mcp_started) * 1000),
                                success=_mcp_success,
                                risk_signal=_mcp_risk,
                            ))
                        except RuntimeError:
                            pass

                    # ── 身份校验：boss_check_login 后立即比对 role_type ──────
                    # 不依赖 LLM 判断——硬编码拦截，防止求职/招聘账号互用导致环境污染
                    if block.name == "boss_check_login" and not is_error and role_type:
                        try:
                            _d = json.loads(result_text)
                            # 兼容 {result:{data:{...}}} 和 {logged_in:...} 两种包装
                            _inner = _d.get("result") or _d
                            if isinstance(_inner, dict) and "data" in _inner:
                                _inner = _inner["data"]
                            _logged_in  = bool(_inner.get("logged_in", False))
                            _is_recruiter = _inner.get("is_recruiter")  # True/False/None
                            _double_identity = bool(_inner.get("double_identity", False))
                        except Exception:
                            _logged_in, _is_recruiter, _double_identity = False, None, False

                        if _logged_in and _is_recruiter is not None:
                            _want_recruiter = (role_type == "recruiter")
                            # double_identity=True 的账号同时持有招聘/求职两套身份,Boss
                            # 内部用同一 userId + cookies,API 调用对两端都生效(实测
                            # is_recruiter=false 的账号 boss/my_job_list 仍能拿到职位)。
                            # 这种账号无需 logout/重登,直接放行,让后续工具调用按需走。
                            if _want_recruiter != _is_recruiter and not _double_identity:
                                _cur  = "招聘方" if _is_recruiter  else "求职者"
                                _want = "招聘方" if _want_recruiter else "求职者"
                                _btn  = "招聘候选人" if _want_recruiter else "求职找工作"
                                _msg = (
                                    f"检测到身份不匹配：当前 Boss直聘 账号是 **{_cur}** 身份，"
                                    f"但您选择的是{_want}模式。\n\n"
                                    f"为避免账号环境污染和风控误判，需要先退出当前登录，再用 "
                                    f"**{_want}** 身份的账号重新扫码。点击下方按钮一键完成："
                                )
                                yield {"type": "text_delta", "delta": _msg}
                                yield {
                                    "type": "action_buttons",
                                    "actions": [
                                        {
                                            "id": "auto_logout_mismatch",
                                            "label_zh": f"自动退出并切换到「{_want}」",
                                            "label_en": f"Auto logout and switch to {_want}",
                                            "send_text_zh": "请调 boss_logout 退出当前账号，然后提示我用正确身份重新扫码登录。",
                                            "send_text_en": "Please call boss_logout to sign out the current account, then guide me to re-login with the correct role.",
                                            "icon": "logout",
                                            "confirm": {
                                                "title_zh": "确认退出当前 Boss 账号？",
                                                "title_en": "Sign out of the current Boss account?",
                                                "body_zh": (
                                                    f"当前已登录 **{_cur}** 身份的账号，与你选择的"
                                                    f"**{_want}**模式不一致。点击确认后会自动退出 Boss"
                                                    f"登录，然后引导你重新扫码登录。"
                                                ),
                                                "body_en": (
                                                    f"You are currently logged in as a {_cur} account, "
                                                    f"which doesn't match your chosen {_want} mode. "
                                                    f"Confirming will sign you out of Boss and guide you "
                                                    f"through re-login with the correct role."
                                                ),
                                                "confirm_zh": "退出并切换",
                                                "confirm_en": "Sign out & switch",
                                                "cancel_zh": "暂不退出",
                                                "cancel_en": "Not now",
                                            },
                                        },
                                        {
                                            "id": "manual_logout_help",
                                            "label_zh": "手动操作：我自己去 Boss 退出",
                                            "label_en": "I'll log out manually",
                                            "url": "https://www.zhipin.com/web/geek/chat",
                                            "icon": "external",
                                        },
                                    ],
                                }
                                yield {"type": "message_end", "stop_reason": "identity_mismatch"}
                                return

                    # ── Indeed 双登录身份不匹配检测 ─────────────────────
                    # Indeed 求职端(indeed.com)和招聘端(employers.indeed.com)是
                    # 完全独立的两个域 / 两套登录,不可能"切身份"——只能让用户去
                    # 对应入口重新登录。spec 3.3 要求显式提示并给"我已切换"chip。
                    _IS_INDEED_JOBSEEKER_CHECK = block.name == "indeed_check_login"
                    _IS_INDEED_EMPLOYER_CHECK = block.name == "indeed_employer_check_login"
                    if (_IS_INDEED_JOBSEEKER_CHECK or _IS_INDEED_EMPLOYER_CHECK) and not is_error and role_type:
                        try:
                            _d = json.loads(result_text)
                            _inner = _d.get("result") or _d
                            if isinstance(_inner, dict) and "data" in _inner:
                                _inner = _inner["data"]
                            _li = bool(_inner.get("logged_in", False)) if isinstance(_inner, dict) else False
                        except Exception:
                            _li = False
                        # 调了 jobseeker 端 check_login 且登录成功,但用户 intent 是
                        # recruiter → 提示去 employers.indeed.com 登录;反之亦然。
                        _wrong_endpoint = False
                        _target_url = ""
                        _target_zone = ""
                        if _li and _IS_INDEED_JOBSEEKER_CHECK and role_type == "recruiter":
                            _wrong_endpoint = True
                            _target_url = "https://employers.indeed.com"
                            _target_zone = "招聘端 (employers.indeed.com)"
                        elif _li and _IS_INDEED_EMPLOYER_CHECK and role_type == "jobseeker":
                            _wrong_endpoint = True
                            _target_url = "https://indeed.com"
                            _target_zone = "求职端 (indeed.com)"
                        if _wrong_endpoint:
                            _is_en = (language or "").lower().startswith("en")
                            # 双语 zone label (label_en 单独算,只用于 action_buttons)
                            _target_zone_zh = (
                                "招聘端 (employers.indeed.com)" if role_type == "recruiter"
                                else "求职端 (indeed.com)"
                            )
                            _cur_zone_zh = (
                                "招聘端 (employers.indeed.com)" if _IS_INDEED_EMPLOYER_CHECK
                                else "求职端 (indeed.com)"
                            )
                            _target_zone_en = (
                                "Employer portal (employers.indeed.com)" if role_type == "recruiter"
                                else "Jobseeker portal (indeed.com)"
                            )
                            _cur_zone_en = (
                                "Employer portal (employers.indeed.com)" if _IS_INDEED_EMPLOYER_CHECK
                                else "Jobseeker portal (indeed.com)"
                            )
                            if _is_en:
                                _msg = (
                                    f"You're currently signed in to Indeed **{_cur_zone_en}**, "
                                    f"but the mode you chose requires **{_target_zone_en}**. "
                                    f"Indeed's jobseeker and employer portals are separate "
                                    f"entry points and require separate logins. Please open "
                                    f"{_target_url} in your browser, sign in, then click below:"
                                )
                            else:
                                _msg = (
                                    f"检测到您当前登录的是 Indeed **{_cur_zone_zh}**,但您选择的"
                                    f"模式需要登录 **{_target_zone_zh}**。Indeed 求职端和招聘端是"
                                    f"两个独立的入口,需要分别登录。请在浏览器中打开 {_target_url} "
                                    f"完成登录后,点下方按钮:"
                                )
                            yield {"type": "text_delta", "delta": _msg}
                            # intent_hint 反映"用户切换到目标端"的语义 ——
                            # 招聘端切换 chip 标 recruiter,反向标 jobseeker。
                            # 前端 BossMessageBubble:fireAction 按 intent_hint
                            # 同步 setUserIntent,替代之前对 send_text 的脆弱 grep。
                            _intent_hint_for_chip = (
                                "recruiter" if role_type == "recruiter" else "jobseeker"
                            )
                            yield {
                                "type": "action_buttons",
                                "actions": [
                                    {
                                        "id": "open_indeed_target_zone",
                                        "label_zh": f"打开 {_target_zone_zh}",
                                        "label_en": f"Open {_target_zone_en}",
                                        "url": _target_url,
                                        "icon": "external",
                                        "intent_hint": _intent_hint_for_chip,
                                    },
                                    {
                                        "id": "indeed_recheck_after_switch",
                                        "label_zh": "我已切换，检查登录状态",
                                        "label_en": "I've switched — check status",
                                        "send_text_zh": f"我已经在 {_target_zone_zh} 完成登录，请再次检查我的登录状态",
                                        "send_text_en": f"I've signed in to Indeed {_target_zone_en}, please re-check my login status",
                                        "icon": "refresh",
                                        "intent_hint": _intent_hint_for_chip,
                                    },
                                ],
                            }
                            yield {"type": "message_end", "stop_reason": "indeed_endpoint_mismatch"}
                            return

                    # ── 拦截候选人搜索结果：预建 DB 记录 ─────────────────
                    if not is_error and block.name in _CANDIDATE_LIST_TOOLS:
                        await _intercept_candidate_list(
                            result_text, block.name, tool_input, user_id,
                        )

                    # ── Indeed Smart Sourcing / 申请人列表 反作弊埋点自动 fire ──
                    # search_resumes 或 find_applicants 返回 rcpRequestId + 候选人/
                    # 申请人列表时,**静默** fire log_candidate_seen,不暴露给 LLM 决策。
                    # Indeed 风控要求每次搜索 / 列表加载后给所有曝光候选人打"已浏览"标,
                    # 漏调会限流。fire-and-forget:不阻塞当前 turn,失败也不影响主流程;
                    # 通过同一 MCP target_session 调用,跨进程边界安全。
                    if (
                        not is_error
                        and block.name in (
                            "indeed_employer_search_resumes",       # spec 5.2 主动寻源
                            "indeed_employer_find_applicants",      # spec 5.4 申请人列表
                        )
                    ):
                        try:
                            _ssr = json.loads(result_text)
                            _inner = _ssr.get("result") or _ssr
                            if isinstance(_inner, dict) and "data" in _inner:
                                _inner = _inner["data"]
                            _rcp_id = (_inner or {}).get("rcpRequestId") or ""
                            # search_resumes 返回 candidates[].accountKey
                            # find_applicants 返回 applicants[].submission_id (legacyID 可能就是 candidate id)
                            _cands = ((_inner or {}).get("candidates") or [])
                            _apps = ((_inner or {}).get("applicants") or [])
                            _ids = [c.get("accountKey") for c in _cands if c.get("accountKey")]
                            if not _ids:
                                # find_applicants 路径:用 legacy_id 作为 candidate identifier
                                _ids = [a.get("legacy_id") for a in _apps if a.get("legacy_id")]
                            _surface = (
                                "candidate-list-page"
                                if block.name == "indeed_employer_find_applicants"
                                else "sourcing-search"
                            )
                            if _rcp_id and _ids:
                                async def _fire_log_seen(sess, lock, rcp, ids, surface):
                                    try:
                                        _payload = {
                                            "candidate_ids": json.dumps(ids),
                                            "rcp_request_id": rcp,
                                            "surface": surface,
                                            "grps": "",
                                        }
                                        if lock:
                                            async with lock:
                                                await asyncio.wait_for(
                                                    sess.call_tool("indeed_employer_log_candidate_seen", _payload),
                                                    timeout=10.0,
                                                )
                                        else:
                                            await asyncio.wait_for(
                                                sess.call_tool("indeed_employer_log_candidate_seen", _payload),
                                                timeout=10.0,
                                            )
                                    except Exception as e:
                                        log.info("[indeed-log-seen] fire-and-forget 失败(忽略): %s", e)
                                _bg_task = asyncio.create_task(_fire_log_seen(
                                    target_session, target_lock, _rcp_id, _ids, _surface,
                                ))
                                # 持有引用 + 完成时自移除,避免 GC + PEP 524 警告
                                _BG_FIRE_AND_FORGET_TASKS.add(_bg_task)
                                _bg_task.add_done_callback(_BG_FIRE_AND_FORGET_TASKS.discard)
                                log.info("[indeed-log-seen] 自动 fire rcp=%s surface=%s for %d ids (from %s)",
                                         _rcp_id[:8], _surface, len(_ids), block.name)
                        except Exception as e:
                            log.debug("[indeed-log-seen] 解析跳过: %s", e)

                    # ── 拦截 base64_pdf：存储到磁盘 + DB，替换为摘要 ────────
                    if not is_error and "base64_pdf" in result_text:
                        result_text = await _intercept_base64_pdf(
                            result_text, block.name, tool_input, user_id,
                        )

                    log.info("[tool←] %s ok=%s result=%s", block.name, not is_error, result_text[:400])

                    _QR_TOOLS = {
                        "boss_login", "boss_capture_qr",
                        "boss_generate_qrcode", "boss_wait_for_login",
                    }
                    preview_size = 500 if block.name in _QR_TOOLS else 200

                    structured_data = None
                    _RECRUITER_DATA_TOOLS = {"boss_list_my_jobs", "boss_rec_job_list", "boss_refresh_my_jobs"}
                    # 所有平台的搜索结果都要解 structured_data 供 job_list_card 使用
                    if (block.name in _ALL_JOB_SEARCH_TOOLS or block.name in _RECRUITER_DATA_TOOLS) and not is_error:
                        try:
                            structured_data = json.loads(result_text)
                        except Exception:
                            pass
                        # LLM 匹配评分：三平台求职侧搜索工具都跑，有简历摘要时对职位列表打分。
                        # evaluate mode 的 system prompt 已指导 LLM 内联产出评估，跳过额外打分。
                        if (block.name in _ALL_JOB_SEARCH_TOOLS
                                and structured_data
                                and resume_summary
                                and mode_name != "evaluate"):
                            _platform = _platform_of_job_tool(block.name)
                            await _score_jobs_with_llm(
                                client, structured_data, resume_summary, platform=_platform,
                            )

                    # QR 工具：将 qrcode_dataurl 单独提取为顶层字段，避免 preview 截断
                    qr_dataurl: str | None = None
                    if block.name in _QR_TOOLS and not is_error:
                        try:
                            _parsed = json.loads(result_text)
                            _inner = _parsed.get("result") or _parsed
                            _raw   = (_inner.get("raw") or {}) if isinstance(_inner, dict) else {}
                            _candidate = (
                                _inner.get("qrcode_dataurl")
                                or _inner.get("qr_dataurl")
                                or _raw.get("qrcode_dataurl")
                            )
                            if isinstance(_candidate, str) and _candidate.startswith("data:image"):
                                qr_dataurl = _candidate
                        except Exception:
                            pass

                    # 非 debug 模式下，错误详情不透传给前端
                    frontend_preview = (
                        result_text[:preview_size]
                        if (config.DEBUG_MODE or not is_error)
                        else ""
                    )
                    yield {
                        "type": "tool_result",
                        "tool": block.name,
                        "ok": not is_error,
                        "preview": frontend_preview,
                        **({"data": structured_data} if structured_data is not None else {}),
                        **({"qr_dataurl": qr_dataurl} if qr_dataurl is not None else {}),
                    }

                    # ── Phase 2 实时化：mid-turn mode 升级 ─────────────────
                    # 记录本轮用过的工具（供下一轮 detect_mode 做 tool_usage_hint）；
                    # 并根据 TOOL_MODE_AFFINITY 即时升级 inferred mode（search → evaluate
                    # → apply 单向漏斗），emit mode_detected 让前端 badge 实时变化。
                    # 本轮 system prompt 不重载（保持 stream 连续 + prompt cache）。
                    _turn_tools.add(block.name)
                    _tgt = TOOL_MODE_AFFINITY.get(block.name)
                    if _tgt and _tgt in _MODE_LADDER and _inferred_mode in _MODE_LADDER:
                        if _MODE_LADDER[_tgt] > _MODE_LADDER[_inferred_mode]:
                            _inferred_mode = _tgt
                            yield {"type": "mode_detected", "mode": _inferred_mode}

                    # ── 职位搜索结果 → 推送结构化列表卡 ──────────────────
                    # 三平台: boss / linkedin / indeed 的 search_jobs + boss_get_recommend_jobs
                    # 前端渲染为可勾选列表，后续批量"查看详情"/"沟通"动作由此触发
                    if block.name in _ALL_JOB_SEARCH_TOOLS and not is_error and structured_data:
                        _card = _build_job_list_card(block.name, structured_data)
                        if _card and _card.get("jobs"):
                            yield _card

                    # ── 候选人搜索结果 → 推送结构化候选人卡 (B6) ─────────────
                    # 招聘方搜人路径: boss_search_candidates / linkedin_search_candidates /
                    # indeed_employer_search_resumes / indeed_employer_search_candidates。
                    # 前端 candidatelist.js 渲染富卡(类比 joblist.js)。
                    if block.name in _CANDIDATE_SEARCH_TOOLS and not is_error and structured_data:
                        _ccard = _build_candidate_list_card(block.name, structured_data)
                        if _ccard and _ccard.get("candidates"):
                            yield _ccard

                    # ── *_request_compose → 推送 compose_request 事件 ────────
                    # spec LinkedIn 4.3/5.3 + Indeed 5.3/5.4:agent 调
                    # linkedin_request_compose / indeed_request_compose 就是在
                    # 请前端弹对应平台的 ComposeModal。这两个工具都是 no-op 信号
                    # 工具,真正的发送在 compose 完成后由 send_message / connect /
                    # employer_send_message 执行(见 __*_compose_send__ 回流处理)。
                    if block.name == "linkedin_request_compose" and not is_error:
                        _ti = tool_input if isinstance(tool_input, dict) else {}
                        _degree_raw = _ti.get("connection_degree")
                        try:
                            _degree_int = int(_degree_raw) if _degree_raw is not None else 0
                        except (TypeError, ValueError):
                            _degree_int = 0
                        # 1 = 已连(DM 无字数限制);2/3 = 未连(connection request 300 char);
                        # 0/null = 未知 → 前端按保守的 connection request 处理。
                        _degree_for_frontend: int | None = _degree_int if _degree_int in (1, 2, 3) else None
                        yield {
                            "type": "compose_request",
                            "platform": "linkedin",
                            "member_urn": str(_ti.get("member_urn") or ""),
                            "target_name": str(_ti.get("target_name") or ""),
                            "connection_degree": _degree_for_frontend,
                            "draft_text": str(_ti.get("draft_text") or ""),
                        }
                    if block.name == "indeed_request_compose" and not is_error:
                        _ti = tool_input if isinstance(tool_input, dict) else {}
                        _intent = str(_ti.get("intent") or "message")
                        if _intent not in ("message", "interview_invite"):
                            _intent = "message"
                        yield {
                            "type": "compose_request",
                            "platform": "indeed",
                            "candidate_key": str(_ti.get("candidate_key") or ""),
                            "target_name": str(_ti.get("target_name") or ""),
                            "draft_text": str(_ti.get("draft_text") or ""),
                            "intent": _intent,
                        }

                    # ── 登录检查 → 按结果推送对应 action_buttons ──────────
                    # 通用 action_buttons 事件：前端按 locale 选 label 并把 send_text 作为用户消息发送
                    _CHECK_LOGIN_TOOLS = {
                        "boss_check_login",
                        "linkedin_check_login",
                        "indeed_check_login",
                        "indeed_employer_check_login",
                    }
                    if block.name in _CHECK_LOGIN_TOOLS:
                        _failed = is_error
                        _is_recruiter: bool | None = None
                        if not _failed:
                            try:
                                _d = json.loads(result_text)
                                _inner = _d.get("result", _d)
                                if isinstance(_inner, dict) and "data" in _inner:
                                    _inner = _inner["data"]
                                if isinstance(_inner, dict):
                                    if _inner.get("logged_in") is False:
                                        _failed = True
                                    elif "is_recruiter" in _inner:
                                        _is_recruiter = bool(_inner["is_recruiter"])
                            except Exception:
                                pass
                        if _failed:
                            _platform_name = {
                                "boss_check_login": "Boss直聘",
                                "linkedin_check_login": "LinkedIn",
                                "indeed_check_login": "Indeed",
                                "indeed_employer_check_login": "Indeed Employer",
                            }[block.name]
                            yield {
                                "type": "action_buttons",
                                "actions": [
                                    {
                                        "id": f"recheck_{block.name}",
                                        # spec 3.3 文案对齐:"我已登录，检查登录状态" /
                                        # "I've signed in — check status"
                                        "label_zh": "我已登录，检查登录状态",
                                        "label_en": "I've signed in — check status",
                                        "send_text_zh": f"我已经登录 {_platform_name}，请再次检查我的登录状态",
                                        "send_text_en": f"I've signed in to {_platform_name}, please re-check my login status",
                                        "icon": "refresh",
                                    }
                                ],
                            }
                        # 登录成功路径不再硬塞 action_buttons —— LLM 按 Step 1
                        # 规则（modes/search.py / recruiter.py 里的"⚡ 强制：分步推进"段落）
                        # 自己生成下一步 chip，避免出现两组重复按钮。
                        # 只保留上面 _failed 路径的"再次检查"按钮，那个是错误恢复入口
                        # 不属于"下一步引导"，跟 LLM 自己产出的 chip 不冲突。

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                        "is_error": is_error,
                    })

                messages.append({"role": "user", "content": tool_results})
