"""chips.py — chip personalizer 规则引擎.

数据 / 策略分离:
- 数据(chip pool):chip_configs DB 表,admin 后台 CRUD
- 策略(规则):本文件 RULES,代码 review 通过后 deploy

规则用 chip key (string) 引用 chip pool 行 — 池里没对应 key 的 action 静默跳过。
这让"工程改规则"和"运营建配置"完全解耦,两边可独立工作。

Action 类型:
  InsertFirst(key)         把指定 chip 提到列表第一位
  Promote(key, +N)         给指定 chip 加 N 权重
  Demote(key, -N)          减权重
  Hide(key)                完全去掉

规则 condition 输入是 user_state dict(see user_state.py)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Any

import db

log = logging.getLogger(__name__)


# ── Action protocol ────────────────────────────────────────────────

@dataclass
class _Action:
    kind: str          # "insert_first" | "promote" | "demote" | "hide"
    key: str
    delta: int = 0     # promote / demote 用


def InsertFirst(key: str) -> _Action: return _Action("insert_first", key)
def Promote(key: str, delta: int = 30) -> _Action: return _Action("promote", key, delta)
def Demote(key: str, delta: int = 30) -> _Action: return _Action("demote", key, -abs(delta))
def Hide(key: str) -> _Action: return _Action("hide", key)


# ── Rule definition ────────────────────────────────────────────────

@dataclass
class ChipRule:
    id: str                                                          # 规则 id(埋点 + 调试)
    when: Callable[[dict], bool]                                     # condition,接 user_state dict
    actions: list[_Action]
    role: str | None = None                                          # 限定 role,None = 任意
    platform: str | None = None                                      # 限定 platform
    description: str = ""                                            # 给运营 / admin UI 看的说明
    enabled: bool = True                                             # 全局开关(代码级,不入库)

    def matches(self, role: str, platform: str) -> bool:
        if not self.enabled: return False
        if self.role and self.role != role: return False
        if self.platform and self.platform != platform: return False
        return True


# ── 规则集 — PM / 运营拍 + 工程实现 ──────────────────────────────────
# 加新规则 = 加一条 ChipRule。规则按声明顺序 apply,后写的 InsertFirst 会再次插到第一位。

RULES: list[ChipRule] = [
    # ── 求职者引导 ─────────────────────────────────────────────────
    ChipRule(
        id="onboard_no_resume",
        role="jobseeker",
        when=lambda s: not s.get("has_resume") and s.get("account_age_days", 0) < 14,
        actions=[InsertFirst("upload_resume"), Promote("upload_resume", 50)],
        description="新注册 14 天内 + 没传简历 → 第一位推上传简历",
    ),
    ChipRule(
        id="onboard_no_prefs",
        role="jobseeker",
        when=lambda s: s.get("has_resume") and not s.get("has_preferences"),
        actions=[InsertFirst("set_preferences"), Promote("set_preferences", 40)],
        description="传了简历但没设偏好 → 推设偏好",
    ),
    # ── 招聘方引导 ─────────────────────────────────────────────────
    ChipRule(
        id="recruiter_no_prefs",
        role="recruiter",
        when=lambda s: not s.get("has_preferences"),
        actions=[InsertFirst("bind_job"), Promote("bind_job", 50)],
        description="recruiter 没绑职位 → 推关联在招职位",
    ),
    # ── 共用:任务态相关 chip 由 PRD §10.3 主动提示规则统一管理(见下方 ui_v3_*) ──
    # ── last_mode 对应 chip 加权(让用户回到上次干的事)───────────────
    ChipRule(
        id="last_mode_evaluate_jobseeker",
        role="jobseeker",
        when=lambda s: s.get("last_mode") == "evaluate",
        actions=[Promote("evaluate_current", 25)],
        description="求职者上次用 evaluate 模式 → 评估当前职位 chip 上提",
    ),
    ChipRule(
        id="last_mode_interview_jobseeker",
        role="jobseeker",
        when=lambda s: s.get("last_mode") == "interview",
        actions=[Promote("interview_prep", 25)],
        description="求职者上次用 interview 模式 → 面试准备 chip 上提",
    ),
    ChipRule(
        id="last_mode_evaluate_recruiter",
        role="recruiter",
        when=lambda s: s.get("last_mode") == "evaluate",
        actions=[Promote("evaluate_candidate", 25)],
        description="招聘方上次用 evaluate → 评估当前候选人 chip 上提",
    ),
    # ── PRD §10.3 9 种主动提示(scenario 9 "非平台页隐藏" 在 sidepanel/app.js
    #    cell 切换层实现,不走 chip 池,这里只做 8 条)─────────────────────
    ChipRule(
        id="ui_v3_recruiter_list",                                 # PRD 114
        role="recruiter",
        when=lambda s: s.get("page_type") == "list" and (s.get("page_item_count") or 0) > 0,
        actions=[InsertFirst("bulk_analyze_candidates"), Promote("bulk_analyze_candidates", 90)],
        description="招聘方在候选人列表页 → 第一位推'批量分析 X 位'",
    ),
    ChipRule(
        id="ui_v3_recruiter_detail",                               # PRD 115
        role="recruiter",
        when=lambda s: s.get("page_type") == "detail",
        actions=[InsertFirst("evaluate_candidate"), Promote("evaluate_candidate", 85)],
        description="招聘方在候选人详情页 → 第一位推'分析匹配度'",
    ),
    ChipRule(
        id="ui_v3_chat_page",                                      # PRD 116
        role=None,
        when=lambda s: s.get("page_type") == "chat",
        actions=[InsertFirst("draft_chat_reply"), Promote("draft_chat_reply", 90)],
        description="任何角色在聊天页 → 第一位推'起草下一条回复'",
    ),
    ChipRule(
        id="ui_v3_jobseeker_list",                                 # PRD 117
        role="jobseeker",
        when=lambda s: s.get("page_type") == "list" and (s.get("page_item_count") or 0) > 0,
        actions=[InsertFirst("bulk_analyze_jobs"), Promote("bulk_analyze_jobs", 90)],
        description="求职者在职位列表页 → 第一位推'批量分析 X 个职位'",
    ),
    ChipRule(
        id="ui_v3_jobseeker_detail",                               # PRD 118
        role="jobseeker",
        when=lambda s: s.get("page_type") == "detail",
        actions=[InsertFirst("evaluate_current"), Promote("evaluate_current", 85)],
        description="求职者在职位详情页 → 第一位推'分析当前职位匹配度'",
    ),
    ChipRule(
        id="ui_v3_task_running",                                   # PRD 119
        role=None,
        when=lambda s: s.get("has_running_task"),
        actions=[InsertFirst("view_running_task"), Promote("view_running_task", 80)],
        description="有进行中长任务 → 第一位推'查看进行中任务'",
    ),
    ChipRule(
        id="ui_v3_task_paused",                                    # PRD 120
        role=None,
        when=lambda s: s.get("has_paused_task"),
        actions=[InsertFirst("handle_paused_task"), Promote("handle_paused_task", 95)],
        description="有 paused_user_action 任务 → 第一位推'处理验证'(高于进行中)",
    ),
    ChipRule(
        id="ui_v3_task_completed",                                 # PRD 121
        role=None,
        when=lambda s: (s.get("recent_completed_with_matches") or 0) > 0
                       and not s.get("has_running_task")
                       and not s.get("has_paused_task"),
        actions=[InsertFirst("view_completed_task"), Promote("view_completed_task", 75)],
        description="24h 内有完成任务且无进行中/暂停 → 推'查看完成报告'",
    ),
]


# ── Personalizer 主入口 ─────────────────────────────────────────────

def _to_lang_dict(row: dict, lang: str) -> dict:
    """DB row → 前端用的 chip dict(按 lang 选 label/send_text)."""
    is_en = lang.lower().startswith("en")
    label     = row["label_en"]     if is_en else row["label_zh"]
    send_text = row["send_text_en"] if is_en else row["send_text_zh"]
    return {
        "id":        row["key"],         # 前端用 id 等于 DB key
        "label":     (row["icon"] + " " + label).strip() if row["icon"] else label,
        "send_text": send_text or label,
        "icon":      row["icon"] or "",
        "weight":    row["weight"],
        "group":     row["grp"],
    }


async def personalize(
    user_id: str,
    role: str,
    platform: str,
    lang: str = "zh",
    *,
    user_state: dict | None = None,
    max_chips: int = 6,
    triggered_rules: list | None = None,   # 出参容器(可选,埋点 / 调试用)
) -> tuple[list[dict], dict]:
    """主入口。返回 (chips, debug_info)。

    chips = [{id, label, send_text, icon, weight, group}, ...] (按权重 desc 取 top N)
    debug_info = {triggered_rules:[ids], pool_size:N, version:M}
    """
    # 1. 拉 chip pool(只 enabled)
    pool_rows = await db.chip_configs_list(role=role, platform=platform, only_enabled=True)
    pool: dict[str, dict] = {r["key"]: dict(r) for r in pool_rows}  # key → row dict
    keys_in_play = list(pool.keys())  # 初始顺序 = DB 排序(weight desc, sort_order asc)

    # 2. 拉 user_state(若调用方没传)
    if user_state is None:
        from . import user_state as _us
        user_state = await _us.collect(user_id, role, platform)

    triggered = []
    # 3. 跑规则
    for rule in RULES:
        if not rule.matches(role, platform):
            continue
        try:
            if not rule.when(user_state):
                continue
        except Exception as e:
            log.warning("rule %s when() raised: %s", rule.id, e)
            continue
        triggered.append(rule.id)
        for action in rule.actions:
            keys_in_play = _apply_action(keys_in_play, pool, action)

    if triggered_rules is not None:
        triggered_rules.extend(triggered)

    # 4. 按 weight 重排(规则可能改了 pool[key]['weight'])+ 截断
    keys_in_play.sort(key=lambda k: -pool[k]["weight"] if k in pool else 0)
    keys_in_play = [k for k in keys_in_play if k in pool][:max_chips]

    # 5. 转 lang 字典
    chips = [_to_lang_dict(pool[k], lang) for k in keys_in_play]

    # 6. version 戳给前端缓存对比
    version = 0
    try:
        version = await db.chip_configs_get_version()
    except Exception:
        pass

    return chips, {
        "triggered_rules": triggered,
        "pool_size":       len(pool),
        "version":         version,
        "user_state":      user_state,
    }


def list_rules_metadata() -> list[dict]:
    """introspect RULES,返 admin UI 用的元数据(每条规则引用了哪些 chip key).

    用途:admin UI 在 chip 行旁显示"被规则 X / Y 引用",防止误删被规则引用的 chip。
    """
    out = []
    for rule in RULES:
        refs = sorted({a.key for a in rule.actions if a.key})
        out.append({
            "id":          rule.id,
            "role":        rule.role,           # None = 任意
            "platform":    rule.platform,
            "description": rule.description,
            "enabled":     rule.enabled,
            "references":  refs,
        })
    return out


def _apply_action(keys: list[str], pool: dict[str, dict], action: _Action) -> list[str]:
    if action.key not in pool:
        # 规则引用的 chip 在 admin 表里不存在(被删/未建) → 静默跳过
        return keys
    if action.kind == "insert_first":
        keys = [k for k in keys if k != action.key]   # 先去重
        keys.insert(0, action.key)
    elif action.kind == "promote":
        pool[action.key]["weight"] = pool[action.key]["weight"] + action.delta
    elif action.kind == "demote":
        pool[action.key]["weight"] = pool[action.key]["weight"] + action.delta
    elif action.kind == "hide":
        keys = [k for k in keys if k != action.key]
    return keys
