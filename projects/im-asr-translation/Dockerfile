FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 服务代码
COPY app/ /app/app/

# 环境变量（可在 docker run -e 覆盖）
ENV HOST=0.0.0.0 \
    PORT=5031 \
    UVICORN_WORKERS=1 \
    LOG_LEVEL=INFO \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRANSLATE_VLLM_HOST=10.224.231.142 \
    TRANSLATE_VLLM_PORT=9898 \
    WHISPER_HOST=10.138.0.46 \
    WHISPER_PORT=8301

EXPOSE 5031

CMD ["sh", "-c", "python -m uvicorn app.main:app --host ${HOST} --port ${PORT} --workers ${UVICORN_WORKERS}"]
