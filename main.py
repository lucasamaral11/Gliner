import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI(title="Qwen Connected API")

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

    # RECONEXÃO: Substitua 'SEU_CONTAINER_OLLAMA_AQUI' pelo nome do seu serviço Ollama existente
    ollama_host = "SEU_CONTAINER_OLLAMA_AQUI" 

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"http://{ollama_host}:11434/api/generate",
                json={
                    "model": "qwen2.5-coder:0.5b",
                    "prompt": f"{prompt_sistema}\n\nTexto da oferta:\n{texto}",
                    "stream": False,
                    "format": "json"
                }
            )
            dados = response.json()
            resposta_ia = dados.get("response", "").strip()
            resposta_limpa = re.sub(r"```json\s*|```", "", resposta_ia).strip()
            return json.loads(resposta_limpa)
        except Exception as e:
            raise Exception(f"Erro ao conectar no Ollama compartilhado: {str(e)}")

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
