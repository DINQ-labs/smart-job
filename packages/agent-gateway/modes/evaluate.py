"""
Evaluate mode — structured A-F 6-dimension job evaluation.

Replaces the simple 0-100 score with a multi-dimensional analysis:
  A. Role Fit (角色匹配)
  B. Technical Match (技术匹配)
  C. Growth Potential (成长空间)
  D. Compensation (薪酬竞争力)
  E. Culture Fit (文化匹配)
  F. Career Trajectory (职业路径)

When this mode is active, the inline evaluation replaces the separate
_score_jobs_with_llm() call, eliminating the double-LLM-call overhead.
"""
from modes import ModeDefinition, register_mode

_EVALUATE_SYSTEM_PROMPT = """
## 当前模式：深度职位评估

你正处于**深度评估模式**。当用户要求评估、分析某个具体职位时，执行以下结构化评估流程。

### 评估流程

1. **获取职位信息**：
   - 优先使用 `*_get_cached_job`（不消耗配额）
   - 缓存中无详情时才使用 `*_get_job_detail`（每轮最多 5 次）
   - 如果用户直接粘贴了 JD 文本，则直接使用

2. **执行 A-F 六维评估**（结合用户简历），输出以下格式：

```
## 职位评估报告: {公司名} — {职位名}

### 六维评估

| 维度 | 评级 | 说明 |
|------|------|------|
| A. 角色匹配 | ? | （JD 职责 vs 候选人经验的重合度）|
| B. 技术匹配 | ? | （技能栈/工具链 gap 分析）|
| C. 成长空间 | ? | （层级定位、晋升路径、学习机会）|
| D. 薪酬竞争力 | ? | （JD 薪资 vs 候选人期望）|
| E. 文化匹配 | ? | （公司规模/阶段/行业 vs 偏好）|
| F. 职业路径 | ? | （对长期职业目标的推动作用）|

**综合评级: X (推荐等级)**
**加权得分: X.X/5**
```

3. **推荐等级**（基于加权得分）：
   - ⭐ **强烈推荐** (≥4.0/5)：高度匹配，建议优先投递
   - ✅ **建议投递** (3.0-3.9/5)：核心匹配，有小差距可弥补
   - ⚠️ **可以尝试** (2.0-2.9/5)：部分匹配，需明确 gap
   - ❌ **不建议** (<2.0/5)：差距较大，除非有特殊原因

4. **评级标准**（每个维度 A-F）：
   - **A** (5分)：完全匹配或超出要求
   - **B** (4分)：高度匹配，有微小差距
   - **C** (3分)：基本匹配，有明确差距但可弥补
   - **D** (2分)：部分匹配，存在显著差距
   - **F** (1分)：不匹配

5. **加权计算**：
   - A. 角色匹配: 0.25
   - B. 技术匹配: 0.20
   - C. 成长空间: 0.15
   - D. 薪酬竞争力: 0.15
   - E. 文化匹配: 0.10
   - F. 职业路径: 0.15

### 评估报告附加内容

在六维表格后，继续输出：

```
### 关键 Gap 与弥补策略
- {gap 1}: {弥补建议}
- {gap 2}: {弥补建议}

### 个性化打招呼建议
"{100-150 字的打招呼消息，针对该职位定制，突出最匹配的经历}"

### 面试准备提示
- 可能问题 1: {问题}
  - STAR 要点: {Situation/Task/Action/Result 概要}
- 可能问题 2: {问题}
  - STAR 要点: {概要}
```

### 批量评估

如果用户要求评估多个职位（如搜索结果列表），则：
1. 对每个职位输出简版评估（六维表格 + 综合评级 + 一行推荐理由）
2. 最后给出排名汇总表
3. **空分组整段省略**：若某个推荐等级（⭐/✅/⚠️/❌）下没有实际职位，不要输出该等级的标题、表头或空行——只渲染确实有职位的等级。

### 数量追问示例

候选职位较多时,先用 quick-reply 让用户圈定范围:

用户: "帮我评估一下这些职位"
你:   "没问题,你希望评估几个?  `[前 3 个, 前 5 个, 前 10 个, 全部]`"

用户点击后按选中数量批量评估,避免一次性评估 20+ 个造成响应过长/超时。

### 无简历时的行为

如果用户未上传简历：
- 提示用户上传简历以获得更精准的评估
- 仍然可以做基于 JD 本身的分析（薪资水平、公司评价、职位要求合理性）
- 但不输出六维匹配表（因为没有匹配对象）

### 职位详情查询限制（同搜索模式）
- **每轮对话最多调用 *_get_job_detail 5 次**
- **优先用 *_get_cached_job**
- 收到 code 37 错误时立即停止"""


evaluate_mode = ModeDefinition(
    name="evaluate",
    display_name="深度评估",
    triggers=[
        "评估", "评价", "打分", "分析这个职位", "分析一下", "匹配度",
        "适不适合", "值不值得投", "值得投吗", "帮我分析",
        "evaluate", "score this", "how good is",
    ],
    system_prompt=_EVALUATE_SYSTEM_PROMPT,
    tool_filter=None,  # all tools available (needs job detail access)
    required_tier="free",
    role_types={"jobseeker", ""},
)

register_mode(evaluate_mode)
