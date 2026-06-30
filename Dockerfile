FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx uvloop httptools

COPY main.py .

EXPOSE 8800

# Executa diretamente o Uvicorn de forma limpa na porta 8800
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8800", "--workers", "1", "--loop", "uvloop", "--http", "httptools"]
