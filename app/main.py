import os
import sys
import asyncio
import uuid
import threading
import json
import logging
from typing import Dict, AsyncGenerator
from fastapi import FastAPI, Query, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Configuração de Autenticação ---
EXPECTED_API_TOKEN = os.getenv("API_TOKEN", "b3e59f8b8c4f48d09e0a0ff172b19a43d79ab69e165d0ec7037cbef967de2a3a")

# Mapa de flags de parada para gravações ativas
STOP_EVENTS: Dict[str, asyncio.Event] = {}
ACTIVE_RECORDINGS: Dict[str, dict] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerenciar ciclo de vida da aplicação."""
    # Startup
    logger.info("🚀 Iniciando Teams Recorder API v2.1.0")
    
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
    
    logger.info(f"📊 Sistema iniciado. Gravações ativas: {len(ACTIVE_RECORDINGS)}")
    
    yield
    
    # Shutdown
    logger.info("🛑 Parando Teams Recorder API...")
    
    # Parar todas as gravações ativas
    if ACTIVE_RECORDINGS:
        logger.info(f"⏹️ Parando {len(ACTIVE_RECORDINGS)} gravação(ões) ativa(s)...")
        for recording_id, stop_event in STOP_EVENTS.items():
            try:
                stop_event.set()
                logger.info(f"⏹️ Sinal de parada enviado para gravação {recording_id[:8]}...")
            except Exception as e:
                logger.error(f"❌ Erro ao parar gravação {recording_id[:8]}: {e}")
        
        # Aguardar um pouco para limpeza
        await asyncio.sleep(2)
    
    logger.info("✅ API encerrada com sucesso")

app = FastAPI(
    title="MarIA Recorder API",
    description="API para gravação de reuniões do Microsoft Teams com suporte a múltiplas gravações simultâneas",
    version="2.1.0",
    lifespan=lifespan
)

async def verify_api_key(x_api_token: str = Header(None, description="Seu token de API secreto.")):
    """
    Dependência para verificar o token da API no cabeçalho X-API-Token.
    """
    if not x_api_token:
        raise HTTPException(status_code=401, detail="Cabeçalho X-API-Token ausente.")
    if x_api_token != EXPECTED_API_TOKEN:
        raise HTTPException(status_code=403, detail="Token da API inválido.")
    return x_api_token

@app.get("/gravar", response_class=StreamingResponse)
async def iniciar_gravacao(
    url: str = Query(..., description="URL da reunião do Teams"),
    segment_time: int = Query(60, description="Segundos por segmento (ex: 60 ou 300)"),
    upload_dest: str = Query("recordings-segments", description="Pasta destino no bucket para segmentos"),
    record_video: bool = Query(True, description="Se deve capturar vídeo além do áudio"),
    api_key: str = Depends(verify_api_key)
):
    """Inicia uma nova gravação usando Playwright Async API para suporte a múltiplas gravações."""
    recording_id = str(uuid.uuid4())
    stop_event = asyncio.Event()
    
    # Registrar a gravação
    STOP_EVENTS[recording_id] = stop_event
    ACTIVE_RECORDINGS[recording_id] = {
        "url": url,
        "started_at": asyncio.get_event_loop().time(),
        "status": "starting"
    }
    
    logger.info(f"🎬 Nova gravação iniciada: {recording_id[:8]}... | Total ativo: {len(ACTIVE_RECORDINGS)}")

    async def event_generator() -> AsyncGenerator[str, None]:
        """Gerador de eventos Server-Sent Events para streaming."""
        try:
            # Importar aqui para evitar problemas de inicialização
            from app.async_recorder import gravar_reuniao_stream_async
            
            # Usar async generator
            async for msg in gravar_reuniao_stream_async(url, stop_event, segment_time=segment_time, upload_dest=upload_dest, record_video=record_video):
                payload = {**msg, "recording_id": recording_id}
                yield f"data: {json.dumps(payload)}\n\n"
                
                # Atualizar status
                if recording_id in ACTIVE_RECORDINGS:
                    ACTIVE_RECORDINGS[recording_id]["status"] = msg.get("event", "running")
                    
        except Exception as e:
            logger.error(f"❌ Erro na gravação {recording_id[:8]}...: {e}")
            error_payload = {
                "event": "error",
                "type": "unexpected_error_main",
                "detail": f"Erro inesperado: {str(e)}",
                "recording_id": recording_id
            }
            yield f"data: {json.dumps(error_payload)}\n\n"
        finally:
            # Limpeza
            STOP_EVENTS.pop(recording_id, None)
            ACTIVE_RECORDINGS.pop(recording_id, None)
            logger.info(f"🧹 Gravação {recording_id[:8]}... finalizada | Total ativo: {len(ACTIVE_RECORDINGS)}")

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/stop/{recording_id}")
async def stop_gravacao(
    recording_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Para uma gravação específica."""
    stop_event = STOP_EVENTS.get(recording_id)
    if not stop_event:
        raise HTTPException(
            status_code=404, 
            detail="Recording ID não encontrado ou já parado."
        )
    
    logger.info(f"⏹️ Parando gravação: {recording_id[:8]}...")
    stop_event.set()
    
    return {
        "message": "Sinal de parada enviado com sucesso",
        "recording_id": recording_id
    }

@app.get("/status")
async def get_status(api_key: str = Depends(verify_api_key)):
    """Retorna o status de todas as gravações ativas."""
    return {
        "active_recordings": len(ACTIVE_RECORDINGS),
        "recordings": {
            rec_id[:8] + "...": {
                "url_domain": rec_info["url"].split("//")[1].split("/")[0] if "//" in rec_info["url"] else "unknown",
                "status": rec_info["status"],
                "duration_seconds": round(asyncio.get_event_loop().time() - rec_info["started_at"], 1)
            }
            for rec_id, rec_info in ACTIVE_RECORDINGS.items()
        }
    }

@app.get("/health")
def health():
    """Endpoint de health check."""
    return {
        "status": "healthy",
        "version": "2.1.0",
        "active_recordings": len(ACTIVE_RECORDINGS)
    }

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

