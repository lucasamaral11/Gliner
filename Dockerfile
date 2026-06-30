FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema necessárias para compilar pacotes Python de IA
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instala as bibliotecas necessárias direto no container
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    gliner \
    torch \
    uvloop \
    httptools

# Copia o código da API para dentro do container
COPY main.py .

# Expõe a porta que configuramos no uvicorn
EXPOSE 8800

# Comando para iniciar a aplicação
CMD ["python", "main.py"]
