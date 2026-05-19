# 部署文档

> 本文记录 SmartJob 后端栈在 `smartjob.top` 服务器上的生产部署 —— 用 Docker Compose
> 跑起六个容器,经宿主 nginx 反代到两个子域名。可作为重新部署 / 全新部署的 runbook。
> 官网(`smartjob.top` → 本机 :3400 的 Next.js 服务)是独立组件,不在本文范围。

## 1. 部署形态

| 子域名 | 指向 | 用途 |
|---|---|---|
| `api.smartjob.top` | nginx → api-gateway / agent-gateway / portal-api | 后端入口(扩展、MCP、对话、鉴权) |
| `dashboard.smartjob.top` | nginx → admin 容器 | 管理后台 |
| `smartjob.top` | nginx → :3400 | 官网(独立 Next.js,非本栈) |

Docker Compose 栈(项目名 `smart-job`)共六个容器:

| 容器 | 构建来源 | 宿主端口(仅 `127.0.0.1`) | 角色 |
|---|---|---|---|
| postgres | `postgres:16-alpine` | 5443 | 数据库 —— `boss_gateway`(两网关共用)+ `smart_job`(portal) |
| redis | `redis:7-alpine` | 6390 | 任务调度 |
| api-gateway | `packages/api-gateway` | 8767 | 命令网关 / MCP / 扩展 WebSocket |
| agent-gateway | `packages/agent-gateway` | 8769 | Agent 对话 / SSE / 长任务 |
| portal-api | `packages/portal-api` | 8771 | 账号注册登录 / JWT / JWKS |
| admin | `packages/admin` | 8081 | Vue 管理后台(容器自带 nginx) |

所有容器端口只绑 `127.0.0.1` —— 外部流量一律经宿主 nginx + HTTPS。

## 2. 前置条件

服务器(Ubuntu)需具备:

- Docker 与 Docker Compose 插件(`docker compose version`)
- nginx(`sites-available` / `sites-enabled` 结构)
- certbot 及其 `--nginx` 插件
- DNS:`api.smartjob.top`、`dashboard.smartjob.top` 的 A 记录已指向服务器 IP

## 3. 部署步骤

### 3.1 同步代码

把仓库的 `packages/`、`docker/`、`docker-compose.yml`、`.env` 同步到服务器
`/opt/smart-job/`(本地仓库根目录执行):

```bash
rsync -az --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='node_modules' \
  --exclude='dist' --exclude='.next' --exclude='.pytest_cache' --exclude='logs' \
  packages docker docker-compose.yml .env \
  root@smartjob.top:/opt/smart-job/
```

`.env` 至少需含 `OPENROUTER_API_KEY`(agent-gateway 启动必需)、`POSTGRES_PASSWORD`、
`ADMIN_PASSWORD`。`--delete` 只清理 `packages/`、`docker/` 内的陈旧文件,不会动
下一步在服务器上创建的 `docker-compose.override.yml`(它不属于任何被同步的目录树)。

### 3.2 生产覆盖文件

在服务器创建 `/opt/smart-job/docker-compose.override.yml` —— Docker Compose 会自动
合并。作用:把发布端口绑到 `127.0.0.1`、注入对外回跳 URL,并开启扩展 WS / SSE 强制鉴权。

```yaml
# 生产覆盖（smartjob.top 服务器）
# ports 用 !override 标签强制替换基础 compose 的 ports —— 否则会「追加」,
# 造成同端口 0.0.0.0 与 127.0.0.1 冲突,up 时报 port is already allocated。
# environment 是 map,自动与基础 compose 合并、本文件优先。
services:
  postgres:
    ports: !override
      - "127.0.0.1:5443:5432"
  redis:
    ports: !override
      - "127.0.0.1:6390:6379"
  api-gateway:
    ports: !override
      - "127.0.0.1:8767:8767"
    environment:
      GATEWAY_PUBLIC_URL: "https://api.smartjob.top"
      # 扩展 WS 强制鉴权:/ext/ws 握手必须带合法 portal access token（JWT）。
      EXT_AUTH_REQUIRED: "true"
      PORTAL_JWKS_URL: "http://portal-api:8771/.well-known/jwks.json"
      JWT_ISSUER: "http://localhost:8771"
      JWT_AUDIENCE: "job-portal"
  agent-gateway:
    ports: !override
      - "127.0.0.1:8769:8769"
    environment:
      # SSE 对话 / 写路径强制鉴权:/agent/sse* 必须带合法 Authorization: Bearer。
      AGENT_AUTH_REQUIRED: "true"
      PORTAL_JWKS_URL: "http://portal-api:8771/.well-known/jwks.json"
      JWT_ISSUER: "http://localhost:8771"
      JWT_AUDIENCE: "job-portal"
  portal-api:
    ports: !override
      - "127.0.0.1:8771:8771"
  admin:
    ports: !override
      - "127.0.0.1:8081:80"
```

校验合并结果(每个端口应只有一条、且 `host_ip: 127.0.0.1`):

```bash
cd /opt/smart-job && docker compose config | grep -E 'host_ip|published'
```

### 3.3 构建并启动

```bash
cd /opt/smart-job && docker compose up -d --build
docker compose ps          # 六个容器应全部 Up,postgres/redis/网关显示 healthy
```

### 3.4 nginx 反代

**WebSocket 升级 map**(各 vhost 共用)—— `/etc/nginx/conf.d/smartjob_ws.conf`:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

**`/etc/nginx/sites-available/api.smartjob.top`** —— 按路径前缀分发到三个网关:

```nginx
server {
    listen 80;
    server_name api.smartjob.top;
    client_max_body_size 32m;

    # agent-gateway (8769):SSE/长任务。去掉 /agent-gw 前缀,关代理缓冲保实时。
    location /agent-gw/ {
        proxy_pass http://127.0.0.1:8769/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }
    # portal-api (8771):账号/鉴权。去掉 /portal 前缀。
    location /portal/ {
        proxy_pass http://127.0.0.1:8771/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    # api-gateway (8767):默认 —— 扩展 WS(/ext/ws)、MCP(/mcp)、命令、admin API。
    location / {
        proxy_pass http://127.0.0.1:8767;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 3600s;
    }
}
```

**`/etc/nginx/sites-available/dashboard.smartjob.top`** —— 整体转发到 admin 容器:

```nginx
server {
    listen 80;
    server_name dashboard.smartjob.top;
    client_max_body_size 32m;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;          # 仪表盘实时事件 /admin/ws
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 3600s;
    }
}
```

启用并重载:

```bash
ln -sf /etc/nginx/sites-available/api.smartjob.top       /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/dashboard.smartjob.top /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### 3.5 HTTPS 证书

```bash
certbot --nginx -d api.smartjob.top -d dashboard.smartjob.top \
  --non-interactive --agree-tos --redirect
```

certbot 会自动改写两个 vhost(追加 `listen 443 ssl` 块 + HTTP→HTTPS 跳转),并注册
后台自动续期。首次在本机用 certbot 时需额外加 `-m you@example.com` 注册账号。

### 3.6 验证

```bash
curl https://api.smartjob.top/health                # → {"status":"ok","service":"job-api-gateway"}
curl https://api.smartjob.top/agent-gw/admin/status # → {"ok":true,"service":"job-agent-gateway",...}
curl https://api.smartjob.top/portal/health         # → {"ok":true,"service":"job-portal-api","db":"up"}
curl -I https://dashboard.smartjob.top/             # → 200,管理后台 SPA
```

## 4. `api.smartjob.top` 路由表

| 路径前缀 | 转发到 | 说明 |
|---|---|---|
| `/agent-gw/*` | `127.0.0.1:8769`(去前缀) | agent-gateway。例:`/agent-gw/agent/sse` → 其 `/agent/sse` |
| `/portal/*` | `127.0.0.1:8771`(去前缀) | portal-api。例:`/portal/.well-known/jwks.json` → 其 `/.well-known/jwks.json` |
| 其余 | `127.0.0.1:8767` | api-gateway。扩展 WS `/ext/ws`、MCP `/mcp`、命令、admin API |

`dashboard.smartjob.top` 整体转发到 admin 容器(8081);容器内置 nginx 再二次路由
`/admin`、`/agent-gw/`、`/portal/` 到三个网关(走 Docker 内网,见 `packages/admin/nginx.conf`)。

## 5. 日常运维

```bash
cd /opt/smart-job
docker compose ps                       # 容器状态
docker compose logs -f --tail=100 api-gateway   # 跟踪某服务日志
docker compose restart agent-gateway    # 重启单个服务
docker compose down                     # 停整个栈(数据在 pgdata 卷,不丢)
```

### 更新代码并重新部署

```bash
# 本地仓库根目录:
rsync -az --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='node_modules' \
  --exclude='dist' --exclude='.next' --exclude='.pytest_cache' --exclude='logs' \
  packages docker docker-compose.yml .env \
  root@smartjob.top:/opt/smart-job/
# 服务器:
cd /opt/smart-job && docker compose up -d --build
```

`docker-compose.override.yml` 只在服务器上、不在仓库内,rsync 不会覆盖或删除它。

## 6. 安全须知

- 六个容器端口只绑 `127.0.0.1`,公网只能经 nginx + HTTPS 到达;数据库 / Redis
  不对外暴露。
- **扩展 WS / SSE 已启用强制鉴权**(配置见 §3.2 override 的 `EXT_AUTH_REQUIRED` /
  `AGENT_AUTH_REQUIRED`)—— 未带合法 portal JWT 的客户端一律拒绝:
  - `wss://api.smartjob.top/ext/ws` 握手须带 `?token=<portal access token>`,否则
    `close(4401)`;扩展登录后会自动带上。
  - `/agent-gw/agent/sse*` 须带 `Authorization: Bearer <portal access token>`,否则 401。
  - 网关用 `PORTAL_JWKS_URL`(Docker 内网指向 portal-api)拉公钥本地 RS256 验签;
    `JWT_ISSUER` / `JWT_AUDIENCE` 对齐 portal-api 实际签发值。
  - admin 后台 `/admin/*` 路由不受影响(走管理后台账号体系的会话 cookie 鉴权)。
  - 临时关闭:把 override 里两个 `*_AUTH_REQUIRED` 改 `"false"` 后 `docker compose up -d`。
- `ADMIN_PASSWORD` 已设 —— 启用管理后台鉴权,并在首次启动时播种首个账号 `admin`;
  之后登录 / 改密 / 增删账号都在后台进行(`admin_users` 表)。
- `.env` 含 `OPENROUTER_API_KEY` 等密钥,仅存于服务器 `/opt/smart-job/.env`,
  注意文件权限,勿提交进仓库。
