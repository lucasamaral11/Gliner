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

```
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
```

# ============================================================

# FASTAPI

# ============================================================

app = FastAPI(
title="Gliner Offer Extraction API",
version="1.4.0",
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

def limpar_valor(valor):
if valor is None:
return None

```
valor = str(valor).strip()

if not valor:
    return None

if valor.lower() in {
    "null",
    "none",
    "n/a",
    "na",
    "não informado",
    "nao informado",
}:
    return None

return valor
```

def normalizar_preco(valor):
valor = limpar_valor(valor)

```
if valor is None:
    return None

valor = (
    valor
    .replace("R$", "")
    .replace("r$", "")
    .replace("(", "")
    .replace(")", "")
    .strip()
)

# Remove textos como "no Pix", "à vista", etc.
match = re.search(
    r"\d+(?:[\.,]\d+)*",
    valor,
)

if not match:
    return None

numero = match.group(0)

numero = (
    numero
    .replace(".00", "")
    .replace(",00", "")
)

return f"R$ {numero}"
```

def limpar_link(link):
if link is None:
return None

```
link = str(link).strip()

if link.lower() in {
    "null",
    "none",
    "",
}:
    return None

# Remove pontuação que possa vir grudada ao link
link = link.rstrip(
    ".,;)]}>\"'"
)

if not link.startswith("http"):
    return None

return link
```

# ============================================================

# PARSER DA RESPOSTA DA IA

# ============================================================

def parsear_resposta_ia(resposta):
"""
Aceita respostas como:

````
nome_produto: Smart TV LG 50" UHD 4K AI UA85,
preco_anterior: R$ 3.099,
preco_atual: R$ 1.767,
cupom: QUEROPROMOML,
link_cupom: null,
link_produto: https://meli.la/2v3p6sJ

Também tolera:
_produto:
nome produto:
pequenas variações de espaços.
"""

texto = resposta.strip()

# Remove markdown caso algum modelo coloque
# ``` ou ```text
texto = re.sub(
    r"```(?:text|json)?",
    "",
    texto,
    flags=re.IGNORECASE,
)

texto = texto.replace("```", "").strip()

# --------------------------------------------------------
# Localiza cada campo usando lookahead.
# Assim valores podem conter vírgulas.
# --------------------------------------------------------

padroes = {
    "nome_produto": [
        r"(?:^|[\n,])\s*nome_produto\s*:\s*(.*?)(?=\s*,\s*preco_anterior\s*:|\s*,\s*preco_atual\s*:|\s*,\s*cupom\s*:|\s*,\s*link_cupom\s*:|\s*,\s*link_produto\s*:|$)",

        r"(?:^|[\n,])\s*nome\s+produto\s*:\s*(.*?)(?=\s*,\s*preco_anterior\s*:|\s*,\s*preco_atual\s*:|\s*,\s*cupom\s*:|\s*,\s*link_cupom\s*:|\s*,\s*link_produto\s*:|$)",

        r"(?:^|[\n,])\s*_produto\s*:\s*(.*?)(?=\s*,\s*preco_anterior\s*:|\s*,\s*preco_atual\s*:|\s*,\s*cupom\s*:|\s*,\s*link_cupom\s*:|\s*,\s*link_produto\s*:|$)",
    ],

    "preco_anterior": [
        r"(?:^|[\n,])\s*preco_anterior\s*:\s*(.*?)(?=\s*,\s*preco_atual\s*:|\s*,\s*cupom\s*:|\s*,\s*link_cupom\s*:|\s*,\s*link_produto\s*:|$)",
    ],

    "preco_atual": [
        r"(?:^|[\n,])\s*preco_atual\s*:\s*(.*?)(?=\s*,\s*cupom\s*:|\s*,\s*link_cupom\s*:|\s*,\s*link_produto\s*:|$)",
    ],

    "cupom": [
        r"(?:^|[\n,])\s*cupom\s*:\s*(.*?)(?=\s*,\s*link_cupom\s*:|\s*,\s*link_produto\s*:|$)",
    ],

    "link_cupom": [
        r"(?:^|[\n,])\s*link_cupom\s*:\s*(.*?)(?=\s*,\s*link_produto\s*:|$)",
    ],

    "link_produto": [
        r"(?:^|[\n,])\s*link_produto\s*:\s*(.*?)$",
    ],
}

dados = {}

for campo, lista_padroes in padroes.items():
    valor_encontrado = None

    for padrao in lista_padroes:
        match = re.search(
            padrao,
            texto,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if match:
            valor_encontrado = match.group(1).strip()
            break

    dados[campo] = limpar_valor(
        valor_encontrado
    )

# --------------------------------------------------------
# Remove aspas externas
# --------------------------------------------------------

for campo in dados:
    if isinstance(dados[campo], str):
        dados[campo] = dados[campo].strip()

        if (
            len(dados[campo]) >= 2
            and dados[campo][0] == '"'
            and dados[campo][-1] == '"'
        ):
            dados[campo] = dados[campo][1:-1].strip()

# --------------------------------------------------------
# Validação do nome
# --------------------------------------------------------

nome = dados.get("nome_produto")

if nome:
    nome = nome.strip(" ,;")

if not nome:
    logger.error(
        "não foi possível extrair nome_produto | resposta=%s",
        resposta,
    )

    raise HTTPException(
        status_code=502,
        detail="O serviço de IA não retornou o nome do produto.",
    )

dados["nome_produto"] = nome

# --------------------------------------------------------
# Normalização dos preços
# --------------------------------------------------------

dados["preco_anterior"] = normalizar_preco(
    dados.get("preco_anterior")
)

dados["preco_atual"] = normalizar_preco(
    dados.get("preco_atual")
)

# --------------------------------------------------------
# Cupom
# --------------------------------------------------------

cupom = limpar_valor(
    dados.get("cupom")
)

if cupom:
    cupom = (
        cupom
        .replace("🎟️", "")
        .replace("🎟", "")
        .strip()
        .strip(",")
    )

    if cupom.lower() in {
        "null",
        "none",
    }:
        cupom = None

dados["cupom"] = cupom

# --------------------------------------------------------
# Links
# --------------------------------------------------------

dados["link_cupom"] = limpar_link(
    dados.get("link_cupom")
)

dados["link_produto"] = limpar_link(
    dados.get("link_produto")
)

return dados
````

# ============================================================

# ORGANIZAÇÃO E VALIDAÇÃO DETERMINÍSTICA

# ============================================================

def organizar_links_e_precos(
dados,
texto_bruto,
):
linhas = [
linha.strip()
for linha in texto_bruto.split("\n")
if linha.strip()
]

```
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
    link.rstrip(".,;)]}>\"'")
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

    link = links_na_linha[0].rstrip(
        ".,;)]}>\"'"
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

if not link_produto_detectado and links_lojas:
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
        link.rstrip(".,;)]}>\"'")
        for link in links_no_texto
        if "t.me" not in link.lower()
        and "whatsapp" not in link.lower()
    ]

    if links_limpos:
        link_produto_detectado = links_limpos[0]

if (
    link_produto_detectado
    == link_cupom_detectado
):
    link_cupom_detectado = None

# ========================================================
# O TEXTO ORIGINAL TEM PRIORIDADE PARA LINKS
# ========================================================

if link_produto_detectado:
    dados["link_produto"] = (
        link_produto_detectado
    )

if link_cupom_detectado:
    dados["link_cupom"] = (
        link_cupom_detectado
    )

# ========================================================
# 2. PREÇOS DETERMINÍSTICOS
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
    dados["preco_anterior"] = (
        f"R$ {match_linha_precos.group(1)}"
    )

    dados["preco_atual"] = (
        f"R$ {match_linha_precos.group(2)}"
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
            dados["preco_anterior"] = (
                f"R$ {match_de.group(1)}"
            )

            dados["preco_atual"] = (
                f"R$ {match_por.group(1)}"
            )

# ========================================================
# 3. GARANTIA DE FORMATO DOS PREÇOS
# ========================================================

dados["preco_anterior"] = normalizar_preco(
    dados.get("preco_anterior")
)

dados["preco_atual"] = normalizar_preco(
    dados.get("preco_atual")
)

if (
    dados.get("preco_anterior")
    == dados.get("preco_atual")
):
    dados["preco_anterior"] = None

# ========================================================
# 4. VALIDAÇÃO DO CUPOM
# ========================================================

cupom_ia = limpar_valor(
    dados.get("cupom")
)

if cupom_ia:
    cupom_limpo = (
        cupom_ia
        .replace("🎟️", "")
        .replace("🎟", "")
        .strip()
    )

    # O cupom precisa realmente existir no texto.
    if (
        cupom_limpo.lower()
        not in texto_bruto.lower()
    ):
        dados["cupom"] = None

    # Não aceitar link como cupom.
    elif any(
        cupom_limpo in link
        for link in links_no_texto
    ):
        dados["cupom"] = None

    else:
        dados["cupom"] = cupom_limpo

else:
    dados["cupom"] = None

return dados
```

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

```
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
# PROMPT REDUZIDO
# ========================================================

prompt_sistema = (
    "Extraia os dados da oferta. "
    "Responda somente nesta linha, sem explicações:\n"
    "\n"
    "nome_produto: X, preco_anterior: X, preco_atual: X, "
    "cupom: X, link_cupom: X, link_produto: X\n"
    "\n"
    "Regras: "
    "nome_produto é o nome comercial completo; "
    "cupom somente se estiver escrito no texto; "
    "dados ausentes devem ser null; "
    "não invente dados."
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
            "content": texto,
        },
    ],

    "temperature": 0.0,
    "top_p": 0.1,

    # A resposta tem aproximadamente 160-200 caracteres.
    # 300 é suficiente para o formato solicitado.
    "max_tokens": 300,

    "stream": False,
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

    dados_resposta = response.json()

    # ====================================================
    # MÉTRICAS
    # ====================================================

    usage = dados_resposta.get(
        "usage",
        {},
    )

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

    cost = dados_resposta.get(
        "cost"
    )

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
        dados_resposta.get(
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
    # RESPOSTA
    # ====================================================

    choices = dados_resposta.get(
        "choices",
        []
    )

    if not choices:
        logger.error(
            "request_id=%s | "
            "9router não retornou choices | response=%s",
            request_id,
            dados_resposta,
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

    if not resposta_ia:
        logger.error(
            "request_id=%s | "
            "9router retornou resposta vazia | response=%s",
            request_id,
            dados_resposta,
        )

        raise HTTPException(
            status_code=502,
            detail="O serviço de IA não retornou dados.",
        )

    # ====================================================
    # PARSE DO TEXTO
    # ====================================================

    dados_extraidos = parsear_resposta_ia(
        resposta_ia
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
        "request_id=%s | "
        "9router_timeout | "
        "duration=%.3fs",
        request_id,
        duracao_ia,
    )

    raise HTTPException(
        status_code=504,
        detail="O serviço de IA demorou demais para responder.",
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
        detail="Erro interno ao processar a oferta.",
    )
```

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

```
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
```

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

```
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
```

# ============================================================

# ENDPOINT PRINCIPAL

# ============================================================

@app.post("/extrair-oferta")
async def extrair_oferta(
payload: TextoPayload,
request: Request,
):
request_id = request.state.request_id

```
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
```
