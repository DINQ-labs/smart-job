# smart-job 架构说明

> ⚠️ **路径已过时**：本文档写于 monorepo 重组之前 —— 文中的 `job-api-gateway/`、
> `job-agent-gateway/` 等目录现已变为 `packages/api-gateway/`、`packages/agent-gateway/` 等；
> `job-evolve-agent`、旧版扩展、cookie 采集功能等已移除。**目录结构以根目录
> [README.md](../README.md) 为准**；本文档保留用于模块职责与数据流的深入参考。

> 原始用途：为重构与新成员上手提供一份"地图"，基于工作区代码静态盘点而成；行为推断以源码为准。

---

## 0. 一句话定位

**smart-job 是一个多平台（BOSS直聘 / LinkedIn / Indeed）求职-招聘双边自动化系统**：用户侧是一个 Chrome MV3 扩展，后端由两个独立网关（API 网关 + Agent 网关）+ 一个外部"代码自演进"工具组成，整体围绕 Claude（API + Code SDK）做对话与任务自动化。

---

## 1. 工程拓扑

工作区根目录：`/home/laohan/workspace/smart-job/`

```
                                    ┌──────────────────────────────┐
                  浏览器 (用户)      │     Claude / OpenRouter       │
                       │            └──────────────────────────────┘
                       │                          ▲
        ┌──────────────▼──────────────┐           │ HTTPS
        │   job-seeker-ext (MV3)      │           │
        │   - background.js           │           │
        │   - sidepanel/popup/options │           │
        │   - ext_shared/*            │           │
        └──────────┬───────────────┬──┘           │
                   │ WSS           │ HTTPS/SSE    │
                   │ /ext/ws       │ /agent-gw    │
       wss://testapi.dinq.me       │              │
                   │               │              │
        ┌──────────▼─────────┐ ┌───▼──────────────┴──────┐
        │  job-api-gateway   │ │  job-agent-gateway      │
        │  port 8767         │ │  port 8769              │
        │  (MCP server +     │ │  (Claude Agent +        │
        │   命令分发 +       │◀┤   SSE 会话 + 任务编排)  │
        │   浏览器代理)      │ │                          │
        └────┬───────────────┘ └─────┬──────────┬────────┘
             │                       │          │
             ▼ PG (boss_gateway)     ▼ PG       ▼ Redis (6380)
                                                 │
                                                 ▼
        ┌────────────────────────────────────────────────┐
        │ job_common/  (sys.path 注入的纯逻辑共享包)     │
        │   risk_signals.py / risk_detector.py           │
        └────────────────────────────────────────────────┘

        ┌─────────────────────────────────┐
        │  job-evolve-agent (port 8770)   │   外挂工具，不在主链路上
        │  SQLite + Claude Code SDK       │   把抓包 → 代码改造任务
        │  在 4 个 repo 上做 evolve/analyze│
        └─────────────────────────────────┘

        ┌─────────────────────────────────┐
        │  job-api-admin (本仓库未拉取)   │   Vue3 + Vite 管理后台
        │  调 agent-gw / api-gw 的 admin  │   被 evolve-agent verifier 引用
        │  REST                            │
        └─────────────────────────────────┘
```

---

## 2. 子工程总览

| 工程 | 类型 | 入口 | 端口 | 数据库 | 关键依赖 | 代码量 |
|---|---|---|---|---|---|---|
| **job-agent-gateway** | FastAPI/Starlette + MCP 客户端 | `server.py` | 8769 | PostgreSQL + Redis(6380) | anthropic, mcp, asyncpg | ≈ 42k 行 Python |
| **job-api-gateway** | FastMCP + Starlette + WS hub | `server.py` | 8767 | PostgreSQL (`boss_gateway`) | mcp, asyncpg, httpx, playwright/chromium | ≈ 18k 行 Python |
| **job-evolve-agent** | Starlette + Vue 前端 + Claude Code SDK | `server.py` | 8770 (127.0.0.1) | SQLite WAL `~/.evolve/evolve.db` | claude-agent-sdk ≥0.1.46 | ≈ 3k 行 Python |
| **job-seeker-ext** | Chrome MV3 扩展 (DingQ 助手 v2.1.4) | `background.js` (SW) | — | chrome.storage | javascript-obfuscator (build only) | ≈ 12k 行 JS |
| **job_common** | Python 共享包（无 IO） | `__init__.py` | — | — | 纯逻辑 | ≈ 15k 字节 |
| **job-api-admin** | Vue3 + Vite 管理后台 | （本工作区未拉取） | — | — | vue-tsc | — |

---

## 3. 各子工程详解

### 3.1 job-agent-gateway —— Agent 对话核心 (port 8769)

**职责**：为每个用户维护一个独立的 Claude 多轮对话 SSE 会话，挂接 MCP 工具集，跑后台自动化任务，管理简历与偏好。

**入口 / 路由前缀**（见 `server.py:1-1261`）：

| 前缀 | 模块 | 用途 |
|---|---|---|
| `/agent/sse` | `sse_router.py` (897 行) | SSE 主聊天端点，流式 token / tool_use / tool_result |
| `/resume/*` | `resume_router.py` | 简历上传/解析/查询 |
| `/user-preferences/*` | `preferences_router.py` | 用户偏好（目标职位/城市/薪资） |
| `/recruiter/*` | `recruiter_router.py` | 招聘官模板、岗位管理 |
| `/jobs/evaluate` | `job_evaluator.py` | LLM 匹配评分 + 自我介绍生成 |
| `/voice/*` | `voice_router.py` | 语音识别与实时流 |
| `/tasks/*` | `tasks_router.py` (559 行) | 后台自动化任务 CRUD |
| `/admin/*` | `server.py` 内联 | 会话/错误监控/统计（供 job-api-admin） |
| `/capture/*` | `capture_router.py` (621 行) | 数据采集（被扩展上报） |
| `/agent-recovery` | `server.py` | 跨 worker SSE 会话恢复 |

**核心机制**：

- **Agent 主循环** — `agent_loop.py` (2107 行)：单轮 Claude API 推理；多 MCP server 编排；mode 系统组装 system prompt；Extended Thinking budget 8000 tokens；fallback 模型链（主→fallback→重试）。
- **Mode 系统**（提示工程）— `modes/`：`base.py`(1098) `search.py`(911) `recruiter.py`(731) `detect.py`(264) + `apply / evaluate / interview / compare / cells`。意图自动检测→不同 system prompt + 工具过滤。
- **SSE 会话管理** — `sse_router.py:SseSessionManager`：Redis 跨 worker 状态、idle sweep、用户中断处理。
- **任务引擎** — `tasks/engine.py`：`TaskRunner` asyncio 循环；支持风控信号（auto_retry/user_action/skip/abort，来自 `job_common.risk_signals`）；暂停/恢复；心跳。`tasks/templates/*` + `tasks/steps/*` 按平台拆分。
- **持久层** — `db.py` (1874 行) asyncpg；主要表：`agent_conv_sessions` / `agent_conv_events` / `tasks` / 用户 / 推荐职位 / MCP 指标。专用表分到 `resume_db.py` / `preferences_db.py` / `recruiter_db.py` / `candidate_resume_db.py` / `capture_db.py`。

**LLM 配置** — `config.py:1-87`：
- 优先 OpenRouter，备用 Anthropic
- 主模型默认 `claude-opus-4-6`（⚠️ 见 §6.3）
- Fallback `z-ai/glm-5-turbo`
- `RESUME_PARSE_MODEL` / `SCORING_MODEL` 可独立覆盖

**对外通信**：
- 上游 MCP：`mcp_manager.py` 调 job-api-gateway 的 `/mcp`
- 外部简历：`dinq_resume_client.py`（dinq-server SaaS）、`internal_resume_client.py`（内部简历 API，硬编码 `47.84.195.154:8082`）

**部署** — `deploy.sh`：rsync + SSH 至 `43.106.141.228`，systemd 启 `uvicorn server:app --port 8769 --workers ${WORKER_COUNT:-1}`。

**docs/**：`extended-thinking.md`（仅一篇）。

---

### 3.2 job-api-gateway —— 命令分发 / 浏览器代理 / MCP 服务 (port 8767)

**职责**：把"高层命令"（搜索职位、发消息、查看候选人…）转译为对真实平台的操作。两种执行路径：① 通过 WebSocket 让浏览器扩展在用户登录态下执行；② 内置 `boss_cli_server.py` 直接以 httpx 调 Boss 直聘 API（无扩展场景）。同时把这套命令以 MCP tools 形式暴露给 Agent 网关。

**多入口**：

| 入口 | 行数 | 用途 |
|---|---|---|
| `server.py` | 192 | FastMCP + Starlette；监听 8767；挂载 `/mcp` `/ext/ws` `/admin/*` |
| `boss_cli.py` | 282 | 轻量 CLI，POST `/cli` 调用网关命令；`BOSS_GATEWAY_URL` 指向 |
| `boss_cli_server.py` | 431 | 长运行进程，**伪装成扩展**连接 `/ext/ws`，用 httpx 直接访问 Boss 站点（无浏览器场景） |
| `deploy.sh` | 52 | rsync→`/opt/job-api-gateway`，同步 `job_common`→`/opt/job_common` |

**模块清单（按规模降序）**：

| 文件 | 行数 | 职责 |
|---|---|---|
| `http_routes.py` | **2689** | 70+ HTTP/WS 路由；OAuth (LinkedIn)；Boss/LinkedIn/Indeed API 代理；admin REST |
| `db.py` | **2678** | asyncpg；18+ 表 CRUD：`account_cookies` / `browser_slots` / `user_job_interests` / `recruiter_geek_interests` / `platform_config` / `proxy_pool` / `cached_jobs` / `searches` / `chats` / `execution_decisions` |
| `commands.py` | **2275** | 60+ 高层命令；按 session 隔离；通过 `ext_client.send_command_to()` 转发 |
| `mcp_tools_boss.py` | 2066 | Boss 直聘 MCP tools（57 个），启动时 `register(mcp)` |
| `mcp_tools_indeed.py` | 1551 | Indeed MCP tools（求职 7 + 雇主 24） |
| `mcp_tools_linkedin.py` | 901 | LinkedIn MCP tools（11 个） |
| `linkedin_commands.py` | 812 | LinkedIn 命令封装 |
| `indeed_commands.py` | 663 | Indeed 求职命令封装 |
| `indeed_employer_commands.py` | 542 | Indeed 雇主命令封装 |
| `server_helpers.py` | 475 | 共享：`_ok/_err`、`_resolve_session`、`_admin_tokens`、`_run_boss_tool` |
| `browser_pool.py` | 417 | Chromium 池：槽位分配 / 生命周期（多用户并发） |
| `ext_client.py` | 294 | 向扩展 WS 发命令；多会话路由；DB 埋点；execution_guard 风控 |
| `geek_context.py` | 280 | 候选人数据缓存（搜索/详情/聊天） |
| `quota_tracker.py` | 274 | Agent 消费配额跟踪 |
| `session_store.py` | 273 | `SessionEntry` 注册表；`app_user_id ↔ session_id` 映射；per-session `job_store` / `geek_store` / `rate_limiter` |
| `dynamic_mcp_registry.py` | 254 | Phase 2：云端 yaml → FastMCP tool 运行时注册（无重启更新） |
| `candidate_preview.py` | 226 | 跨平台候选人字段统一映射（Boss/LinkedIn/Indeed → `_preview`） |
| `execution_guard.py` | 176 | 风控引擎：突发速率检测；`CONSUMPTIVE_PATHS` 硬编码 |
| `rate_limiter.py` | 148 | per-session 令牌桶 |
| `webhook.py` | 143 | webhook 接收 |
| `job_context.py` | 107 | 职位缓存 |
| `agent_tracker.py` | 81 | MCP 连接追踪；`mcp-session-id ↔ 扩展会话` 锁定 |
| `dynamic_command_state.py` | 62 | dynamic command 推送历史 |
| `admin_broadcaster.py` | 35 | admin SSE/WS 广播 |

**dynamic-commands/** 目录（云端 YAML 命令插件）：
- `boss/` / `linkedin/` / `indeed/` —— 各站点 YAML 命令定义
- `chains/` —— token chain 流程定义
- `_examples/` —— 参考样例

**与扩展的协议**（关键）：
- 扩展主动连 `/ext/ws`（反代域名 `wss://testapi.dinq.me/api/v1/job-api/ext/ws`，开发期 `ws://127.0.0.1:*`）
- URL query 携带：`?name=bosszp&kind={jobseeker|recruiter}&bid={browserID}&uid=...&token=...`
- 消息：`{ type, command_id, path, body, ... }`
- 网关调命令 → `ext_client.send_command_to(session_id, method, path, body)` 推 WS；扩展执行后回包 resolve `pending[request_id]` Future

**关键环境变量**（仅 key）：
```
BOSS_GATEWAY_PORT / GATEWAY_PUBLIC_URL / ADMIN_PASSWORD / EXT_TOKEN
DB_POSTGRES_URL / BOSS_GATEWAY_URL / PROXY_POOL / PROXY_ASSIGN_STRATEGY
API_LOG_DIR / API_LOG_LEVEL / AGENT_GATEWAY_URL
```

---

### 3.3 job-evolve-agent —— 代码自演进编排 (port 8770)

**职责**（README 摘要）：给一份原始抓包（HAR / rec / raw / ndjson）+ 一段意图，本服务把它喂给 Claude Code SDK，在 4 个仓库（`job-api-gateway` / `job-agent-gateway` / `job-seeker-ext` / `job-api-admin`）上做 **evolve（改代码 + 验证）** 或 **analyze（仅分析）**，通过 SSE 把进度/diff/verifier 输出推给 Vue3 SPA。

**核心三件套**：

| 文件 | 行数 | 角色 |
|---|---|---|
| `server.py` | 566 | Starlette HTTP（127.0.0.1:8770）；12+ 路由（tasks/analyses/captures）+ 前端 SPA；鉴权 + CSRF |
| `runner.py` | 758 | 任务编排：`submit_task` / `cancel_task` / `events_iter`；串 worktree → claude_runner → verifier；单 worker 后台队列（Phase 1） |
| `claude_runner.py` | 431 | Claude Code SDK 调用层；事件标准化；heartbeat 看门狗（360s）；wall 超时（20min）；cancel 处理 |

**LLM 调用方式（与 agent-gateway 显著不同）**：
- 引用 `claude-agent-sdk>=0.1.46,<0.2`（`claude_runner.py:31-42`）
- 调 SDK `query()` 异步迭代器，收 `AssistantMessage` / `ToolUseBlock` / `ResultMessage`
- 模型 `claude-opus-4-7`（`claude_runner.py:90` / `config.py:53`）
- `enable_thinking=True`（紫色 dot 渲染）
- SDK 内部启 CLI 子进程，工作目录是 `~/.evolve/worktrees/<task_id>/`

**其他模块**：

| 模块 | 行数 | 用途 |
|---|---|---|
| `auth.py` | 237 | 单 token + HMAC cookie + CSRF；session 7 天 |
| `config.py` | 95 | 4 仓库路径、模型、超时、`~/.evolve` 工作目录 |
| `db.py` | 420 | SQLite WAL `~/.evolve/evolve.db`；5 张表：`tasks` / `events` / `proc_registry` / `analyses` / `captures` |
| `capture/` | 586 总 | 抓包脱敏 pipeline：normalizer / redactor / summarizer / parsers；Cookie/token/phone/idcard 正则脱敏；50MB 上限 |
| `capture_storage.py` | 116 | multipart 接收，`raw.bin` 留 7 天，`redacted.json` 喂 Claude |
| `prompt.py` | 126 | evolve / analyze 两套 prompt 模板；抓包正文写 `.evolve/capture.json` 供 `Read` |
| `worktree.py` | 138 | 每任务独立 git worktree；task-scoped `settings.json` 权限 |
| `verifiers/` | 263 总 | 仓库专用脚本：api-gateway/pytest, agent-gateway/pytest, seeker-ext/pnpm build, api-admin/vue-tsc |
| `prompt_templates/` | 空 | 运行时 fs 读取，不打包 |
| `routes/` | 空 | 路由都直接写在 `server.py` |

**与其它子工程的关系**：
- evolve-agent 是 **外部编排者**（一次大任务 = 多次 Claude query + 验证重试）
- agent-gateway 的 `agent_loop` 是 **内部 Agent**（一次会话 = 多轮对话）
- evolve-agent 有独立的 `/api/captures` 上传端点，**不**对接 agent-gateway 的 `/capture/*`（README 第 169 行说扩展可改 push 到 `http://evolve-host:8770/api/captures`，但这只是一个旁路开关，主链路仍走 api-gateway）

**部署**：独立 systemd service；独立 DB；独立端口。

---

### 3.4 job-seeker-ext —— 浏览器扩展 (MV3, DingQ 助手 v2.1.4)

**职责**：在用户登录态下注入 BOSS直聘 / LinkedIn / Indeed 三个站点，监听用户操作 + 接收后端命令 + 注入 stealth 反爬 + 拦截网络请求；通过 WebSocket 把站内 API 能力暴露给后端。

**关键文件**：

| 文件 | 体积 | 职责 |
|---|---|---|
| `background.js` | 1202 行 | Service Worker：连 WSS、命令分发、`tokenStore`（多会话令牌链）、登录状态上报 |
| `sidepanel.html` / `sidepanel.js` | 114KB | MV3 sidePanel 主界面：职位/候选人列表、AI 投递/跟进 |
| `popup.html` / `popup.js` | 16KB / 577 行 | 弹窗：状态展示 + 快速操作 |
| `options.html` / `options.js` | 21KB / 880 行 | 设置：代理 / 账号 / 日志级别 |
| `build.js` | 150 行 | 自写构建：javascript-obfuscator 混淆 → `dist/`；安全文件关闭 `stringArray` 混淆 |
| `manifest.json` | — | MV3；host_permissions：`wss://control.dinq.me/*` + 三站 + `ws://127.0.0.1:*`（开发） |

**Content Script 注入**：
- `ext_shared/stealth.js`（MAIN, document_start）—— 反爬
- `ext_shared/content/interceptor_main.js`（MAIN）—— 流量拦截
- `ext_shared/content/interceptor.js`（ISOLATED, document_idle）—— 消息桥
- `ext_shared/content/zhipin_eval_inject.js` —— Boss 特定 eval 注入

**ext_shared/ 目录划分**：

| 子目录 | 用途 |
|---|---|
| `core/` | registry / token-chain / site-executor / request-builder / config-sync / form-filler / task-monitor / voice-bridge |
| `bosszp/` | tokens.js / executor.js / api.js |
| `linkedin/` | api.js / form-handler.js / recruiter-api.js / search-scraper.js |
| `indeed/` | api.js / form-handler.js / employer-api.js |
| `content/` | 网页注入脚本 + `dinq_probe.js`（探测本地代理） |
| `chains/` | builtin.js / linkedin.js / indeed.js / indeed_employer.js |
| `commands/` | WS 命令处理器 ~6400 行：session / jobs / candidates / linkedin / indeed / dom（18 条 DOM 操作 MCP 工具） |
| `offscreen/` | MV3 offscreen 文档（语音录音） |
| `sidepanel-shared/` | 侧边栏共享库 |

**后端 WS 协议**（`background.js:123-237`）：
- 默认 `wss://testapi.dinq.me/api/v1/job-api/ext/ws`（**反代到 job-api-gateway 的 `/ext/ws`**）
- 开发本地：`ws://${host}:${port}/api/v1/job-api/ext/ws`
- **同时**还有一个 agent-gw HTTP/SSE 入口 `https://job-agent.dinq.me/agent-gw`（→ job-agent-gateway）
- 安装包源 `https://control.dinq.me/boss-api-ext-latest.zip`（独立分发域名，背景注释说"是两个不同的服务"，硬编码）

**HTTP 补充能力**：
- 登录检测 `fetch(${api}/user/profile)`，从 `dinq.me` 域 cookies
- 动态命令：`requestBuilder` 模板（`{{tokens.jobs.detail}}` / `{{body.param}}`）

**docs/dom-primitives.md**：18 条 MCP DOM 工具（`get_clickables` / `click_by_idx` / `wait_for` …）；5s TTL snapshot 缓存；LRU + host 白名单。

---

### 3.5 job_common —— 共享纯逻辑包

**定位**：仅放与 IO 无关的"业务逻辑常量与判定函数"；通过 `sys.path.insert(0, repo_root)` 注入，**不**走 pip install。

**两个模块**：

| 文件 | 大小 | 内容 |
|---|---|---|
| `risk_signals.py` | 9KB | 14 个风控信号枚举 × 4 种处置：`auto_retry` / `user_action` / `skip_item` / `abort` |
| `risk_detector.py` | 6KB | 从工具响应识别风控信号：解析 `structured_data` 强信号、关键词匹配、按平台（Boss/LinkedIn/Indeed）派发 |

**使用方**：
- agent-gateway：`tasks/engine.py` + `mcp_client.py` 导入 `RiskControlSignal` / `detect_risk_signal`
- api-gateway：`commands.py` 导入 `RiskControlSignal`

**历史**：原住在 job-api-gateway，被 agent-gateway 通过 `sys.path.insert(0, '../job-api-gateway')` 反向导入——紧耦合。抽出来后 deploy 时 rsync 到 `/opt/job_common`。

---

### 3.6 job-api-admin —— 管理后台（本仓库未拉取）

虽然不在本工作区，但 evolve-agent 的 `tests/test_audit_regressions.py`、`config.py:23` 和 `README.md:5` 都把它列为四个 scope 之一；agent-gateway 的 `db.py:1571`、`server.py:656`、`capture_router.py:79` 也以注释形式标注 "供 job-api-admin"。

**已知信息**：
- 技术栈 Vue3 + Vite（verifier 跑 `vue-tsc`）
- 用 admin token 调 agent-gateway 的 `/admin/*` 和 api-gateway 的 `/admin/*`
- 后续若进入此项目需要单独 clone

---

## 4. 跨工程协作

### 4.1 关键数据流

**用户对话流（求职者）**：
```
浏览器扩展 sidepanel.js
   ├─ HTTP/SSE → job-agent-gateway:8769 /agent/sse
   │      └─ agent_loop.py 调 Claude / OpenRouter
   │             └─ MCP 客户端 → job-api-gateway:8767 /mcp
   │                    └─ commands.py → ext_client.send_command_to()
   │                           └─ WSS → 扩展 background.js /ext/ws
   │                                  └─ ext_shared/{platform}/api.js
   │                                         └─ 真实站点 API
   └─ WSS → job-api-gateway:8767 /ext/ws  （执行通道，同上）
```

**后台自动化任务流**：
```
agent-gateway tasks_router.py
   └─ tasks/engine.py TaskRunner
          ├─ MCP call → job-api-gateway
          ├─ 检测响应 → job_common.risk_detector
          └─ 信号 → auto_retry / user_action / skip / abort
```

**抓包改代码流（evolve）**：
```
开发者 / 扩展 → job-evolve-agent:8770 /api/captures (POST multipart)
   └─ capture/redactor → ~/.evolve/captures/<id>/redacted.json
   └─ runner.py submit_task
          └─ worktree.py create git worktree on 4 repos
          └─ claude_runner.py SDK query (claude-opus-4-7)
          └─ verifiers/{repo}.py
          └─ events → SSE → Vue SPA
```

### 4.2 三种跨服务协议

| 协议 | 链路 | 协商方式 |
|---|---|---|
| **HTTP/REST** | agent-gw → api-gw `/admin/*`、agent-gw → dinq-server、admin-portal → 两网关 | 普通 httpx |
| **SSE** | 扩展/前端 → agent-gw `/agent/sse`、Vue SPA → evolve `/events` | Starlette streaming |
| **WebSocket** | 扩展 ↔ api-gw `/ext/ws`、admin ↔ api-gw `/admin/ws` | 自定义命令包 `{type, command_id, path, body}` |
| **MCP** | agent-gw → api-gw `/mcp` | mcp >= 1.0 ClientSession |
| **stdin/SDK 子进程** | evolve-agent → claude code CLI | claude-agent-sdk |

### 4.3 共享代码与依赖

- `job_common` 通过 `sys.path` 注入（agent-gw + api-gw 双依赖）
- 部署脚本两个 gw 都会 rsync `/opt/job_common`
- **无**统一 monorepo 工具（poetry workspace / pnpm workspace / nx 等），各 repo 独立 venv / npm

---

## 5. 部署拓扑

| 服务 | 主机 / 端口 | 进程管理 | 同步方式 |
|---|---|---|---|
| job-agent-gateway | `43.106.141.228:8769` | systemd + uvicorn (worker N) | rsync + ssh |
| job-api-gateway | `43.106.141.228:8767` | systemd | rsync + ssh，同步 `job_common` |
| job-evolve-agent | `127.0.0.1:8770`（只本机） | systemd | rsync + npm build + pip install |
| Redis | `127.0.0.1:6380` | — | — |
| PostgreSQL | 默认 `5432` | — | — |
| 扩展分发 | `https://control.dinq.me/boss-api-ext-latest.zip` | — | `deploy.sh` |
| 反代域名 | `testapi.dinq.me`（API + ext WS）、`job-agent.dinq.me`（SSE） | — | Nginx（推测） |

> 反代细节本工作区无 nginx 配置文件，需在服务器侧查看。

---

## 6. 重构观察 / 技术债清单

> 下列条目按"重构启动前最值得先理顺的"优先级编排。

### 6.1 God Files（拆分阻力最大的文件）

| 文件 | 行数 | 痛点 | 拆分建议方向 |
|---|---|---|---|
| `job-api-gateway/http_routes.py` | **2689** | 70+ 路由汇聚一处；OAuth、admin、三平台代理混在一起 | 按 domain 拆 `routes/admin.py` / `routes/oauth.py` / `routes/{site}.py` |
| `job-api-gateway/db.py` | **2678** | 18+ 表 CRUD + 业务聚合 + admin 统计 | 按表族拆 `db/sessions.py` / `db/cookies.py` / `db/jobs.py` …，或上 SQLAlchemy 但代价大 |
| `job-api-gateway/commands.py` | **2275** | 60+ 命令、三站点未分目录 | 按站点拆 `commands/boss/` `commands/linkedin/` `commands/indeed/` |
| `job-api-gateway/mcp_tools_boss.py` | 2066 | 57 个 tool 全塞一个文件 | 同上，按 tool group 拆 |
| `job-agent-gateway/agent_loop.py` | **2107** | 主推理 + fallback + MCP 编排 + mode 检测 + 流处理 | 抽 `llm_provider.py` / `tool_orchestrator.py` / `mode_resolver.py` |
| `job-agent-gateway/db.py` | **1874** | asyncpg + 多业务表 CRUD + admin 聚合 | 拆 `db/conv.py` / `db/tasks.py` / `db/metrics.py` |
| `job-agent-gateway/modes/base.py` | 1098 | 多语言 prompt + 平台规则 + 行为规则混杂 | 拆 `prompts/identity.py` / `prompts/platform_rules.py` / `prompts/behavior.py` |
| `job-seeker-ext/sidepanel.js` | 114KB | 主 UI 单文件 | 按 panel 拆模块；与 build.js 联动 |

合计 `http_routes.py + db.py + commands.py` 占 api-gateway 总行数的 **41%**——拆这三个就是头号杠杆。

### 6.2 耦合点（重构需要先解耦）

1. **agent-gw 硬编码外部地址** — `internal_resume_client.py` 写死 `47.84.195.154:8082`。
2. **api-gw 命令执行三态混杂** — 同一份 `commands.py` 既走"扩展执行"又走"`boss_cli_server` 直连"；分支控制散在文件各处。
3. **dynamic-commands / mcp_tools_* 双轨注册** — 一边静态 `register(mcp)`，一边 `dynamic_mcp_registry.py` 运行时注册。新增命令路径不唯一。
4. **risk 逻辑跨进程复用** — `job_common` 走 `sys.path` 注入而非依赖声明；任何 monorepo 重组都要先解 import 路径。
5. **扩展三站点高度同构但未抽象** — `bosszp/api.js` / `linkedin/api.js` / `indeed/api.js` 形状相似但各写各的。
6. **session_id 含义至少 3 套** — api-gw 的 `SessionEntry`、扩展端 `bid`、agent-gw 的 SSE session，互相映射靠注释。

### 6.3 配置与版本陷阱

- ⚠️ **`job-agent-gateway/config.py:MODEL = "claude-opus-4-6"`** ——这是 OpenRouter 上的字符串，但 evolve-agent 用的是 `claude-opus-4-7`。最新 Anthropic 主力模型已经是 4.7（2026-05 时点），两边模型版本和 prompt 调优策略可能不一致。重构时建议把模型 ID 统一放到 `job_common`，或抽出 `model_registry.py`。
- `ANTHROPIC_BASE_URL` 默认空字符串而不是 SDK 默认值——靠 OpenRouter 兜底；如果配错可能直连官方端点。
- 端口 6380 不是 Redis 默认 6379——本地起开发环境时容易踩。
- PostgreSQL 两套 schema（`boss_gateway` for api-gw，agent-gw 未写默认 DB 名），实际共用一个实例？需在 server 侧确认。

### 6.4 测试与可观测

- 三个 Python 工程均**未发现**完整的 pytest 测试树（evolve-agent 有 `tests/test_*` 几个，主要是 audit 类）。重构前缺安全网。
- 日志走 `API_LOG_DIR`、`api-gw` 有 admin error log 表，但无统一 trace_id 贯穿"扩展 ↔ api-gw ↔ agent-gw ↔ LLM"——排查长链路问题成本高。
- 没看到 metrics（Prometheus）或 OpenTelemetry。

### 6.5 文档稀薄

- 工作区**根目录无 README / ARCHITECTURE / CONTRIBUTING**（本文档是首份）。
- agent-gateway 仅 1 篇 `docs/extended-thinking.md`。
- api-gateway 无 docs/。
- 扩展只有 1 篇 `docs/dom-primitives.md`。
- evolve-agent 有相对最完整的 README（6KB）。

---

## 7. 缺失环节 & 待验证

新人进入项目时建议先核实以下事项（本文档基于静态阅读，可能与运行时不一致）：

1. `job-api-admin` repo 的实际位置和 clone 命令——本工作区没拉。
2. 服务器侧 nginx / 网关反代配置：`testapi.dinq.me` / `job-agent.dinq.me` / `control.dinq.me` 三个域名各自指向何处。
3. PostgreSQL 是否单实例多 schema；agent-gw 的实际 `DATABASE_URL` 在 `.env` 而不在 `.env.example`。
4. `boss_cli_server.py`（伪装扩展的进程）当前是否仍在生产运行；它和真实扩展是 OR 关系还是 AND 关系。
5. `dynamic-commands/*.yaml` 的"云端下发"配置源在哪里（推测在 admin 后台或外部 CMS）。
6. 扩展混淆构建产物 `dist/` 与源代码的对应关系——线上跑 `dist/`，调试要回源码。
7. evolve-agent 改代码后是**直接 commit 到 worktree**还是只 patch；流程中是否有人工 review gate。

---

## 8. Onboarding 速查

**想看用户聊天怎么走的** → `job-agent-gateway/sse_router.py` → `agent_loop.py:run_once` → `modes/{mode}.py` 拼 prompt。

**想看一条求职命令落到平台的全流程** → `agent_loop.py` MCP 调用 → `job-api-gateway/mcp_tools_boss.py` → `commands.py:某命令` → `ext_client.send_command_to` → 扩展 `ext_shared/commands/jobs.js` → `ext_shared/bosszp/api.js`。

**想加一个新平台命令** →
- 旧方式：`mcp_tools_*.py` + `*_commands.py` + 扩展 `ext_shared/{site}/api.js`
- 新方式：`dynamic-commands/{site}/xxx.yaml` + `dynamic_mcp_registry.apply_config()`（不需重启）

**想加一个新风控信号** → `job_common/risk_signals.py` 加枚举 + `risk_detector.py` 加规则；两个网关自动生效。

**想跑一个代码改造任务** → `POST :8770/api/captures` 上传抓包 → `POST :8770/api/tasks` 提交 `{repo, intent}` → SSE `/api/events/{task_id}` 看进度。

**部署任一服务** → 各 repo 根目录 `./deploy.sh`，会 rsync + systemctl restart。

---

*文档版本：v1 / 2026-05-14 / 基于静态代码扫描*
