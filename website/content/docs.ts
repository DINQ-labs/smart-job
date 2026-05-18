import type { Locale } from "@/lib/i18n";

export type DocSlug = "architecture" | "backend" | "extension" | "setup" | "admin";
export const docSlugs: DocSlug[] = ["architecture", "backend", "extension", "setup", "admin"];

export type Block =
  | { t: "h2"; text: string }
  | { t: "p"; text: string }
  | { t: "ul"; items: string[] }
  | { t: "pre"; text: string }
  | { t: "table"; head: string[]; rows: string[][] };

export interface DocContent {
  title: string;
  intro: string;
  blocks: Block[];
}

const FLOW = `用户  →  扩展侧边栏
        │  HTTP / SSE
        ▼
   agent-gateway ──── MCP ────►  api-gateway
        │                            │  WebSocket /ext/ws
        │ JWT 验签                    ▼
        ▼                         扩展 background
   portal-api                        │
                                     ▼
                              隐藏 Worker Tab
                                     │  真实 fetch
                                     ▼
                          求职平台 API（用户登录态）`;

const FLOW_EN = `User  →  Extension side panel
        │  HTTP / SSE
        ▼
   agent-gateway ──── MCP ────►  api-gateway
        │                            │  WebSocket /ext/ws
        │ JWT verify                  ▼
        ▼                         Extension background
   portal-api                        │
                                     ▼
                              Hidden Worker Tab
                                     │  real fetch
                                     ▼
                       Job platform API (user session)`;

const zh: Record<DocSlug, DocContent> = {
  architecture: {
    title: "架构总览",
    intro:
      "smart-job 是一套多平台求职与招聘自动化系统，由一个浏览器扩展、三个后端服务和一个管理后台组成。本文给出整体架构、组件职责与端到端数据流。",
    blocks: [
      { t: "h2", text: "设计目标" },
      {
        t: "ul",
        items: [
          "对话式自动化：用户用自然语言表达意图，Agent 调用工具完成搜索、评估、沟通、投递。",
          "真实浏览器执行：所有平台请求都在用户自己的浏览器、自己的登录态下发出，不在服务端伪造。",
          "求职 / 招聘双模：同一套系统同时服务找工作的求职者和找人的招聘者。",
          "云端可扩展：平台 API 变化时，新命令以配置形式下发到扩展，无需重新发布扩展。",
        ],
      },
      { t: "h2", text: "组件构成" },
      {
        t: "p",
        text: "系统由五个可独立部署的组件组成，外加一个跨服务共享的纯逻辑包 job_common（承载风控信号定义）。",
      },
      {
        t: "table",
        head: ["组件", "端口", "技术栈", "职责"],
        rows: [
          ["job-seeker 扩展", "—", "Chrome MV3 / 原生 JS", "在用户浏览器内执行抓包、自动填表、平台 API 调用"],
          ["api-gateway", "8767", "Python / FastMCP / Starlette", "命令网关、MCP 工具服务、扩展 WebSocket 隧道"],
          ["agent-gateway", "8769", "Python / Starlette", "Agent 对话核心、模式系统、任务编排、SSE"],
          ["portal-api", "8771", "Python / FastAPI", "账号注册登录、多种鉴权、JWT 签发与 JWKS"],
          ["admin 后台", "8080", "Vue 3 / Vite / TS", "运维监控、命令下发、用户与任务管理"],
        ],
      },
      { t: "h2", text: "端到端数据流" },
      { t: "pre", text: FLOW },
      {
        t: "p",
        text: "整个过程中，平台请求始终在用户浏览器内发出；api-gateway 与 agent-gateway 都不直接访问求职平台 —— 前者只是命令隧道，后者只通过 MCP 调用工具。",
      },
      { t: "h2", text: "关键机制" },
      {
        t: "ul",
        items: [
          "MCP 工具层：api-gateway 用 FastMCP 把平台动作暴露成标准 MCP 工具，分静态工具与运行时注册的动态命令。",
          "令牌链：扩展用有向依赖图管理平台 API 所需的一串有时效安全令牌，缺失自动回补。",
          "模式系统：agent-gateway 按用户意图在搜索 / 评估 / 投递 / 面试 / 对比 / 招聘等模式间切换。",
          "任务编排：长任务由模板定义、后台执行，进度与风控暂停通过 SSE / 数据库回报。",
          "去中心化鉴权：portal-api 用 RSA 签发 JWT 并暴露公钥，其它服务本地验签。",
        ],
      },
      { t: "h2", text: "数据存储与部署" },
      {
        t: "p",
        text: "PostgreSQL 分 boss_gateway 与 smart_job 两个库；Redis 用于 agent-gateway 跨 worker 共享会话历史，不可用时优雅降级。推荐用 Docker Compose 一键部署，启动顺序为 PostgreSQL/Redis → portal-api → api-gateway → agent-gateway → admin。",
      },
    ],
  },
  backend: {
    title: "后端设计",
    intro:
      "后端由三个 Python 服务和一个共享纯逻辑包组成。三个服务均为异步、启动时自建表结构、通过 .env 配置。",
    blocks: [
      { t: "h2", text: "api-gateway（:8767）" },
      {
        t: "p",
        text: "命令网关，连接 Agent 与浏览器扩展。技术栈为 FastMCP + Starlette。它把平台动作暴露成 MCP 工具，通过 WebSocket /ext/ws 把命令下发到扩展执行，并管理会话、动态命令、浏览器代理。",
      },
      {
        t: "ul",
        items: [
          "POST /mcp —— FastMCP JSON-RPC 端点，供 agent-gateway 调用。",
          "WS /ext/ws —— 扩展双向命令隧道，按 (用户, 角色) 维度管理会话。",
          "WS /admin/ws —— 管理后台实时事件广播。",
          "动态命令：命令以配置存入数据库，运行时注册成 MCP 工具并下发给在线扩展。",
        ],
      },
      { t: "h2", text: "agent-gateway（:8769）" },
      {
        t: "p",
        text: "Agent 对话的核心。它运行 Agent 推理循环，按用户意图切换模式，调用 MCP 工具，通过 SSE 把过程流式推回前端；同时承载长任务编排、简历管理与个性化。LLM 走 OpenRouter（优先）或 Anthropic。",
      },
      {
        t: "p",
        text: "Agent 推理主循环是一个异步生成器：接收消息 → 组装系统提示 → 多轮“推理-调工具-取结果”循环 → 产出协议事件（文本增量、工具调用、卡片、操作按钮等），经 SSE 实时推给前端。",
      },
      {
        t: "p",
        text: "模式系统按场景切换：search（搜索）、evaluate（评估）、apply（投递）、interview（面试准备）、compare（对比）、recruiter（招聘）。意图检测综合关键词打分与工具使用倾向，模式可在一轮对话中途升级。",
      },
      {
        t: "p",
        text: "任务编排支持批量打招呼、批量简历筛选等长任务；工具抛出风控信号时按策略处理：自动重试、暂停等待用户、跳过当前条目或终止任务。简历管理完全本地，不调用外部服务。",
      },
      { t: "h2", text: "portal-api（:8771）" },
      {
        t: "p",
        text: "用户身份与鉴权中心，技术栈为 FastAPI。支持邮箱密码、第三方 OAuth、一次性验证码、Passkey 等多种鉴权方式，统一抽象为挂在同一账号下的“身份”。它用 RSA 密钥对签发访问令牌与刷新令牌，并在 /.well-known/jwks.json 暴露公钥 —— api-gateway 与 agent-gateway 拿公钥本地验签，无需每次回查。",
      },
      { t: "h2", text: "job_common（共享包）" },
      {
        t: "p",
        text: "跨服务共享的纯逻辑包，承载风控信号注册表：把平台摩擦（验证码、限流、登录失效、配额耗尽）映射到处理策略，并定义 RiskControlSignal 异常。把信号定义独立成包，让它成为各服务的单一事实来源。",
      },
    ],
  },
  extension: {
    title: "扩展设计",
    intro:
      "job-seeker 是一个 Chrome Manifest V3 扩展，是整个系统里唯一直接接触求职平台的组件 —— 所有平台请求都在用户浏览器内、用户登录态下发出。它提供抓包、自动填表、工作自动化三大能力。",
    blocks: [
      { t: "h2", text: "整体结构" },
      {
        t: "ul",
        items: [
          "background —— Service Worker，控制面，负责 WebSocket 连接与命令分发。",
          "content —— 内容脚本，注入平台页面，分 MAIN / ISOLATED 两个世界。",
          "lib/ext-core —— 核心自动化引擎：命令、令牌链、动态命令、站点执行器。",
          "lib/recorder —— 抓包子系统；lib/autofill —— 自动填表子系统。",
          "sidepanel —— 侧边栏主界面；options —— 设置页（后端地址等）。",
        ],
      },
      { t: "h2", text: "抓包（API 录制）" },
      {
        t: "p",
        text: "抓包用于逆向平台接口。它基于 Chrome DevTools Protocol 的 Network 域，把 chrome.debugger 附加到标签页，监听 requestWillBeSent / responseReceived / loadingFinished 事件录制请求与响应。因为走 CDP 而非注入 JS，对页面完全透明。",
      },
      {
        t: "p",
        text: "录制数据可上报到 agent-gateway 做接口分析，分析结果在管理后台查看，并最终转化为动态命令配置下发。响应体超限会截断，未完成的请求有 TTL 清理避免内存泄漏。",
      },
      { t: "h2", text: "自动填表" },
      {
        t: "p",
        text: "自动填表把用户的简历信息智能填进任意招聘网站的申请表单，覆盖 Workday、Greenhouse、Lever 等主流 ATS。流程为：探测页面（含所有 iframe）的表单字段 → 把字段清单与资料快照发给后端匹配（后端不可用时回退本地关键词匹配）→ 在 MAIN world 注入填充原语逐字段填入 → 沉淀站点模板供复用。",
      },
      {
        t: "p",
        text: "填充过程中可捕获表单提交请求；上报前会把请求与响应里出现的用户隐私值替换掉（脱敏），避免泄露 PII。",
      },
      { t: "h2", text: "工作自动化" },
      {
        t: "p",
        text: "工作自动化把平台动作组织成可声明、可组合、可云端扩展的命令体系。命令注册表里每条命令声明 requires（依赖令牌）与 produces（产出令牌），分静态命令（编译进扩展）与动态命令（后端下发）。",
      },
      {
        t: "p",
        text: "平台 API 通常需要一串有时效的安全令牌（列表 → 详情 → 会话 → 发消息），令牌链用有向依赖图建模这种关系：命令分发前校验所需令牌是否存在且未过期，缺失则提示先运行哪条命令补令牌，部分 handler 会自动回补。",
      },
      {
        t: "p",
        text: "站点执行器为每个平台维护一个隐藏的后台 Worker Tab，平台 API 请求在其中用真实 fetch 发出，复用用户登录 Cookie 与真实指纹，并带随机抖动限速。平台接口变化时，后端经 WebSocket 推送动态命令配置，扩展用安全的 JSON 模板渲染成可执行处理器，无需重发扩展。",
      },
      { t: "h2", text: "侧边栏与双模" },
      {
        t: "p",
        text: "侧边栏是用户主界面，按模块组织：对话、任务、抓包、自动填表、个人资料、收藏、快捷指令等。扩展用同一套代码服务求职者与招聘者两种角色，用户在引导页选择身份后写入存储，WebSocket 握手时带上角色，后端据此路由。",
      },
    ],
  },
  setup: {
    title: "扩展加载与首次使用",
    intro:
      "本页一步步引导你把 SmartJob 浏览器扩展加载进 Chrome、连上后端、完成登录与首次引导。",
    blocks: [
      { t: "h2", text: "前置条件" },
      {
        t: "ul",
        items: [
          "一个支持 Manifest V3 与侧边栏的浏览器:较新版本的 Chrome / Chromium / Edge。",
          "后端已启动(推荐用 Docker 一键拉起)。启动后 portal-api、api-gateway、agent-gateway 分别监听 8771 / 8767 / 8769。",
        ],
      },
      { t: "h2", text: "加载扩展到浏览器" },
      {
        t: "ul",
        items: [
          "地址栏打开 chrome://extensions,打开右上角的「开发者模式」。",
          "点「加载已解压的扩展程序」,选择仓库里的 extensions/job-seeker/ 目录。",
          "扩展出现在列表中(名称 SmartJob);建议把它固定到工具栏,方便随时打开。",
          "改动扩展代码后,回 chrome://extensions 点该扩展的「刷新」按钮重新加载。",
        ],
      },
      { t: "h2", text: "配置后端网关地址" },
      {
        t: "p",
        text: "点工具栏的 SmartJob 图标打开侧边栏,首屏即「登录 / 注册」表单。登录前必须先把扩展指向你的后端 —— 网关地址配错会直接导致登录失败。点侧边栏顶部的「⚙️ 设置」(未登录时在登录向导左上角,登录后在主顶栏),在设置页填三个后端地址;本地 Docker 环境点「开发环境」预设可一键填入。",
      },
      {
        t: "table",
        head: ["服务", "本地 Docker 地址"],
        rows: [
          ["portal-api(账号 / 鉴权)", "http://127.0.0.1:8771"],
          ["api-gateway(命令网关)", "http://127.0.0.1:8767"],
          ["agent-gateway(Agent 对话)", "http://127.0.0.1:8769"],
        ],
      },
      { t: "h2", text: "登录与首次引导" },
      {
        t: "p",
        text: "回到侧边栏首屏的登录 / 注册表单:已有账号直接登录;新账号切到「注册」,填邮箱 + 密码后收邮箱验证码完成注册。本地 Docker 在开发模式下会自动播种一个测试账号(smartjob@joyhouselabs.com / 123456),可直接登录 —— 这是弱口令测试账号,生产环境务必关闭播种。",
      },
      {
        t: "p",
        text: "登录成功后,扩展依次引导:选择身份(求职者 / 招聘者)→ 选择平台(BOSS直聘 / LinkedIn / Indeed)→ 登录该平台 →(求职者)上传简历并确认求职偏好。引导走完即进入 AI 助手对话界面。",
      },
      { t: "h2", text: "验证与排查" },
      {
        t: "p",
        text: "扩展连上 api-gateway 后会建立一个 WebSocket 会话,可在管理后台的仪表盘 / 浏览器池看到。常见问题:",
      },
      {
        t: "table",
        head: ["现象", "排查"],
        rows: [
          ["登录一直转圈 / 失败", "多半是网关地址不对 —— 顶部 ⚙️ 设置重新配置并做连通测试"],
          ["侧边栏打不开", "确认浏览器版本支持 sidePanel;点工具栏的扩展图标触发"],
          ["改了扩展代码不生效", "chrome://extensions 里点该扩展的「刷新」按钮"],
          ["平台操作被风控", "扩展与 Agent 会给出提示,按指引到平台站点处理后继续"],
        ],
      },
    ],
  },
  admin: {
    title: "管理后台设计",
    intro:
      "admin 是一个 Vue 3 单页应用，面向运维人员，用于监控系统运行、下发命令配置、管理用户与任务。它不面向终端求职 / 招聘用户。",
    blocks: [
      { t: "h2", text: "技术栈与结构" },
      {
        t: "p",
        text: "技术栈为 Vue 3（Composition API）+ Vite + TypeScript + Pinia + vue-router + vue-i18n + ECharts。源码按 pages（页面）、components（可复用组件）、stores（Pinia 状态）、services/api.ts（统一 API 层）、i18n（国际化）组织。",
      },
      { t: "h2", text: "功能页面" },
      {
        t: "ul",
        items: [
          "监控与诊断：仪表盘、命令日志、错误日志、系统监控、MCP 指标、前端错误。",
          "会话与资源：浏览器池、代理池、岗位缓存。",
          "Agent 与任务：Agent 会话、会话详情、Agent 任务、任务模板、历史、用户。",
          "配置下发：命令注册表、动态命令（核心：JSON 编辑、校验、一键下发、版本回滚）、快捷指令配置。",
          "用户与数据：门户用户、API 录制、自动填表模板与捕获、候选人简历、增长指标。",
        ],
      },
      { t: "h2", text: "与后端的连接" },
      {
        t: "p",
        text: "管理后台同时对接三个后端服务。开发环境用 Vite 代理、生产环境用 Nginx 反代：/admin、/status 转发到 api-gateway，/agent-gw/ 转发到 agent-gateway，/portal/ 转发到 portal-api。仪表盘通过 WebSocket /admin/ws 接收实时事件，组合式函数负责连接管理与指数退避重连。",
      },
      { t: "h2", text: "国际化与构建" },
      {
        t: "p",
        text: "用 vue-i18n 实现中英双语，locales/zh 与 locales/en 下每个文件对应一个命名空间，启动时用 import.meta.glob 自动合并。构建命令 pnpm build 先做 vue-tsc 类型检查再 vite build；Dockerfile 为两段构建，Node 阶段编译产物、Nginx 阶段托管并反代后端，对外端口 8080。",
      },
    ],
  },
};

const en: Record<DocSlug, DocContent> = {
  architecture: {
    title: "Architecture overview",
    intro:
      "smart-job is a multi-platform job-seeking and recruiting automation system, made of one browser extension, three backend services and an admin console. This page covers the overall architecture, component responsibilities and end-to-end data flow.",
    blocks: [
      { t: "h2", text: "Design goals" },
      {
        t: "ul",
        items: [
          "Conversational automation: users express intent in natural language; the agent calls tools to search, evaluate, message and apply.",
          "Real-browser execution: every platform request is made from the user's own browser and login session — never forged server-side.",
          "Dual seeker / recruiter modes: one system serves both job seekers and recruiters.",
          "Cloud-extensible: when platform APIs change, new commands are pushed as config — no extension re-release needed.",
        ],
      },
      { t: "h2", text: "Components" },
      {
        t: "p",
        text: "The system consists of five independently deployable components, plus a shared pure-logic package, job_common, holding risk-signal definitions.",
      },
      {
        t: "table",
        head: ["Component", "Port", "Stack", "Responsibility"],
        rows: [
          ["job-seeker extension", "—", "Chrome MV3 / vanilla JS", "Runs capture, auto-fill and platform API calls in the user's browser"],
          ["api-gateway", "8767", "Python / FastMCP / Starlette", "Command gateway, MCP tool server, extension WebSocket tunnel"],
          ["agent-gateway", "8769", "Python / Starlette", "Agent conversation core, mode system, task orchestration, SSE"],
          ["portal-api", "8771", "Python / FastAPI", "Account registration/login, multi-method auth, JWT and JWKS"],
          ["admin console", "8080", "Vue 3 / Vite / TS", "Operations monitoring, config push, user and task management"],
        ],
      },
      { t: "h2", text: "End-to-end data flow" },
      { t: "pre", text: FLOW_EN },
      {
        t: "p",
        text: "Throughout, platform requests are always made inside the user's browser. Neither api-gateway nor agent-gateway touches the platforms directly — the former is a command tunnel, the latter only calls tools over MCP.",
      },
      { t: "h2", text: "Key mechanisms" },
      {
        t: "ul",
        items: [
          "MCP tool layer: api-gateway uses FastMCP to expose platform actions as standard MCP tools — static tools plus runtime-registered dynamic commands.",
          "Token chain: the extension uses a directed dependency graph to manage the chain of time-limited security tokens platform APIs require, auto-backfilling missing ones.",
          "Mode system: agent-gateway switches among search / evaluate / apply / interview / compare / recruiter modes based on user intent.",
          "Task orchestration: long-running tasks are template-defined and run in the background, reporting progress and risk pauses via SSE / the database.",
          "Decentralized auth: portal-api signs JWTs with RSA and exposes the public key; other services verify locally.",
        ],
      },
      { t: "h2", text: "Storage and deployment" },
      {
        t: "p",
        text: "PostgreSQL is split into two databases, boss_gateway and smart_job; Redis lets agent-gateway share session history across workers and degrades gracefully when unavailable. Docker Compose deploys everything in one command, with startup order PostgreSQL/Redis → portal-api → api-gateway → agent-gateway → admin.",
      },
    ],
  },
  backend: {
    title: "Backend design",
    intro:
      "The backend is three Python services plus a shared pure-logic package. All three services are async, create their own schema on startup, and are configured via .env.",
    blocks: [
      { t: "h2", text: "api-gateway (:8767)" },
      {
        t: "p",
        text: "The command gateway connecting the agent and the browser extension. Built on FastMCP + Starlette, it exposes platform actions as MCP tools, dispatches commands to the extension over WebSocket /ext/ws, and manages sessions, dynamic commands and browser proxies.",
      },
      {
        t: "ul",
        items: [
          "POST /mcp — FastMCP JSON-RPC endpoint, called by agent-gateway.",
          "WS /ext/ws — bidirectional extension command tunnel, sessions keyed by (user, role).",
          "WS /admin/ws — real-time event broadcast for the admin console.",
          "Dynamic commands: commands stored as config in the database, registered as MCP tools at runtime and pushed to online extensions.",
        ],
      },
      { t: "h2", text: "agent-gateway (:8769)" },
      {
        t: "p",
        text: "The core of the agent conversation. It runs the agent reasoning loop, switches modes by user intent, calls MCP tools, and streams the process back over SSE; it also hosts task orchestration, resume management and personalization. The LLM runs via OpenRouter (preferred) or Anthropic.",
      },
      {
        t: "p",
        text: "The reasoning loop is an async generator: receive a message → compose the system prompt → run a multi-turn reason-call-tool-result loop → emit protocol events (text deltas, tool calls, cards, action buttons) streamed to the frontend over SSE.",
      },
      {
        t: "p",
        text: "The mode system switches by scenario: search, evaluate, apply, interview, compare and recruiter. Intent detection combines keyword scoring with tool-usage affinity, and the mode can upgrade mid-turn.",
      },
      {
        t: "p",
        text: "Task orchestration supports long-running jobs like bulk outreach and bulk resume screening; when a tool raises a risk signal it is handled by strategy: auto-retry, pause for the user, skip the current item, or abort. Resume management is fully local and calls no external service.",
      },
      { t: "h2", text: "portal-api (:8771)" },
      {
        t: "p",
        text: "The identity and auth hub, built on FastAPI. It supports multiple auth methods — email/password, third-party OAuth, one-time codes, Passkey — unified as 'identities' attached to one account. It signs access and refresh tokens with an RSA key pair and exposes the public key at /.well-known/jwks.json, so api-gateway and agent-gateway verify tokens locally without a round trip.",
      },
      { t: "h2", text: "job_common (shared package)" },
      {
        t: "p",
        text: "A cross-service pure-logic package holding the risk-signal registry: it maps platform friction (captcha, rate limiting, logout, quota exhaustion) to handling strategies and defines the RiskControlSignal exception, making signal definitions a single source of truth.",
      },
    ],
  },
  extension: {
    title: "Extension design",
    intro:
      "job-seeker is a Chrome Manifest V3 extension — the only component that touches job platforms directly. Every platform request is made inside the user's browser and login session. It provides three capabilities: packet capture, form auto-fill and job automation.",
    blocks: [
      { t: "h2", text: "Overall structure" },
      {
        t: "ul",
        items: [
          "background — the Service Worker control plane, handling the WebSocket connection and command dispatch.",
          "content — content scripts injected into platform pages, split into MAIN / ISOLATED worlds.",
          "lib/ext-core — the core automation engine: commands, token chains, dynamic commands, site executor.",
          "lib/recorder — the packet-capture subsystem; lib/autofill — the auto-fill subsystem.",
          "sidepanel — the side-panel main UI; options — the settings page (backend addresses, etc.).",
        ],
      },
      { t: "h2", text: "Packet capture (API recording)" },
      {
        t: "p",
        text: "Capture is used to reverse-engineer platform APIs. It is based on the Chrome DevTools Protocol Network domain: it attaches chrome.debugger to a tab and listens to requestWillBeSent / responseReceived / loadingFinished events to record requests and responses. Because it uses CDP rather than injected JS, it is fully transparent to the page.",
      },
      {
        t: "p",
        text: "Captured data can be uploaded to agent-gateway for API analysis; results are viewable in the admin console and eventually turned into dynamic-command config. Oversized response bodies are truncated, and incomplete requests have a TTL cleanup to avoid memory leaks.",
      },
      { t: "h2", text: "Form auto-fill" },
      {
        t: "p",
        text: "Auto-fill smartly fills the user's resume data into any job application form, covering Workday, Greenhouse, Lever and other ATS platforms. The flow: detect form fields across the page (including all iframes) → send the field list and a profile snapshot to the backend for matching (falling back to local keyword matching if the backend is unavailable) → inject fill primitives into the MAIN world to fill each field → save a per-site template for reuse.",
      },
      {
        t: "p",
        text: "Form submission requests can be captured during the fill; before upload, any user PII appearing in requests and responses is scrubbed.",
      },
      { t: "h2", text: "Job automation" },
      {
        t: "p",
        text: "Job automation organizes platform actions into a declarative, composable, cloud-extensible command system. Each command in the registry declares requires (dependent tokens) and produces (output tokens), split into static commands (compiled into the extension) and dynamic commands (pushed by the backend).",
      },
      {
        t: "p",
        text: "Platform APIs typically need a chain of time-limited security tokens (list → detail → session → send message). The token chain models this as a directed dependency graph: before dispatch, required tokens are checked for existence and freshness; if missing, the command indicates which command to run first, and some handlers backfill automatically.",
      },
      {
        t: "p",
        text: "The site executor keeps a hidden background Worker Tab per platform; platform API requests run there via real fetch, reusing the user's login cookies and real fingerprint with randomized-jitter rate limiting. When platform APIs change, the backend pushes dynamic-command config over WebSocket, and the extension renders it into executable handlers from safe JSON templates — no re-release needed.",
      },
      { t: "h2", text: "Side panel and dual mode" },
      {
        t: "p",
        text: "The side panel is the user's main UI, organized into modules: conversation, tasks, capture, auto-fill, profile, bookmarks, quick commands. The same codebase serves both job seekers and recruiters — the user picks a role on the onboarding page, it is stored, sent in the WebSocket handshake, and the backend routes accordingly.",
      },
    ],
  },
  setup: {
    title: "Install the extension",
    intro:
      "This page walks you through loading the SmartJob browser extension into Chrome, connecting it to the backend, and completing first-run sign-in and onboarding.",
    blocks: [
      { t: "h2", text: "Prerequisites" },
      {
        t: "ul",
        items: [
          "A browser supporting Manifest V3 and the side panel: a recent Chrome / Chromium / Edge.",
          "The backend running (Docker is recommended). Once up, portal-api, api-gateway and agent-gateway listen on 8771 / 8767 / 8769.",
        ],
      },
      { t: "h2", text: "Load the extension" },
      {
        t: "ul",
        items: [
          "Open chrome://extensions and turn on Developer mode (top right).",
          "Click \"Load unpacked\" and select the extensions/job-seeker/ directory from the repo.",
          "The extension appears in the list (named SmartJob); pin it to the toolbar for quick access.",
          "After changing extension code, click the extension's Reload button on chrome://extensions.",
        ],
      },
      { t: "h2", text: "Configure backend gateways" },
      {
        t: "p",
        text: "Click the SmartJob toolbar icon to open the side panel — the first screen is the login / register form. Before signing in you must point the extension at your backend; a wrong gateway URL makes login fail. Click the gear (Settings) at the top of the side panel (in the wizard's top-left before login, in the main top bar after login) and fill in the three backend URLs; for a local Docker setup the \"Development\" preset fills them in one click.",
      },
      {
        t: "table",
        head: ["Service", "Local Docker URL"],
        rows: [
          ["portal-api (accounts / auth)", "http://127.0.0.1:8771"],
          ["api-gateway (command gateway)", "http://127.0.0.1:8767"],
          ["agent-gateway (agent conversation)", "http://127.0.0.1:8769"],
        ],
      },
      { t: "h2", text: "Sign in and onboarding" },
      {
        t: "p",
        text: "Back on the login / register form: existing users sign in directly; new users switch to \"Register\", enter an email and password, and complete an email verification code. In development mode a local Docker deployment auto-seeds a test account (smartjob@joyhouselabs.com / 123456) you can sign in with directly — this is a weak-password test account, and seeding must be disabled in production.",
      },
      {
        t: "p",
        text: "After signing in, the extension guides you through: choosing a role (job seeker / recruiter), choosing a platform (BOSS Zhipin / LinkedIn / Indeed), logging into that platform, and — for job seekers — uploading a resume and confirming job preferences. Once done you land in the AI assistant chat.",
      },
      { t: "h2", text: "Verify and troubleshoot" },
      {
        t: "p",
        text: "Once the extension connects to api-gateway it opens a WebSocket session, visible in the admin console dashboard / browser pool. Common issues:",
      },
      {
        t: "table",
        head: ["Symptom", "Fix"],
        rows: [
          ["Login hangs or fails", "Most likely a wrong gateway URL — reconfigure via the gear settings and run the connectivity test"],
          ["Side panel won't open", "Make sure the browser supports sidePanel; click the extension toolbar icon"],
          ["Code changes not applied", "Click the extension's Reload button on chrome://extensions"],
          ["Platform action hits anti-abuse checks", "The extension and agent will prompt you; follow the guidance on the platform site"],
        ],
      },
    ],
  },
  admin: {
    title: "Admin console design",
    intro:
      "admin is a Vue 3 single-page application for operations staff — to monitor the system, push command config, and manage users and tasks. It is not for end job-seeking / recruiting users.",
    blocks: [
      { t: "h2", text: "Stack and structure" },
      {
        t: "p",
        text: "The stack is Vue 3 (Composition API) + Vite + TypeScript + Pinia + vue-router + vue-i18n + ECharts. The source is organized into pages, reusable components, Pinia stores, a unified API layer (services/api.ts) and i18n.",
      },
      { t: "h2", text: "Feature pages" },
      {
        t: "ul",
        items: [
          "Monitoring & diagnostics: dashboard, command log, error log, system monitor, MCP metrics, client errors.",
          "Sessions & resources: browser pool, proxy pool, job cache.",
          "Agent & tasks: agent sessions, session detail, agent tasks, task templates, history, users.",
          "Config push: command registry, dynamic commands (the core: JSON editing, validation, one-click push, version rollback), quick-command config.",
          "Users & data: portal users, API recordings, auto-fill templates and captures, candidate resumes, growth metrics.",
        ],
      },
      { t: "h2", text: "Backend connectivity" },
      {
        t: "p",
        text: "The console talks to all three backend services. A Vite proxy in development and Nginx in production route by prefix: /admin and /status to api-gateway, /agent-gw/ to agent-gateway, /portal/ to portal-api. The dashboard receives real-time events over WebSocket /admin/ws, with a composable handling connection management and exponential-backoff reconnects.",
      },
      { t: "h2", text: "i18n and build" },
      {
        t: "p",
        text: "Bilingual support uses vue-i18n: each file under locales/zh and locales/en maps to a namespace, auto-merged on startup via import.meta.glob. The build command pnpm build runs vue-tsc type-checking then vite build; the Dockerfile is a two-stage build — Node compiles the assets, Nginx serves them and proxies the backends, exposed on port 8080.",
      },
    ],
  },
};

const docs: Record<Locale, Record<DocSlug, DocContent>> = { zh, en };

export function getDoc(locale: Locale, slug: DocSlug): DocContent {
  return docs[locale][slug];
}
