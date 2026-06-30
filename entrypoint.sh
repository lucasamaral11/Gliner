#!/bin/sh

# Inicia o servidor do Ollama em segundo plano
ollama serve &

# Aguarda 5 segundos para o Ollama inicializar completamente
sleep 5

# Baixa e pré-carrega o modelo Qwen de 350MB de forma silenciosa
ollama run qwen2.5-coder:0.5b "oi"

# Inicia a API FastAPI com alta performance usando apenas 1 worker para evitar gargalo
exec uvicorn main:app --host 0.0.0.0 --port 8800 --workers 1 --loop uvloop --http httptools
