"""
Mode system — registry and definitions.

Each mode defines a specialized system prompt fragment, optional tool filter,
and access control (tier + role_type). Modes are registered at import time
and looked up by name in agent_loop.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ModeDefinition:
    name: str                           # "search", "evaluate", ...
    display_name: str                   # "搜索模式", "深度评估", ...
    triggers: list[str]                 # detection keywords: ["评估", "分析职位"]
    system_prompt: str                  # mode-specific system prompt fragment(legacy fallback)
    tool_filter: Optional[Callable[[list[dict]], list[dict]]] = None  # None = all tools
    required_tier: str = "free"         # minimum user tier
    role_types: set[str] = field(default_factory=lambda: {"jobseeker", ""})
    # B5: per-platform 组合器 — 每个 (role, platform) session 拿到精简后的 prompt。
    # 若 None,系统降级用 system_prompt 全量(向下兼容)。
    compose_per_platform: Optional[Callable[[str], str]] = None

    def get_prompt(self, platform: str) -> str:
        """返回该 platform 看到的 mode prompt。优先走 compose_per_platform。"""
        if self.compose_per_platform is not None:
            try:
                return self.compose_per_platform(platform)
            except Exception:
                # composer 出错时降级到全量,不阻塞 agent
                pass
        return self.system_prompt


# ── Registry ─────────────────────────────────────────────────────────────────

MODE_REGISTRY: dict[str, ModeDefinition] = {}


def register_mode(defn: ModeDefinition) -> None:
    """Register a mode definition. Called at module import time by each mode."""
    MODE_REGISTRY[defn.name] = defn


def get_mode(name: str) -> Optional[ModeDefinition]:
    return MODE_REGISTRY.get(name)


def list_modes(
    role_type: str = "",
    user_tier: str = "",
) -> list[ModeDefinition]:
    """Return modes accessible to the given role and tier."""
    _TIER_ORDER = {"free": 0, "pro": 1, "premium": 2}
    tier_level = _TIER_ORDER.get(user_tier, 0)
    results = []
    for mode in MODE_REGISTRY.values():
        # tier check
        if _TIER_ORDER.get(mode.required_tier, 0) > tier_level:
            continue
        # role_type check: empty set means all roles, "" in set means default
        if mode.role_types and role_type not in mode.role_types and "" not in mode.role_types:
            continue
        results.append(mode)
    return results


# ── Auto-import all mode modules to trigger registration ─────────────────────
# Order doesn't matter — each module calls register_mode() at module level.

from modes import search  # noqa: F401, E402
from modes import evaluate  # noqa: F401, E402
from modes import apply  # noqa: F401, E402
from modes import interview  # noqa: F401, E402
from modes import compare  # noqa: F401, E402
from modes import recruiter  # noqa: F401, E402
