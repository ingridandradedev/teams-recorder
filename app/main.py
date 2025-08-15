import os
import uuid
import threading
import json
import logging
from typing import Dict
from fastapi import FastAPI, Query, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from app.recorder import gravar_reuniao_stream

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Teams Recorder API",
    description="API para gravação de reuniões do Microsoft Teams",
    version="2.0.0"
)

# --- Configuração de Autenticação ---
# ATENÇÃO: Para produção, carregue este token de uma variável de ambiente!
EXPECTED_API_TOKEN = os.getenv("API_TOKEN", "b3e59f8b8c4f48d09e0a0ff172b19a43d79ab69e165d0ec7037cbef967de2a3a")

# Validar configuração no startup
@app.on_event("startup")
async def startup_event():
    """Verificar configuração no início da aplicação."""
    logger.info("🚀 Iniciando Teams Recorder API v2.0.0")
    
    # Verificar se pelo menos um método de autenticação está configurado
    auth_methods = [
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        os.getenv("GOOGLE_CREDENTIALS_JSON"),
        os.getenv("GOOGLE_SECRET_NAME")
    ]
    
    if not any(auth_methods):
        logger.warning(
            "⚠️ Nenhum método de autenticação Google Cloud detectado! "
            "Configure uma das seguintes variáveis:\n"
            "- GOOGLE_CREDENTIALS_JSON (recomendado para Railway/Heroku)\n"
            "- GOOGLE_APPLICATION_CREDENTIALS (para desenvolvimento local)\n"
            "- GOOGLE_SECRET_NAME (para uso com Secret Manager)\n"
            "Ou configure ADC se estiver no Google Cloud."
        )
    else:
        logger.info("✅ Configuração de autenticação Google Cloud detectada")
    
    if EXPECTED_API_TOKEN == "b3e59f8b8c4f48d09e0a0ff172b19a43d79ab69e165d0ec7037cbef967de2a3a":
        logger.warning("⚠️ Usando token de API padrão! Configure API_TOKEN para produção.")

async def verify_api_key(x_api_token: str = Header(None, description="Seu token de API secreto.")):
    """
    Dependência para verificar o token da API no cabeçalho X-API-Token.
    """
    if not x_api_token:
        raise HTTPException(status_code=401, detail="Cabeçalho X-API-Token ausente.")
    if x_api_token != EXPECTED_API_TOKEN:
        raise HTTPException(status_code=403, detail="Token da API inválido.")
    return x_api_token
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
