# 架构总览

> smart-job 是一套多平台（BOSS直聘 / LinkedIn / Indeed）求职与招聘自动化系统。
> 本文给出系统的整体架构、组件职责与端到端数据流。各组件的深入设计见
> [BACKEND.md](BACKEND.md)、[EXTENSION.md](EXTENSION.md)、[ADMIN.md](ADMIN.md)；
> 平台交互的分层工作流（抓包 / 反编译 API / DOM / 图像识别）见 [WORKFLOW.md](WORKFLOW.md)。

## 1. 设计目标

- **对话式自动化**：用户用自然语言向 Agent 表达意图（"找 NLP 工程师"、"评估这个岗位"、
  "给这批候选人发招呼"），Agent 调用工具完成搜索、评估、沟通、投递等动作。
- **真实浏览器执行**：所有平台请求都在用户自己的浏览器里、用户自己的登录态下发出，
  不在服务端伪造请求，降低风控风险。
- **求职 / 招聘双模**：同一套系统同时服务找工作的求职者和找人的招聘者。
- **云端可扩展**：平台 API 变化时，新命令以配置形式下发到扩展，无需重新发布扩展。

## 2. 组件构成

系统由 **五个可独立部署的组件** 组成：一个浏览器扩展、三个后端服务、一个管理后台。

```
                            ┌──────────────────────────┐
                            │   浏览器扩展 job-seeker    │
                            │  （Chrome MV3，求职/招聘） │
                            │  抓包 · 自动填表 · 自动化  │
                            └─────────────┬────────────┘
                       WebSocket /ext/ws  │  HTTP/SSE
                                          │
        ┌─────────────────────────────────┼──────────────────────────────┐
        │                                 │                              │
┌───────▼────────┐              ┌─────────▼─────────┐          ┌──────────▼────────┐
│  api-gateway   │              │   agent-gateway   │          │    portal-api     │
│     :8767      │◄────/mcp─────│       :8769       │──/auth──►│      :8771        │
│ 命令网关 / MCP  │   工具调用    │ Agent 对话 / 编排  │  鉴权    │  账号 / JWT 签发  │
│  / 浏览器代理   │              │  / 任务 / SSE     │          │                   │
└───────┬────────┘              └─────────┬─────────┘          └──────────┬────────┘
        │                                 │                              │
        └─────────────────┬───────────────┴──────────────┬───────────────┘
                           │                              │
                  ┌────────▼────────┐           ┌─────────▼────────┐
                  │   PostgreSQL    │           │      Redis       │
                  │ boss_gateway /  │           │  会话 / 历史缓存  │
                  │   smart_job     │           │                  │
                  └─────────────────┘           └──────────────────┘

         ┌────────────────────────────────────────────────────┐
         │   管理后台 admin :8080 — Vue3，运维监控与配置下发     │
         │   反代 → api-gateway / agent-gateway / portal-api    │
         └────────────────────────────────────────────────────┘
```

| 组件 | 端口 | 技术栈 | 职责 |
|---|---|---|---|
| `extensions/job-seeker` | — | Chrome MV3 / 原生 JS | 在用户浏览器内执行抓包、自动填表、平台 API 调用 |
| `packages/api-gateway` | 8767 | Python / FastMCP / Starlette | 命令网关、MCP 工具服务、扩展 WebSocket 隧道、浏览器代理 |
| `packages/agent-gateway` | 8769 | Python / Starlette | Agent 对话核心、模式系统、任务编排、SSE 流式输出 |
| `packages/portal-api` | 8771 | Python / FastAPI | 账号注册登录、多种鉴权方式、JWT 签发与 JWKS |
| `packages/admin` | 8080 | Vue 3 / Vite / TS | 运维监控、命令下发、用户与任务管理 |
| `packages/job_common` | — | Python（纯逻辑） | 跨服务共享的风控信号定义与检测 |

## 3. 端到端数据流

以"求职者让 Agent 搜索岗位并打招呼"为例：

```
1. 用户在扩展侧边栏输入"帮我搜 5 年经验的后端岗位并打招呼"
       │
       ▼  HTTP POST /agent/sse（SSE 长连接）
2. agent-gateway 接收消息
   - 校验 JWT（向 portal-api 取公钥）
   - 模式系统判定意图 → search 模式
   - 进入 Agent 对话循环，组装系统提示词
       │
       ▼  调用 MCP 工具（HTTP /mcp）
3. api-gateway 收到工具调用 boss_search_jobs
   - 通过 WebSocket /ext/ws 把命令下发给对应扩展会话
       │
       ▼  WebSocket
4. 扩展 background 收到命令
   - 命令注册表查到 handler
   - 令牌链校验依赖（list 阶段令牌）
   - site-executor 在隐藏的 Worker Tab 内用真实 fetch 请求平台 API
       │
       ▼  返回结果
5. 结果逐层回传：扩展 → api-gateway → agent-gateway
   - agent-gateway 把岗位列表作为工具结果交给模型
   - 模型决定下一步：取详情 → 打招呼 → 发消息
   - 每一步通过 SSE 把 text_delta / tool_call / job_list_card 等事件推回扩展
       │
       ▼  SSE 事件流
6. 扩展侧边栏实时渲染对话、岗位卡片、操作按钮
```

整个过程中：

- **平台请求始终在用户浏览器内发出**，复用用户登录 Cookie 与真实指纹。
- **api-gateway 不直接访问平台**，它只是把命令隧道转发给扩展。
- **agent-gateway 不直接访问平台**，它只通过 MCP 调用 api-gateway 暴露的工具。
- **风控信号**（验证码、限流、登录失效）由扩展或工具识别，沿调用链上抛，
  由任务引擎按 [job_common](../packages/job_common/) 中定义的策略处理（重试 / 暂停 / 跳过 / 终止）。

## 4. 关键机制

### 4.1 MCP 工具层

api-gateway 用 [FastMCP](https://github.com/jlowin/fastmcp) 把"平台动作"暴露成
标准 MCP 工具（搜索岗位、取详情、打招呼、发消息、搜候选人……）。agent-gateway
作为 MCP 客户端调用这些工具。工具集分为：

- **静态工具**：编译进服务，覆盖 BOSS / LinkedIn / Indeed 的核心动作。
- **动态命令**：以配置（YAML / JSON）形式存于数据库，运行时注册成 MCP 工具，
  并同步下发给扩展。平台接口变动时，运维在管理后台改配置即可，无需改代码。

### 4.2 扩展命令与令牌链

扩展侧维护一个**命令注册表**，每条命令声明 `requires`（依赖的令牌）和
`produces`（产出的令牌）。平台 API 通常需要一串有时效的安全令牌
（列表 → 详情 → 会话 → 发消息），扩展用**令牌链（token chain）**这一有向依赖
模型自动管理：调用 `get_job_detail` 前会先确保 `list` 阶段令牌存在且未过期，
缺失则自动回补。详见 [EXTENSION.md](EXTENSION.md)。

### 4.3 模式系统

agent-gateway 根据用户意图在多个**模式**间切换（搜索 / 评估 / 投递 / 面试准备 /
对比 / 招聘），每个模式注入不同的系统提示词与工具偏好。模式可在一轮对话中途
根据实际调用的工具升级。详见 [BACKEND.md](BACKEND.md)。

### 4.4 任务编排

除了即时对话，agent-gateway 还支持**长任务**（批量打招呼、批量简历筛选等）。
任务由模板定义、后台执行，进度与风控暂停通过 SSE / 数据库回报，运维可在管理
后台查看与干预。

### 4.5 鉴权

portal-api 用 RSA 密钥对签发 JWT，并在 `/.well-known/jwks.json` 暴露公钥。
api-gateway 与 agent-gateway 拿公钥**本地校验**令牌，无需每次回查 portal-api。

## 5. 数据存储

- **PostgreSQL**：两个库 —— `boss_gateway`（api-gateway 用）与 `smart_job`
  （agent-gateway / portal-api 用）。库在 Postgres 容器首次启动时由
  [`docker/postgres-init.sql`](../docker/postgres-init.sql) 创建；各服务的表结构
  在自身启动时以 `CREATE TABLE IF NOT EXISTS` 建好（portal-api 使用独立的
  `identity` schema 做隔离）。
- **Redis**：agent-gateway 用于跨 worker 共享会话历史与轮次计数，不可用时
  优雅降级（功能可用，仅失去多 worker 共享能力）。

## 6. 部署

推荐用 Docker Compose 一键拉起全部组件，详见根目录 [README.md](../README.md)。
启动顺序约束：

```
PostgreSQL / Redis  →  portal-api  →  api-gateway  →  agent-gateway  →  admin
```

portal-api 需先就绪（其它服务校验 JWT 依赖其公钥）；agent-gateway 依赖
api-gateway 的 `/mcp`。

## 7. 国际化

扩展与管理后台均支持中 / 英双语：

- 扩展：自带轻量 i18n 引擎（`extensions/job-seeker/lib/i18n/`）。
- 管理后台：`vue-i18n`，按文件名自动合并命名空间（`packages/admin/src/i18n/`）。
