# portal-api 自包含（不依赖 job_common），构建上下文就是本目录。
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8771
# server.py 只暴露 FastAPI app，不含 __main__，用 uvicorn 拉起。
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8771"]
