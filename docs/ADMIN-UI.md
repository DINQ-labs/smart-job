# 管理后台界面一览

> 本文是 [ADMIN.md](ADMIN.md) 的**配套截图说明**:按侧边栏从上到下,逐个菜单页给出
> 界面截图与简述。截图由 Playwright 无头浏览器自动抓取(整页截图),取自**本地开发
> 环境** —— 数据较少,部分页面呈空态属正常。各页面的设计、数据来源与交互细节见
> [ADMIN.md](ADMIN.md)。
>
> 管理后台共 **24 个菜单页**,侧边栏分 5 组。

## 菜单总览

| # | 菜单 | 路由 | 一句话 |
|---|---|---|---|
| 1 | 仪表盘 | `/` | 系统实时总览 |
| 2 | 命令日志 | `/commands` | MCP / 工具调用记录 |
| 3 | 错误日志 | `/error-logs` | 网关 + Agent 错误合并视图 |
| 4 | 命令管理 | `/command-registry` | 工具元数据注册表 |
| 5 | 动态命令推送 | `/dynamic-commands` | 云端命令配置下发 |
| 6 | 浏览器池 | `/pool` | 扩展浏览器会话槽位 |
| 7 | CLI 池 | `/cli-pool` | 无浏览器 CLI 会话池 |
| 8 | 代理池 | `/proxy-pool` | 代理 IP 轮换配置 |
| 9 | 职位缓存 | `/job-cache` | 抓取缓存的岗位列表 |
| 10 | Agent 会话 | `/agent-sessions` | 在线 AI 对话会话 |
| 11 | 长任务监控 | `/agent-tasks` | 批量长任务执行 |
| 12 | 模板编排 | `/agent-templates` | 任务模板(步骤 DAG) |
| 13 | 对话历史 | `/agent-history` | 历史会话与事件时间线 |
| 14 | Agent 用户 | `/agent-users` | Agent 用户名册 |
| 15 | 账户用户 | `/portal-users` | portal-api 账号管理 |
| 16 | 候选人简历 | `/candidate-resumes` | 候选人简历库 |
| 17 | API Capture | `/api-capture` | 抓包会话与 AI 分析 |
| 18 | 表单知识库 | `/autofill-templates` | 自动填表字段模板 |
| 19 | 抓包审查 | `/autofill-captures` | 填表抓包数据审查 |
| 20 | 系统监控 | `/system-monitor` | 后端服务实时指标 |
| 21 | MCP 调用观测 | `/mcp-metrics` | 工具调用可观测性 |
| 22 | 前端 JS 错误 | `/client-errors` | 扩展前端错误聚合 |
| 23 | 快捷指令配置 | `/chip-configs` | 侧边栏快捷按钮配置 |
| 24 | Growth | `/growth` | 增长 KPI |

---

## 一、网关 · 命令 · 资源池

### 1. 仪表盘 `/`

![仪表盘](images/admin/01-dashboard.png)

系统实时总览。顶部统计条:扩展在线数、Agent 在线数、今日命令数、平均耗时、错误率、
WS 连接状态;下方依次为扩展会话(可切代理 / 退出登录 / 强制断开 / 自定义命令)、
Agent 连接、实时事件流、Agent 模式分布(搜索 / 评估 / 投递 / 面试 / 对比 / 招聘)、
CLI 会话、最近命令日志。

### 2. 命令日志 `/commands`

![命令日志](images/admin/02-commands.png)

系统内所有 MCP / 工具调用记录,可按工具、会话、用户筛选。

### 3. 错误日志 `/error-logs`

![错误日志](images/admin/03-error-logs.png)

合并网关错误与 Agent 错误的统一视图,按时间 / 工具 / 关键词筛选。

### 4. 命令管理 `/command-registry`

![命令管理](images/admin/04-command-registry.png)

所有可用 MCP / 扩展工具的元数据注册表。

### 5. 动态命令推送 `/dynamic-commands`

![动态命令推送](images/admin/05-dynamic-commands.png)

**云端配置下发的核心页面**:用 JSON 编辑器定义命令链 —— 从当前生产载入、填示例模板、
本地校验、一键推送给所有在线扩展并跟踪 ACK,支持按版本回滚;右侧列出即将受影响的扩展。

### 6. 浏览器池 `/pool`

![浏览器池](images/admin/06-browser-pool.png)

扩展浏览器会话的槽位状态、平台筛选、容量配置、强制释放。

### 7. CLI 池 `/cli-pool`

![CLI 池](images/admin/07-cli-pool.png)

`boss-cli-server`(httpx、无浏览器)的会话池管理。

### 8. 代理池 `/proxy-pool`

![代理池](images/admin/08-proxy-pool.png)

浏览器代理 IP 的轮换配置。

### 9. 职位缓存 `/job-cache`

![职位缓存](images/admin/09-job-cache.png)

平台抓取并缓存的岗位列表,支持筛选与刷新详情。

---

## 二、Agent 与用户

### 10. Agent 会话 `/agent-sessions`

![Agent 会话](images/admin/10-agent-sessions.png)

在线 AI 对话会话 —— 用户、当前模式、轮次、历史,可中断、踢用户。

### 11. 长任务监控 `/agent-tasks`

![长任务监控](images/admin/11-agent-tasks.png)

长任务执行 —— 状态(待执行 / 运行中 / 暂停 / 完成 / 失败 / 取消)、进度、跳过条目、
强制取消;支持按角色 / 平台 / 关键词筛选,5s 自动刷新。

### 12. 模板编排 `/agent-templates`

![模板编排](images/admin/12-agent-templates.png)

预定义的任务模板 —— 角色 + 平台组合、步骤 DAG、预计耗时。

### 13. 对话历史 `/agent-history`

![对话历史](images/admin/13-agent-history.png)

历史会话与对话事件时间线,含 token 用量与成本。

### 14. Agent 用户 `/agent-users`

![Agent 用户](images/admin/14-agent-users.png)

Agent 用户名册,含简历解析状态、连接状态、偏好。

### 15. 账户用户 `/portal-users`

![账户用户](images/admin/15-portal-users.png)

来自 portal-api 的账号管理 —— 角色、禁用状态、鉴权身份、刷新令牌。

### 16. 候选人简历 `/candidate-resumes`

![候选人简历](images/admin/16-candidate-resumes.png)

候选人简历文件,含解析状态与偏好管理。

---

## 三、工具

### 17. API Capture `/api-capture`

![API Capture](images/admin/17-api-capture.png)

扩展上报的抓包会话与请求,可触发 AI 分析、导出数据 —— 是「反编译平台 API → 动态命令」
流程的入口(详见 [WORKFLOW.md](WORKFLOW.md))。

---

## 四、AutoFill 扩展

### 18. 表单知识库 `/autofill-templates`

![表单知识库](images/admin/18-autofill-templates.png)

自动填表的表单字段知识库与站点模板。

### 19. 抓包审查 `/autofill-captures`

![抓包审查](images/admin/19-autofill-captures.png)

录制的填表抓包数据审查(上报前已对 PII 脱敏)。

---

## 五、系统

### 20. 系统监控 `/system-monitor`

![系统监控](images/admin/20-system-monitor.png)

后端服务的实时指标 —— 内存、运行时长、会话数、连接池、数据库池。

### 21. MCP 调用观测 `/mcp-metrics`

![MCP 调用观测](images/admin/21-mcp-metrics.png)

工具调用的可观测性 —— 成功率、时延、调用量时序图、风控命中趋势、风控信号热点、
用户调用排行;支持 1h / 6h / 24h / 7d 时间窗。

### 22. 前端 JS 错误 `/client-errors`

![前端 JS 错误](images/admin/22-client-errors.png)

聚合自扩展侧边栏的前端 JS 错误。

### 23. 快捷指令配置 `/chip-configs`

![快捷指令配置](images/admin/23-chip-configs.png)

按角色 / 平台 / 语言配置侧边栏的快捷按钮 —— 中英文案、权重、排序、启用开关、实时预览。

### 24. Growth `/growth`

![Growth](images/admin/24-growth.png)

营收、用户增长、功能使用等高层增长 KPI。

---

## 关于截图

- 截图用 Playwright(Chromium 无头)自动抓取:注入管理后台会话 cookie 后,从上到下
  逐个菜单整页截图,输出到 [`images/admin/`](images/admin/)。
- 取自本地开发环境,数据稀少,空态(「暂无任务」「没有风控命中」等)属正常。
- 后台 UI 变更后可重新抓取以更新本文截图。
