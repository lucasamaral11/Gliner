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

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO"
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
    )
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

    logger.info(
        "Gliner iniciado | model=%s | ollama=%s",
        MODEL_NAME,
        OLLAMA_URL,
    )

    yield

    if http_client:
        await http_client.aclose()

    logger.info("Gliner encerrado")


app = FastAPI(
    title="Qwen Strict Schema API",
    version="1.2.0",
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

def organizar_links_e_precos(
    dados_json,
    texto_bruto
):

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

    if not link_produto_detectado and links_lojas:
        link_produto_detectado = links_lojas[0]

    if len(links_lojas) >= 2 and not link_cupom_detectado:

        for link in links_lojas:

            if link != link_produto_detectado:
                link_cupom_detectado = link
                break

    if not link_produto_detectado and links_no_texto:

        links_limpos = [
            l
            for l in links_no_texto
            if "t.me" not in l
            and "whatsapp" not in l
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

async def chamar_ollama(
    texto: str,
    request_id: str
):

    if http_client is None:

        logger.error(
            "request_id=%s | http_client não inicializado",
            request_id,
        )

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

    inicio_ollama = time.perf_counter()

    try:

        response = await http_client.post(
            OLLAMA_URL,
            json=payload_dados
        )

        duracao_ollama_http = (
            time.perf_counter()
            - inicio_ollama
        )

        response.raise_for_status()

        dados = response.json()

        # ====================================================
        # MÉTRICAS NATIVAS DO OLLAMA
        # ====================================================

        total_duration_ns = dados.get(
            "total_duration"
        )

        load_duration_ns = dados.get(
            "load_duration"
        )

        prompt_eval_duration_ns = dados.get(
            "prompt_eval_duration"
        )

        eval_duration_ns = dados.get(
            "eval_duration"
        )

        prompt_eval_count = dados.get(
            "prompt_eval_count"
        )

        eval_count = dados.get(
            "eval_count"
        )

        # Converter nanossegundos para segundos
        def ns_to_seconds(value):

            if value is None:
                return None

            return value / 1_000_000_000

        total_duration = ns_to_seconds(
            total_duration_ns
        )

        load_duration = ns_to_seconds(
            load_duration_ns
        )

        prompt_eval_duration = ns_to_seconds(
            prompt_eval_duration_ns
        )

        eval_duration = ns_to_seconds(
            eval_duration_ns
        )

        # ====================================================
        # TOKENS / SEGUNDO
        # ====================================================

        eval_tokens_per_second = None

        if (
            eval_count is not None
            and eval_duration is not None
            and eval_duration > 0
        ):
            eval_tokens_per_second = (
                eval_count / eval_duration
            )

        prompt_tokens_per_second = None

        if (
            prompt_eval_count is not None
            and prompt_eval_duration is not None
            and prompt_eval_duration > 0
        ):
            prompt_tokens_per_second = (
                prompt_eval_count
                / prompt_eval_duration
            )

        # ====================================================
        # LOG COMPLETO DAS MÉTRICAS
        # ====================================================

        logger.info(
            "request_id=%s | "
            "ollama_metrics | "
            "http_duration=%.3fs | "
            "total_duration=%s | "
            "load_duration=%s | "
            "prompt_eval_duration=%s | "
            "eval_duration=%s | "
            "prompt_eval_count=%s | "
            "eval_count=%s | "
            "prompt_tokens_sec=%s | "
            "eval_tokens_sec=%s",
            request_id,
            duracao_ollama_http,
            (
                f"{total_duration:.3f}s"
                if total_duration is not None
                else "N/A"
            ),
            (
                f"{load_duration:.3f}s"
                if load_duration is not None
                else "N/A"
            ),
            (
                f"{prompt_eval_duration:.3f}s"
                if prompt_eval_duration is not None
                else "N/A"
            ),
            (
                f"{eval_duration:.3f}s"
                if eval_duration is not None
                else "N/A"
            ),
            prompt_eval_count,
            eval_count,
            (
                f"{prompt_tokens_per_second:.2f}"
                if prompt_tokens_per_second is not None
                else "N/A"
            ),
            (
                f"{eval_tokens_per_second:.2f}"
                if eval_tokens_per_second is not None
                else "N/A"
            ),
        )

        # ====================================================
        # RESPOSTA DA IA
        # ====================================================

        resposta_ia = (
            dados
            .get("message", {})
            .get("content", "")
            .strip()
        )

        if not resposta_ia:

            logger.error(
                "request_id=%s | Ollama retornou resposta vazia",
                request_id,
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

        # ====================================================
        # PARSE DO JSON
        # ====================================================

        try:

            json_puro = json.loads(
                resposta_limpa
            )

        except json.JSONDecodeError:

            logger.error(
                "request_id=%s | JSON inválido retornado pelo Ollama",
                request_id,
            )

            raise HTTPException(
                status_code=502,
                detail="O serviço de IA retornou dados inválidos."
            )

        # ====================================================
        # CORREÇÕES DETERMINÍSTICAS
        # ====================================================

        json_corrigido = organizar_links_e_precos(
            json_puro,
            texto
        )

        # ====================================================
        # VALIDAÇÃO FINAL
        # ====================================================

        try:

            oferta_validada = OfertaEstruturada(
                **json_corrigido
            )

        except Exception:

            logger.error(
                "request_id=%s | resposta não passou na validação",
                request_id,
            )

            raise HTTPException(
                status_code=502,
                detail="A resposta da IA não possui o formato esperado."
            )

        return oferta_validada.model_dump()

    except httpx.ConnectError:

        logger.exception(
            "request_id=%s | erro de conexão com Ollama",
            request_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Serviço de IA temporariamente indisponível."
        )

    except httpx.TimeoutException:

        duracao_ollama = (
            time.perf_counter()
            - inicio_ollama
        )

        logger.exception(
            "request_id=%s | ollama_timeout | duration=%.3fs",
            request_id,
            duracao_ollama,
        )

        raise HTTPException(
            status_code=504,
            detail="O serviço de IA demorou demais para responder."
        )

    except httpx.HTTPStatusError as e:

        logger.exception(
            "request_id=%s | Ollama HTTP %s",
            request_id,
            e.response.status_code,
        )

        raise HTTPException(
            status_code=502,
            detail="O serviço de IA retornou um erro."
        )

    except HTTPException:
        raise

    except Exception:

        logger.exception(
            "request_id=%s | erro inesperado no Ollama",
            request_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar a oferta."
        )


# ============================================================
# REQUEST ID + LOGGING
# ============================================================

@app.middleware("http")
async def request_logging_middleware(
    request: Request,
    call_next
):

    request_id = str(uuid.uuid4())

    request.state.request_id = request_id

    inicio = time.perf_counter()

    logger.info(
        "request_id=%s | method=%s | path=%s | start",
        request_id,
        request.method,
        request.url.path,
    )

    try:

        response = await call_next(request)

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

    response.headers["X-Request-ID"] = request_id

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
        "status": "ok"
    }


# ============================================================
# READY
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
            OLLAMA_URL.replace(
                "/api/chat",
                "/api/tags"
            ),
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
async def extrair_oferta(
    payload: TextoPayload,
    request: Request
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

        resultado = await chamar_ollama(
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
            detail="Erro interno ao processar a oferta."
        )
