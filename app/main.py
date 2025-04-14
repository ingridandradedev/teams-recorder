from fastapi import FastAPI, Query
from app.recorder import gravar_reuniao

app = FastAPI(title="Teams Recorder API")

@app.get("/gravar")
def iniciar_gravacao(url: str = Query(..., description="URL da reunião do Teams")):
    resultado = gravar_reuniao(url)
    return resultado
