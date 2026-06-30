import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI(title="Qwen Ollama Ultra-Fast API")
executor = ThreadPoolExecutor(max_workers=1)

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

    # Conecta no serviço do Ollama que estará rodando no mesmo container
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://127.0.0",
            json={
                "model": "qwen2.5-coder:0.5b",
                "prompt": f"{prompt_sistema}\n\nTexto da oferta:\n{texto}",
                "stream": False,
                "format": "json" # Força o Ollama a travar a saída em JSON perfeito
            }
        )
        
        if response.status_code != 200:
            raise Exception("Erro ao chamar o Ollama interno")
            
        dados = response.json()
        resposta_ia = dados.get("response", "").strip()
        
        # Limpeza extra por segurança
        resposta_limpa = re.sub(r"```json\s*|```", "", resposta_ia).strip()
        return json.loads(resposta_limpa)

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8800)
