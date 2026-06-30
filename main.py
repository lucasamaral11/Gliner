import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI(title="Qwen Connected API")

# URL oficial que você já usa para fazer as chamadas
OLLAMA_URL = "http://187.127.36.194:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:0.5b"

class TextoPayload(BaseModel):
    texto: str

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas preciso. Responda APENAS com um objeto JSON válido no formato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n"
        "Regras estritas:\n"
        "1. NUNCA traduza termos de tecnologia. 'Notebook asus' deve continuar exatamente como 'Notebook asus'. Não mude para 'Caderno'.\n"
        "2. Responda APENAS o JSON puro. Não use blocos de código ```json ou explicações."
    )

    # Monta a estrutura correta para o endpoint /api/chat
    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": f"Texto da oferta:\n{texto}"}
        ],
        "stream": False,
        "format": "json" # Força o Ollama a estruturar a saída em JSON válido
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Envia a requisição direto para o IP informado
            response = await client.post(OLLAMA_URL, json=payload_dados)
            
            if response.status_code != 200:
                raise Exception(f"Ollame retornou status {response.status_code}: {response.text}")
                
            dados = response.json()
            
            # No endpoint /api/chat, a resposta da IA fica dentro de ['message']['content']
            resposta_ia = dados.get("message", {}).get("content", "").strip()
            
            # Remove marcações de código markdown por segurança
            resposta_limpa = re.sub(r"```json\s*|```", "", resposta_ia).strip()
            return json.loads(resposta_limpa)
            
        except json.JSONDecodeError:
            raise Exception(f"A IA não retornou um JSON válido. Resposta bruta: {resposta_ia}")
        except Exception as e:
            raise Exception(f"Falha na comunicação com o Ollama no IP informado: {str(e)}")

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
