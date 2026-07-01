import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI(title="Qwen Robust Extractor API")

OLLAMA_URL = "http://187.127.36.194:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:1.5b" 

class TextoPayload(BaseModel):
    texto: str

def tratar_regras_negocio(dados_json, texto_bruto):
    """
    Filtra e limpa alucinações da IA e erros de leitura de preços em links
    """
    # 1. Extração Limpa de PreçosDireto do Texto Bruto (Ignorando URLs para não misturar números)
    # Remove as URLs do texto temporariamente apenas para caçar os preços reais
    texto_sem_links = re.sub(r'https?://\S+', '', texto_bruto)
    
    # Encontra todos os blocos numéricos soltos que representam valores (ex: 673 ou 932,00)
    valores_encontrados = re.findall(r'(?:R\$\s*)?(\d+(?:[\.,]\d{2})?)', texto_sem_links)
    
    # Limpa os valores encontrados transformando em números inteiros para comparação
    valores_limpos = []
    for v in valores_encontrados:
        v_num = "".join(re.findall(r'\d+', v))
        if v_num and len(v_num) <= 5: # Evita pegar números gigantescos que seriam IDs
            valores_limpos.append(int(v_num))
            
    # Aplica a regra de preços com base nos valores REAIS do texto
    if len(valores_limpos) == 1:
        dados_json["preco_atual"] = f"R$ {valores_limpos[0]}"
        dados_json["preco_anterior"] = None
    elif len(valores_limpos) >= 2:
        # Se achou dois preços, o maior é o anterior e o menor é o atual (comum em promoções)
        maior_preco = max(valores_limpos)
        menor_preco = min(valores_limpos)
        if maior_preco != menor_preco:
            dados_json["preco_anterior"] = f"R$ {maior_preco}"
            dados_json["preco_atual"] = f"R$ {menor_preco}"
        else:
            dados_json["preco_atual"] = f"R$ {valores_limpos[0]}"
            dados_json["preco_anterior"] = None
    else:
        # Fallback de segurança usando o que a IA tentou ler, mas limpando excessos
        p_at = "".join(re.findall(r'\d+', str(dados_json.get("preco_atual", ""))))
        if p_at and len(p_at) <= 4:
            dados_json["preco_atual"] = f"R$ {p_at}"

    # 2. Limpeza Cirúrgica do Cupom (Pega apenas o código ou a porcentagem)
    cupom_cru = str(dados_json.get("cupom", "") or "").strip()
    match_cupom = re.search(r'(\d+\s*OFF|[A-Z0-9]{4,}\b)', cupom_cru, re.IGNORECASE)
    if match_cupom:
        dados_json["cupom"] = match_cupom.group(1).upper()
    elif "null" in cupom_cru.lower() or not cupom_cru:
        dados_json["cupom"] = None

    # 3. Separação Estrita de Links usando Regex no texto bruto original
    links_no_texto = re.findall(r'(https?://\S+)', texto_bruto)
    links_validos = [l for l in links_no_texto if "t.me" not in l and "whatsapp" not in l]
    
    dados_json["link_cupom"] = None
    dados_json["link_produto"] = None

    if len(links_validos) == 1:
        dados_json["link_produto"] = links_validos[0]
    elif len(links_validos) >= 2:
        # Varre as linhas do texto para associar o link correto à sua função
        for link in links_validos:
            # Encontra a linha onde o link está inserido
            for linha in texto_bruto.split('\n'):
                if link in linha:
                    if "cupom" in linha.lower() or "resgate" in linha.lower():
                        dados_json["link_cupom"] = link
                    else:
                        dados_json["link_produto"] = link

        # Garantia de preenchimento caso a varredura de linha falhe
        if not dados_json["link_produto"] and links_validos:
            dados_json["link_produto"] = links_validos[-1] # Geralmente o último é o do produto
        if not dados_json["link_cupom"] and len(links_validos) > 1:
            dados_json["link_cupom"] = links_validos[0]

    return dados_json

async def chamar_ollama(texto: str):
    prompt_sistema = (
        "Você é um extrator de dados de ofertas. Responda APENAS com um objeto JSON no formato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n"
        "Regras: Extraia o nome completo do produto. Não invente números."
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
