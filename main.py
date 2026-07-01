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

def organizar_links_e_precos(dados_json, texto_bruto):
    """
    Camada de proteção em Python que corrige falhas de atenção do modelo de IA
    """
    # 1. FILTRO DE LINKS (Mantém apenas links válidos de lojas)
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    termos_banidos = ["t.me", "whatsapp", "mastertechjr", "youtube", "instagram", "facebook", "linktr.ee"]
    links_lojas = [l for l in links_no_texto if not any(termo in l.lower() for termo in termos_banidos)]
    
    dados_json["link_cupom"] = None
    dados_json["link_produto"] = None

    if links_lojas:
        dados_json["link_produto"] = links_lojas[0]
        for link in links_lojas:
            for linha in texto_bruto.split('\n'):
                if link in linha and ("cupom" in linha.lower() or "resgate" in linha.lower()):
                    dados_json["link_cupom"] = link
                    if dados_json["link_produto"] == link and len(links_lojas) > 1:
                        dados_json["link_produto"] = links_lojas[1]

    # 2. ESCUDO DE PROTEÇÃO CONTRA PREÇOS INVERTIDOS OU APAGADOS PELA IA
    # Se a IA apagou o preço anterior (deixou null), mas o texto bruto claramente tem a estrutura "De: ... Por:"
    if dados_json.get("preco_anterior") is None:
        # Procura por linhas contendo "De:" e "Por:" ou símbolos monetários próximos
        linha_de = None
        linha_por = None
        
        for linha in texto_bruto.split('\n'):
            if re.search(r'\bde\b\s*:?\s*r?\$?', linha, re.IGNORECASE):
                linha_de = linha
            if re.search(r'\b(?:por|💵)\b\s*:?\s*r?\$?', linha, re.IGNORECASE):
                linha_por = linha

        if linha_de and linha_por:
            # Captura o número complexo com ponto/vírgula de cada uma das linhas encontradas
            match_de = re.search(r'(\d+(?:[\.,]\d{3})*(?:[\.,]\d{2})?)', linha_de)
            match_por = re.search(r'(\d+(?:[\.,]\d{3})*(?:[\.,]\d{2})?)', linha_por)
            
            if match_de and match_por:
                dados_json["preco_anterior"] = f"R$ {match_de.group(1).strip()}"
                dados_json["preco_atual"] = f"R$ {match_por.group(1).strip()}"

    # 3. GARANTIA MONETÁRIA (Adiciona R$ se faltar e limpa falsos nulos em formato de string)
    for campo in ["preco_atual", "preco_anterior"]:
        valor = dados_json.get(campo)
        if valor is None or str(valor).strip().lower() in ["null", "none", ""]:
            dados_json[campo] = None
        elif "R$" not in str(valor):
            dados_json[campo] = f"R$ {str(valor).replace('R$', '').strip()}"

    # Se a IA errou e colocou os mesmos valores nos dois campos, anula o anterior
    if dados_json.get("preco_anterior") == dados_json.get("preco_atual"):
        dados_json["preco_anterior"] = None

    # Limpeza final do Cupom
    if str(dados_json.get("cupom")).strip().lower() in ["null", "none", ""]:
        dados_json["cupom"] = None

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
        "Regras:\n"
        "1. Capture o nome comercial completo do produto.\n"
        "2. Identifique corretamente o preço de promoção ('Por:') e o preço original ('De:').\n"
        "3. Não mude o valor dos números. Responda apenas o JSON puro."
    )

    exemplo_1_user = "🔥 Controle 8BitDo\n\nDe: R$ 349,86\n💵 Por: R$ 241\n\n🎟 Cupom: BRAE3\n\n🔗 https://aliexpress.com"
    exemplo_1_assistant = {
        "nome_produto": "Controle 8BitDo",
        "preco_anterior": "R$ 349,86",
        "preco_atual": "R$ 241",
        "cupom": "BRAE3",
        "link_cupom": None,
        "link_produto": "https://aliexpress.com"
    }

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": exemplo_1_user},
            {"role": "assistant", "content": json.dumps(exemplo_1_assistant)},
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
            
            # Deixa a engenharia de software Python validar e consertar os erros cometidos pelo LLM
            json_corrigido = organizar_links_e_precos(json_puro, texto)
            
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
