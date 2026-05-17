# 构建上下文是 ../（packages/）—— 同级的 job_common 必须一起拷进镜像：
# server.py 启动时把 repo-root 加入 sys.path，再 `from job_common.* import ...`。
FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用 layer 缓存）
COPY api-gateway/requirements.txt ./api-gateway/requirements.txt
RUN pip install --no-cache-dir -r api-gateway/requirements.txt

# 共享包与服务代码（保持同级目录关系）
COPY job_common ./job_common
COPY api-gateway ./api-gateway

WORKDIR /app/api-gateway
RUN mkdir -p logs

EXPOSE 8767
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://127.0.0.1:8767/status', timeout=5); r.raise_for_status()" || exit 1

CMD ["python", "server.py", "--http"]
