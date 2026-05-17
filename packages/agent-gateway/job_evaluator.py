"""职位-简历 LLM 匹配评估 + 自我介绍生成。"""
from __future__ import annotations

import json

from anthropic import AsyncAnthropic

import config
import db
import resume_db

_EVAL_SYSTEM = "你是职位匹配专家。只返回 JSON 数组，不要解释或 markdown 代码块。"
_INTRO_SYSTEM = "你是求职助手。只返回 JSON 数组，不要解释或 markdown 代码块。"
_EVAL_WITH_INTERVIEW_SYSTEM = (
    "你是资深 HR 顾问兼面试官教练。直接返回严格 JSON,不要 markdown 代码块,不要任何前后解释。"
)


def _parse_json_field(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def _resume_to_text(row: dict) -> str:
    lines = ["## 求职者简历"]
    name = row.get("name")
    work_years = row.get("work_years")
    degree = row.get("degree")
    if name or work_years or degree:
        parts = [p for p in [name, f"{work_years}经验" if work_years else None, degree] if p]
        lines.append("基本：" + " | ".join(parts))
    target_positions = _parse_json_field(row.get("target_positions")) or []
    if target_positions:
        lines.append("期望职位：" + "、".join(target_positions))
    target_cities = _parse_json_field(row.get("target_cities")) or []
    if target_cities:
        lines.append("期望城市：" + "、".join(target_cities))
    if row.get("target_salary_raw"):
        lines.append(f"期望薪资：{row['target_salary_raw']}/月")
    skills = _parse_json_field(row.get("skills")) or []
    if skills:
        lines.append("技能：" + "、".join(skills[:20]))
    work_experience = _parse_json_field(row.get("work_experience")) or []
    if work_experience:
        lines.append("工作经历：")
        for w in work_experience[:4]:
            if isinstance(w, dict):
                parts = [w.get("company", ""), w.get("title", ""), w.get("duration", "")]
                lines.append("  - " + " | ".join(p for p in parts if p))
    education = _parse_json_field(row.get("education")) or []
    if education and isinstance(education, list):
        edu = education[0]
        if isinstance(edu, dict) and edu:
            parts = [edu.get("school", ""), edu.get("degree", ""), edu.get("major", "")]
            lines.append("教育：" + " | ".join(p for p in parts if p))
    return "\n".join(lines)


def _jobs_to_text(jobs: list[dict]) -> str:
    parts = []
    for i, j in enumerate(jobs, 1):
        lines = [
            f"{i}. [{j.get('external_id', '')}] {j.get('title', '')} @ {j.get('company', '')}",
            f"   薪资: {j.get('salary', '')} | 经验: {j.get('experience', '')} | 学历: {j.get('education', '')}",
        ]
        if j.get("tags"):
            lines.append(f"   标签: {j['tags']}")
        if j.get("skills"):
            lines.append(f"   技能: {j['skills']}")
        if j.get("description"):
            lines.append(f"   JD: {j['description'][:500]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _build_eval_prompt(resume_text: str, jobs: list[dict]) -> str:
    jobs_text = _jobs_to_text(jobs)
    return (
        "请对以下职位与简历进行匹配评估，返回 JSON 数组，每项格式：\n"
        '{"job_id":"...", "title":"...", "company":"...", "score":0-100,\n'
        ' "level":"强烈推荐|建议投递|可以尝试|不建议",\n'
        ' "reasons":["..."], "concerns":["..."]}\n\n'
        f"{resume_text}\n\n"
        f"## 职位列表（{len(jobs)}个）\n{jobs_text}"
    )


def _jobs_to_intro_text(jobs: list[dict]) -> str:
    """将前端传来的职位对象（驼峰/snake_case 混合）转为 prompt 文本。"""
    parts = []
    for i, j in enumerate(jobs, 1):
        job_id = (j.get("encryptJobId") or j.get("encrypt_job_id")
                  or j.get("external_id") or str(i))
        title   = j.get("title") or j.get("jobName") or j.get("name") or ""
        company = j.get("company") or j.get("companyName") or j.get("brandName") or ""
        salary  = j.get("salary") or j.get("salaryDesc") or j.get("compensation") or ""
        tags_raw = j.get("tags") or j.get("skills") or []
        tags = ", ".join(str(t) for t in tags_raw) if isinstance(tags_raw, list) else str(tags_raw)
        desc = (j.get("description") or j.get("requirements")
                or j.get("responsibilities") or "")[:400]
        lines = [f"{i}. [{job_id}] {title} @ {company}"]
        if salary:
            lines.append(f"   薪资: {salary}")
        if tags:
            lines.append(f"   标签: {tags}")
        if desc:
            lines.append(f"   JD: {desc}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _build_intro_prompt(resume_text: str, jobs: list[dict]) -> str:
    jobs_text = _jobs_to_intro_text(jobs)
    return (
        "请根据求职者简历，为以下每个职位分别生成一条100-150字的中文自我介绍打招呼消息。"
        "消息要自然真诚，突出与该岗位最匹配的技能和经历，不要千篇一律。"
        "返回 JSON 数组，每项格式：\n"
        '{"job_id":"...", "title":"...", "company":"...", "text":"..."}\n\n'
        f"{resume_text}\n\n"
        f"## 职位列表（{len(jobs)}个）\n{jobs_text}"
    )


async def generate_intros(user_id: str, jobs: list[dict]) -> dict:
    """根据简历为指定职位列表生成个性化自我介绍消息。"""
    row = await resume_db.get_resume_by_user(user_id)
    if not row or row.get("parse_status") != "done":
        return {"ok": True, "has_resume": False, "intros": []}

    client_kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_BASE_URL:
        client_kwargs["base_url"] = config.ANTHROPIC_BASE_URL
    client = AsyncAnthropic(**client_kwargs)

    resume_text = _resume_to_text(row)
    prompt = _build_intro_prompt(resume_text, jobs)
    resp = await client.messages.create(
        model=config.RESUME_PARSE_MODEL,
        max_tokens=2048,
        temperature=0.3,
        system=_INTRO_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    intros = json.loads(resp.content[0].text)
    return {"ok": True, "has_resume": True, "intros": intros}


async def evaluate_job_snapshot(user_id: str, job_snapshot: dict) -> dict:
    """单职位即时评估,直接用前端传来的 list snapshot,不查 cached_jobs。
    snapshot 兼容驼峰 / snake_case key。返回:
      {ok, has_resume, evaluated:{job_id,title,company,score,level,reasons[],concerns[]}}
    简历未上传/未解析时 has_resume=False,evaluated 不返回。"""
    row = await resume_db.get_resume_by_user(user_id)
    if not row or row.get("parse_status") != "done":
        return {"ok": True, "has_resume": False}

    job = dict(job_snapshot or {})
    job["external_id"] = (job.get("external_id") or job.get("encryptJobId")
                          or job.get("encrypt_job_id") or "")
    job["title"]       = job.get("title") or job.get("jobName") or ""
    job["company"]     = (job.get("company") or job.get("brandName")
                          or job.get("companyName") or "")
    job["salary"]      = job.get("salary") or job.get("salaryDesc") or ""
    job["experience"]  = job.get("experience") or job.get("jobExperience") or ""
    job["education"]   = job.get("education") or job.get("jobDegree") or ""
    if not job.get("tags"):
        job["tags"] = job.get("jobLabels") or []
    if not job.get("skills"):
        job["skills"] = job.get("skillTags") or job.get("skills") or []

    client_kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_BASE_URL:
        client_kwargs["base_url"] = config.ANTHROPIC_BASE_URL
    client = AsyncAnthropic(**client_kwargs)

    resume_text = _resume_to_text(row)
    prompt = _build_eval_prompt(resume_text, [job])
    resp = await client.messages.create(
        model=config.RESUME_PARSE_MODEL,
        max_tokens=1024,
        temperature=0,
        system=_EVAL_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = _strip_markdown_codeblock(resp.content[0].text)
    arr = json.loads(raw)
    item = arr[0] if isinstance(arr, list) and arr else {}
    return {"ok": True, "has_resume": True, "evaluated": item}


async def evaluate_jobs(
    user_id: str,
    job_ids: list[str] | None = None,
    limit: int = 20,
) -> dict:
    # 1. 取简历
    row = await resume_db.get_resume_by_user(user_id)
    if not row or row.get("parse_status") != "done":
        return {"ok": True, "has_resume": False, "evaluated": []}

    # 2. 取职位
    jobs = await db.fetch_jobs_for_eval(job_ids=job_ids, limit=limit)
    if not jobs:
        return {"ok": True, "has_resume": True, "evaluated": [], "message": "缓存中暂无职位"}

    # 3. LLM 评估
    client_kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_BASE_URL:
        client_kwargs["base_url"] = config.ANTHROPIC_BASE_URL
    client = AsyncAnthropic(**client_kwargs)

    resume_text = _resume_to_text(row)
    prompt = _build_eval_prompt(resume_text, jobs)
    resp = await client.messages.create(
        model=config.RESUME_PARSE_MODEL,
        max_tokens=4096,
        temperature=0,
        system=_EVAL_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    evaluated = json.loads(resp.content[0].text)
    if isinstance(evaluated, list):
        evaluated.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {"ok": True, "has_resume": True, "evaluated": evaluated}


# ── 单 job 详细评估 + 面试准备(MVP 5,task-detail / recommended 详情按钮触发) ─
def _job_artifact_to_text(job: dict) -> str:
    """从 task artifact dict 拼成给 LLM 的岗位描述。
    artifact 字段:title/company/salary/city/experience/degree/jd_excerpt/...
    """
    fields = [
        ("职位",     job.get("title")),
        ("公司",     job.get("company")),
        ("薪资",     job.get("salary")),
        ("城市",     job.get("city")),
        ("经验要求", job.get("experience")),
        ("学历要求", job.get("degree")),
    ]
    lines = [f"- {k}: {v}" for k, v in fields if v]
    if job.get("jd_excerpt"):
        lines.append(f"- JD 摘要: {job['jd_excerpt']}")
    return "\n".join(lines) or "(无岗位信息)"


def _strip_markdown_codeblock(s: str) -> str:
    """LLM 偶尔返 ```json ...```;剥掉以便 json.loads。"""
    s = (s or "").strip()
    if s.startswith("```"):
        # 去掉首尾 ``` 标记
        s = s.strip("`")
        # 去掉可能的 "json" 语言标记
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    return s.strip()


async def evaluate_job_with_interview(user_id: str, job: dict) -> dict | None:
    """单 job 详细评估 + 面试准备(一次 LLM 调用)。

    返 None = 用户简历未上传/未解析(handler 返 400 提示前端);
    成功 → dict {match_pct, summary, strengths, gaps, interview_questions[]}
    """
    row = await resume_db.get_resume_by_user(user_id)
    if not row or row.get("parse_status") != "done":
        return None

    client_kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
    if config.ANTHROPIC_BASE_URL:
        client_kwargs["base_url"] = config.ANTHROPIC_BASE_URL
    client = AsyncAnthropic(**client_kwargs)

    resume_text = _resume_to_text(row)
    job_text = _job_artifact_to_text(job)

    prompt = (
        "请对以下岗位和候选人简历做匹配评估,并给出针对性的面试准备。\n"
        "返回严格 JSON,无任何 markdown 或前后说明文字:\n"
        "{\n"
        '  "match_pct": <0-100 整数>,\n'
        '  "summary": "<一句话总评,客观中性>",\n'
        '  "strengths": ["<3-5 个简历命中此岗位的具体匹配点>"],\n'
        '  "gaps":      ["<2-3 个简历未达岗位要求的缺口或风险>"],\n'
        '  "interview_questions": [\n'
        '    {"q": "<面试官可能问的问题>", "outline": "<回答提纲/应答思路 1-2 段>"},\n'
        "    ... 共 3-5 个,围绕岗位职责 + 候选人弱项展开\n"
        "  ]\n"
        "}\n\n"
        f"# 岗位\n{job_text}\n\n"
        f"{resume_text}\n"
    )

    resp = await client.messages.create(
        model=config.RESUME_PARSE_MODEL,
        max_tokens=2500,
        temperature=0.3,
        system=_EVAL_WITH_INTERVIEW_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = _strip_markdown_codeblock(resp.content[0].text)
    return json.loads(raw)
