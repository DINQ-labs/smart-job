"""
autofill_router.py — 通用表单自动填写：字段语义匹配。

REST 端点：
  POST /autofill/match   表单字段 + 用户档案 → LLM 逐字段判定填值
  GET  /profile          读用户档案（简历扁平字段 + autofill_profile 额外字段）
  PUT  /profile          保存 autofill_profile 额外字段

设计文档：autofill-ext/docs/backend-autofill-match.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

import httpx
from anthropic import AsyncAnthropic
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import form_template_db
import portal_auth
import resume_db
from db import _get_pool
from resume_parser import _parse_llm_response

log = logging.getLogger(__name__)

_MATCH_MODEL = os.getenv("AUTOFILL_MATCH_MODEL", config.MODEL)

# OCR 视觉模型：AUTOFILL_OCR_MODEL 可覆盖。未设时——OpenRouter 默认 Qwen3-VL
# （多模态、便宜，官方描述含 GUI / 文档识别，适合截图找表单）；Anthropic 直连用 config.MODEL。
_IS_OPENROUTER = "openrouter" in (config.BASE_URL or "").lower()
_OCR_MODEL = (os.getenv("AUTOFILL_OCR_MODEL", "").strip()
              or ("qwen/qwen3-vl-30b-a3b-instruct" if _IS_OPENROUTER else config.MODEL))
_MAX_FIELDS = 120
_CACHE_TTL = 3600

# ── 配套表 ──────────────────────────────────────────────────────────────────

_AUTOFILL_SCHEMA = """
CREATE TABLE IF NOT EXISTS autofill_profile (
    user_id    TEXT        PRIMARY KEY,
    fields     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_schema_ready = False


async def init_autofill_db() -> None:
    """建表。server.py 的 _startup 调一次；_ensure_schema 兜底懒建。"""
    global _schema_ready
    pool = await _get_pool()
    await pool.execute(_AUTOFILL_SCHEMA)
    _schema_ready = True


async def _ensure_schema() -> None:
    if not _schema_ready:
        await init_autofill_db()


# ── 档案组装 ────────────────────────────────────────────────────────────────

def _coerce_json(v):
    """asyncpg 的 JSONB 列常以字符串返回，统一解析为 Python 对象。"""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def _flatten_resume(resume: dict | None) -> tuple[dict, dict]:
    """resume_parsed 行 → (扁平标量字段, 简历明细)。"""
    if not resume:
        return {}, {}
    flat: dict = {}

    def put(k, v):
        if v not in (None, "", [], {}):
            flat[k] = v

    put("fullName", resume.get("name"))
    put("email", resume.get("email"))
    put("phone", resume.get("phone"))
    put("experienceYears", resume.get("work_years"))
    put("degree", resume.get("degree"))

    cities = _coerce_json(resume.get("target_cities")) or []
    if cities:
        put("city", cities[0])
    positions = _coerce_json(resume.get("target_positions")) or []

    smin = resume.get("target_salary_min")
    smax = resume.get("target_salary_max")
    sraw = resume.get("target_salary_raw")
    if sraw or smin or smax:
        put("expectedSalary", sraw or f"{smin or ''}-{smax or ''}")

    work = _coerce_json(resume.get("work_experience")) or []
    if work and isinstance(work[0], dict):
        put("currentCompany", work[0].get("company"))
        put("currentTitle", work[0].get("title"))
    if not flat.get("currentTitle") and positions:
        put("currentTitle", positions[0])

    edu = _coerce_json(resume.get("education")) or []
    if edu and isinstance(edu[0], dict):
        put("school", edu[0].get("school"))
        put("major", edu[0].get("major"))

    skills = _coerce_json(resume.get("skills")) or []
    if skills:
        put("skills", ", ".join(str(s) for s in skills))

    detail = {
        "work_experience": work,
        "education": edu,
        "projects": _coerce_json(resume.get("projects")) or [],
        "self_evaluation": resume.get("self_evaluation") or "",
    }
    return flat, detail


async def _get_extra_fields(user_id: str) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT fields FROM autofill_profile WHERE user_id = $1", user_id
    )
    return (_coerce_json(row["fields"]) if row else {}) or {}


async def _build_profile(user_id: str, snapshot: dict) -> tuple[dict, dict, dict]:
    """合并 简历解析 < autofill_profile < 请求快照，后者覆盖前者。

    返回 (扁平 profile, 简历明细, {字段: 来源})。
    """
    resume = None
    try:
        resume = await resume_db.get_resume_by_user(user_id)
    except Exception as e:
        log.warning("autofill: get_resume failed user_id=%s: %s", user_id, e)

    flat, detail = _flatten_resume(resume)
    source = {k: "resume" for k in flat}

    try:
        extra = await _get_extra_fields(user_id)
    except Exception as e:
        log.warning("autofill: get_extra failed user_id=%s: %s", user_id, e)
        extra = {}
    for k, v in (extra or {}).items():
        if k.startswith("__") or v in (None, ""):
            continue
        flat[k] = v
        source[k] = "profile_extra"

    for k, v in (snapshot or {}).items():
        if k.startswith("__") or v in (None, ""):
            continue
        flat[k] = v
        source[k] = "snapshot"

    return flat, detail, source


# ── LLM 匹配 ────────────────────────────────────────────────────────────────

_SYSTEM = (
    "你是网页表单自动填写助手。给定一组表单字段和用户档案，为每个能确定的字段判定应填入的值。\n"
    "规则：\n"
    "1. 只用用户档案里真实存在的信息，绝不编造、绝不猜测。\n"
    "2. select/radio/checkbox 字段：value 必须严格取自该字段 options 里某一项的 value。\n"
    "3. 跟随表单语言：英文表单填英文、中文表单填中文，必要时做等价翻译或格式转换。\n"
    "4. 从档案无法确定的字段不要硬填，放进 unanswered，并给一句简短中文 ask_prompt 引导补充。\n"
    "5. confidence 取 0~1：label 与档案字段语义越明确越高。\n"
    "6. matches 每项给出 profile_key —— 该值取自用户档案里的哪个键名（顶层键）。\n"
    "只返回 JSON，不要解释、不要 markdown 代码块。"
)

_OUTPUT_EXAMPLE = """{
  "matches": [
    {"fid": "f_0", "value": "zhangsan@example.com", "profile_key": "email", "confidence": 0.97, "reason": "label「邮箱」对应档案 email"}
  ],
  "unanswered": [
    {"fid": "f_5", "ask_prompt": "你的期望年薪是多少？"}
  ]
}"""


async def _llm_match(profile: dict, detail: dict, fields: list) -> dict:
    client = AsyncAnthropic(api_key=config.API_KEY, base_url=config.BASE_URL or None)
    user_prompt = (
        "【用户档案（结构化字段）】\n"
        + json.dumps(profile, ensure_ascii=False, indent=2)
        + "\n\n【简历明细（回答“最近一段工作 / 学历”等问题用）】\n"
        + json.dumps(detail, ensure_ascii=False, indent=2)
        + "\n\n【待匹配的表单字段】\n"
        + json.dumps(fields, ensure_ascii=False, indent=2)
        + "\n\n请按以下 JSON 结构输出：\n"
        + _OUTPUT_EXAMPLE
    )

    # OpenRouter 等代理：用纯字符串 system，避免 cache_control 兼容问题。
    if config.BASE_URL:
        system = _SYSTEM
    else:
        system = [{"type": "text", "text": _SYSTEM,
                   "cache_control": {"type": "ephemeral"}}]

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.messages.create(
                model=_MATCH_MODEL,
                max_tokens=4096,
                temperature=0,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            if not resp.content:
                raise ValueError(f"空 content (stop_reason={resp.stop_reason})")
            raw = (resp.content[0].text or "").strip()
            parsed = _parse_llm_response(raw)
            usage = {
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
            }
            return {"parsed": parsed, "usage": usage}
        except Exception as e:
            last_err = e
            log.warning("autofill match LLM attempt %d failed: %s", attempt + 1, e)

    raise RuntimeError(f"llm_failed: {last_err}")


# ── 工具 ────────────────────────────────────────────────────────────────────

def _fingerprint(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def _stat_incr(key: str) -> None:
    """轻量遥测计数（admin 概览看板用）。Redis 不可用时静默。"""
    try:
        import redis_client
        await redis_client.incr("autofill:stat:" + key)
    except Exception:
        pass


def _guess_source(value, profile: dict, source: dict) -> str:
    """值精确反查命中哪个档案字段 → 取其来源；命中不了说明 LLM 做过转换。"""
    if value is None:
        return "llm"
    sval = str(value).strip().lower()
    for k, v in profile.items():
        if str(v).strip().lower() == sval:
            return source.get(k, "resume")
    return "llm"


# ── 端点：POST /autofill/match ──────────────────────────────────────────────

@portal_auth.require_user
async def autofill_match(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)

    user_id = request.state.user_id
    fields = body.get("fields") or []
    if not isinstance(fields, list) or not fields:
        return JSONResponse({"ok": False, "error": "fields 为空"}, status_code=400)
    await _stat_incr("match_calls")

    truncated = len(fields) > _MAX_FIELDS
    fields = fields[:_MAX_FIELDS]
    page_url = body.get("page_url") or ""
    snapshot = body.get("profile_snapshot") or {}

    await _ensure_schema()
    profile, detail, source = await _build_profile(user_id, snapshot)
    field_by_fid = {f.get("fid"): f for f in fields}

    # ── 模板查找（K2：站点已知结构）──
    site_origin, path_template = form_template_db.normalize_url(page_url)
    tpl = None
    try:
        tpl = await form_template_db.lookup_template(site_origin, path_template, fields)
    except Exception as e:
        log.warning("autofill: lookup_template failed: %s", e)
    tpl_stamp = str(tpl.get("updated_at")) if tpl else ""

    # ── 结果缓存：profile / 模板 一变，键自动失效 ──
    fields_fp = _fingerprint(*[
        f"{f.get('fid')}|{f.get('label')}|{f.get('type')}" for f in fields
    ])
    profile_fp = _fingerprint(json.dumps(profile, ensure_ascii=False, sort_keys=True))
    cache_key = (f"autofill:match:{user_id}:"
                 f"{_fingerprint(page_url, fields_fp, profile_fp, tpl_stamp)}")
    try:
        import redis_client
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            data["cached"] = True
            if data.get("template_hits"):
                await _stat_incr("match_template_hit")
            return JSONResponse(data)
    except Exception:
        pass

    # ── 模板优先解析：信任门槛 置信度≥0.8 且 times_seen≥2 ──
    mappings: list = []
    matched_fids: set = set()
    tpl_by_ident = {}
    if tpl:
        tpl_by_ident = {tf.get("ident"): tf
                        for tf in tpl.get("fields", []) if tf.get("ident")}

    # K6：从模板取「已学到、且填写历史为正」的 widget（重访自愈：
    # 用上次真正填成功的控件策略，覆盖本次可能误判的启发式分类）
    widget_by_fid = {}
    for i, f in enumerate(fields):
        tf = tpl_by_ident.get(form_template_db.field_ident(f, i))
        if not tf:
            continue
        w = tf.get("widget")
        wc = tf.get("widget_confidence", 0) or 0
        if w and wc >= 0.6 and tf.get("fill_ok", 0) > tf.get("fill_fail", 0):
            widget_by_fid[f.get("fid")] = w

    if tpl:
        for i, f in enumerate(fields):
            tf = tpl_by_ident.get(form_template_db.field_ident(f, i))
            if not tf:
                continue
            pk = tf.get("profile_key")
            conf = tf.get("profile_key_confidence", 0) or 0
            if (pk and conf >= 0.8 and tf.get("times_seen", 0) >= 2
                    and profile.get(pk) not in (None, "")):
                fid = f.get("fid")
                mp = {
                    "fid": fid,
                    "value": str(profile[pk]),
                    "profile_key": pk,
                    "confidence": round(min(0.99, conf), 2),
                    "source": "template",
                    "reason": "站点模板已知字段",
                }
                if widget_by_fid.get(fid):
                    mp["widget"] = widget_by_fid[fid]
                mappings.append(mp)
                matched_fids.add(fid)

    template_hits = len(matched_fids)
    if template_hits:
        await _stat_incr("match_template_hit")
    llm_fields = [f for f in fields if f.get("fid") not in matched_fids]

    # ── LLM 匹配剩余字段（全命中模板则跳过 LLM，零成本）──
    usage = {"input_tokens": 0, "output_tokens": 0}
    ask_by_fid: dict = {}
    if llm_fields:
        result = None
        try:
            result = await _llm_match(profile, detail, llm_fields)
        except Exception as e:
            if mappings:
                log.warning("autofill match LLM failed (template hit) user_id=%s: %s",
                            user_id, e)
            else:
                log.warning("autofill match failed user_id=%s: %s", user_id, e)
                await _stat_incr("match_llm_fail")
                return JSONResponse({"ok": False, "error": str(e)})
        if result:
            usage = result["usage"]
            parsed = result["parsed"]
            for m in (parsed.get("matches") or []):
                fid = m.get("fid")
                if (fid not in field_by_fid or fid in matched_fids
                        or m.get("value") in (None, "")):
                    continue
                matched_fids.add(fid)
                try:
                    conf = float(m.get("confidence", 0.8))
                except (TypeError, ValueError):
                    conf = 0.8
                pk = m.get("profile_key") or ""
                src = source.get(pk) if pk and pk in source \
                    else _guess_source(m.get("value"), profile, source)
                mp = {
                    "fid": fid,
                    "value": str(m.get("value")),
                    "profile_key": pk,
                    "confidence": max(0.0, min(1.0, conf)),
                    "source": src,
                    "reason": m.get("reason") or "",
                }
                if widget_by_fid.get(fid):
                    mp["widget"] = widget_by_fid[fid]
                mappings.append(mp)
            ask_by_fid = {u.get("fid"): u.get("ask_prompt")
                          for u in (parsed.get("unanswered") or [])}

    # ── 未解析字段 ──
    unresolved = []
    for f in fields:
        fid = f.get("fid")
        if fid in matched_fids:
            continue
        label = f.get("label") or f.get("name") or fid
        unresolved.append({
            "fid": fid,
            "label": label,
            "type": f.get("type"),
            "required": bool(f.get("required")),
            "ask_prompt": ask_by_fid.get(fid) or f"请补充：{label}",
        })

    data = {
        "ok": True,
        "mappings": mappings,
        "unresolved": unresolved,
        "profile_used": profile,
        "model": _MATCH_MODEL,
        "usage": usage,
        "template_hits": template_hits,
        "cached": False,
        "truncated": truncated,
    }

    try:
        import redis_client
        await redis_client.set(
            cache_key, json.dumps(data, ensure_ascii=False), ttl=_CACHE_TTL
        )
    except Exception:
        pass

    return JSONResponse(data)


# ── 端点：POST /autofill/ocr ────────────────────────────────────────────────

_OCR_SYSTEM = (
    "你是网页表单视觉识别助手。给你一张网页截图，找出图中所有可填写的表单字段"
    "（文本框、下拉框、单选/复选框、文本域、文件上传等）。\n"
    "每个字段返回：\n"
    "- label：字段旁的可见文字标签。\n"
    "- type：text/email/tel/textarea/select/radio/checkbox/file/date 之一，"
    "判断不了填 text。\n"
    "- bbox：输入控件的包围盒 [x, y, w, h]，每个值是 0~1 的小数，表示占图片"
    "宽度/高度的比例（左上角为原点）。\n"
    "- required：能判断是否必填则给 true/false。\n"
    "- options：select/radio 的可选项文字数组，无则 null。\n"
    "只返回 JSON：{\"fields\":[...]}，不要解释、不要 markdown。"
)


async def _ocr_llm(b64: str, media_type: str) -> dict:
    """调视觉模型识别截图。返回 {parsed, usage}。

    - OpenRouter：走 OpenAI 兼容 /chat/completions + image_url（data URL）—— 这是
      OpenRouter 调 Qwen3-VL 等视觉模型的标准、明确路径，不依赖 Anthropic 图片块翻译。
    - Anthropic 直连：用 SDK 的 image content block。
    """
    user_text = "识别这张网页截图里的所有表单字段。"

    if _IS_OPENROUTER:
        url = (config.BASE_URL or "").rstrip("/") + "/chat/completions"
        payload = {
            "model": _OCR_MODEL,
            "temperature": 0,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": _OCR_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ]},
            ],
        }
        async with httpx.AsyncClient(timeout=90) as hc:
            resp = await hc.post(url, json=payload, headers={
                "Authorization": f"Bearer {config.API_KEY}",
                "Content-Type": "application/json",
            })
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("OCR 模型返回空 choices")
        text = (choices[0].get("message") or {}).get("content") or ""
        u = data.get("usage") or {}
        return {
            "parsed": _parse_llm_response(str(text).strip()),
            "usage": {"input_tokens": u.get("prompt_tokens", 0),
                      "output_tokens": u.get("completion_tokens", 0)},
        }

    # Anthropic 直连
    client = AsyncAnthropic(api_key=config.API_KEY, base_url=config.BASE_URL or None)
    resp = await client.messages.create(
        model=_OCR_MODEL,
        max_tokens=4096,
        temperature=0,
        system=_OCR_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": media_type, "data": b64}},
            {"type": "text", "text": user_text},
        ]}],
    )
    if not resp.content:
        raise ValueError("空 content")
    return {
        "parsed": _parse_llm_response((resp.content[0].text or "").strip()),
        "usage": {"input_tokens": getattr(resp.usage, "input_tokens", 0),
                  "output_tokens": getattr(resp.usage, "output_tokens", 0)},
    }


@portal_auth.require_user
async def autofill_ocr(request: Request) -> JSONResponse:
    """截图视觉识别表单字段。bbox 用 0~1 归一坐标，扩展端按视口尺寸还原。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)

    shot = body.get("screenshot") or ""
    if not shot:
        return JSONResponse({"ok": False, "error": "缺 screenshot"}, status_code=400)

    media_type = "image/png"
    if shot.startswith("data:"):
        try:
            media_type = shot[5:].split(";", 1)[0] or "image/png"
            shot = shot.split(",", 1)[1]
        except Exception:
            return JSONResponse({"ok": False, "error": "screenshot 格式错误"},
                                status_code=400)

    await _stat_incr("ocr_calls")
    try:
        r = await _ocr_llm(shot, media_type)
        raw_fields = r["parsed"].get("fields") or []
        usage = r["usage"]
    except Exception as e:
        log.warning("autofill ocr failed (model=%s): %s", _OCR_MODEL, e)
        await _stat_incr("ocr_fail")
        return JSONResponse({"ok": False, "error": str(e)})

    fields = []
    for f in raw_fields:
        bbox = f.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            bb = {"x": _num(bbox[0]), "y": _num(bbox[1]),
                  "w": _num(bbox[2]), "h": _num(bbox[3])}
        elif isinstance(bbox, dict):
            bb = {"x": _num(bbox.get("x")), "y": _num(bbox.get("y")),
                  "w": _num(bbox.get("w")), "h": _num(bbox.get("h"))}
        else:
            continue
        if bb["w"] <= 0 or bb["h"] <= 0:
            continue
        fields.append({
            "label": f.get("label") or "",
            "type": f.get("type") or "text",
            "bbox": bb,
            "required": bool(f.get("required")),
            "options": f.get("options"),
        })

    return JSONResponse({"ok": True, "fields": fields, "model": _OCR_MODEL,
                         "usage": usage})


# ── 端点：GET /profile ──────────────────────────────────────────────────────

@portal_auth.require_user
async def get_profile(request: Request) -> JSONResponse:
    user_id = request.state.user_id
    try:
        await _ensure_schema()
        profile, _detail, _src = await _build_profile(user_id, {})
    except Exception as e:
        log.warning("get_profile failed user_id=%s: %s", user_id, e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    return JSONResponse({"ok": True, "profile": profile})


# ── 端点：PUT /profile ──────────────────────────────────────────────────────

@portal_auth.require_user
async def put_profile(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)

    user_id = request.state.user_id

    # 兼容两种请求体：{user_id, fields:{...}} 或 {user_id, ...扁平KV}
    # （user_id 现取自 token；扩展若仍在 body 带 user_id，下方按键名过滤掉）
    fields = body.get("fields")
    if fields is None:
        fields = {k: v for k, v in body.items() if k != "user_id"}
    fields = {k: v for k, v in (fields or {}).items() if not k.startswith("__")}

    try:
        await _ensure_schema()
        pool = await _get_pool()
        await pool.execute(
            """INSERT INTO autofill_profile (user_id, fields, updated_at)
               VALUES ($1, $2::jsonb, NOW())
               ON CONFLICT (user_id) DO UPDATE
                 SET fields = $2::jsonb, updated_at = NOW()""",
            user_id, json.dumps(fields, ensure_ascii=False),
        )
    except Exception as e:
        log.warning("put_profile failed user_id=%s: %s", user_id, e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)

    return JSONResponse({"ok": True})
