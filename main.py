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
    "NINE_ROUTER_BASE_URL",
    ""
).rstrip("/")

NINE_ROUTER_API_KEY = os.getenv(
    "NINE_ROUTER_API_KEY",
    ""
)

MODEL_NAME = os.getenv(
    "NINE_ROUTER_MODEL",
    ""
)

AI_CONNECT_TIMEOUT = float(
    os.getenv("AI_CONNECT_TIMEOUT", "10")
)

AI_READ_TIMEOUT = float(
    os.getenv("AI_READ_TIMEOUT", "60")
)

AI_WRITE_TIMEOUT = float(
    os.getenv("AI_WRITE_TIMEOUT", "30")
)

AI_POOL_TIMEOUT = float(
    os.getenv("AI_POOL_TIMEOUT", "10")
)

MAX_TEXT_LENGTH = int(
    os.getenv("MAX_TEXT_LENGTH", "10000")
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

    if http_client is not None:
        await http_client.aclose()
        http_client = None

    logger.info("Gliner encerrado")


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Gliner Offer Extraction API",
    version="1.5.0",
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
# UTILITÁRIOS
# ============================================================

def limpar_valor_nulo(valor):
    if valor is None:
        return None

    valor_str = str(valor).strip()

    if valor_str.lower() in {
        "",
        "null",
        "none",
        "nil",
        "n/a",
        "na",
    }:
        return None

    return valor_str


def extrair_primeiro_numero(texto: str):
    if not texto:
        return None

    match = re.search(
        r"\d+(?:[\.,]\d+)*",
        str(texto),
    )

    if match:
        return match.group(0)

    return None


def normalizar_preco(valor):
    valor = limpar_valor_nulo(valor)

    if valor is None:
        return None

    valor_str = str(valor).strip()

    valor_str = (
        valor_str
        .replace("R$", "")
        .replace("r$", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )

    numero = extrair_primeiro_numero(valor_str)

    if numero:
        numero = (
            numero
            .replace(".00", "")
            .replace(",00", "")
        )

        return f"R$ {numero}"

    return f"R$ {valor_str}"


def limpar_string_extraida(valor):
    if valor is None:
        return None

    valor = str(valor).strip()

    valor = valor.rstrip(",")

    if len(valor) >= 2:
        if (
            valor.startswith('"')
            and valor.endswith('"')
        ):
            valor = valor[1:-1]

        elif (
            valor.startswith("'")
            and valor.endswith("'")
        ):
            valor = valor[1:-1]

    return valor.strip()


# ============================================================
# JSON ROBUSTO
# ============================================================

def extrair_objeto_json_balanceado(texto: str):
    """
    Procura um objeto JSON real dentro da resposta,
    respeitando chaves dentro de strings.
    """

    if not texto:
        return None

    inicio = texto.find("{")

    if inicio == -1:
        return None

    profundidade = 0
    dentro_string = False
    escape = False

    for i in range(inicio, len(texto)):
        char = texto[i]

        if dentro_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                dentro_string = False

            continue

        if char == '"':
            dentro_string = True

        elif char == "{":
            profundidade += 1

        elif char == "}":
            profundidade -= 1

            if profundidade == 0:
                trecho = texto[inicio:i + 1]

                try:
                    return json.loads(trecho)
                except json.JSONDecodeError:
                    return None

    return None


def corrigir_json_fragmentado(texto: str):
    """
    Corrige respostas comuns de modelos que removem
    a primeira chave/abrechave ou devolvem JSON incompleto.

    Exemplo:

    _produto":"TV","preco_atual":"R$ 100"

    vira:

    {
      "nome_produto":"TV",
      "preco_atual":"R$ 100"
    }
    """

    if not texto:
        return None

    texto = texto.strip()

    texto = re.sub(
        r"```(?:json)?",
        "",
        texto,
        flags=re.IGNORECASE,
    )

    texto = texto.replace(
        "```",
        "",
    ).strip()

    if re.match(
        r'^_?produto"\s*:',
        texto,
        flags=re.IGNORECASE,
    ):
        texto = (
            '{"nome_produto":'
            + re.sub(
                r'^_?produto"\s*:',
                "",
                texto,
                count=1,
                flags=re.IGNORECASE,
            )
        )

    elif re.match(
        r'^_?produto\s*"\s*:',
        texto,
        flags=re.IGNORECASE,
    ):
        texto = (
            '{"nome_produto":'
            + re.sub(
                r'^_?produto\s*"\s*:',
                "",
                texto,
                count=1,
                flags=re.IGNORECASE,
            )
        )

    texto = texto.strip()

    if (
        texto.startswith("{")
        and not texto.endswith("}")
    ):
        texto += "}"

    try:
        resultado = json.loads(texto)

        if isinstance(resultado, dict):
            return resultado

    except json.JSONDecodeError:
        pass

    return None


# ============================================================
# EXTRAÇÃO DE JSON
# ============================================================

def extrair_json_da_resposta(resposta: str):
    if not resposta:
        return None

    texto = resposta.strip()

    texto = re.sub(
        r"^```(?:json)?\s*",
        "",
        texto,
        flags=re.IGNORECASE,
    )

    texto = re.sub(
        r"\s*```$",
        "",
        texto,
        flags=re.IGNORECASE,
    )

    texto = texto.strip()

    try:
        resultado = json.loads(texto)

        if isinstance(resultado, dict):
            return resultado

    except json.JSONDecodeError:
        pass

    resultado = extrair_objeto_json_balanceado(
        texto
    )

    if isinstance(resultado, dict):
        return resultado

    resultado = corrigir_json_fragmentado(
        texto
    )

    if isinstance(resultado, dict):
        return resultado

    return None


# ============================================================
# PARSER CHAVE / VALOR
# ============================================================

def extrair_formato_chave_valor(resposta: str):
    if not resposta:
        return None

    texto = resposta.strip()

    texto = re.sub(
        r"```(?:json)?",
        "",
        texto,
        flags=re.IGNORECASE,
    )

    texto = texto.replace(
        "```",
        "",
    ).strip()

    def extrair_campo(chaves, chaves_seguinte):
        nomes = "|".join(
            re.escape(chave)
            for chave in chaves
        )

        seguintes = "|".join(
            re.escape(chave)
            for chave in chaves_seguinte
        )

        padrao = (
            r"(?:^|[,{\n])\s*"
            r"(?:"
            + nomes
            + r")"
            r'\s*(?:"|)?\s*:\s*'
            r"(.+?)"
            r"(?=\s*,\s*(?:"
            + seguintes
            + r')\s*(?:"|)?\s*:|\s*$)'
        )

        match = re.search(
            padrao,
            texto,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if not match:
            return None

        return limpar_string_extraida(
            match.group(1)
        )

    nome_produto = extrair_campo(
        [
            "_produto",
            "produto",
            "nome_produto",
            '"_produto"',
            '"produto"',
            '"nome_produto"',
        ],
        [
            "preco_anterior",
            "preco_atual",
            "cupom",
            "link_cupom",
            "link_produto",
        ],
    )

    preco_anterior = extrair_campo(
        [
            "preco_anterior",
            '"preco_anterior"',
        ],
        [
            "preco_atual",
            "cupom",
            "link_cupom",
            "link_produto",
        ],
    )

    preco_atual = extrair_campo(
        [
            "preco_atual",
            '"preco_atual"',
        ],
        [
            "cupom",
            "link_cupom",
            "link_produto",
        ],
    )

    cupom = extrair_campo(
        [
            "cupom",
            '"cupom"',
        ],
        [
            "link_cupom",
            "link_produto",
        ],
    )

    link_cupom = extrair_campo(
        [
            "link_cupom",
            '"link_cupom"',
        ],
        [
            "link_produto",
        ],
    )

    link_produto = extrair_campo(
        [
            "link_produto",
            '"link_produto"',
        ],
        [],
    )

    if not nome_produto:
        return None

    return {
        "nome_produto": nome_produto,
        "preco_anterior": preco_anterior,
        "preco_atual": preco_atual,
        "cupom": cupom,
        "link_cupom": limpar_valor_nulo(
            link_cupom
        ),
        "link_produto": limpar_valor_nulo(
            link_produto
        ),
    }


# ============================================================
# PARSER PRINCIPAL
# ============================================================

def interpretar_resposta_ia(resposta: str):
    dados_json = extrair_json_da_resposta(
        resposta
    )

    if isinstance(dados_json, dict):
        return dados_json

    dados_fallback = extrair_formato_chave_valor(
        resposta
    )

    if isinstance(dados_fallback, dict):
        logger.warning(
            "Resposta da IA não era JSON válido; "
            "fallback chave/valor utilizado"
        )

        return dados_fallback

    return None


# ============================================================
# ORGANIZAÇÃO E VALIDAÇÃO
# ============================================================

def organizar_links_e_precos(
    dados_json,
    texto_bruto,
):
    if not isinstance(dados_json, dict):
        dados_json = {}

    linhas = [
        linha.strip()
        for linha in texto_bruto.split("\n")
        if linha.strip()
    ]

    # ========================================================
    # LINKS
    # ========================================================

    links_no_texto = re.findall(
        r"https?://[^\s<>\"']+",
        texto_bruto,
        flags=re.IGNORECASE,
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
        link.rstrip(".,;)")
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
            r"https?://[^\s<>\"']+",
            linha,
            flags=re.IGNORECASE,
        )

        if not links_na_linha:
            continue

        link = links_na_linha[0].rstrip(
            ".,;)"
        )

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

    if (
        not link_produto_detectado
        and links_lojas
    ):
        link_produto_detectado = links_lojas[0]

    if (
        len(links_lojas) >= 2
        and not link_cupom_detectado
    ):
        for link in links_lojas:
            if link != link_produto_detectado:
                link_cupom_detectado = link
                break

    if (
        not link_produto_detectado
        and links_no_texto
    ):
        links_limpos = [
            link.rstrip(".,;)")
            for link in links_no_texto
            if "t.me" not in link.lower()
            and "telegram" not in link.lower()
            and "whatsapp" not in link.lower()
        ]

        if links_limpos:
            link_produto_detectado = (
                links_limpos[0]
            )

    if (
        link_produto_detectado
        == link_cupom_detectado
    ):
        link_cupom_detectado = None

    # ========================================================
    # LINK PRODUTO
    # ========================================================

    dados_json["link_produto"] = (
        str(link_produto_detectado)
        if link_produto_detectado
        else limpar_valor_nulo(
            dados_json.get("link_produto")
        )
    )

    # ========================================================
    # LINK CUPOM
    # ========================================================

    dados_json["link_cupom"] = (
        str(link_cupom_detectado)
        if link_cupom_detectado
        else limpar_valor_nulo(
            dados_json.get("link_cupom")
        )
    )

    # ========================================================
    # PREÇOS
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
    # PADRONIZAÇÃO
    # ========================================================

    dados_json["preco_atual"] = normalizar_preco(
        dados_json.get("preco_atual")
    )

    dados_json["preco_anterior"] = normalizar_preco(
        dados_json.get("preco_anterior")
    )

    if (
        dados_json.get("preco_anterior")
        == dados_json.get("preco_atual")
    ):
        dados_json["preco_anterior"] = None

    # ========================================================
    # CUPOM
    # ========================================================

    cupom_ia = limpar_valor_nulo(
        dados_json.get("cupom")
    )

    if cupom_ia:
        cupom_limpo = (
            cupom_ia
            .replace("🎟️", "")
            .replace("🎟", "")
            .strip()
        )

        if (
            cupom_limpo.lower()
            not in texto_bruto.lower()
        ):
            dados_json["cupom"] = None
        else:
            dados_json["cupom"] = cupom_limpo

    else:
        dados_json["cupom"] = None

    # ========================================================
    # NOME
    # ========================================================

    nome_produto = limpar_valor_nulo(
        dados_json.get("nome_produto")
    )

    if nome_produto:
        dados_json["nome_produto"] = (
            nome_produto
            .strip()
            .strip('"')
            .strip("'")
            .strip()
        )

    return dados_json


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

    if not NINE_ROUTER_BASE_URL:
        logger.error(
            "request_id=%s | NINE_ROUTER_BASE_URL não configurada",
            request_id,
        )

        raise HTTPException(
            status_code=503,
            detail="URL do 9router não configurada.",
        )

    if not MODEL_NAME:
        logger.error(
            "request_id=%s | NINE_ROUTER_MODEL não configurado",
            request_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Modelo de IA não configurado.",
        )

    # ========================================================
    # PROMPT
    # ========================================================

    prompt_sistema = """
Você é um extrator de dados de ofertas.

Sua única tarefa é transformar o texto recebido em um objeto JSON.

RESPONDA SOMENTE COM JSON VÁLIDO.

NÃO escreva:
- explicações
- comentários
- markdown
- ```json
- texto antes do JSON
- texto depois do JSON

Use EXATAMENTE estas seis chaves:

{
  "nome_produto": "string",
  "preco_anterior": "string ou null",
  "preco_atual": "string",
  "cupom": "string ou null",
  "link_cupom": "string ou null",
  "link_produto": "string ou null"
}

REGRAS:

1. nome_produto:
Extraia o nome comercial completo do produto.
Não invente informações.
Não inclua preço, cupom ou links no nome.

2. preco_anterior:
É o preço antigo indicado por "de".
Se não existir, use null.

3. preco_atual:
É o preço atual indicado por "por".
Se não existir claramente, use null.

4. cupom:
Somente informe um código de cupom se o código estiver escrito explicitamente no texto.
Nunca invente cupom.

5. link_cupom:
Somente informe um link quando o texto indicar que aquele link é para cupom, resgate ou coleta de cupom.
Caso contrário, use null.

6. link_produto:
Informe o link da oferta/produto quando existir.
Nunca invente links.

7. Não altere números.
A aplicação fará a padronização dos preços.

8. Se uma informação não existir, use null.

IMPORTANTE:
A resposta deve ser um ÚNICO objeto JSON válido.
""".strip()

    payload_dados = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": prompt_sistema,
            },
            {
                "role": "user",
                "content": (
                    "Texto da oferta:\n"
                    f"{texto}"
                ),
            },
        ],
        "temperature": 0.0,
        "top_p": 0.1,
        "max_tokens": 7000,
        "stream": False,
        "enable_thinking": False,
    }

    headers = {
        "Authorization": (
            f"Bearer {NINE_ROUTER_API_KEY}"
        ),
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

        usage = dados.get("usage") or {}

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
        # CHOICES
        # ====================================================

        choices = dados.get(
            "choices",
            [],
        )

        if not choices:
            logger.error(
                "request_id=%s | "
                "9router não retornou choices | "
                "response=%s",
                request_id,
                dados,
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "O serviço de IA não retornou "
                    "uma resposta válida."
                ),
            )

        choice = choices[0]

        finish_reason = choice.get(
            "finish_reason"
        )

        message = choice.get(
            "message",
            {},
        )

        resposta_ia = message.get(
            "content",
            "",
        )

        # ====================================================
        # ALGUNS MODELOS PODEM RETORNAR CONTENT NONE
        # ====================================================

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
        # MODELOS DE SAFETY / RESPOSTAS NÃO UTILIZÁVEIS
        # ====================================================

        respostas_invalidas = {
            "user safety: safe",
            "safe",
            "unsafe",
            "blocked",
            "content blocked",
        }

        if resposta_ia.lower() in respostas_invalidas:
            logger.error(
                "request_id=%s | "
                "modelo retornou resposta de safety "
                "em vez da extração | resposta=%s | model=%s",
                request_id,
                resposta_ia,
                dados.get(
                    "model",
                    MODEL_NAME,
                ),
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "O modelo selecionado não retornou "
                    "os dados da oferta."
                ),
            )

        if not resposta_ia:
            logger.error(
                "request_id=%s | "
                "9router retornou resposta vazia | "
                "finish_reason=%s",
                request_id,
                finish_reason,
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "O serviço de IA não retornou dados."
                ),
            )

        # ====================================================
        # INTERPRETAÇÃO ROBUSTA
        # ====================================================

        json_puro = interpretar_resposta_ia(
            resposta_ia
        )

        if not isinstance(
            json_puro,
            dict,
        ):
            logger.error(
                "request_id=%s | "
                "não foi possível interpretar resposta da IA | "
                "resposta=%s",
                request_id,
                resposta_ia,
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "O serviço de IA retornou "
                    "dados em formato inválido."
                ),
            )

        # ====================================================
        # CORREÇÕES DETERMINÍSTICAS
        # ====================================================

        json_corrigido = organizar_links_e_precos(
            json_puro,
            texto,
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
                "request_id=%s | "
                "resposta não passou na validação | "
                "dados=%s",
                request_id,
                json_corrigido,
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "A resposta da IA não possui "
                    "o formato esperado."
                ),
            )

        # ====================================================
        # LOG FINAL
        # ====================================================

        logger.info(
            "request_id=%s | "
            "extração_validada | "
            "produto=%s | "
            "preco_anterior=%s | "
            "preco_atual=%s | "
            "cupom=%s | "
            "link_produto=%s",
            request_id,
            oferta_validada.nome_produto,
            oferta_validada.preco_anterior,
            oferta_validada.preco_atual,
            oferta_validada.cupom,
            oferta_validada.link_produto,
        )

        return oferta_validada.model_dump()

    except httpx.ConnectError:
        logger.exception(
            "request_id=%s | "
            "erro de conexão com 9router",
            request_id,
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "Serviço de IA temporariamente "
                "indisponível."
            ),
        )

    except httpx.TimeoutException:
        duracao_ia = (
            time.perf_counter()
            - inicio_ia
        )

        logger.exception(
            "request_id=%s | "
            "9router_timeout | "
            "duration=%.3fs",
            request_id,
            duracao_ia,
        )

        raise HTTPException(
            status_code=504,
            detail=(
                "O serviço de IA demorou "
                "demais para responder."
            ),
        )

    except httpx.HTTPStatusError as e:
        logger.exception(
            "request_id=%s | "
            "9router HTTP %s | "
            "response=%s",
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
            "request_id=%s | "
            "erro inesperado no 9router",
            request_id,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Erro interno ao processar a oferta."
            ),
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
        "request_id=%s | "
        "method=%s | "
        "path=%s | start",
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
            detail=(
                "Cliente HTTP não inicializado."
            ),
        )

    if not NINE_ROUTER_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "API key do 9router não configurada."
            ),
        )

    if not NINE_ROUTER_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail=(
                "URL do 9router não configurada."
            ),
        )

    if not MODEL_NAME:
        raise HTTPException(
            status_code=503,
            detail=(
                "Modelo de IA não configurado."
            ),
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
            detail=(
                "O serviço de IA não está disponível."
            ),
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
            detail=(
                "Erro interno ao processar a oferta."
            ),
        )
