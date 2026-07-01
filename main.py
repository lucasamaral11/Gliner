import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI(title="Qwen Few-Shot Extractor API")

OLLAMA_URL = "http://187.127.36.194:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:1.5b" 

class TextoPayload(BaseModel):
    texto: str

def organizar_links(dados_json, texto_bruto):
    """
    Mantém apenas a inteligência simples de separação de links baseada nas linhas do texto
    """
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

        # Garantias de preenchimento caso a varredura falhe
        if not dados_json["link_produto"] and links_validos:
            dados_json["link_produto"] = links_validos[-1]
        if not dados_json["link_cupom"] and len(links_validos) > 1 and links_validos[0] != dados_json["link_produto"]:
            dados_json["link_cupom"] = links_validos[0]

    return dados_json

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas. Você deve analisar o texto e responder APENAS com um objeto JSON no formato exato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n"
        "Regras estritas:\n"
        "1. Capture o nome COMPLETO do produto. Nunca resuma.\n"
        "2. Formate os preços sempre com o prefixo 'R$' (ex: 'R$ 673').\n"
        "3. Se houver apenas um preço listado para o produto, ele é o 'preco_atual'. O 'preco_anterior' DEVE ser null.\n"
        "4. Não confunda a porcentagem ou valor do cupom (ex: 30 OFF) com o preço do produto.\n"
        "5. Responda apenas o JSON puro, sem markdown."
    )

    # EXEMPLO DE TREINAMENTO (Few-Shot): Ensinamos o modelo exatamente como agir
    exemplo_usuario = (
        " Smart TV 32” Britânia B32CRA HD Wi-Fi\n\n"
        "💵 673\n\n"
        "🎟️ Resgate Cupom 30 OFF aqui:\n"
        "https://s.shopee.com.br/qehMgA6NA\n\n"
        "🔗 https://s.shopee.com.br/4Av9Knt0po"
    )
    
    exemplo_assistente = {
        "nome_produto": "Smart TV 32” Britânia B32CRA HD Wi-Fi",
        "preco_anterior": None,
        "preco_atual": "R$ 673",
        "cupom": "30 OFF",
        "link_cupom": "https://s.shopee.com.br/qehMgA6NA",
        "link_produto": "https://s.shopee.com.br/4Av9Knt0po"
    }

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": prompt_sistema},
            # Injeta o exemplo perfeito na linha do tempo do chat
            {"role": "user", "content": f"Texto da oferta:\n{exemplo_usuario}"},
            {"role": "assistant", "content": json.dumps(exemplo_assistente)},
            # Envia o texto real do usuário atual
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
            
            # Aplica apenas o organizador de links
            return organizar_links(json_puro, texto)
        except Exception as e:
            raise Exception(f"Erro no processamento: {str(e)}")

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
