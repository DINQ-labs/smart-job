"""Phase 1 invariants — (role × platform) cell 架构

确保:
1. 6 个 cell 都齐全(必填字段非空)
2. compose_system_prompt 里 jobseeker × indeed 不会泄漏 Boss / recruiter 文案
3. _filter_tools_for_session 真正按 role 过滤
4. _get_welcome 按 role 取 cell welcome
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modes.cells import (  # noqa: E402
    CELLS,
    cell_blocks_tool,
    get_cell,
    is_shared_tool,
)


# ── 1. 6 cell 完整性 ────────────────────────────────────────────────────────────

def test_all_6_cells_present():
    for r in ("jobseeker", "recruiter"):
        for p in ("boss", "linkedin", "indeed"):
            cell = get_cell(r, p)
            assert cell.role == r and cell.platform == p
            for f in ("identity_zh", "identity_en", "welcome_zh", "welcome_en"):
                v = getattr(cell, f)
                assert v and isinstance(v, str), f"{r}/{p} 缺 {f}"
            for f in ("chips_zh", "chips_en"):
                v = getattr(cell, f)
                assert isinstance(v, list), f"{r}/{p} {f} 不是 list"


def test_all_6_cells_have_role_appropriate_chips():
    """jobseeker chips 应该跟"求职 / Search jobs"语义相关;recruiter 跟"招聘"。"""
    for p in ("boss", "linkedin", "indeed"):
        js = get_cell("jobseeker", p)
        rc = get_cell("recruiter", p)
        # jobseeker chip 文字大致是搜职位 / 找工作 / 联系招聘 类
        js_zh = " ".join(js.chips_zh)
        assert any(kw in js_zh for kw in ("搜索工作", "搜索职位", "查看")), \
            f"jobseeker × {p} chips 不像 jobseeker(实际:{js.chips_zh})"
        # recruiter chip 文字大致是搜候选人 / 简历 / 回复
        rc_zh = " ".join(rc.chips_zh)
        assert any(kw in rc_zh for kw in ("搜索候选人", "搜索简历", "查看回复")), \
            f"recruiter × {p} chips 不像 recruiter(实际:{rc.chips_zh})"


# ── 2. Prompt 跨 role / 跨 platform 不串味 ──────────────────────────────────────

def _compose(role: str, platform: str, language: str = "zh") -> str:
    from modes.base import compose_system_prompt
    return compose_system_prompt(
        "",  # mode_prompt 留空,只测 cell driven 部分
        platform=platform,
        language=language,
        role_type=role,
    )


def test_no_cross_role_leak_in_jobseeker_prompts():
    """jobseeker × * 的 prompt(identity + welcome rules)不应出现 recruiter 专属表达。"""
    for p in ("boss", "linkedin", "indeed"):
        js_zh = _compose("jobseeker", p, "zh")
        # identity 段是 prompt 第一段,welcome rules 紧跟其后;这两段是 cell-driven 的"敏感区"
        # 整个 prompt 较长还会带共用规则 / EVAL_RULES_JOBSEEKER 等,可能合理出现"招聘"
        # 字眼,所以只检 identity + welcome 那两段。
        head = "\n\n".join(js_zh.split("\n\n")[:5])
        assert "你是 Boss直聘 招聘助手" not in head
        assert "你是 LinkedIn 招聘助手" not in head
        assert "你是 Indeed 招聘助手" not in head
        # 欢迎不应主推招聘动作
        assert "搜索候选人" not in head
        assert "查看回复" not in head


def test_no_cross_role_leak_in_recruiter_prompts():
    for p in ("boss", "linkedin", "indeed"):
        rc_zh = _compose("recruiter", p, "zh")
        head = "\n\n".join(rc_zh.split("\n\n")[:5])
        assert "你是 Boss直聘 求职助手" not in head
        assert "你是 LinkedIn 求职助手" not in head
        assert "你是 Indeed 求职助手" not in head
        assert "搜索工作" not in head
        assert "搜索职位" not in head


def test_no_cross_platform_identity_leak():
    """jobseeker × indeed 的 identity 段不能出现 Boss直聘 Assistant / LinkedIn Assistant。"""
    js_indeed_en = _compose("jobseeker", "indeed", "en")
    head = "\n\n".join(js_indeed_en.split("\n\n")[:5])
    assert "Boss直聘 Assistant" not in head
    assert "LinkedIn Assistant" not in head
    assert "Indeed job search assistant" in head, "jobseeker × indeed identity 缺自己的 brand"

    rc_linkedin_zh = _compose("recruiter", "linkedin", "zh")
    head = "\n\n".join(rc_linkedin_zh.split("\n\n")[:5])
    assert "Boss直聘 招聘助手" not in head
    assert "Indeed 招聘助手" not in head
    assert "LinkedIn 招聘助手" in head, "recruiter × linkedin identity 缺自己的 brand"


# ── 3. 工具过滤 role × platform 维度 ────────────────────────────────────────────

def test_tool_filter_role_dimension():
    from agent_loop import _filter_tools_for_session
    fake = [
        {"name": "boss_search_jobs"},
        {"name": "boss_search_candidates"},
        {"name": "boss_apply_job"},
        {"name": "boss_check_login"},          # shared
        {"name": "boss_get_recommend_jobs"},
        {"name": "boss_employer_get_candidate"},  # employer = recruiter
        {"name": "linkedin_recruiter_send_inmail"},  # other platform
        {"name": "emit_action_buttons"},        # cross-platform
    ]
    js = {t["name"] for t in _filter_tools_for_session(fake, "boss", "jobseeker")}
    rc = {t["name"] for t in _filter_tools_for_session(fake, "boss", "recruiter")}

    # jobseeker × boss 应该有
    assert "boss_search_jobs" in js
    assert "boss_check_login" in js  # shared
    assert "emit_action_buttons" in js  # cross-platform
    # 但不应该有 recruiter 工具 / 其他平台
    assert "boss_search_candidates" not in js
    assert "boss_employer_get_candidate" not in js
    assert "linkedin_recruiter_send_inmail" not in js

    # recruiter × boss 应该有
    assert "boss_search_candidates" in rc
    assert "boss_employer_get_candidate" in rc
    assert "boss_check_login" in rc  # shared
    # 但不应该有 jobseeker 工具
    assert "boss_apply_job" not in rc
    assert "boss_get_recommend_jobs" not in rc


# ── 4. _get_welcome cell-aware ─────────────────────────────────────────────────

def test_get_welcome_role_aware():
    from sse_router import _get_welcome
    js_indeed = _get_welcome("indeed", "zh", role="jobseeker")
    assert "求职助手" in js_indeed
    assert "招聘助手" not in js_indeed

    rc_indeed = _get_welcome("indeed", "zh", role="recruiter")
    assert "招聘助手" in rc_indeed
    assert "求职助手" not in rc_indeed

    # role='' 走 legacy fallback(老 _WELCOME 字典),内容跟旧行为一致(允许角色混合)
    legacy = _get_welcome("boss", "zh", role="")
    assert "Boss直聘助手" in legacy or "Boss直聘 求职助手" in legacy or "Boss直聘 招聘助手" in legacy


# ── 5. 共享工具豁免 ────────────────────────────────────────────────────────────

def test_shared_tools_never_blocked():
    for r in ("jobseeker", "recruiter"):
        for p in ("boss", "linkedin", "indeed"):
            cell = get_cell(r, p)
            for shared_tool_name in (
                f"{p}_check_login",
                f"{p}_login",
                f"{p}_logout",
                f"{p}_get_dom_snapshot",
                f"{p}_navigate_to",
                f"{p}_init_session",
            ):
                assert is_shared_tool(shared_tool_name), \
                    f"{shared_tool_name} 应该被识别为 shared 工具"
                assert not cell_blocks_tool(cell, shared_tool_name), \
                    f"{r}/{p} 不应屏蔽共享工具 {shared_tool_name}"


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK   {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
                traceback.print_exc()
    print(f"\n{'PASS' if failed == 0 else f'FAIL ({failed})'}")
    sys.exit(failed)
