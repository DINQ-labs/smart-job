# PRD ↔ 代码 Gap 分析

> 输入：`https://dinq-product.vercel.app/plugin-prd.html` (v1.0 / 2026-05-11) + `plugin-ui-v3.html`
> 对照：本仓库 `job-agent-gateway` / `job-api-gateway` / `job-seeker-ext` / `job_common`（未拉取 `job-api-admin`）
> 时点：2026-05-14 / 静态代码扫描 / 四个 Explore agent 并行核查
>
> 配套文档：[PROJECT_OVERVIEW.md](./PROJECT_OVERVIEW.md)（工程拓扑）+ [DESIGN.md](./DESIGN.md)（视觉规范）

---

## 0. 执行摘要 Executive Summary

PRD 抽出 **131 条可验证功能需求**。代码实现现状：

| 状态 | 数量 | 占比 | 含义 |
|---|---|---|---|
| ✅ 已实现 | ~42 | 32% | 有代码、有路由、能跑通 |
| ⚠️ 部分实现 | ~33 | 25% | 后端有 / 前端无；或核心有 / 边缘缺；或框架就位 / 业务缺数据 |
| ❌ 缺失 | ~56 | 43% | grep 多个关键词无果，需从零起 |

**按域的健康度（10 分制）**：

| 域 | 健康度 | 评语 |
|---|---|---|
| AI 对话核心能力（求职/招聘 mode + 任务编排） | **7.5** | `modes/*` + `tasks/templates/*` 体系完整，是项目最成熟的部分 |
| 身份×平台 切换（双身份/三平台/数据隔离） | **7.0** | DB 三维隔离 + 切换框架到位，UI 提示缺细节 |
| Onboarding（求职简历→分析→确认） | **6.0** | 后端 `preferences_router.py` 完整，前端缺 Step 4 轮询 + 5a 平台读取 |
| 任务生命周期（5 态 + 双向通知） | **5.5** | 5 态对不上（实现 6 态混用"被动暂停 vs 主动暂停"），双向通知 callback 框架在但**无实现绑定** |
| 反作弊 | **3.0** | 仅 rate_limiter；鼠标轨迹/阅读模拟/分批/避高峰**全部未实现** |
| 订阅 + Credit | **1.5** | 整套订阅/Credit/支付/Banner 几乎从零起，仅 `quota_tracker.py` 274 行做配额跟踪雏形 |
| **收藏模块** | **2.5** | DB 表存在但仅暴露 `mark-interested`；**列表/搜索/标签/排序/Banner 全无 HTTP 路由** |
| 平台注入（列表角标 / 浮按钮 / 表单填充） | **1.0** | 三平台 22 个注入点中**只有 Boss 的"评估"按钮在**；★收藏、批量条、浮按钮全无 |
| UI v3 上下文感知 + 主动提示 | **2.0** | 后端 `chips.py` 规则引擎在；前端 pageContext 字段预留但提取逻辑缺；9 种提示场景全无 chip 配置 |
| 结构化结果卡片 | **4.0** | 后端 markdown 表格输出规范完整；前端卡片组件无 |

**一句话结论**：**后端 AI 能力 ~70% 就绪，前端 + 商业化 + 收藏 ~25% 就绪。当前是"后端先行 5 个月、前端追赶"的形态**。

---

## 1. 详细对照表（按域分块）

> 状态图例：✅ 已实现 · ⚠️ 部分实现 · ❌ 缺失

### 1.1 账户域（身份切换 / Onboarding / 我的 / 订阅·Credit）

#### 身份与平台切换（PRD 1-6）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 1 | 求职/招聘双身份切换 | ✅ | `job-seeker-ext/onboarding.js:178-230` |
| 2 | Boss/LinkedIn/Indeed 三平台选择 | ✅ | `onboarding.js:212-214` |
| 3 | Header 标签可点击切换（v3） | ✅ | `popup.js:20` |
| 4 | 切换时"数据隔离"提示 | ⚠️ | `settings.js:119` 仅"重启"提示，缺隔离专项 |
| 5 | 已登录不重走 Onboarding | ✅ | `onboarding.js:46-56` |
| 6 | 任务/收藏数据按 (user_id, role, platform) 隔离 | ✅ | `preferences_db.py:23`、`recruiter_db.py:25` |

#### Onboarding 流程（PRD 68-81）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 7 | 欢迎页（Logo + 3 功能卡 + 3 平台徽章） | ⚠️ | `onboarding.js:1-30` 框架在，UI 完整度待对照截图 |
| 8 | Step 1 选身份 | ✅ | `onboarding.js:208` |
| 9 | Step 2 选平台 + 自动检测 | ⚠️ | `onboarding.js:212` 选平台 OK，**自动检测页面高亮缺** |
| 10 | Step 3 登录（Google + Email） | ✅ | `oauth_linkedin.py` + `profile.js:102` |
| 11 | Step 4 平台验证轮询 + 手动检查 | ❌ | grep `verify_polling` `manual_verify` 无果 |
| 12 | 求职 5a：三种简历获取方式 | ⚠️ | `resume_router.py:41` 上传 OK；**"从平台读取简历"缺** |
| 13 | 求职 6a：AI 分析期望职位/城市/薪资 | ✅ | `preferences_router.py:69-100` `suggest_preferences` |
| 14 | 求职 7a：用户确认或修改后进入对话 | ⚠️ | 前端 confirm/edit 分支不清晰 |
| 15 | 一次分析跨平台通用，不重分析 | ❌ | 无跨平台 cache 逻辑 |
| 16 | 招聘路径：跳过简历直接对话 | ✅ | `wizard-ext.js:15-25` 招聘配置无 resume |
| 17 | 招聘开场白 + 4 快捷操作 Grid | ⚠️ | `profile.js:51-56` 有模板，**Grid UI 缺硬编码** |
| 18 | 首次安装走欢迎页 | ✅ | `onboarding.js:283` |
| 19 | 换设备：跳步骤 + 云同步 | ⚠️ | 迁移逻辑在，**云同步实现不明** |
| 20 | 已登录重开：跳欢迎页 | ✅ | `onboarding.js:284-289` |

#### 我的 / 账户（PRD 45-53）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 21 | 邮箱/Google 账号显示 | ✅ | `settings.js:71-90` |
| 22 | 退出登录 | ✅ | `settings.js:91-101` |
| 23 | 求职端：简历管理 + 期望职位 | ✅ | `profile.js:40-50` + `preferences_db.py` |
| 24 | 招聘端：公司信息 + JD 管理 | ⚠️ | preferences 存储有，**JD CRUD 端点缺** |
| 25 | 订阅方案 + 剩余用量 + 升级入口 | ❌ | 无订阅 UI |
| 26 | 语言切换（中/英） | ⚠️ | 后端字段在，**前端 UI 缺** |
| 27 | 快捷键自定义（默认 `Ctrl+Shift+A`） | ❌ | 无 keybinding 配置 |
| 28 | 通知开关 | ✅ | `settings.js:148-160` |
| 29 | 任务执行速度设置 | ❌ | 无 speed UI |

#### 订阅 + Credit（PRD 82-100, 110）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 30 | 免费版 10 次/天 + 20/10 收藏 | ❌ | 无每日限额逻辑 |
| 31 | 求职 Pro $9/月 | ❌ | 无套餐定义 |
| 32 | 招聘 Pro $29/月 | ❌ | 无招聘 Pro 方案 |
| 33 | Credit 模型分档（mini=1/sonnet=3/opus=8） | ⚠️ | `quota_tracker.py` 有配额追踪雏形，**模型→Credit 映射缺** |
| 34 | 免费 20cr/天，0:00 重置 | ❌ | 无每日 reset 调度 |
| 35 | Pro 月度 Credit 重置（500/2000） | ❌ | 无月度池管理 |
| 36 | 追加购买 100cr/$2 永不过期 | ❌ | 无购买端点 |
| 37 | Header 右侧⚡Credit 常驻 | ❌ | 无 UI widget |
| 38 | Credit ≤ 20% 数字变红 | ❌ | 无 warning style |
| 39 | Credit 不足插入升级/追加/降级卡片 | ❌ | 无 shortage card |
| 40 | 免费用完输入框禁用 + 倒计时 | ❌ | 无 disabled UI |
| 41 | 收藏满黄色 Banner | ❌ | 无 banner |
| 42 | 信用卡/PayPal 支付 | ❌ | 无支付集成 |
| 43 | 订阅管理（状态/账单/支付/取消） | ❌ | 无管理页 |

---

### 1.2 AI 对话域（求职/招聘能力 + 9 种主动提示 + 结构化卡片）

#### 求职端 AI 能力（PRD 7-12）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 7 | 职位匹配度分析（评分+不足） | ✅ | `modes/evaluate.py:17-115` 六维框架 + A-F 评级 + 推荐等级 4 档 |
| 8 | 简历优化建议（对比 JD 关键词） | ⚠️ | `modes/evaluate.py:75-86` 提及 Gap，**无专属工具/mode** |
| 9 | 模拟面试问答（STAR） | ✅ | `modes/interview.py:40-100` |
| 10 | 起草打招呼语 | ✅ | `modes/apply.py:32-50` |
| 11 | 薪资谈判建议 | ❌ | 仅 `modes/compare.py:61` 顺带提薪资 |
| 12 | 批量分析职位（触发任务） | ✅ | `tasks/templates/jobseeker_find_best_jobs.py:43-57` |

#### 招聘端 AI 能力（PRD 13-18）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 13 | 候选人匹配度分析（评分+亮点） | ✅ | `modes/interview.py:102-169` 招聘端分支 |
| 14 | 候选人评估报告 | ⚠️ | `modes/recruiter.py:30-150` prompt 框架在，**专属"报告"格式规范缺** |
| 15 | 起草招聘消息 / 个性化招呼 | ⚠️ | `modes/recruiter.py` 提及"批量打招呼"，**优化细节缺** |
| 16 | JD 优化建议 | ❌ | 无对应 mode |
| 17 | 候选人对比分析 | ✅ | `modes/compare.py:9-75` 六维对比 |
| 18 | 批量筛选候选人（触发任务） | ✅ | `tasks/templates/recruiter_hiring_pipeline.py` |

#### AI 对话机制（PRD 19-22）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 19 | 页面感知 + 上下文卡片 | ⚠️ | `sidepanel/app.js:37` 预留 `pageContext`、`modes/cells.py:30-62` 后端就绪，**content script 提取逻辑缺** |
| 20 | "批量"关键词识别 → 任务确认卡 | ✅ | `modes/detect.py:31-52` keyword + sticky 评分 |
| 21 | 非平台页"前往支持平台"引导 | ✅ | `modes/base.py:46-57` LOGIN_LINK_RULES |
| 22 | AI 回复下快捷操作 chips | ✅ | `modes/cells.py:46-49` + `personalization/chips.py:66-97` 规则引擎 |

#### UI v3 - 9 种主动提示（PRD 114-122）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 23 | 候选人列表 → "批量分析 X 位" | ⚠️ | chips.py 框架在，**具体 chip pool 缺该条** |
| 24 | 候选人详情 → "分析 XXX 匹配度" | ⚠️ | mode + interview 就位，**前端 context 卡片缺** |
| 25 | 聊天会话 → "起草下一条回复" | ❌ | 无续接对话 mode |
| 26 | 职位搜索 → "批量分析 X 个职位" | ⚠️ | `modes/evaluate.py:96-103` 后端在，**前端 context 卡缺** |
| 27 | 职位详情 → "分析匹配度" | ⚠️ | evaluate mode 完整，**前端感知缺** |
| 28 | 执行中 → "🔄 进行中 X/Y" | ✅ | `tasks_router.py:34-52` progress 可序列化 |
| 29 | 需验证 → "⚠️ 处理验证" | ⚠️ | `tasks/registry.py:25-30` skip_on_signals 在，**卡片暂停 UI 缺** |
| 30 | 完成 → "✅ 完成，发现 X 高匹配" | ✅ | `tasks/steps/common.py` step_summarize |
| 31 | 非平台页隐藏 | ✅ | `sidepanel/app.js:161` + cells filter |

#### 结构化分析卡片（PRD 123-126）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 32 | 卡片替代气泡 | ⚠️ | 后端 `evaluate.py:34-47` 输出 markdown 表格，**前端卡片组件无** |
| 33 | 顶部分数 | ✅ | `evaluate.py:45-46` prompt 规范 |
| 34 | 三色分类（🟢🟡🔴） | ✅ | `evaluate.py:49-53` 4 档对应配色 |
| 35 | 关键词加粗 | ✅ | prompt 规范 `**加粗**` |

---

### 1.3 任务域（生命周期 / 类型 / 反作弊 / 双向通知）

#### 任务生命周期 5 态（PRD 23-28）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 36 | 状态枚举 5 态 | ⚠️ | `job-agent-gateway/db.py:150-151` 实际 6 态：`pending/running/paused_user_action/completed/failed/cancelled`，PRD"需验证"和"被动暂停"被合并为 `paused_user_action`（靠 `paused_signal` 字段区分） |
| 37 | 队列中可取消 | ✅ | `tasks_router.py:340-359` |
| 38 | 执行中进度环 + 可暂停/取消 | ⚠️ | `engine.py:117-170` 进度+取消 OK，**用户主动暂停 API 缺** |
| 39 | 需验证态：暂停+提醒+自动恢复 | ✅ | `engine.py:516-538` user_action signal → `paused_user_action` |
| 40 | 已暂停态（用户主动） | ⚠️ | 取消 OK，**主动暂停入口缺** |
| 41 | 已完成态：高/中/不匹配数量统计 | ⚠️ | `engine.py:184-189` `result_summary` 在，**聚合计数缺** |

#### 任务类型覆盖（PRD 29-30）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 42 | 求职：批量分析职位 | ✅ | `jobseeker_find_best_jobs` |
| 43 | 求职：批量投递 | ❌ | 无 `batch_apply` 模板 |
| 44 | 求职：投递进度追踪 | ❌ | 无 `track_applications` 模板 |
| 45 | 招聘：批量分析候选人 | ❌ | 无 `bulk_analyze_candidates` 模板 |
| 46 | 招聘：批量发招呼 | ✅ | `recruiter_hiring_pipeline` |
| 47 | 招聘：批量筛选简历 | ✅ | `recruiter_inbox_triage` |

> **任务模板实现 3 个 / PRD 要求 6 类**。已注册见 `tasks/templates/__init__.py:11-13`。

#### 任务执行机制（PRD 31-33）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 48 | 创建流程：AI 识别 → 确认卡片 → 进队列 | ⚠️ | `tasks_router.py:91-183` 直接 POST 进队列，**AI 识别 + 前端确认卡阶段缺** |
| 49 | 高匹配项一键批量收藏 | ✅ | `PATCH /tasks/{id}/items/{item_id}` 支持 `starred` |
| 50 | 执行期间保持 tab 活跃 | ❌ | 无 tab 检测 |

#### 反作弊（PRD 101-108）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 51 | 操作间隔 0.8~2.3s 随机 | ✅ | `rate_limiter.py:19-24` chat 3-8s / search 1-3s / detail 3-7.5s（**范围比 PRD 更宽**） |
| 52 | 真实鼠标轨迹（非直线） | ❌ | 无轨迹模拟 |
| 53 | 阅读行为模拟（滚动停顿） | ❌ | 无 |
| 54 | 分批执行（10-20/批 + 批间长间隔） | ❌ | 无分批逻辑 |
| 55 | 避开高峰时段 | ❌ | 无 |
| 56 | Content Script 检测验证码 | ⚠️ | `risk_signals.py:62-97` 信号定义在，**content script 检测代码缺** |
| 57 | 触发"需验证"态 | ✅ | `engine.py:516-528` |
| 58 | 自动恢复 | ✅ | `engine.py:640-650` + resume 端点 |

#### 双向通知（PRD 127-132）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 59 | 创建：任务 Tab + AI 消息 + 小进度卡 | ⚠️ | `db.py:273-295` log_event 在，**AI 消息插入机制缺** |
| 60 | 执行中：双向同步进度 | ⚠️ | `engine.py:117-130` 写 DB OK，**AI 同步缺** |
| 61 | 异常/验证：双向通知（红） | ⚠️ | `engine.py:172-179` on_paused callback 定义，**无实现绑定** |
| 62 | 验证完成：双向通知 | ⚠️ | `engine.py:181-182` on_resumed callback 定义，**无实现** |
| 63 | 完成：双向通知 + 下一步建议 | ⚠️ | `engine.py:184-189` on_done callback 定义，**无下一步建议** |
| 64 | 旧卡片原地更新 + 新消息在底部 | ❌ | 无 |

#### Risk Signal 与 PRD 5 态映射

| Signal Action | 实际行为 | 对应 PRD 态 | 代码 |
|---|---|---|---|
| `auto_retry` | sleep+retry，超 max_retries 转 skip | running（透明重试） | `engine.py:490-514` |
| `user_action` | 暂停 + emit + 等 resume | **PRD"需验证" + PRD"被动暂停"合一** | `engine.py:516-538` |
| `skip_item` | 记录跳过，继续下一项 | running（条目级） | `engine.py:540-541` |
| `abort` | 任务转 failed 退出 | failed | `engine.py:543-544` |

**结论**：PRD 的"需验证"和"被动暂停"是两个独立态，代码合并为 `paused_user_action`，区分仅靠 `paused_signal` 字段——是 PRD/代码语义不齐的一处。

---

### 1.4 收藏与注入域（最大缺口区）

#### 收藏数据模型（PRD 34-44）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 65 | 求职收藏字段（职位/公司/薪资/JD/分） | ✅ | `job-api-gateway/db.py:599-618` 表 `user_job_interests` |
| 66 | 招聘收藏字段（候选人姓名/职位/经历/分） | ✅ | `db.py:622-641` 表 `recruiter_geek_interests` |
| 67 | 三种来源（★/AI 推荐/任务结果） | ⚠️ | `http_routes.py:2446` 仅 `mark-interested`；UI 注入和任务结果集成缺 |
| 68 | 全文搜索 | ❌ | DB 函数 `list_user_job_interests` 存在但**未暴露 HTTP** |
| 69 | 多维筛选（平台/标签/时间） | ❌ | 无过滤接口 |
| 70 | 自定义标签 | ⚠️ | DB 有 `status` 字段，**枚举值不符 PRD**，无管理接口 |
| 71 | 文字备注 | ✅ | `db.py:614/637` `notes TEXT` |
| 72 | 多维排序 | ❌ | 无 HTTP 排序接口 |
| 73 | 跳回平台原页面 | ❌ | 无跳转链接构造 |
| 74 | 重新发起 AI 分析 | ❌ | 无端点 |
| 75 | 收藏数量限制 + 满额 Banner | ❌ | 无配额 + 无 UI |

> **关键判断**：`user_job_interests` / `recruiter_geek_interests` **不是完整的收藏模块**，而是"兴趣追踪历史表"——仅数据存储层完成 ~25-30%。

#### Boss 平台注入（PRD 54-58）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 76 | 列表★+匹配分标签 | ⚠️ | `ext_shared/content/zhipin_eval_inject.js` 有"评估"按钮+分数徽章，**不是★收藏按钮** |
| 77 | 列表顶部"批量分析"条 | ❌ | 无 |
| 78 | 详情页浮动"AI 分析" | ❌ | 无 |
| 79 | 聊天"AI 起草回复" | ❌ | 无 |
| 80 | 职位管理"AI 筛选" | ❌ | 无 |

#### LinkedIn 平台注入（PRD 59-64）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 81 | Jobs 卡片★+匹配标签 | ❌ | 命令存在（`linkedin/search_jobs`），**无 DOM 注入** |
| 82 | Jobs 顶部批量条 | ❌ | 无 |
| 83 | 详情"AI 分析"按钮 | ❌ | 无 |
| 84 | 人才搜索★+批量条 | ❌ | 无 |
| 85 | 个人主页浮动按钮 | ❌ | 无 |
| 86 | InMail "AI 起草" | ❌ | 无 |

#### Indeed 平台注入（PRD 65-67）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 87 | 搜索★+匹配+批量 | ❌ | 无 |
| 88 | 详情"AI 分析" | ❌ | 无 |
| 89 | 申请表单"AI 已填写"提示条 | ❌ | 无 |

#### v3 上下文感知条 + 浮标（PRD 111-113）

| # | PRD 简述 | 状态 | 代码锚点 |
|---|---|---|---|
| 90 | Tab 顶部固定上下文条 | ❌ | 无 |
| 91 | 页面切换实时更新 | ❌ | 无 |
| 92 | 非平台页/无感知隐藏 | ❌ | 无 |
| 93 | 右下角浮标 5 态 | ❌ | 无 |

---

## 2. 关键 Gap Top 10（按修复 ROI 排序）

> 每条标注：**业务影响 × 修复成本** + 起手点

### 🔥 ROI 极高（先做）

1. **收藏列表 HTTP API** ❌
   - 业务影响：**极高**——整个 Tab 3 无法运转
   - 修复成本：**低**——DB 函数已存在，只需在 `http_routes.py` 新增 4-5 个 endpoint（list / filter / sort / search / delete）
   - 起手点：`job-api-gateway/db.py:list_user_job_interests`、`recruiter_geek_interests` → 包成 router

2. **任务模板补齐 3 个**：批量投递 / 投递追踪 / 批量分析候选人 ❌
   - 业务影响：**极高**——一半 PRD 任务类型没有
   - 修复成本：**中**——参考 `recruiter_hiring_pipeline.py` 拷贝 + 改步骤
   - 起手点：`job-agent-gateway/tasks/templates/`

3. **PageContext 提取链路打通** ⚠️
   - 业务影响：**极高**——v3 的"上下文感知条"和 9 种主动提示全依赖它
   - 修复成本：**中**——后端 `pageContext` 字段、`cells.py` 都就绪了；缺扩展 content script 把当前 DOM 关键字段抽出来通过 WS 上报
   - 起手点：`ext_shared/{bosszp,linkedin,indeed}/api.js` 内增加 `extractPageContext()` + 上报到 sidepanel

### 🟡 ROI 中高（次做）

4. **三平台 DOM 注入点（★ + 匹配分 + 批量条 + 浮按钮）** ❌
   - 业务影响：**高**——15 个 PRD 注入点目前只实现 1 个（Boss 的评估按钮）
   - 修复成本：**高**——三个平台的 DOM 选择器各自维护，且需对接收藏 API
   - 起手点：参考 `ext_shared/content/zhipin_eval_inject.js` 横向铺到 LinkedIn / Indeed

5. **结构化结果卡片前端组件** ⚠️
   - 业务影响：**高**——是 v3 视觉一致性的核心
   - 修复成本：**低**——后端 markdown 输出规范完整；前端建一个 `<AnalysisCard>` 组件解析三色分类即可
   - 起手点：`sidepanel.js` + 参考 [DESIGN.md §7](./DESIGN.md) 状态色系

6. **任务双向通知绑定** ⚠️
   - 业务影响：**高**——v3 关键改动之一
   - 修复成本：**低**——`engine.py:172-189` 三个 callback（on_paused/on_resumed/on_done）只缺实现绑定；连接到 SSE 推 AI 消息插入即可
   - 起手点：`job-agent-gateway/tasks/engine.py:172`

### 🟢 ROI 中（按节奏推）

7. **Credit 模型分档 + Header 显示** ❌
   - 业务影响：**中**——直接影响商业化
   - 修复成本：**中**——`quota_tracker.py` 雏形在，需扩成"模型→credit 映射 + 余额查询 endpoint + Header widget"
   - 起手点：`job-api-gateway/quota_tracker.py`

8. **5 态正名 + 主动暂停 API** ⚠️
   - 业务影响：**中**——影响产品语义清晰度
   - 修复成本：**低**——`db.py:150-151` 状态枚举把 `paused_user_action` 拆为 `verification_required` + `paused_by_user`，加 `POST /tasks/{id}/pause`
   - 起手点：`job-agent-gateway/db.py:150`、`tasks_router.py`

9. **反作弊深化（鼠标轨迹 + 阅读模拟 + 分批）** ❌
   - 业务影响：**中**（高频账户安全）
   - 修复成本：**高**——需要扩展端注入 `core/site-executor.js` 加贝塞尔曲线鼠标 + 滚动停顿；后端任务引擎加分批策略
   - 起手点：`job-seeker-ext/ext_shared/core/site-executor.js`

### 🔵 ROI 低（先放着）

10. **订阅/支付/账单完整链路** ❌
   - 业务影响：**高**（商业化）但**当前阶段可推迟**
   - 修复成本：**极高**——需要支付提供商对接（Stripe/PayPal）+ webhook + 订阅状态机
   - 建议：商业化拐点前先用 Credit + 灰度白名单即可

---

## 3. 重构 Backlog 建议（与 [PROJECT_OVERVIEW.md §6](./PROJECT_OVERVIEW.md#6-重构观察--技术债清单) 联动）

如果做"产品补 gap + 工程减债"的合并打包，建议这样排：

### Sprint 1（2 周）— 解锁收藏 + 打通感知链路
- 拆 `job-api-gateway/http_routes.py` → 抽 `routes/bookmark.py`，同时落 PRD 收藏 API 全套（gap #1 + 工程债 §6.1）
- 扩展加 `extractPageContext()` 横切，三平台共享（gap #3）
- 前端 `sidepanel.js` 加 `<ContextBar>` + 监听 pageContext

### Sprint 2（2 周）— 任务模板 + 双向通知
- 补 3 个任务模板（gap #2）
- 任务 5 态正名（gap #8）
- 三个 callback 绑定 SSE 推送（gap #6）
- 拆 `tasks_router.py` 或抽 `tasks/notifier.py`

### Sprint 3（2 周）— UI v3 视觉一致 + 三平台 DOM 注入
- `<AnalysisCard>` 组件（gap #5），落 [DESIGN.md](./DESIGN.md) 状态色
- LinkedIn / Indeed DOM 注入（gap #4 上半），与 Sprint 1 的收藏 API 对接

### Sprint 4（2 周）— Credit + 商业化雏形
- Credit 模型分档 + Header widget（gap #7）
- `quota_tracker.py` 扩为完整套餐管理
- 套餐 / Pro 升级 UI（不含真实支付）

---

## 4. 待澄清问题（影响 gap 判定）

下列项目本静态分析无法判定，需运行/产品确认：

1. **`job-api-admin`** 仓库（未拉取）是否承载部分 UI gap？例如订阅管理可能在那里。
2. **简历"从平台读取"**：是否已委托给 `dinq-server` SaaS 或 `internal_resume_client.py`（`47.84.195.154:8082`）？
3. **Credit 计价**：`quota_tracker.py` 当前的"quota"和 PRD 的"Credit"是否同一概念，还是两套系统？
4. **跨设备云同步**：依赖谁——PostgreSQL 已是云端 DB，但前端的"换设备无缝接续"是否做了 token + state restore？
5. **`personalization/chips.py`** 的规则 pool 是空表还是有种子数据？空的话 9 种主动提示需要先填规则。

---

## 5. 不在 PRD 中 / 代码独有

代码里实现但 PRD v1 未提及（可能是工程预留、也可能是 PRD 应补）：

- **MCP 协议**：agent-gw ↔ api-gw 用 MCP（PRD 完全没提，但这是核心架构）
- **boss_cli / boss_cli_server**：CLI 操作方式 + 伪装扩展进程（产品文档无）
- **dynamic-commands YAML**：云端下发命令（产品文档无）
- **Extended Thinking**：agent-gw 接入 Claude 推理过程透明化（产品文档无）
- **Capture / Evolve**：`job-evolve-agent` 整个工程在 PRD 中无对应
- **Admin / 监控 (job-api-admin)**：管理后台 PRD 未涉及

> 这些不是"代码冗余"，是 PRD 还未覆盖的工程能力。后续产品文档迭代应补一节"内部工具"。

---

*分析版本：v1 / 2026-05-14 / 4 个 Explore agent 并行核查 / 共 131 条 PRD 项*
