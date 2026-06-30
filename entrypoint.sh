#!/bin/sh

# Inicia o servidor do Ollama em segundo plano e redireciona os logs
ollama serve > /dev/null 2>&1 &

# Aguarda o Ollama responder na porta local antes de prosseguir
echo "Aguardando o Ollama inicializar..."
until curl -s http://127.0.0 > /dev/null; do
    sleep 1
done

# Baixa e pré-carrega o modelo Qwen de 350MB de forma silenciosa
echo "Baixando o modelo Qwen-2.5-Coder-0.5B..."
ollama pull qwen2.5-coder:0.5b

# Inicia a API FastAPI com alta performance usando apenas 1 worker para evitar gargalo
echo "Iniciando a API FastAPI na porta 8800..."
exec uvicorn main:app --host 0.0.0.0 --port 8800 --workers 1 --loop uvloop --http httptools
