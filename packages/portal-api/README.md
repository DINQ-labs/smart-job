# job-portal-api

DingQ 统一身份服务（Identity Provider）。FastAPI + asyncpg + Postgres。

**职责**：颁发 RS256 JWT，让浏览器扩展、官方网站、两个 gateway 共用同一套身份。

**端口**：8771（开发）。生产经 nginx 反代到 `https://api.job.joyhouse.chat`。

## 当前切片（扩展直连优先）

| 端点 | 说明 |
|---|---|
| `POST /auth/register` | 邮箱 + 密码注册 → 发 6 位码 |
| `POST /auth/verify-email` | 码 → 激活 + 发 access/refresh |
| `POST /auth/resend-code` | 重发码（60s 冷却 + 防枚举） |
| `POST /auth/login` | 邮密登录 → access/refresh |
| `POST /auth/refresh` | refresh 换新对（旋转 + 旧失效） |
| `POST /auth/logout` | 撤销当前 refresh |
| `GET  /auth/me` | Bearer → 当前用户 |
| `GET  /.well-known/jwks.json` | 公钥（gateway 验签） |
| `GET  /health` | 健康检查 |

**未实现**：Google / WeChat OAuth、密码重置、邮箱变更、删号、设备列表、Stripe。

## 关系

- `job-portal-api`（本项目）：**Python 后端**，所有身份业务的真相
- `job-portal/`：Next.js 营销官网（当前独立有套自己的 auth，**后续会重构为调本服务**；今天先保持现状）
- `job-agent-gateway` / `job-api-gateway`：后续加 JWT 中间件，拉本服务的 JWKS 验签

## 本地开发

### 前置

- Python 3.10+
- Postgres ≥ 13（smart-job 共享实例即可；启动时会自动 `CREATE SCHEMA identity` + 建表）
- 可选：Resend 账号；不配置时开发模式会把验证码打到 stdout

### 启动

```bash
cd /home/laohan/workspace/smart-job/job-portal-api

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env.local
$EDITOR .env.local   # 至少填 DATABASE_URL

uvicorn server:app --port 8771 --reload
```

首次启动会在 `./keys/jwt-private.pem` 落地一个 RSA-2048 私钥。**这把私钥 = 颁发用户身份的根权限**，生产环境必须：

- 用 env `JWT_PRIVATE_KEY_PATH` 指到独立目录（不在 repo 内）
- 文件权限 `chmod 600`
- 备份；同时计划季度轮换（轮换流程：生成新 kid → 双签发期 → 下线旧 kid）

### 不配 Resend 的开发流

API key 留空时所有验证码会以
```
[dev:verification] code=123456 email=foo@bar.com expires_in=10m purpose=signup
```
形式打到 server 终端。注册 → 验证完整闭环可走，无需真邮箱。

## 端到端冒烟（curl）

```bash
# 1) 注册
curl -X POST localhost:8771/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"t1@test.com","password":"hello1234"}'
# → { "verification_id": "...", "email": "t1@test.com", "expires_at": "..." }
#   server 终端打印 [dev:verification] code=123456 ...

# 2) 验证
curl -X POST localhost:8771/auth/verify-email \
  -H 'Content-Type: application/json' \
  -d '{"verification_id":"...","code":"123456"}'
# → { "user": {...}, "tokens": { "access_token": "eyJ...", "refresh_token": "...", ... } }

# 3) /me
curl localhost:8771/auth/me -H 'Authorization: Bearer eyJ...'
# → { "id": "...", "email": "t1@test.com", "email_verified_at": "..." }

# 4) refresh
curl -X POST localhost:8771/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"..."}'
# → 新的 access + refresh（旧 refresh 立刻失效）

# 5) JWKS
curl localhost:8771/.well-known/jwks.json
```

## 浏览器扩展接入要点

- HTTP 客户端：`fetch('https://api.job.joyhouse.chat/auth/login', ...)`
- 存 token：`chrome.storage.local.set({ auth: { access, refresh, access_exp, refresh_exp } })`
- 续期：`chrome.alarms` 在 access 过期前 60s 触发 `/auth/refresh`
- 业务请求：`Authorization: Bearer <access>`
- 401 时尝试一次 refresh，再失败则回到登录页

## 部署

待补。预期 systemd unit 跑 `uvicorn server:app --workers 2 --port 8771`，nginx 反代 `api.job.joyhouse.chat → 127.0.0.1:8771`。
