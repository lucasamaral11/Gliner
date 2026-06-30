#!/bin/sh

# Inicializa o Ollama em segundo plano limpando saídas de log desnecessárias
ollama serve > /dev/null 2>&1 &

# Loop de segurança aguardando a porta local do Ollama abrir
echo "Aguardando o motor do Ollama ligar..."
while ! curl -s http://127.0.0 > /dev/null; do
    sleep 1
done

# Baixa o modelo Qwen quantizado de 350MB comprimido de forma rápida
echo "Baixando o modelo Qwen-2.5-Coder-0.5B de alto desempenho..."
ollama pull qwen2.5-coder:0.5b

# Dispara o servidor FastAPI em processo único para evitar lentidão e concorrência na CPU
echo "Iniciando a API FastAPI com alta performance..."
exec uvicorn main:app --host 0.0.0.0 --port 8800 --workers 1 --loop uvloop --http httptools
