import os
import sys
import asyncio
import uuid
import threading
import json
import logging
import time
from datetime import datetime
from typing import Dict, AsyncGenerator, Optional, List
from fastapi import FastAPI, Query, Depends, HTTPException, Header, UploadFile, File, Body
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel

# Importar módulos de gravação
from app.async_recorder import gravar_reuniao_stream_async
from app.recording_with_transcription import gravar_com_transcricao_async, get_transcription_stream
from app.transcription import transcription_tracker

# Importar módulos de feedback
from app.teams_feedback_service import TeamsFeedbackService
from app.teams_recording_feedback import TeamsRecordingWithFeedback, process_audio_for_feedback
from app.feedback_models import SessionInfo, SSEEvent

# Importar novo serviço de transcrição de áudio
from app.audio_transcription_service import AudioTranscriptionService

# Importar serviço de persistência
from app.supabase_persistence import get_persistence_service, cleanup_persistence_service

# Importar serviços Attendee
from app.attendee_service import (
    get_attendee_service, 
    get_attendee_monitoring, 
    cleanup_attendee_services
)

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === Modelos Pydantic ===

class FeedbackSessionRequest(BaseModel):
    """Modelo para request de início de sessão de feedback"""
    meeting_session_id: str
    teams_url: str
    segment_time: int = 60
    upload_dest: str = "recordings-segments"  
    record_video: bool = True

class FeedbackSessionResponse(BaseModel):
    """Modelo para response de sessão de feedback"""
    session_id: str
    recording_session_id: str
    status: str
    type: str
    teams_url: str
    meeting_session_id: str
    feedback_stream_url: str
    stop_url: str
    context_url: str
    message: str

# === Novos Modelos para Attendee ===

class AttendeeRecordingRequest(BaseModel):
    """Modelo para request de gravação via Attendee"""
    meeting_url: str
    session_name: str
    source_type: str = "live_recording"
    event_id: Optional[str] = None
    user_id: str
    tenant_id: Optional[str] = None
    metadata: Optional[dict] = None

class AttendeeRecordingResponse(BaseModel):
    """Modelo para response de gravação via Attendee"""
    recording_session_id: str
    meeting_session_id: str
    attendee_bot_id: str
    session_name: str
    meeting_url: str
    status: str
    monitoring_active: bool
    created_at: datetime
    message: str

class MeetingSessionMetadata(BaseModel):
    """Modelo para metadados completos da sessão"""
    recording_session_id: str
    meeting_session_id: str
    session_name: Optional[str]
    meeting_url: str
    status: str
    attendee_bot_id: Optional[str]
    attendee_bot_state: Optional[str]
    attendee_recording_state: Optional[str]
    attendee_transcription_state: Optional[str]
    transcription_data: List[dict]
    feedback_analysis: dict
    speaker_data: List[dict]
    final_recording_url: Optional[str]
    calendar_event_title: Optional[str]
    total_chunks: int
    monitoring_active: bool
    created_at: datetime
    updated_at: datetime
    ended_at: Optional[datetime]

# --- Configuração de Autenticação ---
EXPECTED_API_TOKEN = os.getenv("API_TOKEN", "b3e59f8b8c4f48d09e0a0ff172b19a43d79ab69e165d0ec7037cbef967de2a3a")

# Mapa de flags de parada para gravações ativas
STOP_EVENTS: Dict[str, asyncio.Event] = {}
ACTIVE_RECORDINGS: Dict[str, dict] = {}

# Serviços de feedback PNL
teams_feedback_service: TeamsFeedbackService = None
teams_recording_feedback: TeamsRecordingWithFeedback = None

# Serviço de transcrição de áudio
audio_transcription_service: AudioTranscriptionService = None

# Armazena sessões ativas de feedback
ACTIVE_FEEDBACK_SESSIONS: Dict[str, asyncio.Queue] = {}
FEEDBACK_SESSION_INFO: Dict[str, SessionInfo] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerenciar ciclo de vida da aplicação."""
    global teams_feedback_service, teams_recording_feedback
    
    # Startup
    logger.info("🚀 Iniciando Teams Recorder API v2.3.0")
    
    # Verificar configuração Google Cloud
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
    
    # Verificar configuração Gemini e inicializar serviços de feedback
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    logger.info(f"🔍 Debug - GEMINI_API_KEY presente: {bool(gemini_api_key)}")
    if gemini_api_key:
        logger.info(f"🔍 Debug - GEMINI_API_KEY primeiros 10 chars: {gemini_api_key[:10]}...")
    
    if not gemini_api_key:
        logger.warning("⚠️ GEMINI_API_KEY não configurada! Transcrição e feedback não funcionarão.")
        teams_feedback_service = None
        teams_recording_feedback = None
        audio_transcription_service = None
    else:
        logger.info("✅ GEMINI_API_KEY configurada para transcrição e feedback")
        try:
            teams_feedback_service = TeamsFeedbackService(gemini_api_key)
            teams_recording_feedback = TeamsRecordingWithFeedback(teams_feedback_service)
            audio_transcription_service = AudioTranscriptionService(gemini_api_key)
            logger.info("✅ Serviços de feedback PNL e transcrição de áudio inicializados com sucesso")
            logger.info(f"🔍 Debug - audio_transcription_service criado: {audio_transcription_service is not None}")
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar serviços de feedback: {e}")
            logger.error(f"🔍 Debug - Exceção completa: {str(e)}")
            teams_feedback_service = None
            teams_recording_feedback = None
            audio_transcription_service = None
    
    # Inicializar serviço de persistência Supabase
    try:
        persistence_service = await get_persistence_service()
        logger.info("✅ Serviço de persistência Supabase inicializado")
    except Exception as e:
        logger.error(f"❌ Erro ao inicializar persistência Supabase: {e}")
        # Não bloquear a aplicação se a persistência falhar
        logger.warning("⚠️ Aplicação continuará sem persistência no banco de dados")
    
    # Inicializar serviços Attendee se configurados
    attendee_api_key = os.getenv("ATTENDEE_API_KEY")
    if attendee_api_key:
        try:
            await get_attendee_service()
            await get_attendee_monitoring()
            logger.info("✅ Serviços Attendee inicializados com sucesso")
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar serviços Attendee: {e}")
    else:
        logger.warning("⚠️ ATTENDEE_API_KEY não configurada! Funcionalidades Attendee indisponíveis.")
    
    if EXPECTED_API_TOKEN == "b3e59f8b8c4f48d09e0a0ff172b19a43d79ab69e165d0ec7037cbef967de2a3a":
        logger.warning("⚠️ Usando token de API padrão! Configure API_TOKEN para produção.")
    
    logger.info(f"📊 Sistema iniciado. Gravações ativas: {len(ACTIVE_RECORDINGS)}, Sessões de feedback: {len(ACTIVE_FEEDBACK_SESSIONS)}")
    
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
    
    # Encerrar sessões de feedback ativas
    if ACTIVE_FEEDBACK_SESSIONS and teams_feedback_service:
        logger.info(f"⏹️ Encerrando {len(ACTIVE_FEEDBACK_SESSIONS)} sessão(ões) de feedback...")
        for session_id in list(ACTIVE_FEEDBACK_SESSIONS.keys()):
            try:
                teams_feedback_service.end_feedback_session(session_id)
                logger.info(f"⏹️ Sessão de feedback encerrada: {session_id[:8]}...")
            except Exception as e:
                logger.error(f"❌ Erro ao encerrar sessão de feedback {session_id[:8]}: {e}")
    
    # Cleanup dos serviços Attendee
    try:
        await cleanup_attendee_services()
        logger.info("✅ Serviços Attendee finalizados")
    except Exception as e:
        logger.error(f"❌ Erro ao finalizar serviços Attendee: {e}")
    
    # Cleanup do serviço de persistência
    try:
        await cleanup_persistence_service()
        logger.info("✅ Serviço de persistência Supabase finalizado")
    except Exception as e:
        logger.error(f"❌ Erro ao finalizar persistência: {e}")
    
    logger.info("✅ API encerrada com sucesso")

app = FastAPI(
    title="MarIA Recorder API",
    description="API para gravação de reuniões do Microsoft Teams com suporte a múltiplas gravações simultâneas, transcrição e análise de feedback PNL em tempo real",
    version="2.3.0",
    lifespan=lifespan
)

# Configurar CORS para resolver problemas de preflight
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produção, especifique domínios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configurar arquivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/test_feedback_teams.html")
async def test_feedback_teams():
    """Redireciona para a interface de teste de feedback PNL."""
    return RedirectResponse(url="/static/test_feedback_teams.html", status_code=301)

@app.get("/")
async def root():
    """Página inicial da API."""
    return {
        "app": "MarIA Recorder API",
        "version": "2.3.0",
        "description": "API para gravação de reuniões e análise de feedback PNL",
        "test_interface": "/test_feedback_teams.html"
    }

async def verify_api_key(x_api_token: str = Header(None, description="Seu token de API secreto.")):
    """
    Dependência para verificar o token da API no cabeçalho X-API-Token.
    """
    if not x_api_token:
        raise HTTPException(status_code=401, detail="Cabeçalho X-API-Token ausente.")
    if x_api_token != EXPECTED_API_TOKEN:
        raise HTTPException(status_code=403, detail="Token da API inválido.")
    return x_api_token

async def verify_api_key_query(api_key: str = Query(None, description="Seu token de API como query parameter")):
    """
    Dependência para verificar o token da API via query parameter (para EventSource).
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="Query parameter api_key ausente.")
    if api_key != EXPECTED_API_TOKEN:
        raise HTTPException(status_code=403, detail="Token da API inválido.")
    return api_key

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
    feedback_available = teams_feedback_service is not None
    audio_transcription_available = audio_transcription_service is not None
    return {
        "status": "healthy",
        "version": "2.3.0",
        "active_recordings": len(ACTIVE_RECORDINGS),
        "active_feedback_sessions": len(ACTIVE_FEEDBACK_SESSIONS),
        "features": {
            "recording": True,
            "transcription": bool(os.getenv("GEMINI_API_KEY")),
            "feedback_pnl": feedback_available,
            "audio_transcription": audio_transcription_available
        }
    }

# =======================================
# NOVOS ENDPOINTS PARA TRANSCRIÇÃO
# =======================================

@app.post("/record-and-transcribe")
async def iniciar_gravacao_com_transcricao(
    url: str = Query(..., description="URL da reunião do Teams"),
    segment_time: int = Query(60, description="Segundos por segmento (ex: 60 ou 300)"),
    upload_dest: str = Query("recordings-segments", description="Pasta destino no bucket para segmentos"),
    record_video: bool = Query(True, description="Se deve capturar vídeo além do áudio"),
    api_key: str = Depends(verify_api_key)
):
    """
    Inicia uma nova gravação COM transcrição usando Gemini 2.5 Pro.
    Retorna imediatamente o recording_id para acompanhar o progresso.
    """
    
    # Verificar se Gemini está configurado
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(
            status_code=400, 
            detail="GEMINI_API_KEY não configurada. Transcrição não está disponível."
        )
    
    recording_id = str(uuid.uuid4())
    stop_event = asyncio.Event()
    
    # Registrar a gravação
    STOP_EVENTS[recording_id] = stop_event
    ACTIVE_RECORDINGS[recording_id] = {
        "url": url,
        "started_at": asyncio.get_event_loop().time(),
        "status": "starting",
        "type": "recording_with_transcription"
    }
    
    logger.info(f"🎬📝 Nova gravação COM transcrição iniciada: {recording_id[:8]}... | Total ativo: {len(ACTIVE_RECORDINGS)}")
    
    # Iniciar gravação com transcrição em background
    asyncio.create_task(
        execute_recording_with_transcription(
            recording_id, url, stop_event, segment_time, upload_dest, record_video
        )
    )
    
    return {
        "message": "Gravação com transcrição iniciada com sucesso",
        "recording_id": recording_id,
        "status": "started",
        "transcription_stream_url": f"/transcription-stream/{recording_id}",
        "stop_url": f"/stop/{recording_id}"
    }

@app.post("/record-audio-and-transcribe")
async def record_audio_and_transcribe_meeting(
    url: str = Query(..., description="URL da reunião do Teams"),
    segment_time: int = Query(60, description="Segundos por segmento de gravação (ex: 60 ou 300)"),
    upload_dest: str = Query("audio-recordings", description="Pasta destino no bucket para gravação"),
    api_key: str = Depends(verify_api_key)
):
    """
    Grava áudio da reunião do Teams e faz transcrição completa após finalização.
    
    Este endpoint:
    1. Grava apenas áudio da reunião (sem vídeo para economizar recursos)
    2. Após a gravação ser finalizada, mescla os segmentos em um arquivo MP3
    3. Envia o áudio para Gemini 2.0 Flash para transcrição com diarização
    4. Retorna a transcrição completa na resposta
    
    Diferente do /record-and-transcribe que faz transcrição em tempo real,
    este endpoint faz transcrição completa apenas no final.
    """
    
    global audio_transcription_service
    
    # Verificar se serviço está disponível
    logger.info(f"🔍 Debug endpoint - audio_transcription_service: {audio_transcription_service is not None}")
    
    # Se o serviço não foi inicializado, tentar inicializar agora
    if not audio_transcription_service:
        logger.warning("🔍 Debug endpoint - Tentando inicializar serviço agora...")
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        logger.info(f"🔍 Debug endpoint - GEMINI_API_KEY presente: {bool(gemini_api_key)}")
        
        if gemini_api_key:
            try:
                audio_transcription_service = AudioTranscriptionService(gemini_api_key)
                logger.info("✅ Debug endpoint - Serviço inicializado com sucesso")
            except Exception as e:
                logger.error(f"❌ Debug endpoint - Erro ao inicializar serviço: {e}")
                raise HTTPException(
                    status_code=400, 
                    detail=f"Erro ao inicializar serviço de transcrição: {str(e)}"
                )
        else:
            logger.error("🔍 Debug endpoint - GEMINI_API_KEY não encontrada")
            raise HTTPException(
                status_code=400, 
                detail="Serviço de transcrição de áudio não está disponível. GEMINI_API_KEY não configurada."
            )
    
    if not audio_transcription_service:
        logger.error("🔍 Debug endpoint - Serviço de transcrição ainda não está disponível")
        raise HTTPException(
            status_code=400, 
            detail="Serviço de transcrição de áudio não está disponível. Verifique GEMINI_API_KEY."
        )
    
    recording_id = str(uuid.uuid4())
    stop_event = asyncio.Event()
    
    # Registrar a gravação
    STOP_EVENTS[recording_id] = stop_event
    ACTIVE_RECORDINGS[recording_id] = {
        "url": url,
        "started_at": asyncio.get_event_loop().time(),
        "status": "starting",
        "type": "audio_recording_with_final_transcription"
    }
    
    logger.info(f"🎵📝 Nova gravação de áudio para transcrição iniciada: {recording_id[:8]}... | Total ativo: {len(ACTIVE_RECORDINGS)}")
    
    try:
        # Executar gravação e transcrição (função síncrona, mas aguarda completion)
        result = await audio_transcription_service.record_and_transcribe_meeting(
            teams_url=url,
            stop_event=stop_event,
            segment_time=segment_time,
            upload_dest=upload_dest
        )
        
        # Atualizar status
        if recording_id in ACTIVE_RECORDINGS:
            ACTIVE_RECORDINGS[recording_id]["status"] = "completed"
        
        # Preparar resposta
        response_data = {
            "message": "Gravação e transcrição concluídas com sucesso" if result["recording_completed"] else "Gravação não foi concluída adequadamente",
            "recording_id": recording_id,
            "recording_completed": result["recording_completed"],
            "transcription": result["transcription"],
            "segments_uploaded": len(result["segments_uploaded"]),
            "segments_info": result["segments_uploaded"],
            "stop_url": f"/stop/{recording_id}"
        }
        
        # Adicionar erro se houver
        if result["error"]:
            response_data["error"] = result["error"]
        
        return response_data
        
    except Exception as e:
        logger.error(f"❌ Erro na gravação e transcrição de áudio {recording_id[:8]}...: {e}")
        
        # Atualizar status de erro
        if recording_id in ACTIVE_RECORDINGS:
            ACTIVE_RECORDINGS[recording_id]["status"] = "error"
        
        raise HTTPException(
            status_code=500,
            detail=f"Erro durante gravação e transcrição: {str(e)}"
        )
    
    finally:
        # Limpeza
        STOP_EVENTS.pop(recording_id, None)
        ACTIVE_RECORDINGS.pop(recording_id, None)
        logger.info(f"🧹 Gravação de áudio {recording_id[:8]}... finalizada | Total ativo: {len(ACTIVE_RECORDINGS)}")

@app.get("/transcription-stream/{recording_id}", response_class=StreamingResponse)
async def stream_transcricao(
    recording_id: str,
    api_key: str = Depends(verify_api_key_query)
):
    """
    Stream de eventos de transcrição em tempo real para um recording_id específico.
    Retorna apenas eventos de transcrição, não de navegação.
    """
    
    # Verificar se o recording_id existe
    if recording_id not in ACTIVE_RECORDINGS and recording_id not in transcription_tracker.active_transcriptions:
        raise HTTPException(
            status_code=404, 
            detail="Recording ID não encontrado"
        )
    
    logger.info(f"📺 Cliente conectado ao stream de transcrição: {recording_id[:8]}...")

    async def transcription_event_generator() -> AsyncGenerator[str, None]:
        """Gerador de eventos de transcrição para streaming."""
        try:
            async for event in get_transcription_stream(recording_id):
                payload = {**event, "recording_id": recording_id}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                
        except Exception as e:
            logger.error(f"❌ Erro no stream de transcrição {recording_id[:8]}...: {e}")
            error_payload = {
                "event": "transcription_stream_error",
                "recording_id": recording_id,
                "error": str(e)
            }
            yield f"data: {json.dumps(error_payload)}\n\n"

    return StreamingResponse(
        transcription_event_generator(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream; charset=utf-8"
        }
    )

@app.get("/transcription-status/{recording_id}")
async def get_transcription_status(
    recording_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Retorna o status atual de uma transcrição específica."""
    
    status = transcription_tracker.get_transcription_status(recording_id)
    if not status:
        raise HTTPException(
            status_code=404,
            detail="Recording ID não encontrado"
        )
    
    results = transcription_tracker.get_transcription_results(recording_id)
    
    # Contar falas processadas
    transcription_segments = [
        r for r in results 
        if r.get("event") == "transcription_segment"
    ]
    
    return {
        "recording_id": recording_id,
        "status": status.get("status", "unknown"),
        "started_at": status.get("started_at"),
        "finished_at": status.get("finished_at"),
        "total_segments_processed": status.get("processed_segments", 0),
        "total_transcription_events": len(results),
        "total_speech_segments": len(transcription_segments),
        "metadata": status.get("metadata", {})
    }

async def execute_recording_with_transcription(
    recording_id: str,
    url: str,
    stop_event: asyncio.Event,
    segment_time: int,
    upload_dest: str,
    record_video: bool
):
    """
    Executa a gravação com transcrição em background.
    Esta função roda assincronamente e não bloqueia a resposta da API.
    """
    try:
        async for event in gravar_com_transcricao_async(
            url, recording_id, stop_event, segment_time, upload_dest, record_video
        ):
            # Log apenas eventos importantes
            if event.get("event") in ["transcription_recording_start", "transcription_recording_complete"]:
                logger.info(f"📝 {event.get('message', 'Evento de transcrição')}")
                
    except Exception as e:
        logger.error(f"❌ Erro na execução de gravação com transcrição {recording_id[:8]}...: {e}")
        
    finally:
        # Limpeza
        STOP_EVENTS.pop(recording_id, None)
        ACTIVE_RECORDINGS.pop(recording_id, None)
        logger.info(f"🧹 Gravação com transcrição {recording_id[:8]}... finalizada | Total ativo: {len(ACTIVE_RECORDINGS)}")


@app.get("/transcription-data/{recording_id}")
async def stream_transcription_data(recording_id: str):
    """
    Endpoint de stream limpo para dados de transcrição.
    Retorna apenas os objetos JSON gerados pelo Gemini, sem eventos intermediários.
    """
    from app.transcription import transcription_tracker
    
    async def generate_clean_stream():
        try:
            yield "data: " + json.dumps({
                "event": "stream_start",
                "recording_id": recording_id,
                "message": "Iniciando stream de dados de transcrição"
            }) + "\n\n"
            
            async for data in transcription_tracker.stream_clean_transcription_data(recording_id):
                yield f"data: {json.dumps(data)}\n\n"
                
        except Exception as e:
            error_payload = {
                "event": "stream_error",
                "recording_id": recording_id,
                "error": str(e)
            }
            yield f"data: {json.dumps(error_payload)}\n\n"
    
    return StreamingResponse(
        generate_clean_stream(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream"
        }
    )


# =======================================
# NOVOS ENDPOINTS PARA FEEDBACK PNL INTEGRADO
# =======================================

@app.post("/api/feedback/start", response_model=FeedbackSessionResponse)
async def start_feedback_session(
    request: FeedbackSessionRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Inicia uma nova sessão de feedback PNL integrada com gravação do Teams.
    Combina gravação automática do Teams com análise de feedback em tempo real.
    Agora integrado com persistência no Supabase.
    """
    
    if not teams_feedback_service:
        raise HTTPException(
            status_code=500, 
            detail="Serviço de feedback PNL não está disponível. Verifique GEMINI_API_KEY."
        )
    
    # Gerar IDs únicos
    session_id = str(uuid.uuid4())
    stop_event = asyncio.Event()
    
    # Criar sessão no banco de dados
    try:
        persistence_service = await get_persistence_service()
        recording_session_id = await persistence_service.create_recording_session(
            meeting_session_id=request.meeting_session_id,
            session_id=session_id,
            teams_url=request.teams_url,
            segment_time=request.segment_time,
            record_video=request.record_video,
            upload_dest=request.upload_dest
        )
        
        if not recording_session_id:
            raise HTTPException(
                status_code=500,
                detail="Erro ao criar sessão no banco de dados"
            )
            
        logger.info(f"✅ Sessão criada no banco: recording_session_id={recording_session_id}")
        
    except Exception as e:
        logger.error(f"❌ Erro na persistência: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao salvar sessão no banco: {str(e)}"
        )
    
    # Registrar a sessão em memória
    STOP_EVENTS[session_id] = stop_event
    ACTIVE_RECORDINGS[session_id] = {
        "url": request.teams_url,
        "meeting_session_id": request.meeting_session_id,
        "recording_session_id": recording_session_id,
        "started_at": asyncio.get_event_loop().time(),
        "status": "starting",
        "type": "feedback_pnl_with_teams_recording"
    }
    
    # Criar queue para streaming de eventos
    ACTIVE_FEEDBACK_SESSIONS[session_id] = asyncio.Queue()
    
    # Criar info da sessão
    FEEDBACK_SESSION_INFO[session_id] = SessionInfo(
        session_id=session_id,
        created_at=datetime.now(),
        status="active"
    )
    
    logger.info(f"🎬💬 Nova sessão de feedback PNL + gravação Teams iniciada: {session_id[:8]}... (meeting: {request.meeting_session_id})")
    
    # Iniciar gravação com feedback em background
    asyncio.create_task(
        execute_teams_recording_with_feedback(
            session_id, request.teams_url, stop_event, 
            request.segment_time, request.upload_dest, request.record_video,
            request.meeting_session_id, recording_session_id
        )
    )
    
    return FeedbackSessionResponse(
        session_id=session_id,
        recording_session_id=recording_session_id,
        status="active",
        type="feedback_pnl_with_teams_recording",
        teams_url=request.teams_url,
        meeting_session_id=request.meeting_session_id,
        feedback_stream_url=f"/api/feedback/stream/{session_id}",
        stop_url=f"/api/feedback/session/{session_id}",
        context_url=f"/api/feedback/context/{session_id}",
        message="Sessão de feedback PNL com gravação do Teams iniciada com sucesso e persistida no banco"
    )


@app.get("/api/feedback/session/{session_id}/recording")
async def get_recording_session_data(
    session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Retorna os dados da sessão de gravação armazenados no banco.
    """
    try:
        persistence_service = await get_persistence_service()
        
        # Buscar dados da sessão no banco
        recording_data = await persistence_service.get_recording_session_by_session_id(session_id)
        
        if not recording_data:
            raise HTTPException(
                status_code=404,
                detail="Sessão de gravação não encontrada no banco"
            )
            
        return {
            "recording_session_id": recording_data.get("id"),
            "meeting_session_id": recording_data.get("meeting_session_id"),
            "session_id": recording_data.get("session_id"),
            "teams_url": recording_data.get("teams_url"),
            "status": recording_data.get("status"),
            "feedback_context": recording_data.get("feedback_context"),
            "recording_url": recording_data.get("recording_url"),
            "public_url": recording_data.get("public_url"),
            "total_segments": recording_data.get("total_segments"),
            "metadata": recording_data.get("metadata"),
            "created_at": recording_data.get("created_at"),
            "updated_at": recording_data.get("updated_at")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao buscar dados da sessão {session_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao buscar dados: {str(e)}"
        )


@app.get("/api/feedback/meeting/{meeting_session_id}/recordings")
async def get_meeting_recordings(
    meeting_session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Retorna todas as gravações de feedback de uma reunião específica.
    """
    try:
        persistence_service = await get_persistence_service()
        
        # Buscar todas as gravações da reunião
        recordings = await persistence_service.get_recordings_by_meeting_session(meeting_session_id)
        
        if not recordings:
            return {
                "meeting_session_id": meeting_session_id,
                "recordings": [],
                "total": 0
            }
            
        return {
            "meeting_session_id": meeting_session_id,
            "recordings": recordings,
            "total": len(recordings)
        }
        
    except Exception as e:
        logger.error(f"❌ Erro ao buscar gravações da reunião {meeting_session_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao buscar gravações: {str(e)}"
        )


@app.post("/api/feedback/process-audio/{session_id}")
async def process_feedback_audio(
    session_id: str, 
    audio: UploadFile = File(...),
    api_key: str = Depends(verify_api_key)
):
    """
    Processa chunk de áudio para análise de feedback PNL contextual.
    Este endpoint permite upload manual de áudio para análise.
    """
    
    # Valida sessão
    if session_id not in ACTIVE_FEEDBACK_SESSIONS:
        raise HTTPException(status_code=404, detail="Sessão de feedback não encontrada")
    
    if not teams_feedback_service:
        raise HTTPException(status_code=500, detail="Serviço de feedback PNL não disponível")
    
    try:
        # Lê o áudio
        audio_bytes = await audio.read()
        logger.info(f"Processando áudio de feedback manual: {len(audio_bytes)} bytes, tipo: {audio.content_type}")
        
        # Valida formato de áudio
        if not teams_feedback_service.validate_audio_format(audio.content_type):
            logger.warning(f"Formato de áudio não suportado: {audio.content_type}")
        
        # Processa com análise contextual
        result = await teams_feedback_service.process_feedback_audio_with_context(
            session_id=session_id,
            audio_bytes=audio_bytes, 
            mime_type=audio.content_type
        )
        
        if result:
            # Recupera informações de contexto
            conversation_summary = teams_feedback_service.get_conversation_summary(session_id)
            
            # Envia resultado via SSE
            event = SSEEvent(
                type="feedback_analysis",
                data={
                    **result.dict(),
                    "context_info": {
                        "total_segments": conversation_summary.get("total_chunks", 0) if conversation_summary else 0,
                        "padroes_identificados": len(conversation_summary.get("padroes_identificados", [])) if conversation_summary else 0,
                        "evolucao_disponivel": bool(conversation_summary.get("evolucao_emocional", []) if conversation_summary else False)
                    }
                },
                timestamp=time.time()
            )
            
            await ACTIVE_FEEDBACK_SESSIONS[session_id].put(event.dict())
            logger.info(f"Resultado de análise de feedback enviado para sessão {session_id}")
            
            return {
                "status": "processed",
                "type": "feedback_pnl_analysis",
                "session_id": session_id,
                "timestamp": time.time(),
                "audio_size": len(audio_bytes),
                "context_segments": conversation_summary.get("total_chunks", 0) if conversation_summary else 0
            }
        else:
            # Erro no processamento
            error_event = SSEEvent(
                type="error",
                message="Erro ao processar áudio para análise de feedback PNL",
                timestamp=time.time()
            )
            
            await ACTIVE_FEEDBACK_SESSIONS[session_id].put(error_event.dict())
            
            return {
                "status": "error",
                "message": "Erro no processamento do áudio para feedback PNL"
            }
        
    except Exception as e:
        logger.error(f"Erro ao processar áudio de feedback: {str(e)}")
        
        # Envia erro para a sessão
        try:
            error_event = SSEEvent(
                type="error",
                message=f"Erro interno no processamento de feedback: {str(e)}",
                timestamp=time.time()
            )
            await ACTIVE_FEEDBACK_SESSIONS[session_id].put(error_event.dict())
        except:
            pass
        
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/stream/{session_id}")
async def stream_feedback_updates(
    session_id: str,
    api_key: str = Depends(verify_api_key_query)
):
    """Stream SSE de atualizações da sessão de feedback PNL integrada"""
    
    if session_id not in ACTIVE_FEEDBACK_SESSIONS:
        raise HTTPException(status_code=404, detail="Sessão de feedback não encontrada")
    
    async def event_generator():
        queue = ACTIVE_FEEDBACK_SESSIONS[session_id]
        logger.info(f"Iniciando stream SSE para sessão de feedback PNL {session_id}")
        
        while True:
            try:
                # Aguarda próximo evento na queue (com timeout para heartbeat)
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                
            except asyncio.TimeoutError:
                # Heartbeat para manter conexão viva
                heartbeat = SSEEvent(
                    type="heartbeat",
                    timestamp=time.time()
                )
                yield f"data: {json.dumps(heartbeat.dict())}\n\n"
                
            except Exception as e:
                logger.error(f"Erro no stream SSE de feedback: {str(e)}")
                error_event = SSEEvent(
                    type="error",
                    message=f"Erro na conexão de feedback: {str(e)}",
                    timestamp=time.time()
                )
                yield f"data: {json.dumps(error_event.dict())}\n\n"
                break
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control",
        }
    )


@app.delete("/api/feedback/session/{session_id}")
async def end_feedback_session(
    session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Encerra sessão de feedback PNL e para gravação do Teams associada"""
    
    # Para a gravação se estiver ativa
    stop_event = STOP_EVENTS.get(session_id)
    if stop_event:
        logger.info(f"⏹️ Parando gravação associada à sessão de feedback: {session_id[:8]}...")
        stop_event.set()
    
    # Encerra sessão contextual no serviço
    context_ended = False
    if teams_feedback_service:
        context_ended = teams_feedback_service.end_feedback_session(session_id)
    
    # Remove da lista de sessões ativas
    if session_id in ACTIVE_FEEDBACK_SESSIONS:
        del ACTIVE_FEEDBACK_SESSIONS[session_id]
        logger.info(f"Sessão de feedback encerrada: {session_id}")
    
    if session_id in FEEDBACK_SESSION_INFO:
        FEEDBACK_SESSION_INFO[session_id].status = "ended"
    
    # Remove da lista de gravações ativas
    STOP_EVENTS.pop(session_id, None)
    ACTIVE_RECORDINGS.pop(session_id, None)
    
    return {
        "status": "session_ended",
        "sessionId": session_id,
        "type": "feedback_pnl_with_teams_recording",
        "context_cleared": context_ended,
        "recording_stopped": stop_event is not None,
        "timestamp": time.time()
    }


@app.get("/api/feedback/context/{session_id}")
async def get_feedback_context(
    session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Retorna o contexto acumulado da sessão de feedback PNL"""
    
    if not teams_feedback_service:
        raise HTTPException(status_code=500, detail="Serviço de feedback PNL não disponível")
    
    conversation_summary = teams_feedback_service.get_conversation_summary(session_id)
    
    if not conversation_summary:
        raise HTTPException(status_code=404, detail="Sessão de feedback não encontrada")
    
    return {
        "sessionId": session_id,
        "context": conversation_summary,
        "timestamp": time.time()
    }


@app.get("/api/feedback/sessions")
async def list_feedback_sessions(api_key: str = Depends(verify_api_key)):
    """Lista todas as sessões de feedback PNL ativas"""
    
    # Inclui informações de contexto se disponível
    context_info = {}
    if teams_feedback_service:
        for session_id in ACTIVE_FEEDBACK_SESSIONS.keys():
            summary = teams_feedback_service.get_conversation_summary(session_id)
            if summary:
                context_info[session_id] = {
                    "total_segments": summary.get("total_chunks", 0),
                    "patterns_count": len(summary.get("padroes_identificados", [])),
                    "themes_count": len(summary.get("temas_recorrentes", [])),
                    "teams_url": summary.get("teams_url"),
                    "recording_id": summary.get("recording_id")
                }
    
    return {
        "active_feedback_sessions": list(ACTIVE_FEEDBACK_SESSIONS.keys()),
        "feedback_session_count": len(ACTIVE_FEEDBACK_SESSIONS),
        "feedback_sessions_info": {
            sid: info.dict() for sid, info in FEEDBACK_SESSION_INFO.items()
        },
        "context_info": context_info,
        "service_available": teams_feedback_service is not None
    }


@app.post("/api/feedback/session/{session_id}/pause")
async def pause_feedback_session(
    session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Pausa uma sessão de feedback PNL ativa"""
    
    if not teams_feedback_service:
        raise HTTPException(status_code=500, detail="Serviço de feedback PNL não disponível")
    
    success = teams_feedback_service.pause_feedback_session(session_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Sessão de feedback não encontrada")
    
    # Atualiza info da sessão
    if session_id in FEEDBACK_SESSION_INFO:
        FEEDBACK_SESSION_INFO[session_id].status = "paused"
    
    return {
        "status": "paused",
        "sessionId": session_id,
        "message": "Sessão de feedback pausada com sucesso",
        "timestamp": time.time()
    }


@app.post("/api/feedback/session/{session_id}/resume")
async def resume_feedback_session(
    session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Retoma uma sessão de feedback PNL pausada"""
    
    if not teams_feedback_service:
        raise HTTPException(status_code=500, detail="Serviço de feedback PNL não disponível")
    
    success = teams_feedback_service.resume_feedback_session(session_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Sessão de feedback não encontrada")
    
    # Atualiza info da sessão
    if session_id in FEEDBACK_SESSION_INFO:
        FEEDBACK_SESSION_INFO[session_id].status = "active"
    
    return {
        "status": "resumed",
        "sessionId": session_id,
        "message": "Sessão de feedback retomada com sucesso",
        "timestamp": time.time()
    }


async def execute_teams_recording_with_feedback(
    session_id: str,
    url: str,
    stop_event: asyncio.Event,
    segment_time: int,
    upload_dest: str,
    record_video: bool,
    meeting_session_id: str,
    recording_session_id: str
):
    """
    Executa a gravação do Teams com análise de feedback PNL em background.
    Esta função roda assincronamente e envia eventos via SSE.
    Agora com persistência no Supabase.
    """
    try:
        # Atualizar status no banco
        try:
            persistence_service = await get_persistence_service()
            await persistence_service.update_recording_session_status(
                recording_session_id, "active"
            )
        except Exception as e:
            logger.warning(f"⚠️ Erro ao atualizar status inicial: {e}")
        
        # Inicia sessão de feedback no serviço
        feedback_session = teams_feedback_service.create_feedback_session(
            session_id=session_id,
            teams_url=url,
            recording_id=session_id
        )
        
        # Envia evento de início
        start_event = SSEEvent(
            type="feedback_session_started",
            data={
                "session_id": session_id,
                "teams_url": url,
                "feedback_enabled": True,
                "meeting_session_id": meeting_session_id,
                "recording_session_id": recording_session_id
            },
            message="Sessão de feedback PNL iniciada com gravação do Teams",
            timestamp=time.time()
        )
        await ACTIVE_FEEDBACK_SESSIONS[session_id].put(start_event.dict())
        
        # Usar a integração correta que processa segmentos para análise PNL
        async for event in teams_recording_feedback.start_recording_with_feedback(
            session_id=session_id,
            teams_url=url,
            stop_event=stop_event,
            segment_time=segment_time,
            upload_dest=upload_dest,
            record_video=record_video
        ):
            # Log todos os eventos recebidos para debug
            logger.info(f"🎬💬 Evento: {event.get('event', 'unknown')}")
            
            # Salvar contexto de feedback no banco se disponível
            if event.get("event") == "feedback_analysis":
                logger.info(f"📊 Processando evento de análise de feedback para recording_session_id: {recording_session_id}")
                try:
                    analysis_data = event.get("analysis", {})
                    if analysis_data:
                        logger.info(f"💾 Salvando contexto de feedback no banco (dados presentes: {len(str(analysis_data))} chars)")
                        persistence_service = await get_persistence_service()
                        update_success = await persistence_service.update_feedback_context(
                            recording_session_id, analysis_data
                        )
                        if update_success:
                            logger.info(f"✅ Contexto de feedback salvo com sucesso no banco")
                        else:
                            logger.error(f"❌ Falha ao salvar contexto de feedback no banco")
                    else:
                        logger.warning(f"⚠️ Dados de análise vazios no evento feedback_analysis")
                except Exception as e:
                    logger.error(f"❌ Erro ao salvar contexto de feedback: {e}")
                    import traceback
                    logger.error(f"❌ Stack trace: {traceback.format_exc()}")
                    
                feedback_event = SSEEvent(
                    type="feedback_analysis",
                    data=event.get("analysis"),
                    message=event.get("message"),
                    timestamp=time.time()
                )
            elif event.get("event") == "feedback_final_recording":
                # Salvar URL da gravação final no banco
                try:
                    persistence_service = await get_persistence_service()
                    await persistence_service.save_final_recording(
                        recording_session_id,
                        event.get("file_url"),
                        event.get("public_url", event.get("file_url")),
                        event.get("total_segments", 0)
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Erro ao salvar gravação final: {e}")
                
                # Evento especial para gravação final com link
                feedback_event = SSEEvent(
                    type="final_recording_url",
                    data={
                        "file_url": event.get("file_url"),
                        "public_url": event.get("public_url", event.get("file_url")),
                        "session_id": session_id,
                        "total_segments": event.get("total_segments", 0),
                        "meeting_session_id": meeting_session_id,
                        "recording_session_id": recording_session_id
                    },
                    message=event.get("message", "Gravação finalizada"),
                    timestamp=time.time()
                )
            else:
                feedback_event = SSEEvent(
                    type="recording_update",
                    data=event,
                    message=event.get("message"),
                    timestamp=time.time()
                )
            
            await ACTIVE_FEEDBACK_SESSIONS[session_id].put(feedback_event.dict())
            
            # Log eventos importantes
            event_name = event.get("event", "unknown")
            if event_name in ["recording_started", "recording_completed", "recording_stopped", "feedback_analysis", "feedback_final_recording"]:
                logger.info(f"🎬💬 {event.get('message', f'Evento: {event_name}')}")
            
            # Se for evento final, adicionar informações extras
            if event_name in ["recording_completed", "recording_stopped", "feedback_final_recording"]:
                if event.get("file_url"):
                    logger.info(f"📹 URL da gravação final: {event.get('file_url')}")
        
        # Obter informações finais da sessão
        final_session = teams_feedback_service.get_feedback_session(session_id)
        total_segments = final_session.total_chunks if final_session else 0
        
        # Atualizar status final no banco
        try:
            persistence_service = await get_persistence_service()
            await persistence_service.update_recording_session_status(
                recording_session_id, "completed"
            )
        except Exception as e:
            logger.warning(f"⚠️ Erro ao atualizar status final: {e}")
        
        # Evento de conclusão
        complete_event = SSEEvent(
            type="feedback_recording_complete",
            data={
                "session_id": session_id,
                "total_segments": total_segments,
                "meeting_session_id": meeting_session_id,
                "recording_session_id": recording_session_id
            },
            message="Gravação com análise de feedback PNL concluída",
            timestamp=time.time()
        )
        await ACTIVE_FEEDBACK_SESSIONS[session_id].put(complete_event.dict())
                
    except Exception as e:
        logger.error(f"❌ Erro na execução de gravação com feedback {session_id[:8]}...: {e}")
        
        # Atualizar status de erro no banco
        try:
            persistence_service = await get_persistence_service()
            await persistence_service.update_recording_session_status(
                recording_session_id, "failed"
            )
        except Exception as db_error:
            logger.warning(f"⚠️ Erro ao atualizar status de erro: {db_error}")
        
        # Envia erro via SSE
        error_event = SSEEvent(
            type="error",
            message=f"Erro na gravação com feedback: {str(e)}",
            timestamp=time.time()
        )
        try:
            await ACTIVE_FEEDBACK_SESSIONS[session_id].put(error_event.dict())
        except:
            pass
        
    finally:
        # Limpeza
        STOP_EVENTS.pop(session_id, None)
        ACTIVE_RECORDINGS.pop(session_id, None)
        logger.info(f"🧹 Gravação com feedback {session_id[:8]}... finalizada | Total ativo: {len(ACTIVE_RECORDINGS)}")


# =======================================
# NOVOS ENDPOINTS PARA INTEGRAÇÃO ATTENDEE
# =======================================

@app.post("/api/attendee/start-recording", response_model=AttendeeRecordingResponse)
async def start_attendee_recording(
    request: AttendeeRecordingRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Inicia gravação de reunião usando a API do Attendee.
    
    Este endpoint:
    1. Cria uma meeting_session no banco
    2. Cria um bot no Attendee
    3. Cria uma recording_session vinculada
    4. Inicia monitoramento em background
    """
    try:
        # Obter serviços necessários
        attendee_service = await get_attendee_service()
        attendee_monitoring = await get_attendee_monitoring()
        persistence = await get_persistence_service()
        
        # Gerar IDs únicos
        meeting_session_id = str(uuid.uuid4())
        recording_session_id = str(uuid.uuid4())
        
        # Criar meeting_session no banco
        try:
            async with persistence.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO meeting_sessions (
                        id, user_id, tenant_id, calendar_event_id, 
                        source_type, status, session_name, metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    meeting_session_id,
                    request.user_id,
                    request.tenant_id,
                    request.event_id if request.event_id else None,
                    request.source_type,
                    'processing',
                    request.session_name,
                    json.dumps(request.metadata or {})
                )
                
                logger.info(f"✅ Meeting session criada: {meeting_session_id}")
        except Exception as e:
            logger.error(f"❌ Erro ao criar meeting_session: {e}")
            raise HTTPException(status_code=500, detail="Erro ao criar sessão de reunião")
        
        # Criar bot no Attendee
        bot_metadata = {
            "meeting_session_id": meeting_session_id,
            "recording_session_id": recording_session_id,
            "session_name": request.session_name,
            "source_type": request.source_type
        }
        
        bot_data = await attendee_service.create_bot(
            meeting_url=request.meeting_url,
            bot_name=f"MarIA Bot - {request.session_name}",
            metadata=bot_metadata
        )
        
        if not bot_data:
            # Limpar meeting_session criada
            try:
                async with persistence.pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM meeting_sessions WHERE id = $1",
                        meeting_session_id
                    )
            except:
                pass
            raise HTTPException(status_code=500, detail="Erro ao criar bot no Attendee")
        
        attendee_bot_id = bot_data["id"]
        
        # Criar recording_session no banco
        recording_session_uuid = await persistence.create_attendee_recording_session(
            meeting_session_id=meeting_session_id,
            session_id=recording_session_id,
            attendee_meeting_url=request.meeting_url,
            attendee_bot_id=attendee_bot_id,
            session_name=request.session_name,
            metadata=bot_metadata
        )
        
        if not recording_session_uuid:
            raise HTTPException(status_code=500, detail="Erro ao criar sessão de gravação")
        
        # Iniciar monitoramento em background
        await attendee_monitoring.start_monitoring(recording_session_uuid, attendee_bot_id)
        
        logger.info(f"🤖 Gravação Attendee iniciada: bot={attendee_bot_id}, session={request.session_name}")
        
        return AttendeeRecordingResponse(
            recording_session_id=recording_session_uuid,
            meeting_session_id=meeting_session_id,
            attendee_bot_id=attendee_bot_id,
            session_name=request.session_name,
            meeting_url=request.meeting_url,
            status="active",
            monitoring_active=True,
            created_at=datetime.now(),
            message=f"Gravação iniciada com sucesso. Bot ID: {attendee_bot_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro inesperado ao iniciar gravação Attendee: {e}")
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.get("/api/attendee/meeting-metadata/{recording_session_id}", response_model=MeetingSessionMetadata)
async def get_meeting_metadata(
    recording_session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Recupera metadados completos de uma sessão de reunião.
    
    Retorna:
    - Status atual da gravação e bot
    - Transcrições com timestamps
    - Análise de feedback gerada
    - URL da gravação (quando disponível)
    - Informações dos falantes
    """
    try:
        persistence = await get_persistence_service()
        
        # Buscar metadados completos
        metadata = await persistence.get_meeting_session_metadata(recording_session_id)
        
        if not metadata:
            raise HTTPException(status_code=404, detail="Sessão de reunião não encontrada")
        
        # Formatar resposta
        return MeetingSessionMetadata(
            recording_session_id=str(metadata["id"]),
            meeting_session_id=str(metadata["meeting_session_id"]),
            session_name=metadata.get("meeting_session_name"),
            meeting_url=metadata.get("attendee_meeting_url") or metadata.get("teams_url"),
            status=metadata["status"],
            attendee_bot_id=metadata.get("attendee_bot_id"),
            attendee_bot_state=metadata.get("attendee_bot_state"),
            attendee_recording_state=metadata.get("attendee_recording_state"),
            attendee_transcription_state=metadata.get("attendee_transcription_state"),
            transcription_data=metadata.get("transcription_data", []),
            feedback_analysis=metadata.get("feedback_analysis", {}),
            speaker_data=metadata.get("speaker_data", []),
            final_recording_url=metadata.get("final_recording_url"),
            calendar_event_title=metadata.get("calendar_event_title"),
            total_chunks=metadata.get("total_chunks", 0),
            monitoring_active=metadata.get("bot_monitoring_active", False),
            created_at=metadata["created_at"],
            updated_at=metadata["updated_at"],
            ended_at=metadata.get("ended_at")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro ao buscar metadados da reunião: {e}")
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.post("/api/attendee/stop-recording/{recording_session_id}")
async def stop_attendee_recording(
    recording_session_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Para monitoramento de uma gravação Attendee específica.
    
    Note: Isso apenas para o monitoramento local. O bot do Attendee
    continua gravando até que a reunião termine naturalmente.
    """
    try:
        attendee_monitoring = await get_attendee_monitoring()
        
        # Parar monitoramento
        await attendee_monitoring.stop_monitoring(recording_session_id)
        
        # Atualizar status no banco
        persistence = await get_persistence_service()
        await persistence.update_recording_session_status(recording_session_id, "cancelled")
        
        logger.info(f"⏹️ Monitoramento Attendee parado: {recording_session_id}")
        
        return {
            "message": "Monitoramento parado com sucesso",
            "recording_session_id": recording_session_id,
            "status": "cancelled"
        }
        
    except Exception as e:
        logger.error(f"❌ Erro ao parar monitoramento Attendee: {e}")
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
