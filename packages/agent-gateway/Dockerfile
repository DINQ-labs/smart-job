# 构建上下文是 ../（packages/）—— 同级的 job_common 必须一起拷进镜像：
# server.py 启动时把 repo-root 加入 sys.path，再 `from job_common.* import ...`。
FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用 layer 缓存）
COPY agent-gateway/requirements.txt ./agent-gateway/requirements.txt
RUN pip install --no-cache-dir -r agent-gateway/requirements.txt

# 共享包与服务代码（保持同级目录关系）
COPY job_common ./job_common
COPY agent-gateway ./agent-gateway

WORKDIR /app/agent-gateway
RUN mkdir -p logs uploads/resumes

EXPOSE 8769
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://127.0.0.1:8769/admin/status', timeout=5); r.raise_for_status()" || exit 1

# 多 worker 由 WORKER_COUNT 控制（默认 1）；SSE 无状态、Redis 存会话，可多 worker。
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port 8769 --workers ${WORKER_COUNT:-1}"]
