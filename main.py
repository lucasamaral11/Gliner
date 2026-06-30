import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from gliner import GLiNER
import uvicorn

app = FastAPI(title="GLiNER High-Concurrency API")

model = None
executor = ThreadPoolExecutor(max_workers=2) 

class TextoPayload(BaseModel):
    texto: str

def processar_gliner(texto: str):
    global model
    if model is None:
        print("Carregando modelo GLiNER otimizado...")
        model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
        print("Modelo carregado com sucesso!")
        
    # Rótulos simplificados e universais para o modelo entender melhor
    labels = ["product", "old price", "new price", "coupon", "url"]
    
    # Reduzido o threshold para 0.25 para aumentar a sensibilidade em português
    entities = model.predict_entities(texto, labels, threshold=0.25)
    
    json_resultado = {
        "nome_produto": None, "preco_anterior": None, 
        "preco_atual": None, "cupom": None, "link_produto": None
    }
    
    for entity in entities:
        label = entity["label"]
        text = entity["text"].strip()
        
        if label == "product":
            json_resultado["nome_produto"] = text
        elif label == "old price":
            json_resultado["preco_anterior"] = text
        elif label == "new price":
            json_resultado["preco_atual"] = text
        elif label == "coupon":
            # Evita capturar a palavra literal "cupom" como o código do cupom
            if text.lower() != "cupom":
                json_resultado["cupom"] = text
        elif label == "url":
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
    uvicorn.run("main:app", host="0.0.0.0", port=8800)
