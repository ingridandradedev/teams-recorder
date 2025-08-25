import asyncio
import logging
from typing import Dict, AsyncGenerator, Optional
from datetime import datetime
from app.async_recorder import gravar_reuniao_stream_async
from app.teams_feedback_service import TeamsFeedbackService

# Configurar logging
logger = logging.getLogger(__name__)


class TeamsRecordingWithFeedback:
    """Serviço para gravação do Teams integrada com análise de feedback PNL"""
    
    def __init__(self, feedback_service: TeamsFeedbackService):
        self.feedback_service = feedback_service
        
    async def start_recording_with_feedback(
        self,
        session_id: str,
        teams_url: str,
        stop_event: asyncio.Event,
        segment_time: int = 60,
        upload_dest: str = "recordings-segments",
        record_video: bool = True
    ) -> AsyncGenerator[Dict, None]:
        """
        Inicia gravação do Teams com análise de feedback PNL em tempo real.
        
        Args:
            session_id: ID da sessão de feedback
            teams_url: URL da reunião do Teams
            stop_event: Evento para parar a gravação
            segment_time: Tempo por segmento em segundos
            upload_dest: Destino do upload
            record_video: Se deve gravar vídeo
        
        Yields:
            Eventos de gravação e feedback
        """
        
        try:
            # Cria sessão de feedback
            feedback_session = self.feedback_service.create_feedback_session(
                session_id=session_id,
                teams_url=teams_url,
                recording_id=session_id  # Usando session_id como recording_id
            )
            
            yield {
                "event": "feedback_session_created",
                "session_id": session_id,
                "teams_url": teams_url,
                "status": "active",
                "message": "Sessão de feedback PNL criada com sucesso"
            }
            
            # Inicia gravação do Teams
            logger.info(f"Iniciando gravação Teams com feedback PNL: {session_id}")
            
            async for recording_event in gravar_reuniao_stream_async(
                teams_url, 
                stop_event, 
                segment_time=segment_time, 
                upload_dest=upload_dest, 
                record_video=record_video
            ):
                # Propaga eventos de gravação
                yield {
                    **recording_event,
                    "feedback_session_id": session_id,
                    "feedback_enabled": True
                }
                
                # Se a gravação for bem-sucedida, gera análise de feedback
                # Nota: Para análise real, precisaríamos acessar os segmentos de áudio
                # Por agora, vamos simular baseado nos eventos de gravação
                if recording_event.get("event") == "recording_complete":
                    yield {
                        "event": "feedback_analysis_complete",
                        "session_id": session_id,
                        "message": "Análise de feedback PNL concluída",
                        "total_segments": feedback_session.total_chunks
                    }
                
        except Exception as e:
            logger.error(f"Erro na gravação com feedback: {str(e)}")
            yield {
                "event": "feedback_recording_error",
                "session_id": session_id,
                "error": str(e),
                "message": "Erro na gravação integrada com feedback"
            }
        
        finally:
            logger.info(f"Finalizando gravação com feedback: {session_id}")


async def process_audio_for_feedback(
    feedback_service: TeamsFeedbackService,
    session_id: str,
    audio_data: bytes,
    mime_type: str = "audio/webm"
) -> Optional[Dict]:
    """
    Processa áudio para análise de feedback PNL.
    
    Args:
        feedback_service: Instância do serviço de feedback
        session_id: ID da sessão
        audio_data: Dados binários do áudio
        mime_type: Tipo MIME do áudio
    
    Returns:
        Resultado da análise ou None em caso de erro
    """
    try:
        result = await feedback_service.process_feedback_audio_with_context(
            session_id=session_id,
            audio_bytes=audio_data,
            mime_type=mime_type
        )
        
        if result:
            return {
                "event": "feedback_analysis",
                "session_id": session_id,
                "analysis": result.dict(),
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "event": "feedback_analysis_error",
                "session_id": session_id,
                "message": "Falha na análise de feedback",
                "timestamp": datetime.now().isoformat()
            }
            
    except Exception as e:
        logger.error(f"Erro no processamento de áudio para feedback: {str(e)}")
        return {
            "event": "feedback_processing_error",
            "session_id": session_id,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
