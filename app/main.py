import uuid
import threading
import json
from typing import Dict
from fastapi import FastAPI, Query, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from app.recorder import gravar_reuniao_stream

app = FastAPI(title="Teams Recorder API")

# --- Configuração de Autenticação ---
# ATENÇÃO: Para produção, carregue este token de uma variável de ambiente ou de um gerenciador de segredos!
# Exemplo: API_TOKEN = os.getenv("TEAMS_RECORDER_API_TOKEN", "fallback_token_se_nao_definido")
EXPECTED_API_TOKEN = "COLOQUE_SEU_TOKEN_SECRETO_E_FORTE_AQUI" 

async def verify_api_key(x_api_token: str = Header(None, description="Seu token de API secreto.")):
    """
    Dependência para verificar o token da API no cabeçalho X-API-Token.
    """
    if not x_api_token:
        raise HTTPException(status_code=401, detail="Cabeçalho X-API-Token ausente.")
    if x_api_token != EXPECTED_API_TOKEN:
        raise HTTPException(status_code=403, detail="Token da API inválido.")
    return x_api_token # Retorna o token se válido, pode ser usado no endpoint se necessário
# --- Fim da Configuração de Autenticação ---


# mapa de flags de parada
STOP_EVENTS: Dict[str, threading.Event] = {}

@app.get("/gravar", response_class=StreamingResponse)
async def iniciar_gravacao(
    url: str = Query(..., description="URL da reunião do Teams"),
    api_key: str = Depends(verify_api_key) # Adiciona a dependência de autenticação
):
    recording_id = str(uuid.uuid4())
    stop_event = threading.Event()
    STOP_EVENTS[recording_id] = stop_event

    def event_generator():
        # cada yield é um SSE: data: {...}\n\n
        try:
            for msg in gravar_reuniao_stream(url, stop_event):
                payload = {**msg, "recording_id": recording_id}
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            STOP_EVENTS.pop(recording_id, None)


    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/stop/{recording_id}")
def stop_gravacao(
    recording_id: str,
    api_key: str = Depends(verify_api_key) # Adiciona a dependência de autenticação
):
    ev = STOP_EVENTS.get(recording_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Recording ID não encontrado ou já parado.")
    ev.set()
    # Limpa o evento do mapa após sinalizar para parar, 
    # pois o loop em event_generator fará a remoção final.
    # STOP_EVENTS.pop(recording_id, None) # Removido daqui, pois o finally em event_generator já faz isso.
    return {"status": "stopping_signal_sent", "recording_id": recording_id}

@app.get("/health")
def health():
    return {"status": "ok", "message": "API está operacional."}
