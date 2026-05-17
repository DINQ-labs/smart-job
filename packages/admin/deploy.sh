#!/usr/bin/env bash
# Build job-api-admin locally and rsync the dist/ to the remote server.
# Static admin UI — no Python venv, no LLM calls. Assumes the remote has
# nginx (or similar) serving /opt/job-api-admin/dist as static files.
set -euo pipefail

REMOTE_HOST="${DEPLOY_HOST:?请设置环境变量 DEPLOY_HOST, 例如 root@1.2.3.4}"
REMOTE_DIR="/opt/job-api-admin"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="job-api-admin"   # optional systemd unit; skipped if not installed

cd "$LOCAL_DIR"

# pnpm is the repo's package manager. Bail early with a clear message if it's
# missing instead of falling back to npm and producing a wrong lockfile.
if ! command -v pnpm >/dev/null 2>&1; then
    echo "error: pnpm not found on PATH. install with: npm i -g pnpm" >&2
    exit 1
fi

if [ ! -d node_modules ]; then
    echo "==> 首次构建，安装依赖 ..."
    pnpm install --frozen-lockfile
fi

echo "==> 本地构建 dist/ ..."
rm -rf dist
pnpm build

if [ ! -d dist ]; then
    echo "error: build finished but dist/ missing" >&2
    exit 1
fi

echo "==> 同步 dist/ 到 ${REMOTE_HOST}:${REMOTE_DIR}/dist/ ..."
# Only touch dist/ on the remote — don't clobber any source or configs that
# may live under /opt/job-api-admin alongside the built artifacts.
rsync -avz --delete \
    "$LOCAL_DIR/dist/" "${REMOTE_HOST}:${REMOTE_DIR}/dist/"

# Restart the service only if a matching systemd unit exists; nginx-served
# setups don't need a restart, so we don't want to hard-fail there.
echo "==> 检查 ${SERVICE_NAME} systemd 单元 ..."
if ssh "$REMOTE_HOST" "systemctl list-unit-files | grep -q '^${SERVICE_NAME}\\.service'"; then
    echo "==> 重启 ${SERVICE_NAME} ..."
    ssh "$REMOTE_HOST" "systemctl restart ${SERVICE_NAME}"
    sleep 2
    ssh "$REMOTE_HOST" "systemctl is-active ${SERVICE_NAME} && systemctl status ${SERVICE_NAME} --no-pager -l | tail -8"
else
    echo "    未发现 ${SERVICE_NAME}.service — 跳过重启（静态文件由 nginx/caddy 服务）"
fi

echo "==> 部署完成！访问后台查看新功能（tokens/成本列）。"
