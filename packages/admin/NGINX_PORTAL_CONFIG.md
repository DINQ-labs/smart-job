# Nginx 配置说明 - Portal 用户管理

## 问题

管理后台新增的 **"账户用户"** 页面 (`/portal-users`) 需要调用 `job-portal-api` 的 admin 端点。

## 解决方案

需要在 nginx 配置中添加 `/portal` 路径代理到 `job-portal-api:8771`。

### Nginx 配置示例

```nginx
# 在现有的 server 块中添加
location /portal/ {
    proxy_pass http://127.0.0.1:8771/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### 完整配置示例

```nginx
server {
    listen 443 ssl http2;
    server_name job.joyhouse.chat;

    # ... SSL 配置 ...

    # 管理后台静态文件
    location / {
        root /opt/job-api-admin/dist;
        try_files $uri $uri/ /index.html;
    }

    # job-api-gateway 代理
    location /admin/ {
        proxy_pass http://127.0.0.1:8767/;
        # ...
    }

    # job-agent-gateway 代理
    location /agent-gw/ {
        proxy_pass http://127.0.0.1:8769/;
        # ...
    }

    # ★ 新增：job-portal-api 代理
    location /portal/ {
        proxy_pass http://127.0.0.1:8771/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 重载 nginx

```bash
# 测试配置
sudo nginx -t

# 重载配置
sudo nginx -s reload
```

## 部署步骤

1. 确保 `job-portal-api` 服务运行在 `8771` 端口
2. 添加上述 nginx 配置
3. 重载 nginx
4. 重新部署管理后台：`cd /opt/job-api-admin && ./deploy.sh`
5. 访问 `https://job.joyhouse.chat/portal-users` 测试

## API 端点说明

`job-portal-api` 提供的 admin 端点：

- `GET /portal/admin/users` - 用户列表
- `GET /portal/admin/users/{id}` - 用户详情
- `PATCH /portal/admin/users/{id}/role` - 修改角色
- `PATCH /portal/admin/users/{id}/disabled` - 启用/禁用
- `GET /portal/admin/users/{id}/identities` - 登录方式列表
- `DELETE /portal/admin/users/{id}/identities/{id}` - 解绑身份
- `GET /portal/admin/users/{id}/events` - 审计日志
- `GET /portal/admin/users/{id}/tokens` - 设备列表
- `DELETE /portal/admin/tokens/{id}` - 撤销 token
- `DELETE /portal/admin/users/{id}/tokens` - 撤销所有 token
