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

def ajustar_regras_telegram(dados_json, texto_bruto):
    """
    Ajusta o nome do produto caso a IA pegue apenas o bordão da oferta
    """
    nome_capturado = dados_json.get("nome_produto", "").strip()
    linhas = [l.strip() for l in texto_bruto.split('\n') if l.strip()]
    
    # Se o nome capturado for muito curto ou estiver idêntico à primeira linha da mensagem
    if len(linhas) > 1 and (len(nome_capturado.split()) <= 3 or nome_capturado.lower() in linhas[0].lower()):
        # Varre as primeiras linhas procurando palavras-chave de produtos comerciais
        palavras_chave = ["tênis", "smart", "tv", "notebook", "fone", "caixa", "placa", "processador", "monitor", "smartphone", "iphone"]
        for linha in linhas[:3]:
            if any(p in linha.lower() for p in palavras_chave):
                dados_json["nome_produto"] = linha
                break
                
    # Organização de Links
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    links_validos = [l for l in links_no_texto if "t.me" not in l and "whatsapp" not in l]
    
    dados_json["link_cupom"] = None
    dados_json["link_produto"] = None

    if len(links_validos) == 1:
        dados_json["link_produto"] = links_validos[0]
    elif len(links_validos) >= 2:
        for link in links_validos:
            for linha in texto_bruto.split('\n'):
                if link in linha:
                    if "cupom" in linha.lower() or "resgate" in linha.lower():
                        dados_json["link_cupom"] = link
                    else:
                        dados_json["link_produto"] = link

        if not dados_json["link_produto"] and links_validos:
            dados_json["link_produto"] = links_validos[-1]
            
    return dados_json

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas para o Telegram. Analise o texto e responda APENAS com um objeto JSON no formato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n\n"
        "Regra Estrita de Nome:\n"
        "Ignore bordões ou chamadas de efeito na primeira linha (como 'CASUALZINHO DA PUMA', 'ESTOUROU', 'IMPERDÍVEL').\n"
        "Capture sempre o nome real e comercial do produto que possui marca e descrição (ex: 'Tênis Casual Masculino E Feminino Up Puma (34 a 43)')."
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
            dados = response.json()
            resposta_ia = dados.get("message", {}).get("content", "").strip()
            resposta_limpa = re.sub(r"```json\s*|```", "", resposta_ia).strip()
            
            json_puro = json.loads(resposta_limpa)
            
            # Aplica o filtro híbrido do Telegram
            json_corrigido = ajustar_regras_telegram(json_puro, texto)
            
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
