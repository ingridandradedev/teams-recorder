import os
import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, AsyncGenerator
from app.async_recorder import gravar_reuniao_stream_async
from app.transcription import TranscriptionManager, transcription_tracker

# Configurar logging
logger = logging.getLogger(__name__)

async def gravar_com_transcricao_async(
    link_reuniao_original: str,
    recording_id: str,
    stop_event: asyncio.Event,
    segment_time: int = 60,
    upload_dest: str = "recordings-segments",
    record_video: bool = True
) -> AsyncGenerator[Dict, None]:
    """
    Executa gravação com transcrição em tempo real.
    Esta função combina a gravação normal com processamento de transcrição usando Gemini.
    """
    
    # Inicializar gerenciador de transcrição
    try:
        transcription_manager = TranscriptionManager()
    except ValueError as e:
        yield {
            "event": "transcription_error",
            "error": f"Erro na configuração do Gemini: {e}"
        }
        return
    
    # Registrar início da transcrição
    transcription_tracker.start_transcription(recording_id, {
        "url": link_reuniao_original,
        "segment_time": segment_time,
        "upload_dest": upload_dest
    })
    
    yield {
        "event": "transcription_recording_start",
        "recording_id": recording_id,
        "message": "Gravação com transcrição iniciada"
    }
    
    # Variáveis para tracking
    segments_dir = None
    processed_segments = set()
    segment_counter = 0
    
    try:
        # Iniciar gravação normal (sem yield dos eventos de navegação)
        recording_generator = gravar_reuniao_stream_async(
            link_reuniao_original,
            stop_event,
            segment_time,
            upload_dest,
            record_video
        )
        
        # Processar eventos da gravação
        async for event in recording_generator:
            event_type = event.get("event", "")
            
            # Capturar diretório de segmentos quando a gravação iniciar
            if event_type == "recording_started":
                # Extrair o nome base da gravação do timestamp
                basename = datetime.now().strftime('gravacao_%Y%m%d_%H%M%S')
                segments_dir = f"segments_{basename}"
                
                yield {
                    "event": "transcription_segments_ready",
                    "segments_dir": segments_dir,
                    "message": "Monitoramento de segmentos iniciado"
                }
                
                # Iniciar task de monitoramento de segmentos
                asyncio.create_task(
                    monitor_segments_for_transcription(
                        segments_dir,
                        transcription_manager,
                        recording_id,
                        stop_event
                    )
                )
            
            # Repassar apenas eventos importantes (não navegação)
            elif event_type in ["recording_started", "stop_requested", "cleanup_complete"]:
                yield event
    
    except Exception as e:
        logger.error(f"❌ Erro na gravação com transcrição: {e}")
        yield {
            "event": "transcription_recording_error",
            "error": str(e)
        }
    
    finally:
        # Finalizar tracking da transcrição
        transcription_tracker.finish_transcription(recording_id)
        yield {
            "event": "transcription_recording_complete",
            "recording_id": recording_id
        }

async def monitor_segments_for_transcription(
    segments_dir: str,
    transcription_manager: TranscriptionManager,
    recording_id: str,
    stop_event: asyncio.Event
):
    """
    Monitora o diretório de segmentos e processa novos arquivos para transcrição.
    Esta função roda em background durante a gravação.
    """
    processed_segments = set()
    segment_counter = 0
    
    logger.info(f"🔍 Iniciando monitoramento de segmentos em: {segments_dir}")
    
    while not stop_event.is_set():
        try:
            if not os.path.exists(segments_dir):
                await asyncio.sleep(2)
                continue
            
            # Listar arquivos .ts no diretório
            segment_files = [
                f for f in os.listdir(segments_dir) 
                if f.endswith('.ts') and f not in processed_segments
            ]
            
            # Processar novos segmentos
            for segment_file in sorted(segment_files):
                if stop_event.is_set():
                    break
                
                segment_path = os.path.join(segments_dir, segment_file)
                
                # Aguardar o arquivo estar completo (não sendo escrito)
                try:
                    # Verificar se arquivo não está sendo modificado
                    initial_size = os.path.getsize(segment_path)
                    await asyncio.sleep(1)
                    final_size = os.path.getsize(segment_path)
                    
                    if initial_size != final_size:
                        continue  # Arquivo ainda sendo escrito
                    
                    # Verificar tamanho mínimo (evitar arquivos vazios)
                    if final_size < 1024:  # Menor que 1KB
                        continue
                        
                except (OSError, FileNotFoundError):
                    continue
                
                # Marcar como processado antes de iniciar (evitar duplicação)
                processed_segments.add(segment_file)
                segment_counter += 1
                
                logger.info(f"🎬 Processando segmento #{segment_counter}: {segment_file}")
                
                # Processar transcrição do segmento
                try:
                    async for transcription_event in transcription_manager.process_segment_for_transcription(
                        segment_path, segment_counter
                    ):
                        # Adicionar resultado ao tracker
                        transcription_tracker.add_transcription_result(recording_id, transcription_event)
                        
                        # Log apenas eventos importantes
                        if transcription_event.get("event") == "transcription_segment":
                            logger.info(
                                f"📝 Transcrição - {transcription_event.get('falante', 'N/A')}: "
                                f"{transcription_event.get('texto', '')[:50]}..."
                            )
                
                except Exception as e:
                    logger.error(f"❌ Erro no processamento do segmento {segment_file}: {e}")
                    transcription_tracker.add_transcription_result(recording_id, {
                        "event": "segment_processing_error",
                        "segment_file": segment_file,
                        "error": str(e)
                    })
                
                # Atualizar progresso
                transcription_tracker.update_segment_progress(
                    recording_id, len(processed_segments), len(processed_segments)
                )
            
            # Aguardar antes da próxima verificação
            await asyncio.sleep(3)
        
        except Exception as e:
            logger.error(f"❌ Erro no monitoramento de segmentos: {e}")
            await asyncio.sleep(5)
    
    logger.info(f"✅ Monitoramento de segmentos finalizado. Total processados: {len(processed_segments)}")

async def get_transcription_stream(recording_id: str) -> AsyncGenerator[Dict, None]:
    """
    Stream de eventos de transcrição para um recording_id específico.
    """
    last_sent_index = 0
    
    yield {
        "event": "transcription_stream_start",
        "recording_id": recording_id
    }
    
    while True:
        # Verificar se a transcrição ainda está ativa
        status = transcription_tracker.get_transcription_status(recording_id)
        if not status:
            yield {
                "event": "transcription_not_found",
                "recording_id": recording_id,
                "error": "Transcrição não encontrada"
            }
            break
        
        # Obter novos resultados
        results = transcription_tracker.get_transcription_results(recording_id)
        
        # Enviar novos eventos
        if len(results) > last_sent_index:
            for i in range(last_sent_index, len(results)):
                yield results[i]
            last_sent_index = len(results)
        
        # Verificar se a transcrição foi finalizada
        if status.get("status") == "completed":
            yield {
                "event": "transcription_stream_complete",
                "recording_id": recording_id,
                "total_events": len(results)
            }
            break
        
        # Aguardar antes da próxima verificação
        await asyncio.sleep(2)
