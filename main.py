import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from gliner import GLiNER
import uvicorn

app = FastAPI(title="GLiNER High-Concurrency API")

# Inicializa a variável do modelo global vazia
model = None
executor = ThreadPoolExecutor(max_workers=2) 

class TextoPayload(BaseModel):
    texto: str

def processar_gliner(texto: str):
    global model
    # Carrega o modelo na primeira requisição se ainda não estiver na memória
    if model is None:
        print("Carregando modelo GLiNER pela primeira vez...")
        model = GLiNER.from_pretrained("urchinsec/gliner_medium-v2.1")
        print("Modelo carregado com sucesso!")
        
    labels = ["nome do produto", "preço anterior", "preço atual", "cupom", "link do produto"]
    entities = model.predict_entities(texto, labels, threshold=0.4)
    
    json_resultado = {
        "nome_produto": None, "preco_anterior": None, 
        "preco_atual": None, "cupom": None, "link_produto": None
    }
    
    for entity in entities:
        label = entity["label"]
        text = entity["text"].strip()
        
        if label == "nome do produto":
            json_resultado["nome_produto"] = text
        elif label == "preço anterior":
            json_resultado["preco_anterior"] = text
        elif label == "preço atual":
            json_resultado["preco_atual"] = text
        elif label == "cupom":
            json_resultado["cupom"] = text
        elif label == "link do produto":
            if "prime" not in text.lower() or json_resultado["link_produto"] is None:
                json_resultado["link_produto"] = text
                
    return json_resultado

@app.post("/extrair-oferta")
async def extrair_oferta(payload: TextoPayload):
    try:
        loop = asyncio.get_running_loop()
        resultado = await loop.run_in_executor(executor, processar_gliner, payload.texto)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Fallback caso rode localmente fora do Docker
    uvicorn.run("main:app", host="0.0.0.0", port=8800)
