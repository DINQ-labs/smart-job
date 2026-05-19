# smart-job

> 多平台（BOSS直聘 / LinkedIn / Indeed）求职 — 招聘自动化系统：浏览器扩展 + Agent 对话 + 任务编排。

**官网 / 在线文档**：[smartjob.top](https://smartjob.top)

smart-job 由一个 Chrome MV3 扩展、三个后端服务和一个 Vue3 管理后台组成，围绕大语言模型
（Claude / OpenRouter）做求职与招聘场景的对话式自动化。扩展与管理后台均支持中 / 英双语。

## 两种使用方式

| 方式 | 适合 | 你要做的 |
|---|---|---|
| **连演示环境** | 只想体验一下 | 装浏览器扩展、用演示账号登录 —— 不用跑后端、不用配 `.env` |
| **本地部署** | 自己跑 / 改代码 / 自建 | 用 Docker 跑全栈、配 `.env`,扩展指向本地 |

- **连演示环境** —— 扩展开箱默认就指向官方演示站 `api.smartjob.top`。直接看下面
  [浏览器扩展](#浏览器扩展) 一节装好扩展,用演示账号 `demo@smartjob.top` / `123456`
  登录即可;Docker 与 `.env` 步骤全部跳过。
- **本地部署** —— 按下面「快速开始」与「配置 `.env`」走。

## 目录结构

```
smart-job/
├── packages/
│   ├── job_common/      # 共享纯逻辑包（风控信号）
│   ├── api-gateway/     # 命令网关 / MCP 服务 / 浏览器代理      :8767
│   ├── agent-gateway/   # Agent 对话核心 / 任务编排 / SSE       :8769
│   ├── portal-api/      # 账号 / 鉴权（JWT）                    :8771
│   └── admin/           # Vue3 管理后台                         :8080
├── extensions/
│   └── job-seeker/      # Chrome MV3 扩展（求职 + 招聘 + 表单自填 + API 录制）
├── docker/              # 容器初始化脚本
├── docs/                # 架构与设计文档
└── docker-compose.yml
```

## 技术栈

- 后端：Python 3.12（FastAPI / Starlette / FastMCP）、PostgreSQL、Redis
- 管理后台：Vue 3 + Vite + TypeScript + Pinia + vue-i18n
- 扩展：Chrome Manifest V3（原生 JS）
- LLM：OpenRouter（优先）/ Anthropic

## 快速开始（Docker，本地部署）

前置：Docker + Docker Compose。

```bash
cp .env.example .env          # 按需填写 OPENROUTER_API_KEY 等
docker compose up -d --build
```

启动后：

| 服务 | 地址 |
|---|---|
| 管理后台 admin | http://localhost:8081 |
| api-gateway | http://localhost:8767 |
| agent-gateway | http://localhost:8769 |
| portal-api | http://localhost:8771 |
| PostgreSQL | localhost:5443 |
| Redis | localhost:6390 |

数据库 `boss_gateway`、`smart_job` 在 postgres 容器首次启动时自动创建；各服务的表结构在自身启动时建。

停止：`docker compose down`（加 `-v` 连数据卷一并删除）。

## 配置 `.env`

根目录 `.env` 供 `docker-compose.yml` 读取(由 `.env.example` 复制而来)。**全部留空也能
把栈跑起来** —— 按需填:

| 变量 | 留空的后果 | 何时要填 |
|---|---|---|
| `POSTGRES_PASSWORD` | 用默认弱口令 `password` | 公网部署务必改强口令 |
| `OPENROUTER_API_KEY` | Agent 对话不可用(扩展其余功能正常) | 想用 AI 助手对话就填 |
| `ADMIN_PASSWORD` | 管理后台不鉴权(本地开发可接受) | 公网部署务必设 —— 它同时是首个管理员账号 `admin` 的初始口令 |

完整变量与说明见 `.env.example` 内的注释。各 Python 服务单独(非 Docker)运行时改用
`packages/<服务>/.env`(见各自的 `.env.example`);docker-compose 模式只认根 `.env`。
公网部署的额外加固(扩展 WebSocket 鉴权等)见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 本地开发（不走 Docker）

需本地的 PostgreSQL 与 Redis。三个 Python 服务各自建 venv：

```bash
# 对 api-gateway / agent-gateway / portal-api 各执行一次：
cd packages/<service>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # 修改数据库连接串等
```

启动后端：

```bash
cd packages/api-gateway   && .venv/bin/python server.py
cd packages/agent-gateway && .venv/bin/python server.py
cd packages/portal-api    && .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8771
```

启动管理后台：

```bash
cd packages/admin
pnpm install
pnpm dev                      # 开发服务器，默认 http://localhost:5174
```

## 浏览器扩展

`extensions/job-seeker/` 是未打包的 Chrome MV3 扩展。加载方式:

1. 打开 `chrome://extensions`,启用「开发者模式」。
2. 点「加载已解压的扩展程序」,选择 `extensions/job-seeker/` 目录。
3. 打开扩展侧边栏 → 点「⚙️ 设置」选后端环境预设:
   - **演示环境**(默认)—— 指向官方演示站 `api.smartjob.top`,无需本地后端。
   - **开发环境** —— 一键填入本地 `127.0.0.1` 的三个端口(需先 `docker compose up`)。
   - **自定义** —— 后端在你自己的服务器时逐项手填。
4. 回登录页,用演示账号 `demo@smartjob.top` / `123456` 登录(本地部署时 portal-api
   开发模式会自动播种该账号)。

首次加载与登录引导的完整步骤见 [docs/EXTENSION-SETUP.md](docs/EXTENSION-SETUP.md)。

## 在 Claude Code 中使用(MCP)

api-gateway 本身是一个 MCP server —— [Claude Code](https://claude.com/claude-code) 连上后即可
调用平台自动化工具(`boss_* / linkedin_* / indeed_*`)。仓库根目录已带 `.mcp.json`:扩展登录
默认账号 `demo@smartjob.top` 后,在仓库目录打开 Claude Code 并确认信任本项目 MCP 即可。

`.mcp.json` 默认指向本地 `http://127.0.0.1:8767/mcp`;连演示站则把 URL 换成
`https://api.smartjob.top/mcp`。详细步骤见 [docs/EXTENSION-SETUP.md](docs/EXTENSION-SETUP.md) 第 8 节。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 架构总览与端到端数据流
- [docs/BACKEND.md](docs/BACKEND.md) — 后端设计（三个 Python 服务 + 共享包）
- [docs/EXTENSION.md](docs/EXTENSION.md) — 扩展设计（抓包 / 自动填表 / 工作自动化）
- [docs/EXTENSION-SETUP.md](docs/EXTENSION-SETUP.md) — 扩展加载与首次使用引导
- [docs/ADMIN.md](docs/ADMIN.md) — 管理后台设计
- [CONTRIBUTING.md](CONTRIBUTING.md) — 贡献指南

## 免责声明

本项目用于学习与研究目的。使用者需自行确保遵守目标平台的服务条款及所在地的法律法规。

## 许可证

[MIT](LICENSE)
