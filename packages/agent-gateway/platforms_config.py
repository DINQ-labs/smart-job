"""platforms_config.py — Single source of truth for platform metadata

加新平台(如 51job / Liepin / Glassdoor)时,只需在 PLATFORMS dict 加一条
entry + 在 modes/cells.py 加 2 个 cell,其余代码自动按 manifest dispatch。

跟前端 ext_shared/sidepanel-shared/platforms-config.js 的 PLATFORMS dict 同步
(初期 hardcode 同步 OK;后续可改为后端通过 GET /platforms API 下发,前端拉取)。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlatformDef:
    id: str            # "boss" / "linkedin" / "indeed" — 也是 SSE/DB session 三元组里的 platform 值
    label_zh: str      # 用户面中文名:"Boss直聘"
    label_en: str      # 用户面英文名(品牌名一般不翻译,跟 zh 同)
    short_code: str    # UI pill 短代码:"BZ" / "IN" / "ID"
    site_url: str      # 该平台主登录 URL(给 fallback 跳转用)
    tool_prefix: str   # MCP 工具命名前缀:"boss_" / "linkedin_" / "indeed_"

    # ID-字段映射(LinkedIn 用 jobId,Boss 用 securityId,Indeed 用 jobKey)
    # agent_loop.py 原本 9 处 if-elif 之一,现在改为查这里
    id_field: str = "id"

    # 该平台对默认 rate-limit / quota 的覆盖(Phase 3 用)
    rate_limit_overrides: dict = field(default_factory=dict)
    quota_specs: dict = field(default_factory=dict)


PLATFORMS: dict[str, PlatformDef] = {
    "boss": PlatformDef(
        id="boss",
        label_zh="Boss直聘",
        label_en="Boss直聘",
        short_code="BZ",
        site_url="https://www.zhipin.com/",
        tool_prefix="boss_",
        id_field="securityId",
        quota_specs={
            "candidate_contact": {"daily_limit": 20},
            "job_application":   {"daily_limit": 100},
        },
    ),
    "linkedin": PlatformDef(
        id="linkedin",
        label_zh="LinkedIn",
        label_en="LinkedIn",
        short_code="IN",
        site_url="https://www.linkedin.com/",
        tool_prefix="linkedin_",
        id_field="jobId",
        quota_specs={
            "inmail": {"daily_limit": 30},  # Recruiter Lite default
        },
    ),
    "indeed": PlatformDef(
        id="indeed",
        label_zh="Indeed",
        label_en="Indeed",
        short_code="ID",
        site_url="https://www.indeed.com/",
        tool_prefix="indeed_",
        id_field="jobKey",
    ),
}


def list_platforms() -> list[str]:
    """所有 platform id 的列表(顺序固定 = manifest 插入序)。"""
    return list(PLATFORMS.keys())


def list_platform_prefixes() -> tuple[str, ...]:
    """给 _filter_tools_for_session 用的 prefix tuple,等价于旧 _PLATFORM_PREFIXES。"""
    return tuple(p.tool_prefix for p in PLATFORMS.values())


def get_platform(platform_id: str) -> PlatformDef | None:
    """无 fallback — 调用方自己处理 None。"""
    return PLATFORMS.get(platform_id)


def is_known_platform(platform_id: str) -> bool:
    return platform_id in PLATFORMS


def label_for(platform_id: str, language: str = "zh") -> str:
    """人话 label,语言不识别时降级到 zh,manifest 缺该 platform 时返回原始 id。"""
    p = PLATFORMS.get(platform_id)
    if p is None:
        return platform_id or ""
    return p.label_en if language[:2].lower() == "en" else p.label_zh
