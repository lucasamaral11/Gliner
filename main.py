import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import json
import re

app = FastAPI(title="Qwen Ultra-Light Extractor API")

model = None
tokenizer = None
executor = ThreadPoolExecutor(max_workers=2)

class TextoPayload(BaseModel):
    texto: str

def processar_qwen(texto: str):
    global model, tokenizer
    
    # Carrega o modelo de 350MB na primeira requisição
    if model is None:
        print("Carregando Qwen 0.5B Coder...")
        model_name = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype="auto",
            device_map="auto"
        )
        print("Qwen pronto!")

    # Prompt estrito para garantir que ele só responda o JSON correto
    prompt_sistema = (
        "Você é um extrator de dados de ofertas. Responda APENAS com um objeto JSON válido no formato:\n"
        "{\n"
        "  \"nome_produto\": \"string\",\n"
        "  \"preco_anterior\": \"string ou null\",\n"
        "  \"preco_atual\": \"string\",\n"
        "  \"cupom\": \"string ou null\",\n"
        "  \"link_produto\": \"string\"\n"
        "}\n"
        "Não adicione textos extras, introduções ou blocos de código markdown. Responda apenas o JSON puro."
    )

    mensagens = [
        {"role": "system", "content": prompt_sistema},
        {"role": "user", "content": f"Extraia os dados deste texto:\n{texto}"}
    ]

    text = tokenizer.apply_chat_template(mensagens, tokenize=False, add_generation_prompt=True)
    model_inputs = tokenizer([text], return_tensors="pt").to(model.device)

    # Gera a resposta de forma ultra rápida por ser um modelo minúsculo
    generated_ids = model.generate(**model_inputs, max_new_tokens=256, temperature=0.1)
    generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)]
    
    resposta = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

    # Limpeza de segurança caso o modelo insira markdown por teimosia
    resposta_limpa = re.sub(r"```json\s*|```", "", resposta).strip()

    try:
        return json.loads(resposta_limpa)
    except Exception:
        # Retorna a string bruta se houver erro de parsing
        return {"erro": "Falha ao gerar JSON limpo", "resposta_bruta": resposta}

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        loop = asyncio.get_running_loop()
        resultado = await loop.run_in_executor(executor, processar_qwen, payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8800)
