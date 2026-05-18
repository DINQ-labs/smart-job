import type { Locale } from "./i18n";

export type FeatureId =
  | "agent"
  | "multiplatform"
  | "capture"
  | "autofill"
  | "automation"
  | "admin";

export interface Feature {
  id: FeatureId;
  title: string;
  desc: string;
}

export interface ComponentRow {
  name: string;
  port: string;
  role: string;
}

export interface QuickStep {
  title: string;
  desc: string;
  code?: string;
}

export interface Dictionary {
  nav: { features: string; showcase: string; architecture: string; docs: string; quickstart: string };
  hero: {
    badge: string;
    title: string;
    titleAccent: string;
    subtitle: string;
    ctaPrimary: string;
    ctaSecondary: string;
    note: string;
  };
  highlights: { value: string; label: string }[];
  features: { title: string; subtitle: string; items: Feature[] };
  pitch: {
    title: string;
    subtitle: string;
    pillars: { icon: string; name: string; tagline: string; points: { name: string; desc: string }[] }[];
  };
  showcase: { title: string; subtitle: string; shots: { img: string; title: string; desc: string }[] };
  adminPreview: { title: string; subtitle: string; shots: { img: string; title: string }[]; cta: string };
  architecture: {
    title: string;
    subtitle: string;
    components: ComponentRow[];
    colName: string;
    colPort: string;
    colRole: string;
    flowTitle: string;
    flow: string[];
  };
  quickstart: {
    title: string;
    subtitle: string;
    steps: QuickStep[];
    docsHint: string;
  };
  docsCta: { title: string; subtitle: string; cards: { slug: string; title: string; desc: string }[] };
  footer: {
    tagline: string;
    disclaimer: string;
    built: string;
    funding: { prefix: string; suffix: string };
  };
  docsNav: { overview: string; items: { slug: string; label: string }[]; onThisPage: string; backHome: string };
}

const zh: Dictionary = {
  nav: { features: "功能特性", showcase: "界面预览", architecture: "系统架构", docs: "文档", quickstart: "快速开始" },
  hero: {
    badge: "开源 · 对话式求职招聘自动化",
    title: "让 AI Agent 替你",
    titleAccent: "搜索、评估、沟通、投递",
    subtitle:
      "smart-job 是一套多平台（BOSS直聘 / LinkedIn / Indeed）求职与招聘自动化系统：浏览器扩展在你自己的登录态下执行，后端由对话式 Agent 驱动任务编排。求职者和招聘者都能用。",
    ctaPrimary: "快速开始",
    ctaSecondary: "阅读文档",
    note: "MIT 许可 · Docker 一键部署 · 中英双语",
  },
  highlights: [
    { value: "3", label: "求职平台" },
    { value: "5", label: "可独立部署组件" },
    { value: "2", label: "求职 / 招聘双模" },
    { value: "100%", label: "真实浏览器执行" },
  ],
  features: {
    title: "核心能力",
    subtitle: "从对话到执行，一条完整的自动化链路。",
    items: [
      {
        id: "agent",
        title: "对话式 Agent",
        desc:
          "用自然语言表达意图，Agent 自动判定场景、组装提示词、调用工具完成搜索、评估、沟通与投递，过程通过 SSE 实时流式呈现。",
      },
      {
        id: "multiplatform",
        title: "多平台支持",
        desc:
          "同一套系统覆盖 BOSS直聘、LinkedIn、Indeed，并支持求职者与招聘者双向场景，无需为每个平台重写一遍。",
      },
      {
        id: "capture",
        title: "抓包 / API 录制",
        desc:
          "基于 Chrome DevTools Protocol 透明录制页面网络请求，逆向平台接口，沉淀为可下发的动态命令。",
      },
      {
        id: "autofill",
        title: "自动填表",
        desc:
          "把简历信息智能填进任意招聘网站的申请表单，覆盖 Workday、Greenhouse、Lever 等主流 ATS，支持多 iframe 与隐私脱敏。",
      },
      {
        id: "automation",
        title: "工作自动化",
        desc:
          "命令注册表 + 令牌链建模平台调用依赖，隐藏 Worker Tab 用真实 fetch 执行，动态命令让平台接口变化无需重发扩展。",
      },
      {
        id: "admin",
        title: "管理后台",
        desc:
          "Vue3 运维后台：实时监控会话与 Agent、下发命令配置、管理用户与长任务、观测 MCP 调用指标。",
      },
    ],
  },
  pitch: {
    title: "三大核心能力",
    subtitle:
      "Agent 懂你要什么 → 调度层把任务变成可控流水线 → 扩展在你自己的浏览器里真实落地。",
    pillars: [
      {
        icon: "🧩",
        name: "浏览器扩展",
        tagline: "在你自己的浏览器里、用真实登录态干活 —— 不伪造请求、不靠脆弱页面脚本、平台改接口也不用重发版。",
        points: [
          { name: "真实登录态执行", desc: "—— 全系统唯一接触招聘平台的组件,所有请求都用用户自己的 Cookie 和真实指纹发出,后端从不在服务器侧伪造平台流量。" },
          { name: "四层降级阶梯", desc: "—— ①抓包捕获真实 API → ②固化成可复用命令 → ③没 API 才退到 DOM 操作 → ④DOM 读不懂才上图像识别;可靠性优先、成本递增。" },
          { name: "抓包逆向", desc: "—— 基于 Chrome DevTools Protocol 旁路捕获平台真实 API 流量,不注入脚本、对页面透明,把接口沉淀成可复用命令。" },
          { name: "令牌链", desc: "—— 平台 API 的有时效安全令牌用有向依赖图建模,缺失或过期自动回补,无需人工关心调用顺序。" },
          { name: "隐藏 Worker Tab", desc: "—— 每平台一个后台工作标签页,真实 fetch + 按操作类型数秒级随机抖动限速,模拟人类节奏。" },
          { name: "动态命令热更", desc: "—— 平台改接口时,后端以安全 JSON 模板经 WebSocket 推下即时生效,不重发扩展、杜绝代码注入。" },
          { name: "自动填表", desc: "—— 把简历智能填进 Workday / Greenhouse / Lever 等任意 ATS 表单,跨 iframe 探测字段、上报前自动脱敏 PII。" },
          { name: "求职 / 招聘双模", desc: "—— 同一套代码、同一个扩展,既服务求职者也服务招聘者。" },
        ],
      },
      {
        icon: "⚙️",
        name: "调度",
        tagline: "把不可靠的平台操作变成可控、可观测、可扩容的流水线 —— 资源池化 + 分布式编排 + 拟人限速 + 全链路审计。",
        points: [
          { name: "浏览器槽位池", desc: "—— 带状态机的槽位管理,按平台限容、FIFO 排队、超时回收;槽位释放时强制登出清态,多用户零串扰。" },
          { name: "会话隧道", desc: "—— 扩展经单条 WebSocket 接入,按(用户, 角色)管理会话、踢掉重复登录,请求响应用 req_id 精确配对。" },
          { name: "三模式代理池", desc: "—— 轮询 / 随机 / 粘性(同浏览器复用同代理)三种分配策略可选。" },
          { name: "限速 · 配额 · 守卫", desc: "—— 每会话按操作类型 1~8 秒随机抖动限速,每日配额硬约束,敏感操作前置守卫拦截。" },
          { name: "任务编排引擎", desc: "—— 长任务拆成模板 → 平台专属步骤流水线 → 逐条目执行,支持批量打招呼与简历筛选,进度经 SSE 实时回报。" },
          { name: "分布式协调", desc: "—— Redis 撑起跨实例并发计数、任务租约、取消恢复信号;Redis 不可用自动降级单实例,服务不中断。" },
          { name: "风控信号驱动", desc: "—— 平台摩擦统一抽象成信号,按策略自动处理:自动重试 / 暂停转人工 / 跳过当前条目 / 终止任务。" },
          { name: "全链路审计", desc: "—— 每条命令、每次槽位分配、每个任务进度都落库,管理后台实时 WebSocket 看板可观测。" },
        ],
      },
      {
        icon: "🤖",
        name: "Agent",
        tagline: "一句话下达意图,它自己选模式、调工具、跑批量 —— 对话式驱动 + 模式感知 + 流式体验 + 工具化执行。",
        points: [
          { name: "对话式自动化", desc: "—— 用自然语言说需求,Agent 自动调工具完成搜索、评估、沟通、投递。" },
          { name: "推理主循环 + 流式输出", desc: "—— 推理 → 调工具 → 取结果 → 再推理多轮循环,全程以 SSE 推流:文本、思考、工具调用、职位卡片实时可见。" },
          { name: "六大模式系统", desc: "—— 搜索 / 评估 / 投递 / 面试准备 / 对比 / 招聘,按关键词自动判定意图,模式可中途升级。" },
          { name: "约 210 个 MCP 工具", desc: "—— 覆盖三大平台求职端与招聘端,经 FastMCP 标准协议暴露,任意 MCP 客户端可直连。" },
          { name: "扩展思考", desc: "—— 开启 extended thinking,复杂决策的推理过程可流式展开查看。" },
          { name: "模型自动降级", desc: "—— 主模型过载时自动切到备用模型,对话不中断。" },
          { name: "简历与个性化完全本地", desc: "—— 简历 PDF/DOCX 在服务内解析、缓存、版本化,不调任何外部服务。" },
          { name: "跨 worker 会话", desc: "—— 会话历史经 Redis 跨实例共享、空闲自动回收,水平扩容无缝。" },
        ],
      },
    ],
  },
  showcase: {
    title: "侧边栏实拍",
    subtitle: "扩展侧边栏就是用户的全部操作界面 —— 对话、长任务、抓包工具集于一处。",
    shots: [
      { img: "/showcase/chat-joblist.png", title: "AI 助手 · 职位列表", desc: "自然语言搜岗，结果可勾选批量分析或打招呼。" },
      { img: "/showcase/chat-analyze.png", title: "AI 助手 · 职位分析", desc: "流式输出公司、薪资、HR 活跃度与 JD 解读。" },
      { img: "/showcase/chat-interview.png", title: "AI 助手 · 面试准备", desc: "结合简历给出岗位匹配挑战与高频面试题。" },
      { img: "/showcase/tasks.png", title: "长任务", desc: "内置模板：找最匹配职位、批量投递、进度追踪。" },
      { img: "/showcase/tools-recorder.png", title: "API 录制", desc: "基于 CDP 透明抓包，逆向平台接口。" },
    ],
  },
  adminPreview: {
    title: "管理后台",
    subtitle:
      "面向运维人员的 Vue 3 控制台 —— 实时监控会话与 Agent、下发命令配置、观测 MCP 调用指标，共 24 个菜单页。",
    shots: [
      { img: "/admin/01-dashboard.png", title: "仪表盘 · 系统实时总览" },
      { img: "/admin/21-mcp-metrics.png", title: "MCP 调用观测" },
      { img: "/admin/05-dynamic-commands.png", title: "动态命令推送" },
    ],
    cta: "浏览全部 24 个后台界面",
  },
  architecture: {
    title: "系统架构",
    subtitle: "一个浏览器扩展、三个后端服务、一个管理后台 —— 各自独立部署。",
    components: [
      { name: "job-seeker 扩展", port: "—", role: "在用户浏览器内执行抓包、自动填表、平台 API 调用" },
      { name: "api-gateway", port: "8767", role: "命令网关、MCP 工具服务、扩展 WebSocket 隧道" },
      { name: "agent-gateway", port: "8769", role: "Agent 对话核心、模式系统、任务编排、SSE 流式输出" },
      { name: "portal-api", port: "8771", role: "账号注册登录、多种鉴权、JWT 签发与 JWKS" },
      { name: "admin 后台", port: "8080", role: "运维监控、命令下发、用户与任务管理" },
    ],
    colName: "组件",
    colPort: "端口",
    colRole: "职责",
    flowTitle: "端到端数据流",
    flow: [
      "用户在扩展侧边栏用自然语言表达意图",
      "agent-gateway 校验身份、判定模式、进入 Agent 推理循环",
      "经 MCP 调用 api-gateway 暴露的工具",
      "api-gateway 通过 WebSocket 把命令下发给扩展",
      "扩展在隐藏 Worker Tab 内用真实 fetch 请求平台 API",
      "结果逐层回传，SSE 把过程实时推回侧边栏",
    ],
  },
  quickstart: {
    title: "快速开始",
    subtitle: "前置：Docker 与 Docker Compose。三个后端、数据库、管理后台一键拉起。",
    steps: [
      { title: "克隆仓库", desc: "获取源码。", code: "git clone https://github.com/DINQ-labs/smart-job.git\ncd smart-job" },
      { title: "配置环境变量", desc: "复制示例文件，按需填写 OPENROUTER_API_KEY 等。", code: "cp .env.example .env" },
      { title: "一键启动", desc: "构建并拉起全部服务。", code: "docker compose up -d --build" },
      { title: "加载浏览器扩展", desc: "在 chrome://extensions 启用开发者模式，加载 extensions/job-seeker/ 目录。" },
    ],
    docsHint: "想了解每个组件如何工作？阅读文档。",
  },
  docsCta: {
    title: "深入文档",
    subtitle: "四篇文档，覆盖架构、后端、扩展与管理后台。",
    cards: [
      { slug: "architecture", title: "架构总览", desc: "组件构成、端到端数据流与关键机制。" },
      { slug: "backend", title: "后端设计", desc: "三个 Python 服务与共享风控包。" },
      { slug: "extension", title: "扩展设计", desc: "抓包、自动填表、工作自动化。" },
      { slug: "setup", title: "安装扩展", desc: "加载扩展、配置网关与首次登录引导。" },
      { slug: "admin", title: "管理后台", desc: "运维监控与配置下发。" },
    ],
  },
  footer: {
    tagline: "多平台求职 / 招聘自动化系统",
    disclaimer: "本项目用于学习与研究目的。使用者需自行确保遵守目标平台的服务条款及所在地法律法规。",
    built: "基于 Next.js 构建",
    funding: { prefix: "本项目由", suffix: "资助" },
  },
  docsNav: {
    overview: "文档总览",
    items: [
      { slug: "architecture", label: "架构总览" },
      { slug: "backend", label: "后端设计" },
      { slug: "extension", label: "扩展设计" },
      { slug: "setup", label: "安装扩展" },
      { slug: "admin", label: "管理后台" },
    ],
    onThisPage: "本页目录",
    backHome: "返回首页",
  },
};

const en: Dictionary = {
  nav: { features: "Features", showcase: "Preview", architecture: "Architecture", docs: "Docs", quickstart: "Quick Start" },
  hero: {
    badge: "Open source · Conversational job automation",
    title: "Let an AI agent",
    titleAccent: "search, evaluate, message and apply",
    subtitle:
      "smart-job is a multi-platform (BOSS Zhipin / LinkedIn / Indeed) job-seeking and recruiting automation system. A browser extension runs in your own logged-in session, while a conversational agent drives task orchestration on the backend — for both job seekers and recruiters.",
    ctaPrimary: "Get started",
    ctaSecondary: "Read the docs",
    note: "MIT licensed · One-command Docker deploy · Bilingual",
  },
  highlights: [
    { value: "3", label: "Job platforms" },
    { value: "5", label: "Deployable components" },
    { value: "2", label: "Seeker / recruiter modes" },
    { value: "100%", label: "Real-browser execution" },
  ],
  features: {
    title: "Core capabilities",
    subtitle: "A complete automation chain — from conversation to execution.",
    items: [
      {
        id: "agent",
        title: "Conversational agent",
        desc:
          "Express intent in natural language. The agent detects the scenario, composes prompts, and calls tools to search, evaluate, message and apply — streamed live over SSE.",
      },
      {
        id: "multiplatform",
        title: "Multi-platform",
        desc:
          "One system covers BOSS Zhipin, LinkedIn and Indeed, serving both job seekers and recruiters — no per-platform rewrite.",
      },
      {
        id: "capture",
        title: "Packet capture / API recording",
        desc:
          "Transparently records page network traffic via the Chrome DevTools Protocol to reverse-engineer platform APIs into pushable dynamic commands.",
      },
      {
        id: "autofill",
        title: "Form auto-fill",
        desc:
          "Smartly fills resume data into any job application form — Workday, Greenhouse, Lever and other ATS platforms — with multi-iframe support and PII scrubbing.",
      },
      {
        id: "automation",
        title: "Job automation",
        desc:
          "A command registry plus token chains model API call dependencies; a hidden Worker Tab executes real fetches, and dynamic commands absorb platform API changes without re-shipping the extension.",
      },
      {
        id: "admin",
        title: "Admin console",
        desc:
          "A Vue 3 operations console: monitor sessions and agents live, push command configs, manage users and long-running tasks, and observe MCP call metrics.",
      },
    ],
  },
  pitch: {
    title: "Three core capabilities",
    subtitle:
      "The agent understands your intent → the scheduling layer turns it into a controllable pipeline → the extension executes it for real, inside your own browser.",
    pillars: [
      {
        icon: "🧩",
        name: "Browser extension",
        tagline: "Real work inside your own browser — no forged requests, no brittle page scripts, no re-release when a platform changes its API.",
        points: [
          { name: "Real logged-in execution", desc: "— the only component that touches job platforms; every request goes out with the user's own cookies and real fingerprint, never forged server-side." },
          { name: "Four-tier fallback ladder", desc: "— ① capture the real API → ② freeze it into a reusable command → ③ fall back to DOM when there is no API → ④ resort to vision when the DOM is unreadable; reliability first." },
          { name: "Transparent capture", desc: "— passively captures real platform API traffic via the Chrome DevTools Protocol, injecting no script and changing nothing on the page." },
          { name: "Token chain", desc: "— the time-limited security tokens platform APIs require are modeled as a directed dependency graph; missing or expired tokens are backfilled automatically." },
          { name: "Hidden Worker Tab", desc: "— a background tab per platform issues real fetch calls with randomized-jitter rate limiting by operation type, mimicking human rhythm." },
          { name: "Hot-updatable commands", desc: "— when a platform changes its API, the backend pushes new commands as safe JSON templates over WebSocket, live — no extension re-release." },
          { name: "Form auto-fill", desc: "— fills resume data into any ATS form (Workday, Greenhouse, Lever, …), detecting fields across iframes and scrubbing PII before upload." },
          { name: "Dual seeker / recruiter mode", desc: "— one codebase, one extension, serving both job seekers and recruiters." },
        ],
      },
      {
        icon: "⚙️",
        name: "Scheduling",
        tagline: "Turns unreliable platform actions into a controllable, observable, scalable pipeline — resource pooling + distributed orchestration + human-like pacing + a full audit trail.",
        points: [
          { name: "Browser slot pool", desc: "— slots managed by a state machine, with per-platform capacity, FIFO queuing and idle eviction; on release a slot is force-logged-out — zero data bleed." },
          { name: "Session tunnel", desc: "— extensions connect over a single WebSocket; sessions keyed by (user, role), duplicate logins kicked, requests correlated by req_id." },
          { name: "Three-mode proxy pool", desc: "— round-robin / random / sticky (same proxy reused per browser) allocation strategies." },
          { name: "Rate limit · quota · guard", desc: "— per-session jitter rate limiting (1–8 s) by operation type, hard daily quotas, and a guard intercepting sensitive operations up front." },
          { name: "Task orchestration engine", desc: "— long tasks become template → platform-specific step pipeline → per-item execution, with progress streamed live over SSE." },
          { name: "Distributed coordination", desc: "— Redis powers cross-instance turn counting, task leases and cancel/resume signals; without Redis it degrades to single-instance." },
          { name: "Risk-signal driven", desc: "— platform friction is unified into signals handled by strategy: auto-retry / pause-for-human / skip-item / abort." },
          { name: "End-to-end audit", desc: "— every command, slot assignment and task step is persisted and observable on a live admin dashboard." },
        ],
      },
      {
        icon: "🤖",
        name: "Agent",
        tagline: "State your intent in one sentence; it picks the mode, calls the tools, runs the batch — conversation-driven + mode-aware + streamed + tool-executed.",
        points: [
          { name: "Conversational automation", desc: "— state your need in natural language; the agent calls tools to search, evaluate, message and apply." },
          { name: "Reasoning loop + streaming", desc: "— a multi-turn reason → call tool → take result → reason loop, streamed end-to-end over SSE: text, thinking, tool calls and job cards in real time." },
          { name: "Six-mode system", desc: "— search / evaluate / apply / interview / compare / recruiter; intent is auto-detected from keywords and the mode can upgrade mid-conversation." },
          { name: "~210 MCP tools", desc: "— covering the seeker and recruiter sides of all three platforms, exposed over the standard FastMCP protocol for any MCP client." },
          { name: "Extended thinking", desc: "— with extended thinking on, the reasoning behind complex decisions can be streamed and expanded." },
          { name: "Automatic model fallback", desc: "— if the primary model is overloaded, it switches to a backup model without breaking the conversation." },
          { name: "Fully local resume", desc: "— resume PDFs/DOCX are parsed, cached and versioned inside the service, calling no external service." },
          { name: "Cross-worker sessions", desc: "— session history is shared across instances via Redis and idle-swept; horizontal scaling is seamless." },
        ],
      },
    ],
  },
  showcase: {
    title: "Inside the side panel",
    subtitle:
      "The extension side panel is the whole user surface — chat, long-running tasks and capture tools in one place.",
    shots: [
      { img: "/showcase/chat-joblist.png", title: "Agent · Job list", desc: "Search in natural language; bulk-analyze or message the results." },
      { img: "/showcase/chat-analyze.png", title: "Agent · Job analysis", desc: "Streamed breakdown of company, pay, recruiter activity and the JD." },
      { img: "/showcase/chat-interview.png", title: "Agent · Interview prep", desc: "Match challenges and likely questions, grounded in your resume." },
      { img: "/showcase/tasks.png", title: "Long-running tasks", desc: "Built-in templates: best-match search, bulk apply, progress tracking." },
      { img: "/showcase/tools-recorder.png", title: "API recording", desc: "Transparent CDP capture to reverse-engineer platform APIs." },
    ],
  },
  adminPreview: {
    title: "Admin console",
    subtitle:
      "A Vue 3 operations console — monitor sessions and agents live, push command configs and observe MCP call metrics across 24 menu pages.",
    shots: [
      { img: "/admin/01-dashboard.png", title: "Dashboard · live system overview" },
      { img: "/admin/21-mcp-metrics.png", title: "MCP call metrics" },
      { img: "/admin/05-dynamic-commands.png", title: "Dynamic command push" },
    ],
    cta: "Browse all 24 console screens",
  },
  architecture: {
    title: "System architecture",
    subtitle: "One browser extension, three backend services, one admin console — each independently deployable.",
    components: [
      { name: "job-seeker extension", port: "—", role: "Runs capture, auto-fill and platform API calls inside the user's browser" },
      { name: "api-gateway", port: "8767", role: "Command gateway, MCP tool server, extension WebSocket tunnel" },
      { name: "agent-gateway", port: "8769", role: "Agent conversation core, mode system, task orchestration, SSE streaming" },
      { name: "portal-api", port: "8771", role: "Account registration/login, multi-method auth, JWT issuance and JWKS" },
      { name: "admin console", port: "8080", role: "Operations monitoring, config push, user and task management" },
    ],
    colName: "Component",
    colPort: "Port",
    colRole: "Responsibility",
    flowTitle: "End-to-end data flow",
    flow: [
      "User states intent in natural language in the extension side panel",
      "agent-gateway verifies identity, detects mode, enters the agent reasoning loop",
      "It calls tools exposed by api-gateway over MCP",
      "api-gateway dispatches the command to the extension over WebSocket",
      "The extension runs a real fetch against the platform API in a hidden Worker Tab",
      "Results flow back up; SSE streams the process live into the side panel",
    ],
  },
  quickstart: {
    title: "Quick start",
    subtitle: "Prerequisite: Docker and Docker Compose. Backends, database and admin console start with one command.",
    steps: [
      { title: "Clone the repo", desc: "Get the source.", code: "git clone https://github.com/DINQ-labs/smart-job.git\ncd smart-job" },
      { title: "Configure env vars", desc: "Copy the example file and fill in OPENROUTER_API_KEY etc.", code: "cp .env.example .env" },
      { title: "Start everything", desc: "Build and bring up all services.", code: "docker compose up -d --build" },
      { title: "Load the extension", desc: "Enable developer mode at chrome://extensions and load the extensions/job-seeker/ directory." },
    ],
    docsHint: "Want to know how each component works? Read the docs.",
  },
  docsCta: {
    title: "Dive into the docs",
    subtitle: "Four documents covering architecture, backend, extension and admin console.",
    cards: [
      { slug: "architecture", title: "Architecture", desc: "Components, end-to-end data flow and key mechanisms." },
      { slug: "backend", title: "Backend design", desc: "Three Python services and the shared risk-control package." },
      { slug: "extension", title: "Extension design", desc: "Packet capture, form auto-fill, job automation." },
      { slug: "setup", title: "Install the extension", desc: "Load the extension, configure gateways and first-run sign-in." },
      { slug: "admin", title: "Admin console", desc: "Operations monitoring and config push." },
    ],
  },
  footer: {
    tagline: "Multi-platform job-seeking & recruiting automation",
    disclaimer:
      "This project is for learning and research purposes. Users are responsible for complying with the target platforms' terms of service and local laws.",
    built: "Built with Next.js",
    funding: { prefix: "Funded by", suffix: "" },
  },
  docsNav: {
    overview: "Documentation",
    items: [
      { slug: "architecture", label: "Architecture" },
      { slug: "backend", label: "Backend design" },
      { slug: "extension", label: "Extension design" },
      { slug: "setup", label: "Install" },
      { slug: "admin", label: "Admin console" },
    ],
    onThisPage: "On this page",
    backHome: "Back to home",
  },
};

const dictionaries: Record<Locale, Dictionary> = { zh, en };

export function getDictionary(locale: Locale): Dictionary {
  return dictionaries[locale];
}
