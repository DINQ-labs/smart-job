"""审核补丁 #5：_MODE_LADDER 纳入 recruiter 使其也享受 mid-turn 升级。

验证 agent_loop 里的 _MODE_LADDER 结构和语义：
- search / recruiter 同级（ladder=0），两种起点都可以升到 evaluate / apply
- evaluate = 1，apply = 2，单向升级
- 不应出现意外的反向降级或跨分支跳跃
"""
from pathlib import Path
import sys

_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))


def test_mode_ladder_structure():
    """_MODE_LADDER 必须包含 search / recruiter / evaluate / compare / apply / interview 六级"""
    # 从 agent_loop.py 源码里 grep 出常量（避免 import 整个 module 带副作用）
    src = (_GW / "agent_loop.py").read_text(encoding="utf-8")
    # 找 _MODE_LADDER = { ... } 这段 literal
    import re
    m = re.search(
        r"_MODE_LADDER\s*=\s*\{([^}]+)\}",
        src, re.DOTALL,
    )
    assert m, "未在 agent_loop.py 找到 _MODE_LADDER"
    ladder_body = m.group(1)

    # 解析每个 key → level
    kv = re.findall(r'"(\w+)"\s*:\s*(\d+)', ladder_body)
    ladder = {k: int(v) for k, v in kv}

    # 契约
    assert ladder.get("search") == 0, "search 应为 ladder=0"
    assert ladder.get("recruiter") == 0, "recruiter 应为 ladder=0（审核补丁 #5）"
    assert ladder.get("evaluate") == 1, "evaluate 应为 ladder=1"
    assert ladder.get("compare") == 1, "compare 应为 ladder=1（评估阶段同级）"
    assert ladder.get("apply") == 2, "apply 应为 ladder=2"
    assert ladder.get("interview") == 2, "interview 应为 ladder=2（动作阶段同级）"


def test_ladder_upgrade_semantics():
    """根据 ladder 和 TOOL_MODE_AFFINITY 手工模拟：
    recruiter → evaluate → apply 这条链路在 mid-turn upgrade 条件下成立"""
    from modes.detect import TOOL_MODE_AFFINITY
    # agent_loop 里的 ladder（复制一份做断言，避免 import 整个模块）
    LADDER = {
        "search": 0, "recruiter": 0,
        "evaluate": 1, "compare": 1,
        "apply": 2, "interview": 2,
    }

    def simulate_upgrade(current: str, tool: str) -> str:
        tgt = TOOL_MODE_AFFINITY.get(tool)
        if not tgt or tgt not in LADDER or current not in LADDER:
            return current  # 条件不成立，不升级
        if LADDER[tgt] > LADDER[current]:
            return tgt
        return current

    # 场景 1：recruiter 起点 → 看候选人详情 → 升 evaluate
    assert simulate_upgrade("recruiter", "boss_view_geek_detail") == "evaluate"
    assert simulate_upgrade("recruiter", "boss_get_cached_job") == "evaluate"

    # 场景 2：evaluate → 联系候选人 → 升 apply
    assert simulate_upgrade("evaluate", "boss_contact_candidate") == "apply"
    assert simulate_upgrade("evaluate", "boss_send_message") == "apply"

    # 场景 3：search 起点 → 看 job detail → 升 evaluate
    assert simulate_upgrade("search", "boss_get_job_detail") == "evaluate"

    # 场景 4：search → 搜候选人（affinity=recruiter，同 ladder=0）不升级
    assert simulate_upgrade("search", "boss_search_candidates") == "search"

    # 场景 5：recruiter → 搜候选人（affinity=recruiter，同级）不升级
    assert simulate_upgrade("recruiter", "boss_rec_geek_list") == "recruiter"

    # 场景 6：apply → 搜人（reverse），不降级
    assert simulate_upgrade("apply", "boss_search_candidates") == "apply"
    assert simulate_upgrade("apply", "boss_get_job_detail") == "apply"

    # 场景 7：未知工具不影响 mode
    assert simulate_upgrade("recruiter", "unknown_tool") == "recruiter"

    # 场景 8：compare 起点（ladder=1）→ 调 apply 工具 → 升 apply（ladder=2）
    assert simulate_upgrade("compare", "boss_contact_candidate") == "apply"
    assert simulate_upgrade("compare", "boss_start_chat") == "apply"

    # 场景 9：compare 起点 → 调 evaluate 工具（同级 ladder=1）→ 不升级
    assert simulate_upgrade("compare", "boss_get_job_detail") == "compare"

    # 场景 10：interview 起点（ladder=2）→ 调 apply 工具（同级）→ 不升级
    assert simulate_upgrade("interview", "boss_start_chat") == "interview"
    assert simulate_upgrade("interview", "boss_get_job_detail") == "interview"
