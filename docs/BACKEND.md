# 后端设计

> 后端由三个 Python 服务和一个共享纯逻辑包组成。三个服务均为异步（asyncio）、
> 启动时自建表结构、通过 `.env` 配置。总体定位见 [ARCHITECTURE.md](ARCHITECTURE.md)。

```
packages/
├── job_common/      共享纯逻辑包（风控信号）
├── api-gateway/     :8767  命令网关 / MCP 服务 / 浏览器代理
├── agent-gateway/   :8769  Agent 对话核心 / 任务编排 / SSE
└── portal-api/      :8771  账号 / 鉴权（JWT）
```

`job_common` 通过相对路径注入到各服务的 `sys.path`，因此在 Docker 镜像中需与服务
目录保持同级关系（参见各服务的 `Dockerfile`）。

---

## 1. api-gateway（:8767）

**职责**：作为 Agent 与浏览器扩展之间的命令网关。它把平台动作暴露成 MCP 工具，
通过 WebSocket 隧道把命令下发到扩展执行，并管理会话、动态命令、浏览器代理。

**技术栈**：FastMCP + Starlette + uvicorn，PostgreSQL（`asyncpg`）。

### 1.1 主要模块

| 模块 | 职责 |
|---|---|
| `server.py` | 入口；以 HTTP + WebSocket 模式启动，挂载 MCP 服务与 HTTP 路由 |
| `mcp_tools_*.py` | 按平台划分的 MCP 工具定义（BOSS / LinkedIn / Indeed） |
| `commands.py` | 工具背后的命令处理实现 |
| `http_routes.py` | HTTP / WebSocket 路由：状态、登录、管理后台 REST API |
| `dynamic_mcp_registry.py` | 运行时把数据库里的动态命令配置注册成 MCP 工具 |
| `db.py` | PostgreSQL 持久化：会话、命令日志、浏览器槽位、缓存岗位、动态配置等 |
| `billing/` | 计费相关（Stripe 集成、定价、额度账本）——可选模块 |

### 1.2 对外接口

- `POST /mcp` —— FastMCP 的 JSON-RPC 端点，供 agent-gateway 作为 MCP 客户端调用。
- `WS /ext/ws` —— 扩展双向命令隧道。扩展以 `name / kind / bid` 等参数握手，
  服务端按 `(用户, 角色)` 维度管理会话、踢除重复登录。
- `WS /admin/ws` —— 管理后台实时事件广播通道。
- `GET /status`、`GET /health` —— 健康检查。
- `/admin/*` —— 管理后台 REST API（会话列表、命令日志、浏览器槽位、动态命令下发等），
  由 `ADMIN_PASSWORD` 保护（留空则本地开发不校验）。

### 1.3 动态命令

平台接口经常变动。api-gateway 支持把命令以配置形式存入数据库
（`ext_dynamic_config` 表），运行时：

1. 启动时从数据库加载最新版本，注册成 MCP 工具。
2. 运维在管理后台编辑并下发新版本。
3. 同一份配置经 WebSocket 推送给所有在线扩展，扩展据此生成本地命令处理器。

这样平台 API 变化时只需改配置，无需重新发布扩展或后端。

### 1.4 关键配置（`.env`）

| 变量 | 说明 |
|---|---|
| `BOSS_GATEWAY_PORT` | 监听端口（默认 8767） |
| `DB_POSTGRES_URL` | PostgreSQL 连接串（`boss_gateway` 库） |
| `ADMIN_PASSWORD` | 管理后台 / 管理 API 密码，留空则不校验 |
| `EXT_TOKEN` | 扩展 WebSocket 接入令牌 |
| `PROXY_POOL` | 可选，浏览器代理 IP 列表 |

---

## 2. agent-gateway（:8769）

**职责**：Agent 对话的核心。它运行 Agent 推理循环，按用户意图切换模式，调用 MCP
工具，通过 SSE 把过程流式推回前端；同时承载长任务编排、简历管理与个性化。

**技术栈**：Starlette + uvicorn，PostgreSQL（`asyncpg`），Redis；LLM 走
OpenRouter（优先）或 Anthropic。

### 2.1 主要模块

| 模块 | 职责 |
|---|---|
| `server.py` | 入口；初始化数据库、Redis、MCP 连接 |
| `agent_loop.py` | Agent 推理主循环：组装系统提示、调用工具、产出协议事件 |
| `sse_router.py` | SSE 端点：发送消息、初始化 / 恢复会话、中断、清理 |
| `modes/` | 模式系统：模式定义、意图检测、各模式的系统提示词 |
| `tasks/` | 任务引擎：模板注册、后台执行、风控信号处理、进度通知 |
| `mcp_manager.py` | MCP 服务连接管理（含自动重连、指数退避） |
| `resume_db.py` / `preferences_db.py` / `recruiter_db.py` | 简历、求职偏好、招聘画像的持久化 |
| `redis_client.py` | Redis 单例，跨 worker 共享会话历史与轮次计数，不可用时优雅降级 |
| `db.py` | 会话、对话事件、消息历史、任务、工具调用日志的持久化 |

### 2.2 Agent 对话核心

`agent_loop.py` 是一个异步生成器：

1. 接收用户消息，按模式系统组装系统提示词。
2. 进入"模型推理 → 工具调用 → 工具结果 → 再推理"的多轮循环。
3. 每一步产出协议事件（文本增量、思考增量、工具调用、工具结果、卡片、操作按钮……）。
4. 事件经 `sse_router.py` 以 SSE 推给前端，实现实时流式体验。

SSE 事件类型包括：`connected`、`text_delta`、`thinking_delta`、`tool_call`、
`tool_result`、`job_list_card`、`candidate_list_card`、`action_buttons`、
`message_end`、`error`、`aborted`、`done`。

### 2.3 模式系统

`modes/` 目录定义若干**模式**，每个模式对应一种求职 / 招聘场景：

| 模式 | 场景 |
|---|---|
| `search` | 搜索岗位 / 候选人（默认模式） |
| `evaluate` | 评估某个岗位与简历的匹配度 |
| `apply` | 投递 / 打招呼 / 发消息 |
| `interview` | 面试准备 |
| `compare` | 多个岗位 / 候选人对比 |
| `recruiter` | 招聘方专用 |

意图检测综合**关键词打分**与**工具使用倾向**：用户消息里的关键词、上一轮调用过的
工具都会影响模式判定；当前模式有"粘性加分"避免抖动。模式可在一轮对话中途根据
实际调用的工具升级（例如调用了发消息工具 → 切到 `apply` 模式）。

### 2.4 任务编排

`tasks/` 提供长任务能力。任务由**模板**定义（一组步骤，每个步骤含多个条目，
如"给 100 个候选人发招呼"）。任务引擎：

1. 逐步骤、逐条目执行。
2. 工具调用若抛出风控信号（`RiskControlSignal`），按信号策略处理：
   - **auto_retry**：等待后自动重试（平台抖动 / 限流）。
   - **user_action**：暂停任务，提示用户处理（验证码、登录失效）。
   - **skip_item**：记录原因、跳过当前条目（配额耗尽、候选人已拉黑）。
   - **abort**：致命错误，终止任务（扩展掉线、会话被踢）。
3. 定期心跳；进度与暂停状态写入数据库，运维可在管理后台查看与干预。

### 2.5 简历与个性化

- 简历管理**完全本地**：上传的 PDF / DOCX 在本服务内解析、缓存、版本化，
  不调用任何外部服务（既不拉取也不推送）。
- `preferences_db` 保存求职偏好（目标岗位 / 城市 / 薪资 / 资历）。
- `recruiter_db` 保存招聘画像（目标角色、必备 / 加分技能、经验与学历要求等）。

### 2.6 关键配置（`.env`）

| 变量 | 说明 |
|---|---|
| `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` | LLM 接入密钥 |
| `AGENT_MODEL` / `FALLBACK_MODEL` | 主模型与降级模型 |
| `BOSS_API_GATEWAY_URL` | api-gateway 地址（取 MCP 工具） |
| `JOB_PORTAL_API_URL` | portal-api 地址（取 JWT 公钥） |
| `DB_POSTGRES_URL` | PostgreSQL 连接串（`smart_job` 库） |
| `REDIS_URL` | Redis 连接串 |
| `CONCURRENT_TURNS` / `TURN_TIMEOUT` | 并发轮次与超时控制 |

---

## 3. portal-api（:8771）

**职责**：用户身份与鉴权中心。负责账号注册登录、多种鉴权方式、JWT 签发，
并向其它服务暴露验签公钥。

**技术栈**：FastAPI + uvicorn，PostgreSQL（`asyncpg`，独立 `identity` schema）。

### 3.1 主要模块

| 模块 | 职责 |
|---|---|
| `server.py` | FastAPI 入口；生命周期内初始化 JWT 密钥与数据库池 |
| `auth_router.py` | 注册、邮箱验证、登录、刷新、登出、改密、注销、找回密码 |
| `oauth_router.py` | 第三方登录（Google / 微信等）的授权与回调 |
| `admin_router.py` | 管理端：用户列表、身份、登录事件、禁用、角色分配 |
| `jwt_service.py` | RSA 密钥对管理、JWT 签发、JWKS 文档 |
| `db.py` | `identity` schema 下的表：用户、身份、刷新令牌、验证码、审计事件 |

### 3.2 鉴权设计

- 支持多种鉴权方式：邮箱 + 密码、第三方 OAuth、一次性验证码、Passkey 等，
  统一抽象为"身份（identity）"挂在同一个用户账号下。
- 签发**访问令牌 + 刷新令牌**，刷新令牌轮换（rotation）。
- `GET /.well-known/jwks.json` 暴露公钥。api-gateway 与 agent-gateway 拿公钥
  **本地验签**，无需每次回查 portal-api —— 这是一种去中心化的鉴权校验。
- 所有关键动作（登录、登出、改密、注销、身份绑定）写入 `auth_events` 审计表。

### 3.3 关键配置（`.env`）

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接串（`smart_job` 库，`identity` schema） |
| `JWT_PRIVATE_KEY_PATH` | RSA 私钥路径，缺失时自动生成（**切勿提交到版本库**） |
| `ACCESS_TTL_MIN` / `REFRESH_TTL_DAYS` | 令牌有效期 |
| `RESEND_API_KEY` | 发送验证邮件的服务密钥，留空则验证码打到日志（开发模式） |

---

## 4. job_common（共享包）

跨服务共享的**纯逻辑**包，不含 I/O，主要承载风控逻辑：

| 模块 | 职责 |
|---|---|
| `risk_signals.py` | 风控信号注册表：把平台摩擦（验证码、限流、登录失效、配额耗尽……）映射到处理策略（auto_retry / user_action / skip_item / abort），并定义 `RiskControlSignal` 异常 |
| `risk_detector.py` | 从工具的错误信息中匹配出风控信号 |

工具执行遇到平台摩擦时抛出 `RiskControlSignal`，由 agent-gateway 的任务引擎
按策略统一处理。把这部分逻辑独立成包，是为了让"信号定义"成为各服务的单一事实来源。

---

## 5. 服务间协作小结

```
扩展  ──WebSocket /ext/ws──►  api-gateway
agent-gateway  ──HTTP /mcp──►  api-gateway        （调用工具）
agent-gateway  ──HTTP /auth──►  portal-api         （取 JWT 公钥验签）
api-gateway / agent-gateway / portal-api  ──►  PostgreSQL
agent-gateway  ──►  Redis                          （会话历史 / 轮次计数）
```

- api-gateway 与 agent-gateway **都不直接访问求职平台**，平台请求一律由扩展在
  用户浏览器内发出。
- 三个服务可共用一个 PostgreSQL 实例（不同库 / schema 隔离）。
