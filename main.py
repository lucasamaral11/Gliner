import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import httpx
import json
import re

app = FastAPI(title="Qwen Strict Schema API")

OLLAMA_URL = "http://ollama-e10mgfwnydotbjgl9upgsunt:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:1.5b" 

class TextoPayload(BaseModel):
    texto: str

class OfertaEstruturada(BaseModel):
    nome_produto: str
    preco_anterior: Optional[str] = None
    preco_atual: str
    cupom: Optional[str] = None
    link_cupom: Optional[str] = None
    link_produto: Optional[str] = None # Tornou-se opcional para evitar crash se o texto vier bizarro

def organizar_links_e_precos(dados_json, texto_bruto):
    """
    Lista branca de lojas (White List) e tratamento estrito para retornar sempre Strings
    """
    linhas = [l.strip() for l in texto_bruto.split('\n') if l.strip()]
    
    # 1. FILTRO POR LISTA BRANCA DE LOJAS VALIDADAS
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    lojas_permitidas = ["amazon", "shopee", "aliexpress", "mercadolivre", "meli", "magaziniluiza", "magalu", "casasbahia", "girafa", "kabum", "pichau", "terabyte"]
    
    links_lojas = [
        l for l in links_no_texto 
        if any(loja in l.lower() for loja in lojas_permitidas)
    ]
    
    link_produto_detectado = None
    link_cupom_detectado = None
    
    # Varre as linhas mapeando apenas os links de lojas permitidas
    for linha in linhas:
        links_na_linha = re.findall(r'(https?://\S+)', linha)
        if links_na_linha:
            link = links_na_linha[0] # Garante que pega a String do primeiro link da linha
            if not any(loja in link.lower() for loja in lojas_permitidas):
                continue
                
            linha_lower = linha.lower()
            if "cupom" in linha_lower or "resgate" in linha_lower or "coletar" in linha_lower:
                link_cupom_detectado = link
            elif "compre" in linha_lower or "link" in linha_lower or "🛒" in linha_lower or "🔗" in linha_lower or "por r$" in linha_lower:
                link_produto_detectado = link

    # Fallbacks inteligentes para garantir que sejam Strings puras
    if not link_produto_detectado and links_lojas:
        link_produto_detectado = links_lojas[0]
        
    if len(links_lojas) >= 2 and not link_cupom_detectado:
        for l in links_lojas:
            if l != link_produto_detectado:
                link_cupom_detectado = l
                break

    # SEGUNDO ESCUDO: Se a lista branca falhou por completo, pega qualquer link para não dar erro 500
    if not link_produto_detectado and links_no_texto:
        # Filtra apenas links que não sejam de canais óbvios
        links_limpos = [l for l in links_no_texto if "t.me" not in l and "whatsapp" not in l]
        if links_limpos:
            link_produto_detectado = links_limpos[0]

    dados_json["link_produto"] = str(link_produto_detectado) if link_produto_detectado else None
    dados_json["link_cupom"] = str(link_cupom_detectado) if link_cupom_detectado else None

    # 2. VALIDAÇÃO HÍBRIDA DE PREÇOS (DE / POR)
    match_linha_precos = re.search(r'\bde\b\s*:?\s*r?\$?\s*(\d+(?:[\.,]\d+)*)\s*\bpor\b\s*:?\s*r?\$?\s*(\d+(?:[\.,]\d+)*)', texto_bruto, re.IGNORECASE)
    
    if match_linha_precos:
        dados_json["preco_anterior"] = match_linha_precos.group(1).strip()
        dados_json["preco_atual"] = match_linha_precos.group(2).strip()
    else:
        linha_de = None
        linha_por = None
        for linha in linhas:
            if re.search(r'\bde\b\s*:?\s*r?\$?\s*\d+', linha, re.IGNORECASE):
                linha_de = linha
            if re.search(r'\b(?:por|💵)\b\s*:?\s*r?\$?\s*\d+', linha, re.IGNORECASE):
                linha_por = linha

        if linha_de and linha_por:
            match_de = re.search(r'(\d+(?:[\.,]\d+)*)', linha_de)
            match_por = re.search(r'(\d+(?:[\.,]\d+)*)', linha_por)
            if match_de and match_por:
                dados_json["preco_anterior"] = match_de.group(1).strip()
                dados_json["preco_atual"] = match_por.group(1).strip()

    # 3. PADRONIZAÇÃO MONETÁRIA
    for campo in ["preco_atual", "preco_anterior"]:
        valor = dados_json.get(campo)
        if valor is None or str(valor).strip().lower() in ["null", "none", ""]:
            dados_json[campo] = None
        else:
            valor_str = str(valor)
            if valor_str.endswith('.00') or valor_str.endswith(',00'):
                valor_str = valor_str[:-3]
            valor_limpo = valor_str.replace('R$', '').replace('(', '').replace(')', '').strip()
            
            match_num = re.search(r'(\d+(?:[\.,]\d+)*)', valor_limpo)
            if match_num:
                dados_json[campo] = f"R$ {match_num.group(1)}"
            else:
                dados_json[campo] = f"R$ {valor_limpo}"

    if dados_json.get("preco_anterior") == dados_json.get("preco_atual"):
        dados_json["preco_anterior"] = None

    # 4. LIMPEZA REAL DE CUPOM
    cupom_ia = str(dados_json.get("cupom", "") or "").strip()
    if cupom_ia and cupom_ia.lower() != "null":
        cupom_limpo = cupom_ia.replace('🎟️', '').replace('🎟', '').strip()
        if cupom_limpo.lower() not in texto_bruto.lower() or any(cupom_limpo in l for l in links_no_texto):
            dados_json["cupom"] = None
        else:
            dados_json["cupom"] = cupom_limpo
    else:
        dados_json["cupom"] = None

    return dados_json

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas do Telegram. Analise o texto e responda APENAS com um objeto JSON no formato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n\n"
        "Regras cruciais:\n"
        "1. Capture o nome comercial completo do produto.\n"
        "2. Se não houver um código de cupom explícito em formato de texto escrito no anúncio, defina a chave 'cupom' obrigatoriamente como null.\n"
        "3. Nunca invente ou adivinhe códigos de cupom."
    )

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": f"Texto da oferta:\n{texto}"}
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "top_p": 0.1
        }
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
