FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instala pacotes necessários para rodar o Qwen
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    transformers \
    torch \
    accelerate \
    uvloop \
    httptools

COPY main.py .

EXPOSE 8800

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8800", "--workers", "4", "--loop", "uvloop", "--http", "httptools"]
