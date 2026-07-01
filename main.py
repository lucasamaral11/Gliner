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
    Ajusta e higieniza preços, nomes e links vindos da IA
    """
    # 1. Correção Estrita de Preços (Garante R$ e remove "null" em formato de texto)
    for campo in ["preco_atual", "preco_anterior"]:
        valor = dados_json.get(campo)
        
        # Se a IA preencheu o campo com a palavra literal "null" (texto), limpa para None
        if valor is None or str(valor).strip().lower() in ["null", "none", ""]:
            dados_json[campo] = None
            continue
            
        valor_str = str(valor).strip()
        
        # Extrai apenas os números do preço para remontar com segurança
        numeros = "".join(re.findall(r'\d+', valor_str))
        
        if numeros:
            # Sempre monta no formato padrão do seu grupo: R$ X.XXX
            dados_json[campo] = f"R$ {numeros}"
        else:
            dados_json[campo] = None

    # 2. Correção de Título Inteligente (Caso a IA pegue apenas chamadas de efeito)
    nome_capturado = dados_json.get("nome_produto", "").strip()
    linhas = [l.strip() for l in texto_bruto.split('\n') if l.strip()]
    
    if len(linhas) > 1 and (len(nome_capturado.split()) <= 3 or nome_capturado.lower() in linhas[0].lower()):
        palavras_chave = ["tênis", "smart", "tv", "notebook", "fone", "caixa", "placa", "processador", "monitor", "smartphone", "iphone", "geforce", "rtx"]
        for linha in linhas[:3]:
            if any(p in linha.lower() for p in palavras_chave):
                dados_json["nome_produto"] = linha
                break

    # 3. Organização Segura de Links baseada nas linhas do Telegram
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
        "Regras:\n"
        "1. Capture o nome comercial completo do produto.\n"
        "2. Formate os preços com R$ (ex: R$ 2199).\n"
        "3. Se não houver preço anterior, preencha a chave preco_anterior com null (sem aspas).\n"
        "4. Responda apenas o JSON puro."
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
            
            # Executa a higienização forçada em Python antes de validar
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
