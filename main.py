import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI(title="Qwen Robust Extractor API")

OLLAMA_URL = "http://187.127.36.194:11434/api/chat"
# UPGRADE: Mudamos para a versão de 1.5B que é muito mais obediente e estável
MODEL_NAME = "qwen2.5-coder:1.5b" 

class TextoPayload(BaseModel):
    texto: str

def tratar_regras_negocio(dados_json, texto_bruto):
    """
    Blinda o JSON contra falhas e esquecimentos da IA usando programação estrita
    """
    # 1. Tratamento e padronização dos Preços
    p_atual = str(dados_json.get("preco_atual", "") or "").strip()
    p_anterior = str(dados_json.get("preco_anterior", "") or "").strip()
    
    # Remove textos extras se a IA tiver colocado além do número
    p_atual_num = "".join(re.findall(r'\d+', p_atual))
    p_anterior_num = "".join(re.findall(r'\d+', p_anterior))
    
    if p_atual_num:
        dados_json["preco_atual"] = f"R$ {p_atual_num}"
    
    # Regra estrita: Preço anterior não pode ser igual ao atual ou menor
    if p_anterior_num and p_anterior_num != p_atual_num and int(p_anterior_num) > int(p_atual_num):
        dados_json["preco_anterior"] = f"R$ {p_anterior_num}"
    else:
        dados_json["preco_anterior"] = None

    # 2. Correção de Links usando varredura Regex direta no texto bruto
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    
    # Filtra links de convite ou canais secundários
    links_validos = [l for l in links_no_texto if "t.me" not in l and "whatsapp" not in l and "achados" not in l]
    
    if links_validos:
        # Tenta identificar se o primeiro link está associado a cupom no texto bruto
        primeiro_link = links_validos[0]
        pos_link = texto_bruto.find(primeiro_link)
        contexto_anterior = texto_bruto[max(0, pos_link-50):pos_link].lower()
        
        if "cupom" in contexto_anterior or "resgate" in contexto_anterior:
            dados_json["link_cupom"] = primeiro_link
            # Se houver um segundo link, ele será o do produto
            if len(links_validos) > 1:
                dados_json["link_produto"] = links_validos[1]
        else:
            dados_json["link_produto"] = primeiro_link
            if dados_json.get("link_cupom") == primeiro_link:
                dados_json["link_cupom"] = None

    return dados_json

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas. Responda APENAS com um objeto JSON válido no formato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n"
        "Regras:\n"
        "1. Capture o nome COMPLETO do produto. Nunca o resuma.\n"
        "2. Identifique qual link é para coletar cupom e qual é para comprar o produto.\n"
        "3. Responda apenas o JSON puro, sem markdown."
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
                raise Exception(f"Ollama erro: {response.text}")
                
            dados = response.json()
            resposta_ia = dados.get("message", {}).get("content", "").strip()
            resposta_limpa = re.sub(r"```json\s*|```", "", resposta_ia).strip()
            
            json_puro = json.loads(resposta_limpa)
            
            # Aplica a camada de programação para corrigir os esquecimentos da IA
            return tratar_regras_negocio(json_puro, texto)
            
        except Exception as e:
            raise Exception(f"Erro no processamento: {str(e)}")

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
