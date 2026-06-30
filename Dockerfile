FROM python:3.11-slim

WORKDIR /app

# Instala curl e dependências básicas do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instala o Ollama oficial dentro do container
RUN curl -fsSL https://ollama.com | sh

# Instala bibliotecas Python necessárias
RUN pip install --no-cache-dir fastapi uvicorn httpx uvloop httptools

# Copia os arquivos do projeto
COPY main.py .
COPY entrypoint.sh .

# Dá permissão de execução para o script de inicialização
RUN chmod +x entrypoint.sh

EXPOSE 8800

# Executa o script que gerencia os dois serviços juntos de forma limpa
CMD ["./entrypoint.sh"]
