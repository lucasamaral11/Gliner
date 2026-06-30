FROM python:3.11-slim

WORKDIR /app

# Instala curl, ca-certificates e dependências que o script do Ollama exige (procps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Instala o Ollama oficial usando a URL correta do script de instalação
RUN curl -fsSL https://ollama.com | sh

# Instala as bibliotecas Python necessárias
RUN pip install --no-cache-dir fastapi uvicorn httpx uvloop httptools

# Copia os arquivos do projeto para o container
COPY main.py .
COPY entrypoint.sh .

# Garante a permissão de execução para o script de inicialização
RUN chmod +x entrypoint.sh

EXPOSE 8800

# Executa o script que gerencia os serviços juntos
CMD ["./entrypoint.sh"]
