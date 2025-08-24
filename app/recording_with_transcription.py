import os
import asyncio
import logging
import time
from datetime import datetime, timedelta
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
        transcription_manager = TranscriptionManager(recording_id=recording_id)
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
                # O basename é gerado dentro da gravação, vamos pegar do tempo atual
                # mas aguardar um pouco para o diretório ser criado
                await asyncio.sleep(1)
                
                # Procurar por diretórios de segmentos recentes
                current_time = datetime.now()
                possible_basenames = []
                
                # Gerar possíveis nomes baseados no horário (pode ter diferença de alguns segundos)
                for offset in range(-30, 31):  # +/- 30 segundos
                    test_time = current_time + timedelta(seconds=offset)
                    test_basename = test_time.strftime('gravacao_%Y%m%d_%H%M%S')
                    test_dir = f"segments_{test_basename}"
                    if os.path.exists(test_dir):
                        segments_dir = test_dir
                        logger.info(f"🎯 Diretório de segmentos encontrado: {segments_dir}")
                        break
                
                if not segments_dir:
                    # Fallback: procurar qualquer diretório segments_* recente
                    import glob
                    segment_dirs = glob.glob("segments_gravacao_*")
                    if segment_dirs:
                        # Pegar o mais recente
                        segments_dir = max(segment_dirs, key=os.path.getctime)
                        logger.info(f"🎯 Diretório de segmentos encontrado (fallback): {segments_dir}")
                
                if segments_dir:
                    yield {
                        "event": "transcription_segments_ready",
                        "segments_dir": segments_dir,
                        "message": "Monitoramento de segmentos iniciado"
                    }
                    
                    # Aguardar um pouco para garantir que a gravação já criou alguns segmentos
                    logger.info(f"⏳ Aguardando 10 segundos antes de iniciar monitoramento...")
                    await asyncio.sleep(10)
                    
                    # Iniciar task de monitoramento de segmentos
                    monitor_task = asyncio.create_task(
                        monitor_segments_for_transcription(
                            segments_dir,
                            transcription_manager,
                            recording_id,
                            stop_event
                        )
                    )
                    logger.info(f"🔍 Task de monitoramento iniciada para: {segments_dir}")
                else:
                    logger.warning("⚠️ Diretório de segmentos não encontrado")
                    yield {
                        "event": "transcription_warning",
                        "message": "Diretório de segmentos não encontrado"
                    }
            
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
    check_counter = 0
    
    logger.info(f"🔍 Iniciando monitoramento de segmentos em: {segments_dir}")
    
    while not stop_event.is_set():
        check_counter += 1
        try:
            # Log a cada 10 verificações para acompanhar o progresso
            if check_counter % 10 == 0:
                logger.info(f"🔄 Verificação #{check_counter} - Processados: {len(processed_segments)} segmentos")
            
            if not os.path.exists(segments_dir):
                logger.warning(f"📂 Diretório ainda não existe: {segments_dir}")
                await asyncio.sleep(2)
                continue
            
            # Listar todos os arquivos no diretório para debug
            all_files = os.listdir(segments_dir)
            segment_files = [f for f in all_files if f.endswith('.ts')]
            new_segment_files = [f for f in segment_files if f not in processed_segments]
            
            logger.debug(f"📁 Arquivos no diretório: {len(all_files)} total, {len(segment_files)} .ts, {len(new_segment_files)} novos")
            
            # Processar novos segmentos
            for segment_file in sorted(new_segment_files):
                if stop_event.is_set():
                    logger.info("⏹️ Stop event detectado, parando processamento")
                    break
                
                segment_path = os.path.join(segments_dir, segment_file)
                
                # Aguardar o arquivo estar completo (não sendo escrito)
                try:
                    # Verificar se arquivo existe e não está sendo modificado
                    if not os.path.exists(segment_path):
                        logger.warning(f"⚠️ Arquivo não encontrado: {segment_path}")
                        continue
                        
                    initial_size = os.path.getsize(segment_path)
                    await asyncio.sleep(1.5)  # Aumentar tempo de espera
                    final_size = os.path.getsize(segment_path)
                    
                    if initial_size != final_size:
                        logger.info(f"📝 Arquivo ainda sendo escrito: {segment_file} ({initial_size} → {final_size} bytes)")
                        continue  # Arquivo ainda sendo escrito
                    
                    # Verificar tamanho mínimo (evitar arquivos vazios)
                    if final_size < 10240:  # Aumentar para 10KB
                        logger.info(f"📦 Arquivo muito pequeno: {segment_file} ({final_size} bytes)")
                        continue
                        
                    logger.info(f"✅ Arquivo pronto para processamento: {segment_file} ({final_size} bytes)")
                        
                except (OSError, FileNotFoundError) as e:
                    logger.error(f"💥 Erro ao acessar arquivo {segment_file}: {e}")
                    continue
                
                # Marcar como processado antes de iniciar (evitar duplicação)
                processed_segments.add(segment_file)
                segment_counter += 1
                
                logger.info(f"🎬 Iniciando processamento do segmento #{segment_counter}: {segment_file}")
                
                # Processar transcrição do segmento
                try:
                    transcription_success = False
                    async for transcription_event in transcription_manager.process_segment_for_transcription(
                        segment_path, segment_counter
                    ):
                        # Adicionar resultado ao tracker
                        transcription_tracker.add_transcription_result(recording_id, transcription_event)
                        transcription_success = True
                        
                        # Log apenas eventos importantes
                        if transcription_event.get("event") == "transcription_segment":
                            falante = transcription_event.get('falante', 'N/A')
                            texto = transcription_event.get('texto', '')
                            logger.info(f"📝 Transcrição recebida - {falante}: {texto[:100]}...")
                    
                    if transcription_success:
                        logger.info(f"✅ Segmento {segment_file} processado com sucesso")
                    else:
                        logger.warning(f"⚠️ Nenhuma transcrição recebida para {segment_file}")
                
                except Exception as e:
                    logger.error(f"❌ Erro no processamento do segmento {segment_file}: {e}")
                    import traceback
                    logger.error(f"Stack trace: {traceback.format_exc()}")
                    
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
            logger.error(f"❌ Erro geral no monitoramento de segmentos: {e}")
            import traceback
            logger.error(f"Stack trace: {traceback.format_exc()}")
            await asyncio.sleep(5)
    
    logger.info(f"✅ Monitoramento de segmentos finalizado. Total processados: {len(processed_segments)}")
    
    # Log final dos arquivos processados
    if processed_segments:
        logger.info(f"📋 Segmentos processados: {sorted(list(processed_segments))}")
    else:
        logger.warning("⚠️ NENHUM SEGMENTO FOI PROCESSADO!")
        # Verificar se o diretório ainda existe e listar seus arquivos
        if os.path.exists(segments_dir):
            all_files = os.listdir(segments_dir)
            logger.info(f"📁 Arquivos finais no diretório: {all_files}")
        else:
            logger.error(f"📂 Diretório não existe no final: {segments_dir}")

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
