import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import httpx
import json
import re

app = FastAPI(title="Qwen Strict Schema API")

OLLAMA_URL = "http://187.127.36.194:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:1.5b" 

class TextoPayload(BaseModel):
    texto: str

class OfertaEstruturada(BaseModel):
    nome_produto: str
    preco_anterior: Optional[str] = None
    preco_atual: str
    cupom: Optional[str] = None
    link_cupom: Optional[str] = None
    link_produto: str

def organizar_links_e_limpeza(dados_json, texto_bruto):
    """
    Remove links de canais/redes sociais e padroniza campos nulos reais
    """
    # 1. Filtro estrito de Links de Lojas
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    termos_banidos = ["t.me", "whatsapp", "mastertechjr", "youtube", "instagram", "facebook", "linktr.ee"]
    links_lojas = [l for l in links_no_texto if not any(termo in l.lower() for termo in termos_banidos)]
    
    dados_json["link_cupom"] = None
    dados_json["link_produto"] = None

    if links_lojas:
        dados_json["link_produto"] = links_lojas[0] # Define o primeiro link válido como principal
        for link in links_lojas:
            for linha in texto_bruto.split('\n'):
                if link in linha and ("cupom" in linha.lower() or "resgate" in linha.lower()):
                    dados_json["link_cupom"] = link
                    if dados_json["link_produto"] == link and len(links_lojas) > 1:
                        dados_json["link_produto"] = links_lojas[1]

    # 2. Força nulo real (None) onde a IA colocou string textua "null"
    for campo in ["preco_anterior", "cupom", "link_cupom"]:
        if str(dados_json.get(campo)).strip().lower() in ["null", "none", ""]:
            dados_json[campo] = None

    return dados_json

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas do Telegram. Analise o texto fornecido e retorne APENAS um objeto JSON no formato exato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n\n"
        "Regras estritas de Preço:\n"
        "1. Capture o valor completo dos preços exatamente como aparecem no texto, mantendo pontos e vírgulas (ex: '2.483,21' vira 'R$ 2.483,21', '1.262' vira 'R$ 1.262').\n"
        "2. Certifique-se de adicionar sempre o prefixo 'R$ ' com espaço caso ele não exista no texto.\n"
        "3. Se não houver preço anterior, defina 'preco_anterior' como null (sem aspas).\n"
        "4. Responda apenas o JSON puro, sem explicações ou markdown."
    )

    # EXEMPLOS DE APRENDIZADO (Few-Shot): Ensinamos a IA a lidar com pontos e milhares
    exemplo_1_user = "🔥 Controle 8BitDo\n\nDe: R$ 349,86\n💵 Por: R$ 241\n\n🎟 Cupom: BRAE3\n\n🔗 https://aliexpress.com"
    exemplo_1_assistant = {
        "nome_produto": "Controle 8BitDo",
        "preco_anterior": "R$ 349,86",
        "preco_atual": "R$ 241",
        "cupom": "BRAE3",
        "link_cupom": None,
        "link_produto": "https://aliexpress.com"
    }

    exemplo_2_user = "🔥 Projetor ThundeaL\n\nDe: R$ 2.483,21\n💵 Por: R$ 1.262\n\n🎟 Cupom: MASTERJR\n\n🔗 https://aliexpress.com"
    exemplo_2_assistant = {
        "nome_produto": "Projetor ThundeaL",
        "preco_anterior": "R$ 2.483,21",
        "preco_atual": "R$ 1.262",
        "cupom": "MASTERJR",
        "link_cupom": None,
        "link_produto": "https://aliexpress.com"
    }

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            # Injeção dos exemplos de calibração de preços
            {"role": "user", "content": exemplo_1_user},
            {"role": "assistant", "content": json.dumps(exemplo_1_assistant)},
            {"role": "user", "content": exemplo_2_user},
            {"role": "assistant", "content": json.dumps(exemplo_2_assistant)},
            # Envio do texto real recebido pelo n8n
            {"role": "user", "content": f"Texto da oferta:\n{texto}"}
        ],
        "stream": False,
        "format": "json"
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(OLLAMA_URL, json=payload_dados)
            dados = response.json()
            resposta_ia = dados.get("message", {}).get("content", "").strip()
            resposta_limpa = re.sub(r"```json\s*|```", "", resposta_ia).strip()
            
            json_puro = json.loads(resposta_limpa)
            
            # Deixa o Python arrumar apenas os links e os nulos
            json_corrigido = organizar_links_e_limpeza(json_puro, texto)
            
            oferta_validada = OfertaEstruturada(**json_corrigido)
            return oferta_validada.model_dump()
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
