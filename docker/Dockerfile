FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium --with-deps || \
    (sleep 5 && playwright install chromium --with-deps) || \
    (sleep 15 && playwright install chromium --with-deps)

COPY . .

ENV PYTHONPATH=/app/src
ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 创建运行时目录
RUN mkdir -p /app/output /app/logs /app/cache /app/input /app/config

# ENTRYPOINT + CMD 模式：默认运行 daily-run，可通过 docker run image --topics-only 覆盖参数
ENTRYPOINT ["python", "main.py"]
CMD ["--daily-run"]
