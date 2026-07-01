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
    Higienização avançada de centavos, exclusão de links institucionais e validação de preços
    """
    # 1. Extração Inteligente de Preços direto do Texto Bruto (Evitando quebras da IA)
    texto_sem_links = re.sub(r'https?://\S+', '', texto_bruto)
    
    # Captura padrões monetários comuns como: 349,86 ou 241
    padrao_precos = re.findall(r'(?:De|Por|R\$)?\s*:?\s*R?\$\s*(\d+(?:[\.,]\d{2})?)\b', texto_sem_links, re.IGNORECASE)
    
    precos_formatados = []
    for p in padrao_precos:
        p_limpo = p.strip().replace(' ', '')
        if p_limpo:
            # Garante a padronização do R$ mantendo os centavos se existirem
            precos_formatados.append(f"R$ {p_limpo}")

    # Aplica a distribuição correta de preços com base na ordem de leitura do texto
    if len(precos_formatados) >= 2:
        dados_json["preco_anterior"] = precos_formatados[0]
        dados_json["preco_atual"] = precos_formatados[1]
    elif len(precos_formatados) == 1:
        dados_json["preco_atual"] = precos_formatados[0]
        dados_json["preco_anterior"] = None
    else:
        # Fallback de segurança se a regex falhar, apenas limpando o que a IA trouxe
        p_at = dados_json.get("preco_atual", "")
        if p_at and "R$" not in str(p_at):
            dados_json["preco_atual"] = f"R$ {str(p_at).strip()}"

    # Validação extra: se os preços forem idênticos, anula o anterior
    if dados_json["preco_anterior"] == dados_json["preco_atual"]:
        dados_json["preco_anterior"] = None

    # 2. Filtro Cirúrgico de Links (Descarta links institucionais e foca nos de compra)
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    
    # Palavras banidas que indicam links de canais, bots ou sites do dono do grupo
    termos_banidos = ["t.me", "whatsapp", "mastertechjr", "youtube", "instagram", "facebook", "linktr.ee"]
    
    links_lojas = [l for l in links_no_texto if not any(termo in l.lower() for termo in termos_banidos)]
    
    dados_json["link_cupom"] = None
    dados_json["link_produto"] = None

    if links_lojas:
        # Em grupos do AliExpress, se houver mais de um link de loja, o primeiro costuma ser o mobile/moedas
        # Vamos salvar o link principal de compra
        dados_json["link_produto"] = links_lojas[0]
        
        # Se houver um segundo link de loja (ex: link normal de PC), mantém ele ou verifica se há link de cupom
        for link in links_lojas:
            for linha in texto_bruto.split('\n'):
                if link in linha and ("cupom" in linha.lower() or "resgate" in linha.lower()):
                    dados_json["link_cupom"] = link
                    # Se o link do cupom era o primeiro, joga o segundo link para o produto
                    if dados_json["link_produto"] == link and len(links_lojas) > 1:
                        dados_json["link_produto"] = links_lojas[1]

    # 3. Garante que os campos nulos textuais virem Nulos reais (None)
    for campo in ["preco_anterior", "cupom", "link_cupom"]:
        if str(dados_json.get(campo)).strip().lower() in ["null", "none", ""]:
            dados_json[campo] = None

    return dados_json

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas. Analise o texto e devolva APENAS um JSON no formato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n"
        "Regra: Capture o nome completo do produto com marca e modelo. Não confunda links de canais com links de compra."
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
            
            # Aplica o novo filtro de alta precisão para o Telegram/AliExpress
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
