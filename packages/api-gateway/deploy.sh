#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${DEPLOY_HOST:?请设置环境变量 DEPLOY_HOST, 例如 root@1.2.3.4}"
REMOTE_DIR="/opt/job-api-gateway"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> 同步代码到 ${REMOTE_HOST}:${REMOTE_DIR} ..."
# 注意：static/ 由 deploy/package-ext.sh 维护（zip + version.json），
# 这里必须 exclude，否则 --delete 会把扩展包冲掉。
rsync -avz --delete \
    --exclude '.env' \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude 'logs/' \
    --exclude '.git/' \
    --exclude '*.pyc' \
    --exclude '*.db' \
    --exclude '*.db-*' \
    --exclude 'uploads/' \
    --exclude 'tests/' \
    --exclude '.pytest_cache/' \
    --exclude 'static/' \
    "$LOCAL_DIR/" "${REMOTE_HOST}:${REMOTE_DIR}/"

# server.py 把 repo-root 加入 sys.path 后 import job_common — parent repo 的
# 共享包必须同步到 /opt/job_common/,否则 commands.py risk_signals 路径 500。
echo "==> 同步 job_common 共享包到 ${REMOTE_HOST}:/opt/job_common/ ..."
rsync -avz --delete \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    "${LOCAL_DIR}/../job_common/" "${REMOTE_HOST}:/opt/job_common/"

echo "==> 安装/更新依赖 ..."
ssh "$REMOTE_HOST" "cd ${REMOTE_DIR} && venv/bin/pip install -q -r requirements.txt"

echo "==> 重启 job-api-gateway 服务 ..."
ssh "$REMOTE_HOST" "systemctl restart job-api-gateway"

echo "==> 等待服务启动 ..."
sleep 3

echo "==> 检查服务状态 ..."
ssh "$REMOTE_HOST" "systemctl is-active job-api-gateway && systemctl status job-api-gateway --no-pager -l | tail -10"

if [[ "${WITH_EXT:-0}" == "1" ]]; then
    echo "==> 同时打包并发布扩展（WITH_EXT=1）..."
    bash "$LOCAL_DIR/../deploy/package-ext.sh"
fi

echo "==> 部署完成!"
