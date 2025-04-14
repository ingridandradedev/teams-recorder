# ✅ app/main.py
from fastapi import FastAPI, Query
from app.recorder import gravar_reuniao
import asyncio
import concurrent.futures

app = FastAPI(title="Teams Recorder API")

executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

@app.get("/gravar")
async def iniciar_gravacao(url: str = Query(..., description="URL da reunião do Teams")):
    loop = asyncio.get_running_loop()
    resultado = await loop.run_in_executor(executor, gravar_reuniao, url)
    return resultado

@app.get("/health")
def health():
    return {"status": "ok"}
