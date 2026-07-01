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
    Camada de proteção que separa links, valida preços e remove cupons falsos baseados em IDs de URL
    """
    # 1. FILTRO DE LINKS (Mantém apenas links válidos de lojas)
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    termos_banidos = ["t.me", "whatsapp", "mastertechjr", "youtube", "instagram", "facebook", "linktr.ee"]
    links_lojas = [l for l in links_no_texto if not any(termo in l.lower() for termo in termos_banidos)]
    
    dados_json["link_cupom"] = None
    dados_json["link_produto"] = None

    if links_lojas:
        dados_json["link_produto"] = links_lojas[0] # O primeiro link de loja costuma ser o do produto
        for link in links_lojas:
            for linha in texto_bruto.split('\n'):
                if link in linha:
                    if "cupom" in linha.lower() or "resgate" in linha.lower():
                        dados_json["link_cupom"] = link
                    elif "compre aqui" in linha.lower() or "🔥por" in linha.lower() or "link do produto" in linha.lower():
                        dados_json["link_produto"] = link

        # Garantias de preenchimento caso a varredura falhe
        if not dados_json["link_produto"] and links_lojas:
            dados_json["link_produto"] = links_lojas[0]
        if not dados_json["link_cupom"] and len(links_lojas) > 1 and links_lojas[1] != dados_json["link_produto"]:
            dados_json["link_cupom"] = links_lojas[1]

    # 2. ESCUDO DE PROTEÇÃO CONTRA PREÇOS INVERTIDOS OU APAGADOS PELA IA
    if dados_json.get("preco_anterior") is None:
        linha_de = None
        linha_por = None
        
        for linha in texto_bruto.split('\n'):
            if re.search(r'\bde\b\s*:?\s*r?\$?', linha, re.IGNORECASE):
                linha_de = linha
            if re.search(r'\b(?:por|💵)\b\s*:?\s*r?\$?', linha, re.IGNORECASE):
                linha_por = linha

        if linha_de and linha_por:
            match_de = re.search(r'(\d+(?:[\.,]\d{3})*(?:[\.,]\d{2})?)', linha_de)
            match_por = re.search(r'(\d+(?:[\.,]\d{3})*(?:[\.,]\d{2})?)', line_por if 'line_por' in locals() else linha_por)
            
            if match_de and match_por:
                dados_json["preco_anterior"] = f"R$ {match_de.group(1).strip()}"
                dados_json["preco_atual"] = f"R$ {match_por.group(1).strip()}"

    # 3. GARANTIA MONETÁRIA
    for campo in ["preco_atual", "preco_anterior"]:
        valor = dados_json.get(campo)
        if valor is None or str(valor).strip().lower() in ["null", "none", ""]:
            dados_json[campo] = None
        elif "R$" not in str(valor):
            dados_json[campo] = f"R$ {str(valor).replace('R$', '').strip()}"

    if dados_json.get("preco_anterior") == dados_json.get("preco_atual"):
        dados_json["preco_anterior"] = None

    # 4. BLINDAGEM DO CUPOM (Evita que a IA extraia IDs finais de links como se fossem cupons)
    cupom_ia = str(dados_json.get("cupom", "") or "").strip()
    if cupom_ia and cupom_ia.lower() != "null":
        # Se o cupom inventado estiver contido dentro de qualquer link do texto bruto, anula ele
        if cupom_ia in texto_bruto and any(cupom_ia in l for l in links_no_texto):
            dados_json["cupom"] = None
    else:
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
        "2. Se não houver um código de cupom de texto escrito (ex: VALE20), deixe a chave 'cupom' como null.\n"
        "3. Responda apenas o JSON puro."
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
