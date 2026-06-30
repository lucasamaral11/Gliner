import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI(title="Qwen Connected API")

# Mantém a URL oficial do seu Ollama externo
OLLAMA_URL = "http://187.127.36.194:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:0.5b"

class TextoPayload(BaseModel):
    texto: str

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas cirúrgico e preciso. Responda APENAS com um objeto JSON válido no formato exato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n\n"
        "Regras estritas de extração:\n"
        "1. NOME DO PRODUTO: Capture o nome COMPLETO do produto comercial com modelo e marca (ex: 'Smart TV 32” Britânia B32CRA HD Wi-Fi'). Nunca corte ou resuma o nome.\n"
        "2. PREÇOS: Adicione sempre o prefixo 'R\$' nos preços (ex: 'R\$ 673'). Se na mensagem houver apenas UM preço solto, ele é o 'preco_atual'. O 'preco_anterior' deve ser estritamente 'null' se não houver um preço mais alto explicitamente listado antes (nunca repita o mesmo valor nos dois campos).\n"
        "3. CUPOM: Extraia apenas o código do cupom se houver. Se o texto apenas disser para resgatar no link, deixe 'cupom' como null.\n"
        "4. LINKS DE CUPOM: Se houver um link específico para resgatar ou coletar o cupom (ex: 'Resgate Cupom... aqui: link'), coloque este link obrigatoriamente no campo 'link_cupom'. Não o misture com o link de compra.\n"
        "5. LINK DO PRODUTO: Identifique o link principal de compra do produto anunciado (geralmente o último ou o que está com o emoji de link) e coloque em 'link_produto'. Ignore links de redes sociais ou convites como Telegram/WhatsApp.\n"
        "6. FORMATO: Responda APENAS o JSON puro. Não use blocos de código markdown ```json ou introduções."
    )

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": f"Texto da oferta:\n{texto}"}
        ],
        "stream": False,
        "format": "json"
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(OLLAMA_URL, json=payload_dados)
            if response.status_code != 200:
                raise Exception(f"Ollame retornou status {response.status_code}: {response.text}")
                
            dados = response.json()
            resposta_ia = dados.get("message", {}).get("content", "").strip()
            resposta_limpa = re.sub(r"```json\s*|```", "", resposta_ia).strip()
            return json.loads(resposta_limpa)
            
        except json.JSONDecodeError:
            raise Exception(f"A IA não retornou um JSON válido. Resposta bruta: {resposta_ia}")
        except Exception as e:
            raise Exception(f"Falha na comunicação com o Ollama: {str(e)}")

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
