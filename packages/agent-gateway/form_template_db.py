"""
form_template_db.py — 站点表单知识库数据层。

表：
  form_templates           众包表单模板（结构 + 字段→profile_key 映射，零用户值）
  form_template_captures   opt-in HTTP 抓包

设计文档：autofill-ext/docs/form-knowledge-base.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from db import _get_pool

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS form_templates (
    id              BIGSERIAL   PRIMARY KEY,
    site_origin     TEXT        NOT NULL,
    path_template   TEXT        NOT NULL,
    form_signature  TEXT        NOT NULL,
    page_title      TEXT        NOT NULL DEFAULT '',
    fields          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    observed_count  INTEGER     NOT NULL DEFAULT 0,
    distinct_users  INTEGER     NOT NULL DEFAULT 0,
    has_captures    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_origin, path_template, form_signature)
);
CREATE INDEX IF NOT EXISTS idx_ft_site ON form_templates (site_origin, path_template);

CREATE TABLE IF NOT EXISTS form_template_captures (
    id            BIGSERIAL   PRIMARY KEY,
    template_id   BIGINT      REFERENCES form_templates(id) ON DELETE CASCADE,
    user_id       TEXT        NOT NULL,
    site_origin   TEXT        NOT NULL,
    page_url      TEXT        NOT NULL,
    requests      JSONB       NOT NULL,
    processed     BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ftc_unprocessed ON form_template_captures (processed, created_at);
"""

_ready = False


async def init_form_template_db() -> None:
    global _ready
    pool = await _get_pool()
    await pool.execute(_SCHEMA)
    _ready = True


async def _ensure() -> None:
    if not _ready:
        await init_form_template_db()


def _coerce_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rowd(row) -> dict:
    """asyncpg Record → dict，datetime 字段转 ISO 字符串（可 JSON 序列化）。"""
    d = {}
    for k, v in row.items():
        d[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return d


# ── 站点 / 表单标识 ─────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_RE = re.compile(r"^[0-9a-f]{12,}$", re.I)
_DIGIT_RE = re.compile(r"^\d+$")


def normalize_url(page_url: str) -> tuple[str, str]:
    """page_url → (site_origin, path_template)。路径里的 id 段归一为 *。"""
    try:
        parts = urlsplit(page_url or "")
    except Exception:
        return "", "/"
    origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme else ""
    out = []
    for seg in [s for s in (parts.path or "").split("/") if s]:
        if _DIGIT_RE.match(seg) or _UUID_RE.match(seg) or _HEX_RE.match(seg):
            out.append("*")
        elif len(seg) > 16 and re.search(r"\d", seg) and re.search(r"[a-zA-Z]", seg):
            out.append("*")
        else:
            out.append(seg)
    return origin, "/" + "/".join(out)


def _norm_label(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()[:60]


def field_ident(f: dict, idx: int) -> str:
    """字段稳定标识：name || autocomplete || 归一 label || type#idx。"""
    return (f.get("name") or f.get("autocomplete")
            or _norm_label(f.get("label")) or f"{f.get('type') or 'x'}#{idx}")


def form_signature(fields: list) -> str:
    h = hashlib.sha1()
    for x in sorted(field_ident(f, i) for i, f in enumerate(fields)):
        h.update(x.encode("utf-8", "ignore"))
        h.update(b"\x00")
    return h.hexdigest()


# ── 模板查找（match 用）────────────────────────────────────────────────────

async def lookup_template(site_origin: str, path_template: str, fields: list) -> dict | None:
    """按 site+path 取候选，挑字段集合 Jaccard 最相似的一行。无合适项返回 None。"""
    await _ensure()
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM form_templates WHERE site_origin=$1 AND path_template=$2",
        site_origin, path_template,
    )
    if not rows:
        return None
    want = {field_ident(f, i) for i, f in enumerate(fields)}
    if not want:
        return None
    best, best_sim = None, 0.0
    for row in rows:
        tfields = _coerce_json(row["fields"]) or []
        have = {tf.get("ident") for tf in tfields if tf.get("ident")}
        if not have:
            continue
        sim = len(want & have) / len(want | have)
        if sim > best_sim:
            best_sim, best = sim, row
    if best is None or best_sim < 0.3:
        return None
    d = dict(best)
    d["fields"] = _coerce_json(best["fields"]) or []
    d["_similarity"] = best_sim
    return d


# ── 记录 / 合并（record 用）────────────────────────────────────────────────

def _add_variant(lst: list, v: str | None) -> None:
    v = (v or "").strip()
    if v and v not in lst and len(lst) < 8:
        lst.append(v)


def _apply_outcome(tf: dict, o: dict) -> None:
    """把一次填写结果（field→profile_key）并入字段知识。"""
    pk = o.get("profile_key")
    if not pk:
        return
    corrected = bool(o.get("user_corrected"))
    if corrected:
        tf["profile_key"] = pk
        tf["times_corrected"] = tf.get("times_corrected", 0) + 1
        tf["profile_key_confidence"] = max(0.9, tf.get("profile_key_confidence", 0.0))
    elif tf.get("profile_key") == pk:
        tf["profile_key_confidence"] = min(1.0, tf.get("profile_key_confidence", 0.5) + 0.15)
    elif not tf.get("profile_key"):
        tf["profile_key"] = pk
        tf["profile_key_confidence"] = 0.55
    else:
        # 冲突：已学到别的 key。被纠正过的不动；否则轻微下调，不被未确认值取代。
        if tf.get("times_corrected", 0) == 0:
            tf["profile_key_confidence"] = max(0.3, tf.get("profile_key_confidence", 0.5) - 0.1)


def _apply_widget(tf: dict, widget, o) -> None:
    """K6：学习字段的控件类型（widget / fill_strategy）+ 累计填写成败。

    重访时 match 会回传「已学到、且历史为正」的 widget，覆盖本次可能误判的
    启发式分类 —— 启发式重访自愈。
    """
    if widget:
        if tf.get("widget") == widget:
            tf["widget_confidence"] = min(1.0, tf.get("widget_confidence", 0.5) + 0.15)
        elif not tf.get("widget"):
            tf["widget"] = widget
            tf["widget_confidence"] = 0.5
        else:
            # 冲突：本次 widget 与已学不同。本次填成功 → 改用新的；否则保留旧的、降信心。
            if o and o.get("filled_ok"):
                tf["widget"] = widget
                tf["widget_confidence"] = 0.5
            else:
                tf["widget_confidence"] = max(0.3, tf.get("widget_confidence", 0.5) - 0.1)
    # fill_strategy 学习信号：该 widget 这次填成 / 填败
    if o is not None and "filled_ok" in o:
        if o.get("filled_ok"):
            tf["fill_ok"] = tf.get("fill_ok", 0) + 1
        else:
            tf["fill_fail"] = tf.get("fill_fail", 0) + 1


def _new_field(ident: str, f: dict) -> dict:
    return {
        "ident": ident,
        "type": f.get("type"),
        "labels": [],
        "selectors": [],
        "autocomplete": f.get("autocomplete") or "",
        "required": bool(f.get("required")),
        "options": f.get("options"),
        "profile_key": None,
        "profile_key_confidence": 0.0,
        "widget": None,
        "widget_confidence": 0.0,
        "fill_ok": 0,
        "fill_fail": 0,
        "times_seen": 0,
        "times_corrected": 0,
        "last_seen": None,
    }


async def record_template(page_url: str, page_title: str, fields: list,
                           outcome: list, user_id: str) -> tuple[int, int]:
    """记录一次填写：upsert 模板行，合并字段知识。返回 (template_id, 字段总数)。"""
    await _ensure()
    site_origin, path_template = normalize_url(page_url)
    sig = form_signature(fields)
    pool = await _get_pool()

    oc = {o.get("fid"): o for o in (outcome or []) if o.get("fid")}

    row = await pool.fetchrow(
        """SELECT id, fields FROM form_templates
           WHERE site_origin=$1 AND path_template=$2 AND form_signature=$3""",
        site_origin, path_template, sig,
    )
    existing = (_coerce_json(row["fields"]) if row else []) or []
    by_ident = {tf.get("ident"): tf for tf in existing if tf.get("ident")}
    now = _now_iso()

    for i, f in enumerate(fields):
        ident = field_ident(f, i)
        tf = by_ident.get(ident)
        if tf is None:
            tf = _new_field(ident, f)
            by_ident[ident] = tf
        tf["times_seen"] = tf.get("times_seen", 0) + 1
        tf["last_seen"] = now
        _add_variant(tf["labels"], f.get("label"))
        _add_variant(tf["selectors"], f.get("selector"))
        if f.get("options"):
            tf["options"] = f.get("options")
        o = oc.get(f.get("fid"))
        if o:
            _apply_outcome(tf, o)
        _apply_widget(tf, f.get("widget"), o)

    merged = list(by_ident.values())
    fields_json = json.dumps(merged, ensure_ascii=False)

    if row:
        await pool.execute(
            """UPDATE form_templates
               SET fields=$1::jsonb, page_title=$2,
                   observed_count=observed_count+1, updated_at=NOW()
               WHERE id=$3""",
            fields_json, page_title or "", row["id"],
        )
        return row["id"], len(merged)

    tid = await pool.fetchval(
        """INSERT INTO form_templates
             (site_origin, path_template, form_signature, page_title,
              fields, observed_count, distinct_users)
           VALUES ($1,$2,$3,$4,$5::jsonb,1,1) RETURNING id""",
        site_origin, path_template, sig, page_title or "", fields_json,
    )
    return tid, len(merged)


# ── 抓包入库 ────────────────────────────────────────────────────────────────

async def insert_capture(user_id: str, page_url: str, requests: list) -> int:
    await _ensure()
    site_origin, _path = normalize_url(page_url)
    pool = await _get_pool()
    tid = await pool.fetchval(
        """SELECT id FROM form_templates
           WHERE site_origin=$1 AND path_template=$2
           ORDER BY observed_count DESC LIMIT 1""",
        site_origin, normalize_url(page_url)[1],
    )
    cid = await pool.fetchval(
        """INSERT INTO form_template_captures
             (template_id, user_id, site_origin, page_url, requests)
           VALUES ($1,$2,$3,$4,$5::jsonb) RETURNING id""",
        tid, user_id, site_origin, page_url, json.dumps(requests, ensure_ascii=False),
    )
    if tid:
        await pool.execute(
            "UPDATE form_templates SET has_captures=TRUE WHERE id=$1", tid)
    return cid


# ── 富化任务用（K5）─────────────────────────────────────────────────────────

async def fetch_pending_captures(limit: int = 5) -> list[dict]:
    await _ensure()
    pool = await _get_pool()
    rows = await pool.fetch(
        """SELECT id, template_id, site_origin, page_url, requests
           FROM form_template_captures
           WHERE processed=FALSE
           ORDER BY created_at ASC LIMIT $1""",
        limit,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["requests"] = _coerce_json(r["requests"]) or []
        out.append(d)
    return out


async def mark_capture_processed(capture_id: int) -> None:
    pool = await _get_pool()
    await pool.execute(
        "UPDATE form_template_captures SET processed=TRUE WHERE id=$1", capture_id)


async def get_template_by_id(template_id: int) -> dict | None:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM form_templates WHERE id=$1", template_id)
    if not row:
        return None
    d = _rowd(row)
    d["fields"] = _coerce_json(row["fields"]) or []
    return d


async def merge_enriched_fields(template_id: int, enriched: list) -> int:
    """把富化任务推断出的字段知识并入模板。enriched: [{ident,profile_key,required,note}]。"""
    tpl = await get_template_by_id(template_id)
    if not tpl:
        return 0
    by_ident = {tf.get("ident"): tf for tf in tpl["fields"] if tf.get("ident")}
    changed = 0
    for e in enriched:
        ident = e.get("ident")
        pk = e.get("profile_key")
        if not ident:
            continue
        tf = by_ident.get(ident)
        if tf is None:
            tf = {"ident": ident, "type": e.get("type"), "labels": [], "selectors": [],
                  "autocomplete": "", "required": bool(e.get("required")),
                  "options": None, "profile_key": None, "profile_key_confidence": 0.0,
                  "widget": None, "widget_confidence": 0.0, "fill_ok": 0, "fill_fail": 0,
                  "times_seen": 1, "times_corrected": 0, "last_seen": _now_iso()}
            by_ident[ident] = tf
        # 抓包富化是中等强度信号：填补空缺 + 适度抬升，不覆盖已确认的高置信值。
        if pk and not tf.get("profile_key"):
            tf["profile_key"] = pk
            tf["profile_key_confidence"] = max(tf.get("profile_key_confidence", 0.0), 0.7)
            changed += 1
        elif pk and tf.get("profile_key") == pk:
            tf["profile_key_confidence"] = min(1.0, tf.get("profile_key_confidence", 0.7) + 0.1)
            changed += 1
        if e.get("required"):
            tf["required"] = True
    pool = await _get_pool()
    await pool.execute(
        "UPDATE form_templates SET fields=$1::jsonb, updated_at=NOW() WHERE id=$2",
        json.dumps(list(by_ident.values()), ensure_ascii=False), template_id,
    )
    return changed


# ── 管理后台查询（admin）────────────────────────────────────────────────────

_TPL_LIST_COLS = (
    "id, site_origin, path_template, form_signature, page_title, "
    "jsonb_array_length(fields) AS field_count, observed_count, "
    "distinct_users, has_captures, updated_at"
)


async def list_templates(q: str = "", limit: int = 50, offset: int = 0) -> dict:
    """模板列表（分页）。q 按 site_origin 模糊过滤。"""
    await _ensure()
    pool = await _get_pool()
    if q:
        total = await pool.fetchval(
            "SELECT COUNT(*) FROM form_templates WHERE site_origin ILIKE $1", f"%{q}%")
        rows = await pool.fetch(
            f"SELECT {_TPL_LIST_COLS} FROM form_templates WHERE site_origin ILIKE $1 "
            f"ORDER BY updated_at DESC LIMIT $2 OFFSET $3", f"%{q}%", limit, offset)
    else:
        total = await pool.fetchval("SELECT COUNT(*) FROM form_templates")
        rows = await pool.fetch(
            f"SELECT {_TPL_LIST_COLS} FROM form_templates "
            f"ORDER BY updated_at DESC LIMIT $1 OFFSET $2", limit, offset)
    return {"total": total, "items": [_rowd(r) for r in rows]}


async def delete_template(template_id: int) -> bool:
    await _ensure()
    pool = await _get_pool()
    res = await pool.execute("DELETE FROM form_templates WHERE id=$1", template_id)
    return str(res).endswith(" 1")


async def patch_template_field(template_id: int, ident: str,
                               profile_key=None, widget=None) -> dict | None:
    """运营人工纠正字段：覆盖 profile_key / widget，置信度拉到 0.95。返回更新后的字段。"""
    tpl = await get_template_by_id(template_id)
    if not tpl:
        return None
    hit = None
    for tf in tpl["fields"]:
        if tf.get("ident") == ident:
            hit = tf
            break
    if hit is None:
        return None
    if profile_key is not None:
        pk = (profile_key or "").strip()
        hit["profile_key"] = pk or None
        hit["profile_key_confidence"] = 0.95 if pk else 0.0
        hit["times_corrected"] = hit.get("times_corrected", 0) + 1
    if widget is not None:
        w = (widget or "").strip()
        hit["widget"] = w or None
        hit["widget_confidence"] = 0.95 if w else 0.0
    pool = await _get_pool()
    await pool.execute(
        "UPDATE form_templates SET fields=$1::jsonb, updated_at=NOW() WHERE id=$2",
        json.dumps(tpl["fields"], ensure_ascii=False), template_id)
    return hit


async def list_captures(processed=None, site: str = "",
                         limit: int = 50, offset: int = 0) -> dict:
    """抓包列表（分页）。processed/site 可选过滤。"""
    await _ensure()
    pool = await _get_pool()
    conds, args = [], []
    if processed is not None:
        args.append(bool(processed))
        conds.append(f"processed = ${len(args)}")
    if site:
        args.append(f"%{site}%")
        conds.append(f"site_origin ILIKE ${len(args)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM form_template_captures {where}", *args)
    rows = await pool.fetch(
        f"SELECT id, template_id, site_origin, page_url, "
        f"jsonb_array_length(requests) AS request_count, processed, created_at "
        f"FROM form_template_captures {where} "
        f"ORDER BY created_at DESC LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
        *args, limit, offset)
    return {"total": total, "items": [_rowd(r) for r in rows]}


async def get_capture(capture_id: int) -> dict | None:
    await _ensure()
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM form_template_captures WHERE id=$1", capture_id)
    if not row:
        return None
    d = _rowd(row)
    d["requests"] = _coerce_json(row["requests"]) or []
    return d


async def delete_capture(capture_id: int) -> bool:
    await _ensure()
    pool = await _get_pool()
    res = await pool.execute(
        "DELETE FROM form_template_captures WHERE id=$1", capture_id)
    return str(res).endswith(" 1")


async def autofill_stats() -> dict:
    """知识库 / 抓包的结构性统计。"""
    await _ensure()
    pool = await _get_pool()
    tpl_total = await pool.fetchval("SELECT COUNT(*) FROM form_templates")
    field_total = await pool.fetchval(
        "SELECT COALESCE(SUM(jsonb_array_length(fields)), 0) FROM form_templates")
    cap_total = await pool.fetchval("SELECT COUNT(*) FROM form_template_captures")
    cap_pending = await pool.fetchval(
        "SELECT COUNT(*) FROM form_template_captures WHERE processed=FALSE")
    site_count = await pool.fetchval(
        "SELECT COUNT(DISTINCT site_origin) FROM form_templates")
    top = await pool.fetch(
        "SELECT site_origin, observed_count, "
        "jsonb_array_length(fields) AS field_count "
        "FROM form_templates ORDER BY observed_count DESC LIMIT 10")
    return {
        "templates": tpl_total,
        "fields_learned": int(field_total or 0),
        "captures_total": cap_total,
        "captures_pending": cap_pending,
        "sites": site_count,
        "top_sites": [_rowd(r) for r in top],
    }
