"""tasks/registry.py — TaskTemplate 注册表

Template 是"逻辑业务"(jobseeker:find_best_jobs),
不同平台用不同 step 编排实现 — `steps_by_platform: {platform: [TaskStep]}`。

加新平台:在 tasks/steps/{platform}.py 实现 step,在 template 文件里加
一行 `'linkedin': [...]`。模板代码不动公共逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable


# Step 函数签名:接收 (task, ctx) → 返回 dict (放进 ctx 给后续 step 用)
StepFn = Callable[[dict, dict], Awaitable[dict]]


@dataclass
class TaskStep:
    """单个 step:fn 是 async 函数,iter_items 控制是否对 inputs 列表逐项执行(
    每 item 触发一次 fn,fn 拿到 item 在 ctx['_item'])。"""
    id: str                # 'search' / 'fetch_details' / 'llm_score'
    title: str             # 给用户看的中文标签 "搜索职位"
    fn: StepFn
    iter_items: bool = False        # True = 对 ctx['items'] 逐项 fn
    op_type: str = "default"        # rate_limiter 桶:'search'/'detail'/'chat'/'default'
    skip_on_signals: list[str] = field(default_factory=list)
                                      # 触发这些 signals 时直接 skip_item 而非走默认策略
    credit_sku: str = ""             # Sprint 6 / G4: 每 item 完成后扣的 SKU
                                      #   '' = 不扣（如 summarize / score 等非外联步骤）
                                      #   'batch_analyze' / 'batch_greeting' / 'batch_apply'
                                      #   见 job-api-gateway billing/pricing.py CREDIT_COSTS


@dataclass
class TaskTemplate:
    """逻辑模板(用户视角):一个业务流程,可能在多个平台有不同实现。

    `steps_by_platform`: `{'boss': [step1, step2, ...], 'linkedin': [...]}`
    engine 跑时按 task.platform 选对应 steps 列表。

    `steps`(向后兼容):没设 steps_by_platform 时用,通常用于平台无关任务。
    """
    id: str                          # logical id, e.g. 'jobseeker:find_best_jobs'
    role: str                        # 'jobseeker' / 'recruiter'
    title: str                       # 给用户看的中文名
    description: str
    emoji: str = "⚡"
    estimated_min: int = 5            # 估时(分钟)
    steps_by_platform: dict[str, list[TaskStep]] = field(default_factory=dict)
    steps: list[TaskStep] = field(default_factory=list)  # 兜底/平台无关 steps

    @property
    def supported_platforms(self) -> list[str]:
        """该模板支持哪些平台。前端按 user 当前 platform sub-tab 过滤。"""
        return list(self.steps_by_platform.keys())

    def steps_for(self, platform: str) -> list[TaskStep] | None:
        """按平台拿 steps。先查 steps_by_platform,fallback 到 steps。
        都没有返 None,engine 应该立即 fail task。"""
        if platform in self.steps_by_platform:
            return self.steps_by_platform[platform]
        if self.steps:
            return self.steps  # 平台无关 fallback
        return None


# ── 全局注册表 ───────────────────────────────────────────────────
TEMPLATES: dict[str, TaskTemplate] = {}


def register_template(tpl: TaskTemplate) -> None:
    if tpl.id in TEMPLATES:
        raise ValueError(f"template '{tpl.id}' already registered")
    TEMPLATES[tpl.id] = tpl


def get_template(template_id: str) -> TaskTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates_for_role(role: str, platform: str | None = None) -> list[TaskTemplate]:
    """按 role 列模板,可选 platform 过滤(只返回该 platform 实现的模板)。"""
    out = []
    for t in TEMPLATES.values():
        if t.role != role:
            continue
        if platform and platform not in t.supported_platforms and not t.steps:
            continue
        out.append(t)
    return out
