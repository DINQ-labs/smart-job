import type { Locale } from "@/lib/i18n";

export interface Screen {
  img: string;
  route: string;
  label: string;
}

export interface ScreenGroup {
  title: string;
  screens: Screen[];
}

export interface ScreensPage {
  title: string;
  intro: string;
  backHome: string;
  groups: ScreenGroup[];
}

const zh: ScreensPage = {
  title: "管理后台界面总览",
  intro:
    "管理后台是面向运维人员的 Vue 3 单页应用，共 24 个菜单页、分 5 组。以下为各页面的整页截图，取自本地开发环境 —— 数据较少，部分页面呈空态属正常。点击任意截图可查看原图。",
  backHome: "返回首页",
  groups: [
    {
      title: "一、网关 · 命令 · 资源池",
      screens: [
        { img: "/admin/01-dashboard.png", route: "/", label: "仪表盘" },
        { img: "/admin/02-commands.png", route: "/commands", label: "命令日志" },
        { img: "/admin/03-error-logs.png", route: "/error-logs", label: "错误日志" },
        { img: "/admin/04-command-registry.png", route: "/command-registry", label: "命令管理" },
        { img: "/admin/05-dynamic-commands.png", route: "/dynamic-commands", label: "动态命令推送" },
        { img: "/admin/06-browser-pool.png", route: "/pool", label: "浏览器池" },
        { img: "/admin/07-cli-pool.png", route: "/cli-pool", label: "CLI 池" },
        { img: "/admin/08-proxy-pool.png", route: "/proxy-pool", label: "代理池" },
        { img: "/admin/09-job-cache.png", route: "/job-cache", label: "职位缓存" },
      ],
    },
    {
      title: "二、Agent 与用户",
      screens: [
        { img: "/admin/10-agent-sessions.png", route: "/agent-sessions", label: "Agent 会话" },
        { img: "/admin/11-agent-tasks.png", route: "/agent-tasks", label: "长任务监控" },
        { img: "/admin/12-agent-templates.png", route: "/agent-templates", label: "模板编排" },
        { img: "/admin/13-agent-history.png", route: "/agent-history", label: "对话历史" },
        { img: "/admin/14-agent-users.png", route: "/agent-users", label: "Agent 用户" },
        { img: "/admin/15-portal-users.png", route: "/portal-users", label: "账户用户" },
        { img: "/admin/16-candidate-resumes.png", route: "/candidate-resumes", label: "候选人简历" },
      ],
    },
    {
      title: "三、工具",
      screens: [
        { img: "/admin/17-api-capture.png", route: "/api-capture", label: "API Capture" },
      ],
    },
    {
      title: "四、AutoFill 扩展",
      screens: [
        { img: "/admin/18-autofill-templates.png", route: "/autofill-templates", label: "表单知识库" },
        { img: "/admin/19-autofill-captures.png", route: "/autofill-captures", label: "抓包审查" },
      ],
    },
    {
      title: "五、系统",
      screens: [
        { img: "/admin/20-system-monitor.png", route: "/system-monitor", label: "系统监控" },
        { img: "/admin/21-mcp-metrics.png", route: "/mcp-metrics", label: "MCP 调用观测" },
        { img: "/admin/22-client-errors.png", route: "/client-errors", label: "前端 JS 错误" },
        { img: "/admin/23-chip-configs.png", route: "/chip-configs", label: "快捷指令配置" },
        { img: "/admin/24-growth.png", route: "/growth", label: "Growth" },
      ],
    },
  ],
};

const en: ScreensPage = {
  title: "Admin console — all screens",
  intro:
    "The admin console is a Vue 3 single-page app for operators — 24 menu pages across 5 groups. Below are full-page screenshots from a local dev environment, so sparse data and empty states are expected. Click any screenshot to open it full size.",
  backHome: "Back to home",
  groups: [
    {
      title: "1 · Gateway · Commands · Pools",
      screens: [
        { img: "/admin/01-dashboard.png", route: "/", label: "Dashboard" },
        { img: "/admin/02-commands.png", route: "/commands", label: "Command log" },
        { img: "/admin/03-error-logs.png", route: "/error-logs", label: "Error log" },
        { img: "/admin/04-command-registry.png", route: "/command-registry", label: "Command registry" },
        { img: "/admin/05-dynamic-commands.png", route: "/dynamic-commands", label: "Dynamic commands" },
        { img: "/admin/06-browser-pool.png", route: "/pool", label: "Browser pool" },
        { img: "/admin/07-cli-pool.png", route: "/cli-pool", label: "CLI pool" },
        { img: "/admin/08-proxy-pool.png", route: "/proxy-pool", label: "Proxy pool" },
        { img: "/admin/09-job-cache.png", route: "/job-cache", label: "Job cache" },
      ],
    },
    {
      title: "2 · Agents & Users",
      screens: [
        { img: "/admin/10-agent-sessions.png", route: "/agent-sessions", label: "Agent sessions" },
        { img: "/admin/11-agent-tasks.png", route: "/agent-tasks", label: "Long-running tasks" },
        { img: "/admin/12-agent-templates.png", route: "/agent-templates", label: "Task templates" },
        { img: "/admin/13-agent-history.png", route: "/agent-history", label: "Conversation history" },
        { img: "/admin/14-agent-users.png", route: "/agent-users", label: "Agent users" },
        { img: "/admin/15-portal-users.png", route: "/portal-users", label: "Portal users" },
        { img: "/admin/16-candidate-resumes.png", route: "/candidate-resumes", label: "Candidate resumes" },
      ],
    },
    {
      title: "3 · Tools",
      screens: [
        { img: "/admin/17-api-capture.png", route: "/api-capture", label: "API Capture" },
      ],
    },
    {
      title: "4 · AutoFill",
      screens: [
        { img: "/admin/18-autofill-templates.png", route: "/autofill-templates", label: "Autofill templates" },
        { img: "/admin/19-autofill-captures.png", route: "/autofill-captures", label: "Autofill captures" },
      ],
    },
    {
      title: "5 · System",
      screens: [
        { img: "/admin/20-system-monitor.png", route: "/system-monitor", label: "System monitor" },
        { img: "/admin/21-mcp-metrics.png", route: "/mcp-metrics", label: "MCP metrics" },
        { img: "/admin/22-client-errors.png", route: "/client-errors", label: "Client errors" },
        { img: "/admin/23-chip-configs.png", route: "/chip-configs", label: "Chip configs" },
        { img: "/admin/24-growth.png", route: "/growth", label: "Growth" },
      ],
    },
  ],
};

const pages: Record<Locale, ScreensPage> = { zh, en };

export function getScreens(locale: Locale): ScreensPage {
  return pages[locale];
}
