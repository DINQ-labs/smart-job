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
