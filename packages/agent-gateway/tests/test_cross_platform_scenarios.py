"""D2 跨平台场景 few-shot 注入

验证 CROSS_PLATFORM_SCENARIOS 只在 platform='cross' 时进入 system prompt，
其他单平台模式（Boss / LinkedIn / Indeed）仍然保持 platform-locked，不应见到
跨平台示例（会和 PLATFORM_IDENTITY 里"不要列出其他平台"的约束冲突）。
"""
from pathlib import Path
import sys

_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from modes.base import (  # noqa: E402
    CROSS_PLATFORM_SCENARIOS,
    compose_system_prompt,
)


SENTINEL = "## 跨平台场景（多平台 Agent 专属）"  # CROSS_PLATFORM_SCENARIOS 首行


def test_cross_platform_injects_scenarios():
    prompt = compose_system_prompt("irrelevant mode prompt", platform="cross")
    assert SENTINEL in prompt


def test_boss_platform_excludes_scenarios():
    """Boss 单平台 Agent 是 platform-locked 的，不应包含跨平台引导。"""
    prompt = compose_system_prompt("mode", platform="boss")
    assert SENTINEL not in prompt


def test_linkedin_platform_excludes_scenarios():
    prompt = compose_system_prompt("mode", platform="linkedin")
    assert SENTINEL not in prompt


def test_indeed_platform_excludes_scenarios():
    prompt = compose_system_prompt("mode", platform="indeed")
    assert SENTINEL not in prompt


def test_scenarios_cover_boss_and_linkedin_and_indeed_tools():
    """示例里必须显式提及三个平台的工具名，否则对 Agent 没有路径指引价值。"""
    assert "boss_search_candidates" in CROSS_PLATFORM_SCENARIOS
    assert "linkedin_search_candidates" in CROSS_PLATFORM_SCENARIOS
    assert "indeed_search_jobs" in CROSS_PLATFORM_SCENARIOS
    # 也必须有"什么时候不要跨平台"的反向约束，避免 Agent 见到多平台就乱窜
    assert "不要" in CROSS_PLATFORM_SCENARIOS


def test_cross_mode_also_includes_platform_addons():
    """cross 模式里跨平台示例和各平台 addon 应共存（用户视角是 All-in-one）。"""
    prompt = compose_system_prompt("mode", platform="cross")
    assert SENTINEL in prompt
    assert "## LinkedIn 工具" in prompt
    assert "## Indeed 工具" in prompt
