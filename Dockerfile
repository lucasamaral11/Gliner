FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx uvloop httptools pydantic

COPY main.py .

EXPOSE 8800

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8800", "--workers", "1", "--loop", "uvloop", "--http", "httptools"]
