"""tasks/steps/ — 可组合的原子 step 库。

每个 step 是一个独立的 async fn,签名 `async def step_xxx(_task, ctx) -> dict`。
ctx 是 task 范围共享 dict,step fn 通过 ctx 拿前一步结果 + 写自己的产出。

模块组织:
  common.py    — 跨平台原子 step(score / summarize / 通用工具)
  boss.py      — Boss 专属 step(发招呼+等回复+拉简历的特化流程)
  linkedin.py  — LinkedIn 专属(InMail + 配额 + connection degree)
  indeed.py    — Indeed 专属(简历库主动联系)

Template 在 tasks/templates/ 里把这些 step 编排成业务流程。
"""
