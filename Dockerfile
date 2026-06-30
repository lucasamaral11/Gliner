FROM python:3.11-slim

WORKDIR /app

# Instala dependências essenciais do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instala pacotes Python essenciais
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    gliner \
    torch \
    uvloop \
    httptools

# Copia o script para dentro do container
COPY main.py .

# Expõe a nova porta configurada
EXPOSE 8800

# Executa o uvicorn apontando para a porta 8800
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8800", "--workers", "4", "--loop", "uvloop", "--http", "httptools"]
