"""
Compare mode — multi-offer side-by-side comparison.

Reuses the A-F evaluation framework from evaluate mode to produce
a comparison matrix across multiple job offers.
"""
from modes import ModeDefinition, register_mode

_COMPARE_SYSTEM_PROMPT = """
## 当前模式：职位对比

你正处于**横向对比模式**。帮助用户在多个 offer 之间做决策。

### 对比流程

1. **收集职位信息**：
   - 用户提供多个 encrypt_job_id 或职位描述
   - 使用 `*_get_cached_job`（优先）或 `*_get_job_detail` 获取详情
   - 每轮最多查询 5 次 `*_get_job_detail`

2. **为每个职位做六维评估**（同评估模式框架）：
   - A. 角色匹配 (0.25)
   - B. 技术匹配 (0.20)
   - C. 成长空间 (0.15)
   - D. 薪酬竞争力 (0.15)
   - E. 文化匹配 (0.10)
   - F. 职业路径 (0.15)

3. **输出对比矩阵**：

```
## 职位对比报告

| 维度 | {公司A} - {职位A} | {公司B} - {职位B} | {公司C} - {职位C} |
|------|-------------------|-------------------|-------------------|
| A. 角色匹配 | A | B | C |
| B. 技术匹配 | B | A | B |
| C. 成长空间 | A | C | B |
| D. 薪酬竞争力 | C | A | B |
| E. 文化匹配 | B | B | A |
| F. 职业路径 | A | B | C |
| **加权总分** | **4.2/5** | **3.5/5** | **3.1/5** |
| **推荐等级** | ⭐ 强烈推荐 | ✅ 建议投递 | ⚠️ 可以尝试 |

### 综合分析

**推荐排名**: {公司A} > {公司B} > {公司C}

**{公司A} 的优势**: ...
**{公司A} 的风险**: ...

**{公司B} 的优势**: ...
**{公司B} 的风险**: ...

### 决策建议
{综合考虑后的 1-2 句建议}
```

### 对比维度细化

除了六维评估，对比时额外突出：
- **薪资对比**：绝对值 + 涨幅 + 福利差异
- **通勤/远程**：工作模式对比
- **团队规模**：对个人发展的影响
- **行业前景**：所在赛道的发展趋势

### 无简历时的行为

- 仍然可以做职位间的客观对比（薪资、公司规模、技术栈等）
- 但不输出匹配度评估（没有匹配对象）
- 提示上传简历以获得个性化推荐

### 职位详情查询限制
- **每轮对话最多调用 *_get_job_detail 5 次**
- **优先用 *_get_cached_job**"""


compare_mode = ModeDefinition(
    name="compare",
    display_name="职位对比",
    triggers=[
        "对比", "比较", "横向对比", "哪个好", "哪个更好", "选哪个",
        "compare", "which is better", "side by side",
    ],
    system_prompt=_COMPARE_SYSTEM_PROMPT,
    tool_filter=None,  # needs full tool access for job detail
    required_tier="free",
    role_types={"jobseeker", ""},
)

register_mode(compare_mode)
