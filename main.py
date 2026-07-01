import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import httpx
import json
import re

app = FastAPI(title="Qwen Strict Schema API")

OLLAMA_URL = "http://187.127.36.194:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:1.5b" 

class TextoPayload(BaseModel):
    texto: str

# Definição estrita do formato do JSON. O Pydantic garante que APENAS estas chaves existam.
class OfertaEstruturada(BaseModel):
    nome_produto: str
    preco_anterior: Optional[str] = None
    preco_atual: str
    cupom: Optional[str] = None
    link_cupom: Optional[str] = None
    link_produto: str

def organizar_links(dados_dict, texto_bruto):
    """
    Varre o texto para separar o link do cupom e do produto de forma lógica
    """
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    links_validos = [l for l in links_no_texto if "t.me" not in l and "whatsapp" not in l]
    
    dados_dict["link_cupom"] = None
    dados_dict["link_produto"] = None

    if len(links_validos) == 1:
        dados_dict["link_produto"] = links_validos[0]
    elif len(links_validos) >= 2:
        for link in links_validos:
            for linha in texto_bruto.split('\n'):
                if link in linha:
                    if "cupom" in linha.lower() or "resgate" in linha.lower():
                        dados_dict["link_cupom"] = link
                    else:
                        dados_json_link = link
                        dados_dict["link_produto"] = link

        if not dados_dict["link_produto"] and links_validos:
            dados_dict["link_produto"] = links_validos[-1]
        if not dados_dict["link_cupom"] and len(links_validos) > 1 and links_validos[0] != dados_dict["link_produto"]:
            dados_dict["link_cupom"] = links_validos[0]

    return dados_dict

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas. Analise o texto e extraia as informações para o formato JSON.\n"
        "Você deve preencher APENAS as seguintes chaves:\n"
        "- nome_produto (string)\n"
        "- preco_anterior (string ou null)\n"
        "- preco_atual (string)\n"
        "- cupom (string ou null)\n"
        "- link_cupom (string ou null)\n"
        "- link_produto (string)\n\n"
        "Regras estritas:\n"
        "1. Nunca crie chaves com emojis ou nomes diferentes das listadas acima.\n"
        "2. Formate os preços sempre com 'R$' (ex: R$ 673).\n"
        "3. Ignore avisos de anúncios ou links de redes sociais."
    )

    exemplo_usuario = (
        " Smart TV 32” Britânia B32CRA HD Wi-Fi\n\n💵 673\n\n🎟️ Resgate Cupom 30 OFF aqui:\n"
        "https://s.shopee.com.br/qehMgA6NA\n\n🔗 https://s.shopee.com.br/4Av9Knt0po"
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
            {"role": "user", "content": f"Texto da oferta:\n{exemplo_usuario}"},
            {"role": "assistant", "content": json.dumps(exemplo_assistente)},
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
            
            # 1. Filtra os links primeiro
            json_com_links = organizar_links(json_puro, texto)
            
            # 2. Força a validação do Pydantic (destrói chaves extras como 'Anúncio' ou 'Telegram')
            oferta_validada = OfertaEstruturada(**json_com_links)
            
            # Retorna o objeto limpo convertido em dicionário Python comum
            return oferta_validada.model_dump()
            
        except Exception as e:
            # Fallback de emergência caso a IA quebre muito o formato
            print(f"Erro na validação do Pydantic, limpando manualmente: {str(e)}")
            return {
                "nome_produto": json_puro.get("nome_produto", "Produto não identificado"),
                "preco_anterior": json_puro.get("preco_anterior", None),
                "preco_atual": json_puro.get("preco_atual", "Consulte o link"),
                "cupom": json_puro.get("cupom", None),
                "link_cupom": json_puro.get("link_cupom", None),
                "link_produto": json_puro.get("link_produto", "")
            }

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        resultado = await chamar_ollama(payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
