FROM python:3.11-slim

WORKDIR /app

# Instala apenas o curl e ca-certificates essenciais
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Baixa o binário oficial e pré-compilado do Ollama para Linux e move para a pasta executável do sistema
RUN curl -L https://ollama.com -o /usr/bin/ollama && \
    chmod +x /usr/bin/ollama

# Instala as bibliotecas de alta performance do Python
RUN pip install --no-cache-dir fastapi uvicorn httpx uvloop httptools

# Copia os scripts do seu repositório
COPY main.py .
COPY entrypoint.sh .

# Garante permissão de execução total para o inicializador
RUN chmod +x entrypoint.sh

EXPOSE 8800

CMD ["./entrypoint.sh"]
