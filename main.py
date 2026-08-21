import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURAÇÃO
# ============================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://ollama-e10mgfwnydotbjgl9upgsunt:11434/api/chat"
)

MODEL_NAME = os.getenv(
    "OLLAMA_MODEL",
    "qwen2.5-coder:3b"
)

OLLAMA_CONNECT_TIMEOUT = float(
    os.getenv("OLLAMA_CONNECT_TIMEOUT", "10")
)

OLLAMA_READ_TIMEOUT = float(
    os.getenv("OLLAMA_READ_TIMEOUT", "60")
)

OLLAMA_WRITE_TIMEOUT = float(
    os.getenv("OLLAMA_WRITE_TIMEOUT", "10")
)

OLLAMA_POOL_TIMEOUT = float(
    os.getenv("OLLAMA_POOL_TIMEOUT", "10")
)

MAX_TEXT_LENGTH = int(
    os.getenv("MAX_TEXT_LENGTH", "20000")
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("gliner")


# ============================================================
# HTTP CLIENT
# ============================================================

http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client

    timeout = httpx.Timeout(
        connect=OLLAMA_CONNECT_TIMEOUT,
        read=OLLAMA_READ_TIMEOUT,
        write=OLLAMA_WRITE_TIMEOUT,
        pool=OLLAMA_POOL_TIMEOUT,
    )

    http_client = httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
    )

    logger.info("Gliner iniciado")
    logger.info("Ollama URL: %s", OLLAMA_URL)
    logger.info("Modelo: %s", MODEL_NAME)

    yield

    if http_client:
        await http_client.aclose()

    logger.info("Gliner encerrado")


app = FastAPI(
    title="Qwen Strict Schema API",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# MODELOS
# ============================================================

class TextoPayload(BaseModel):
    texto: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TEXT_LENGTH
    )


class OfertaEstruturada(BaseModel):
    nome_produto: str
    preco_anterior: Optional[str] = None
    preco_atual: str
    cupom: Optional[str] = None
    link_cupom: Optional[str] = None
    link_produto: Optional[str] = None


# ============================================================
# ORGANIZAÇÃO E VALIDAÇÃO
# ============================================================

def organizar_links_e_precos(dados_json, texto_bruto):
    """
    Lista branca de lojas e tratamento estrito
    para retornar sempre strings válidas.
    """

    linhas = [
        l.strip()
        for l in texto_bruto.split("\n")
        if l.strip()
    ]

    # ========================================================
    # 1. FILTRO POR LISTA BRANCA DE LOJAS
    # ========================================================

    links_no_texto = re.findall(
        r"(https?://\S+)",
        texto_bruto
    )

    lojas_permitidas = [
        "amazon",
        "shopee",
        "aliexpress",
        "mercadolivre",
        "meli",
        "magazineluiza",
        "magalu",
        "casasbahia",
        "girafa",
        "kabum",
        "pichau",
        "terabyte",
        "link.amazon",
    ]

    links_lojas = [
        l
        for l in links_no_texto
        if any(
            loja in l.lower()
            for loja in lojas_permitidas
        )
    ]

    link_produto_detectado = None
    link_cupom_detectado = None

    for linha in linhas:

        links_na_linha = re.findall(
            r"(https?://\S+)",
            linha
        )

        if not links_na_linha:
            continue

        link = links_na_linha[0]

        if not any(
            loja in link.lower()
            for loja in lojas_permitidas
        ):
            continue

        linha_lower = linha.lower()

        if (
            "cupom" in linha_lower
            or "resgate" in linha_lower
            or "coletar" in linha_lower
        ):
            link_cupom_detectado = link

        elif (
            "compre" in linha_lower
            or "link" in linha_lower
            or "🛒" in linha_lower
            or "🔗" in linha_lower
            or "por r$" in linha_lower
        ):
            link_produto_detectado = link

    # Fallback para primeiro link de loja
    if not link_produto_detectado and links_lojas:
        link_produto_detectado = links_lojas[0]

    # Segundo link de loja vira link de cupom
    if len(links_lojas) >= 2 and not link_cupom_detectado:
        for link in links_lojas:

            if link != link_produto_detectado:
                link_cupom_detectado = link
                break

    # ========================================================
    # SEGUNDO ESCUDO
    # ========================================================

    if not link_produto_detectado and links_no_texto:

        links_limpos = [
            l
            for l in links_no_texto
            if "t.me" not in l
            and "whatsapp" not in l
        ]

        if links_limpos:
            link_produto_detectado = links_limpos[0]

    # Produto e cupom não podem ser o mesmo link
    if link_produto_detectado == link_cupom_detectado:
        link_cupom_detectado = None

    dados_json["link_produto"] = (
        str(link_produto_detectado)
        if link_produto_detectado
        else None
    )

    dados_json["link_cupom"] = (
        str(link_cupom_detectado)
        if link_cupom_detectado
        else None
    )

    # ========================================================
    # 2. VALIDAÇÃO HÍBRIDA DE PREÇOS
    # ========================================================

    match_linha_precos = re.search(
        r"\bde\b\s*:?\s*r?\$?\s*"
        r"(\d+(?:[\.,]\d+)*)"
        r"\s*\bpor\b\s*:?\s*r?\$?\s*"
        r"(\d+(?:[\.,]\d+)*)",
        texto_bruto,
        re.IGNORECASE,
    )

    if match_linha_precos:

        dados_json["preco_anterior"] = (
            match_linha_precos.group(1).strip()
        )

        dados_json["preco_atual"] = (
            match_linha_precos.group(2).strip()
        )

    else:

        linha_de = None
        linha_por = None

        for linha in linhas:

            if re.search(
                r"\bde\b\s*:?\s*r?\$?\s*\d+",
                linha,
                re.IGNORECASE,
            ):
                linha_de = linha

            if re.search(
                r"\b(?:por|💵)\b\s*:?\s*r?\$?\s*\d+",
                linha,
                re.IGNORECASE,
            ):
                linha_por = linha

        if linha_de and linha_por:

            match_de = re.search(
                r"(\d+(?:[\.,]\d+)*)",
                linha_de
            )

            match_por = re.search(
                r"(\d+(?:[\.,]\d+)*)",
                linha_por
            )

            if match_de and match_por:

                dados_json["preco_anterior"] = (
                    match_de.group(1).strip()
                )

                dados_json["preco_atual"] = (
                    match_por.group(1).strip()
                )

    # ========================================================
    # 3. PADRONIZAÇÃO MONETÁRIA
    # ========================================================

    for campo in [
        "preco_atual",
        "preco_anterior"
    ]:

        valor = dados_json.get(campo)

        if (
            valor is None
            or str(valor).strip().lower()
            in ["null", "none", ""]
        ):
            dados_json[campo] = None
            continue

        valor_str = str(valor)

        valor_str = (
            valor_str
            .replace(".00", "")
            .replace(",00", "")
        )

        valor_limpo = (
            valor_str
            .replace("R$", "")
            .replace("(", "")
            .replace(")", "")
            .strip()
        )

        match_num = re.search(
            r"(\d+(?:[\.,]\d+)*)",
            valor_limpo
        )

        if match_num:
            dados_json[campo] = (
                f"R$ {match_num.group(1)}"
            )
        else:
            dados_json[campo] = (
                f"R$ {valor_limpo}"
            )

    if (
        dados_json.get("preco_anterior")
        == dados_json.get("preco_atual")
    ):
        dados_json["preco_anterior"] = None

    # ========================================================
    # 4. LIMPEZA REAL DE CUPOM
    # ========================================================

    cupom_ia = str(
        dados_json.get("cupom", "") or ""
    ).strip()

    if cupom_ia and cupom_ia.lower() != "null":

        cupom_limpo = (
            cupom_ia
            .replace("🎟️", "")
            .replace("🎟", "")
            .strip()
        )

        if (
            cupom_limpo.lower()
            not in texto_bruto.lower()
            or any(
                cupom_limpo in link
                for link in links_no_texto
            )
        ):
            dados_json["cupom"] = None

        else:
            dados_json["cupom"] = cupom_limpo

    else:
        dados_json["cupom"] = None

    return dados_json


# ============================================================
# OLLAMA
# ============================================================

async def chamar_ollama(texto: str):

    if http_client is None:
        logger.error("HTTP client ainda não foi inicializado")

        raise HTTPException(
            status_code=503,
            detail="Serviço temporariamente indisponível."
        )

    prompt_sistema = (
        "Você é um extrator de dados de ofertas do Telegram. "
        "Analise o texto e responda APENAS com um objeto JSON "
        "no formato:\n"
        "{\n"
        '  "nome_produto": "string",\n'
        '  "preco_anterior": "string ou null",\n'
        '  "preco_atual": "string",\n'
        '  "cupom": "string ou null",\n'
        '  "link_cupom": "string ou null",\n'
        '  "link_produto": "string"\n'
        "}\n\n"
        "Regras cruciais:\n"
        "1. Capture o nome comercial completo do produto.\n"
        "2. Se não houver um código de cupom explícito "
        "em formato de texto escrito no anúncio, defina "
        "a chave 'cupom' obrigatoriamente como null.\n"
        "3. Nunca invente ou adivinhe códigos de cupom."
    )

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": prompt_sistema
            },
            {
                "role": "user",
                "content": f"Texto da oferta:\n{texto}"
            }
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "top_p": 0.1
        }
    }

    try:

        response = await http_client.post(
            OLLAMA_URL,
            json=payload_dados
        )

        response.raise_for_status()

        dados = response.json()

        resposta_ia = (
            dados
            .get("message", {})
            .get("content", "")
            .strip()
        )

        if not resposta_ia:
            logger.error(
                "Ollama retornou resposta vazia"
            )

            raise HTTPException(
                status_code=502,
                detail="O serviço de IA não retornou dados."
            )

        resposta_limpa = re.sub(
            r"```json\s*|```",
            "",
            resposta_ia
        ).strip()

        try:
            json_puro = json.loads(resposta_limpa)

        except json.JSONDecodeError:

            logger.error(
                "Ollama retornou JSON inválido: %s",
                resposta_ia[:1000]
            )

            raise HTTPException(
                status_code=502,
                detail="O serviço de IA retornou dados inválidos."
            )

        json_corrigido = organizar_links_e_precos(
            json_puro,
            texto
        )

        try:

            oferta_validada = OfertaEstruturada(
                **json_corrigido
            )

        except Exception:

            logger.error(
                "Resposta do Ollama não passou na validação: %s",
                json_corrigido
            )

            raise HTTPException(
                status_code=502,
                detail="A resposta da IA não possui o formato esperado."
            )

        return oferta_validada.model_dump()

    except httpx.ConnectError:

        logger.exception(
            "Não foi possível conectar ao Ollama"
        )

        raise HTTPException(
            status_code=503,
            detail="Serviço de IA temporariamente indisponível."
        )

    except httpx.TimeoutException:

        logger.exception(
            "Timeout ao consultar Ollama"
        )

        raise HTTPException(
            status_code=504,
            detail="O serviço de IA demorou demais para responder."
        )

    except httpx.HTTPStatusError as e:

        logger.exception(
            "Ollama retornou HTTP %s",
            e.response.status_code
        )

        raise HTTPException(
            status_code=502,
            detail="O serviço de IA retornou um erro."
        )

    except HTTPException:
        raise

    except Exception:

        logger.exception(
            "Erro inesperado ao processar oferta"
        )

        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar a oferta."
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "ok"
    }


# ============================================================
# READINESS CHECK
# ============================================================

@app.get("/ready")
async def ready():

    if http_client is None:

        raise HTTPException(
            status_code=503,
            detail="Cliente HTTP não inicializado."
        )

    try:

        response = await http_client.get(
            OLLAMA_URL.replace("/api/chat", "/api/tags"),
            timeout=5.0
        )

        response.raise_for_status()

        return {
            "status": "ready",
            "ollama": "ok"
        }

    except Exception:

        logger.exception(
            "Ollama não está disponível"
        )

        raise HTTPException(
            status_code=503,
            detail="O serviço de IA não está disponível."
        )


# ============================================================
# ENDPOINT PRINCIPAL
# ============================================================

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):

    try:

        resultado = await chamar_ollama(
            payload.texto
        )

        return resultado

    except HTTPException:
        raise

    except Exception:

        logger.exception(
            "Erro inesperado no endpoint /extrair-oferta"
        )

        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar a oferta."
        )
