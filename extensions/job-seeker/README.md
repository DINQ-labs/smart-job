# job-seeker-ext-v3

DingQ 助手扩展 — v3 视觉 + 标准化业务架构。

## 与 v2(`../job-seeker-ext`)的差异

| 维度 | v2 | v3 |
|------|----|----|
| 视觉 | 暖米色 + 散落 67 处硬编码 hex | DESIGN.md token 全集,CSS 变量驱动 |
| sidepanel.html | 3089 行(逻辑+样式混) | < 400 行(纯结构,样式分离) |
| sidepanel 样式 | inline 1500+ 行 | `styles/{tokens,reset,components}.css` 分文件 |
| background.js | 1355 行单文件 | `background/{index,ws,api,page-context,messages}.js` 4-5 文件 |
| 业务 fetch | 散在 10+ 个 sidepanel module | `sidepanel/shared/api.js` 集中 client |
| 事件名 | window.dispatchEvent 全局散落 | `sidepanel/shared/events.js` 集中常量 |
| 文件命名 | 混合(snake/kebab/camel) | 统一 kebab-case |
| 类名 | 业务名(.task-row.paused) | DESIGN.md §6(.task-card / .chip-amber) |

## 目录

```
job-seeker-ext-v3/
├── manifest.json           # MV3 + module 类型 service worker
├── background/             # 拆分后的后台
│   ├── index.js              # 入口 + chrome.runtime 路由
│   ├── ws.js                 # control.dinq.me WebSocket
│   ├── api.js                # 各 gateway HTTP client
│   ├── page-context.js       # PageContext cache + 广播
│   └── messages.js           # runtime.onMessage 派发器
├── content/
│   ├── shared/               # interceptor / page-context-extractor / injection-widgets / stealth
│   ├── boss/ linkedin/ indeed/
│   └── dinq_probe.js
├── sidepanel/
│   ├── index.html            # 结构骨架
│   ├── styles/               # tokens / reset / components
│   ├── app.js                # boot + state + tab router
│   ├── modules/              # chat / tasks / bookmarks / profile / onboarding / chips-loader / ...
│   └── shared/               # api.js / events.js
├── popup/ index.html + index.js
├── options/ index.html + index.js
└── lib/                      # 跨 entry 共享(platforms-config / design.js / ext-core)
```

## 设计 token 约束

- 任何 CSS hex 必须来自 `styles/tokens.css` 的 `--*` 变量
- 任何状态颜色必须落在 DESIGN.md §7 状态语义对照表(blue/green/amber/red/dark)
- 任何新组件类名沿用 §6 前缀(chat-/task-/chip-/bm-/my-/ob-/plat-/...)
- 不引入 web font,系统栈 only

## 统一系统(v3.1 — 合并 api-recorder-v2 + autofill-ext)

v3 现已是**唯一统一扩展**:`job-seeker-ext`(v2)的全部能力本就是 v3 的干净重写;
`api-recorder-v2` 与 `autofill-ext` 两个独立扩展的能力已并入。

### 合并进来的能力

| 来源扩展 | 能力 | 落点 |
|---|---|---|
| api-recorder-v2 | CDP 隐身 API 录制(chrome.debugger 抓取流量) | background: `lib/recorder/recorder-bg.js`;UI: `sidepanel/modules/recorder-view.js` |
| autofill-ext | 通用表单自动检测/匹配/填写 + 多步编排 + 知识库 | background: `lib/autofill/autofill-bg.js`;content: `content/autofill/`;UI: `sidepanel/autofill/` |

- 两个子系统各自包在 IIFE 内,经 `background/index.js` 的 `importScripts` 注入同一 SW;
  消息互不串扰:recorder 用 `{ action }`、autofill 用 `{ type: 'AF_*' }`、v3 用 `{ type: 'DQ_*' }`,
  各监听器只认本子系统消息,其余放行。
- 新增权限:`debugger`(录制)、`webNavigation`(autofill 帧枚举);content_scripts 增加
  `content/autofill/detector.js`(全站)+ `shadow-shim.js`(ATS 站点)。

### 二级菜单

sidepanel 顶部 Tab 新增第 5 项 **🛠️ 工具**,其下为二级菜单:

```
工具
├── 🔍 表单填写   — autofill 检测 → 匹配 → 填写 → 多步编排
├── 📇 表单资料   — 简历 + 个人字段(填写知识库)
├── 📜 填写记录   — 历史
└── 📹 API录制    — CDP 抓包 / 上送 / LLM 分析
```

### 本地连接

全栈默认连本地,不连线上服务器:

- 扩展默认 `gatewayHost = 127.0.0.1` → api-gw `:8767`、agent-gw `:8769`、portal-api `:8771`
  (options 设置页可切线上;`shared/api.js`、`shared/auth-api.js`、`background/index.js`、
  `lib/ext-core/core/task-monitor.js`、合并子系统全部同源选址)
- 三个后端 `config.py` 默认 `127.0.0.1`;`job-portal-api` 的 `DATABASE_URL` 已给本地默认值
- 管理后台 `job-api-admin` 经 Vite proxy 代理到本地三个网关
