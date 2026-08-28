````python
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURAÇÃO
# ============================================================

NINE_ROUTER_BASE_URL = os.getenv(
    "NINE_ROUTER_BASE_URL"
).rstrip("/")

NINE_ROUTER_API_KEY = os.getenv("NINE_ROUTER_API_KEY")

MODEL_NAME = os.getenv(
    "NINE_ROUTER_MODEL"
)

AI_CONNECT_TIMEOUT = float(
    os.getenv("AI_CONNECT_TIMEOUT")
)

AI_READ_TIMEOUT = float(
    os.getenv("AI_READ_TIMEOUT")
)

AI_WRITE_TIMEOUT = float(
    os.getenv("AI_WRITE_TIMEOUT")
)

AI_POOL_TIMEOUT = float(
    os.getenv("AI_POOL_TIMEOUT")
)

MAX_TEXT_LENGTH = int(
    os.getenv("MAX_TEXT_LENGTH")
)

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=LOG_LEVEL,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
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
        connect=AI_CONNECT_TIMEOUT,
        read=AI_READ_TIMEOUT,
        write=AI_WRITE_TIMEOUT,
        pool=AI_POOL_TIMEOUT,
    )

    http_client = httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
    )

    logger.info(
        "Gliner iniciado | model=%s | 9router=%s",
        MODEL_NAME,
        NINE_ROUTER_BASE_URL,
    )

    yield

    if http_client:
        await http_client.aclose()

    logger.info("Gliner encerrado")


app = FastAPI(
    title="Gliner Offer Extraction API",
    version="1.3.0",
    lifespan=lifespan,
)


# ============================================================
# MODELOS
# ============================================================

class TextoPayload(BaseModel):
    texto: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
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

def organizar_links_e_precos(
    dados_json,
    texto_bruto,
):
    linhas = [
        linha.strip()
        for linha in texto_bruto.split("\n")
        if linha.strip()
    ]

    # ========================================================
    # 1. FILTRO POR LISTA BRANCA DE LOJAS
    # ========================================================

    links_no_texto = re.findall(
        r"(https?://\S+)",
        texto_bruto,
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
        link
        for link in links_no_texto
        if any(
            loja in link.lower()
            for loja in lojas_permitidas
        )
    ]

    link_produto_detectado = None
    link_cupom_detectado = None

    for linha in linhas:
        links_na_linha = re.findall(
            r"(https?://\S+)",
            linha,
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

    if not link_produto_detectado and links_lojas:
        link_produto_detectado = links_lojas[0]

    if len(links_lojas) >= 2 and not link_cupom_detectado:
        for link in links_lojas:
            if link != link_produto_detectado:
                link_cupom_detectado = link
                break

    if not link_produto_detectado and links_no_texto:
        links_limpos = [
            link
            for link in links_no_texto
            if "t.me" not in link.lower()
            and "whatsapp" not in link.lower()
        ]

        if links_limpos:
            link_produto_detectado = links_limpos[0]

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
                linha_de,
            )

            match_por = re.search(
                r"(\d+(?:[\.,]\d+)*)",
                linha_por,
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
        "preco_anterior",
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
            valor_limpo,
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
    # 4. LIMPEZA DE CUPOM
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
# PARSER DA RESPOSTA DA IA
# ============================================================

def parsear_resposta_ia(
    resposta: str,
    request_id: str,
):
    """
    Converte a resposta textual da IA:

    nome_produto: ...
    preco_anterior: ...
    preco_atual: ...
    cupom: ...
    link_cupom: ...
    link_produto: ...

    em um dicionário.
    """

    resposta = resposta.strip()

    # Remove eventuais blocos de markdown
    resposta = re.sub(
        r"```(?:text|txt)?\s*|\s*```",
        "",
        resposta,
        flags=re.IGNORECASE,
    ).strip()

    dados = {
        "nome_produto": None,
        "preco_anterior": None,
        "preco_atual": None,
        "cupom": None,
        "link_cupom": None,
        "link_produto": None,
    }

    # --------------------------------------------------------
    # Localiza cada campo até o próximo campo
    # --------------------------------------------------------

    padroes = {
        "nome_produto": r"nome_produto\s*:\s*(.*?)(?=\s*,?\s*preco_anterior\s*:|$)",
        "preco_anterior": r"preco_anterior\s*:\s*(.*?)(?=\s*,?\s*preco_atual\s*:|$)",
        "preco_atual": r"preco_atual\s*:\s*(.*?)(?=\s*,?\s*cupom\s*:|$)",
        "cupom": r"cupom\s*:\s*(.*?)(?=\s*,?\s*link_cupom\s*:|$)",
        "link_cupom": r"link_cupom\s*:\s*(.*?)(?=\s*,?\s*link_produto\s*:|$)",
        "link_produto": r"link_produto\s*:\s*(.*?)$",
    }

    for campo, padrao in padroes.items():
        match = re.search(
            padrao,
            resposta,
            re.IGNORECASE | re.DOTALL,
        )

        if match:
            valor = match.group(1).strip()

            # Remove vírgula final
            valor = valor.rstrip(",").strip()

            # Trata null/none/vazio
            if valor.lower() in [
                "",
                "null",
                "none",
                "não informado",
                "nao informado",
            ]:
                valor = None

            dados[campo] = valor

    # --------------------------------------------------------
    # Verificação mínima
    # --------------------------------------------------------

    if not dados["nome_produto"]:
        logger.error(
            "request_id=%s | "
            "não foi possível extrair nome_produto | "
            "resposta=%s",
            request_id,
            resposta,
        )

        raise HTTPException(
            status_code=502,
            detail="O serviço de IA retornou dados em formato inválido.",
        )

    if not dados["preco_atual"]:
        logger.error(
            "request_id=%s | "
            "não foi possível extrair preco_atual | "
            "resposta=%s",
            request_id,
            resposta,
        )

        raise HTTPException(
            status_code=502,
            detail="O serviço de IA não retornou o preço atual.",
        )

    return dados


# ============================================================
# 9ROUTER
# ============================================================

async def chamar_9router(
    texto: str,
    request_id: str,
):
    if http_client is None:
        logger.error(
            "request_id=%s | http_client não inicializado",
            request_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Serviço temporariamente indisponível.",
        )

    if not NINE_ROUTER_API_KEY:
        logger.error(
            "request_id=%s | NINE_ROUTER_API_KEY não configurada",
            request_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Serviço de IA não configurado.",
        )

    # ========================================================
    # PROMPT OTIMIZADO
    # ========================================================

    prompt_sistema = (
        "Extraia os dados da oferta.\n"
        "Responda SOMENTE nesta linha, sem explicações:\n"
        "nome_produto: X, preco_anterior: X, preco_atual: X, "
        "cupom: X, link_cupom: X, link_produto: X\n"
        "\n"
        "Regras:\n"
        "- nome_produto: nome comercial completo do produto.\n"
        "- preco_anterior: preço antigo ou null.\n"
        "- preco_atual: preço atual.\n"
        "- cupom: somente o código escrito no texto ou null.\n"
        "- link_cupom: somente se houver link específico para cupom ou null.\n"
        "- link_produto: link do produto ou null.\n"
        "- Não invente informações.\n"
        "- Se não existir, use null.\n"
        "- Não use JSON.\n"
        "- Não use markdown.\n"
        "- Não explique nada.\n"
        "- Responda somente a linha solicitada."
    )

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": prompt_sistema,
            },
            {
                "role": "user",
                "content": f"Texto da oferta:\n{texto}",
            },
        ],
        "temperature": 0.0,
        "top_p": 0.1,
        "max_tokens": 5000,
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {NINE_ROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    inicio_ia = time.perf_counter()

    try:
        response = await http_client.post(
            f"{NINE_ROUTER_BASE_URL}/chat/completions",
            json=payload_dados,
            headers=headers,
        )

        duracao_http = (
            time.perf_counter()
            - inicio_ia
        )

        response.raise_for_status()

        dados = response.json()

        # ====================================================
        # MÉTRICAS
        # ====================================================

        usage = dados.get("usage", {})

        prompt_tokens = usage.get(
            "prompt_tokens"
        )

        completion_tokens = usage.get(
            "completion_tokens"
        )

        total_tokens = usage.get(
            "total_tokens"
        )

        cached_tokens = (
            usage
            .get(
                "prompt_tokens_details",
                {},
            )
            .get("cached_tokens")
        )

        cost = dados.get("cost")

        logger.info(
            "request_id=%s | "
            "9router_metrics | "
            "http_duration=%.3fs | "
            "model=%s | "
            "prompt_tokens=%s | "
            "completion_tokens=%s | "
            "total_tokens=%s | "
            "cached_tokens=%s | "
            "cost=%s",
            request_id,
            duracao_http,
            dados.get(
                "model",
                MODEL_NAME,
            ),
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cached_tokens,
            cost,
        )

        # ====================================================
        # RESPOSTA DA IA
        # ====================================================

        choices = dados.get(
            "choices",
            [],
        )

        if not choices:
            logger.error(
                "request_id=%s | 9router não retornou choices | response=%s",
                request_id,
                dados,
            )

            raise HTTPException(
                status_code=502,
                detail="O serviço de IA não retornou uma resposta válida.",
            )

        choice = choices[0]

        finish_reason = choice.get(
            "finish_reason"
        )

        resposta_ia = (
            choice
            .get("message", {})
            .get("content", "")
        )

        if resposta_ia is None:
            resposta_ia = ""

        resposta_ia = str(
            resposta_ia
        ).strip()

        logger.info(
            "request_id=%s | "
            "finish_reason=%s | "
            "completion_chars=%d",
            request_id,
            finish_reason,
            len(resposta_ia),
        )

        # ====================================================
        # DETECTA TRUNCAMENTO
        # ====================================================

        if finish_reason == "length":
            logger.error(
                "request_id=%s | "
                "resposta truncada pelo limite de tokens | "
                "completion_chars=%d",
                request_id,
                len(resposta_ia),
            )

            raise HTTPException(
                status_code=502,
                detail="A IA interrompeu a resposta por limite de tokens.",
            )

        if not resposta_ia:
            logger.error(
                "request_id=%s | "
                "9router retornou resposta vazia | "
                "response=%s",
                request_id,
                dados,
            )

            raise HTTPException(
                status_code=502,
                detail="O serviço de IA não retornou dados.",
            )

        # ====================================================
        # PARSE DO FORMATO TEXTUAL
        # ====================================================

        dados_extraidos = parsear_resposta_ia(
            resposta_ia,
            request_id,
        )

        # ====================================================
        # CORREÇÕES DETERMINÍSTICAS
        # ====================================================

        dados_corrigidos = organizar_links_e_precos(
            dados_extraidos,
            texto,
        )

        # ====================================================
        # VALIDAÇÃO FINAL
        # ====================================================

        try:
            oferta_validada = OfertaEstruturada(
                **dados_corrigidos
            )

        except Exception:
            logger.error(
                "request_id=%s | "
                "resposta não passou na validação | "
                "dados=%s",
                request_id,
                dados_corrigidos,
            )

            raise HTTPException(
                status_code=502,
                detail="A resposta da IA não possui o formato esperado.",
            )

        return oferta_validada.model_dump()

    except httpx.ConnectError:
        logger.exception(
            "request_id=%s | erro de conexão com 9router",
            request_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Serviço de IA temporariamente indisponível.",
        )

    except httpx.TimeoutException:
        duracao_ia = (
            time.perf_counter()
            - inicio_ia
        )

        logger.exception(
            "request_id=%s | 9router_timeout | duration=%.3fs",
            request_id,
            duracao_ia,
        )

        raise HTTPException(
            status_code=504,
            detail="O serviço de IA demorou demais para responder.",
        )

    except httpx.HTTPStatusError as e:
        logger.exception(
            "request_id=%s | 9router HTTP %s | response=%s",
            request_id,
            e.response.status_code,
            e.response.text,
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "O serviço de IA retornou "
                f"um erro HTTP {e.response.status_code}."
            ),
        )

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "request_id=%s | erro inesperado no 9router",
            request_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar a oferta.",
        )


# ============================================================
# REQUEST ID + LOGGING
# ============================================================

@app.middleware("http")
async def request_logging_middleware(
    request: Request,
    call_next,
):
    request_id = str(
        uuid.uuid4()
    )

    request.state.request_id = request_id

    inicio = time.perf_counter()

    logger.info(
        "request_id=%s | method=%s | path=%s | start",
        request_id,
        request.method,
        request.url.path,
    )

    try:
        response = await call_next(
            request
        )

    except Exception:
        duracao = (
            time.perf_counter()
            - inicio
        )

        logger.exception(
            "request_id=%s | "
            "method=%s | "
            "path=%s | "
            "exception | "
            "duration=%.3fs",
            request_id,
            request.method,
            request.url.path,
            duracao,
        )

        raise

    duracao = (
        time.perf_counter()
        - inicio
    )

    response.headers[
        "X-Request-ID"
    ] = request_id

    logger.info(
        "request_id=%s | "
        "method=%s | "
        "path=%s | "
        "status=%s | "
        "duration=%.3fs",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duracao,
    )

    return response


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
    }


# ============================================================
# READY
# ============================================================

@app.get("/ready")
async def ready():
    if http_client is None:
        raise HTTPException(
            status_code=503,
            detail="Cliente HTTP não inicializado.",
        )

    if not NINE_ROUTER_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="API key do 9router não configurada.",
        )

    try:
        response = await http_client.get(
            f"{NINE_ROUTER_BASE_URL}/models",
            headers={
                "Authorization": (
                    f"Bearer {NINE_ROUTER_API_KEY}"
                ),
            },
            timeout=10.0,
        )

        response.raise_for_status()

        return {
            "status": "ready",
            "9router": "ok",
            "model": MODEL_NAME,
        }

    except httpx.HTTPError as e:
        logger.exception(
            "9router não está disponível: %s",
            str(e),
        )

        raise HTTPException(
            status_code=503,
            detail="O serviço de IA não está disponível.",
        )


# ============================================================
# ENDPOINT PRINCIPAL
# ============================================================

@app.post("/extrair-oferta")
async def extrair_oferta(
    payload: TextoPayload,
    request: Request,
):
    request_id = request.state.request_id

    inicio = time.perf_counter()

    logger.info(
        "request_id=%s | "
        "oferta recebida | "
        "text_length=%d",
        request_id,
        len(payload.texto),
    )

    try:
        resultado = await chamar_9router(
            payload.texto,
            request_id,
        )

        duracao = (
            time.perf_counter()
            - inicio
        )

        logger.info(
            "request_id=%s | "
            "oferta concluída | "
            "duration=%.3fs",
            request_id,
            duracao,
        )

        return resultado

    except HTTPException:
        duracao = (
            time.perf_counter()
            - inicio
        )

        logger.warning(
            "request_id=%s | "
            "oferta falhou | "
            "duration=%.3fs",
            request_id,
            duracao,
        )

        raise

    except Exception:
        duracao = (
            time.perf_counter()
            - inicio
        )

        logger.exception(
            "request_id=%s | "
            "erro inesperado | "
            "duration=%.3fs",
            request_id,
            duracao,
        )

        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar a oferta.",
        )
````
