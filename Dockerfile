FROM python:3.11-slim

WORKDIR /app

# Instala curl e dependências básicas
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instala o Ollama oficial dentro do container
RUN curl -fsSL https://ollama.com | sh

# Instala bibliotecas leves do Python
RUN pip install --no-cache-dir fastapi uvicorn httpx uvloop httptools

COPY main.py .

EXPOSE 8800

# Inicializa o Ollama em segundo plano, baixa o Qwen de 350MB comprimido e liga a API FastAPI na porta 8800 com 1 único worker estável
CMD ["sh", "-c", "ollama serve & sleep 5 && ollama run qwen2.5-coder:0.5b 'oi' && uvicorn main:app --host 0.0.0.0 --port 8800 --workers 1 --loop uvloop --http httptools"]
