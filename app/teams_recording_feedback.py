import asyncio
import logging
import os
import glob
import subprocess
import tempfile
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
            
            # Variáveis para tracking de segmentos processados
            processed_segments = set()
            segment_counter = 0
            current_segments_dir = None
            segment_monitor_task = None
            
            # Inicializar queue para eventos de feedback
            self.feedback_queue = asyncio.Queue()
            
            # Criar task para consumir eventos de feedback em paralelo
            feedback_consumer_task = asyncio.create_task(
                self._consume_feedback_events()
            )
            
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
                
                # Capturar diretório de segmentos e iniciar monitoramento quando a gravação iniciar
                if recording_event.get("event") == "recording_started" and recording_event.get("segments_dir"):
                    current_segments_dir = recording_event.get("segments_dir")
                    logger.info(f"📁 Monitoramento de segmentos para análise PNL iniciado em: {current_segments_dir}")
                    
                    # Iniciar task de monitoramento de segmentos em background
                    segment_monitor_task = asyncio.create_task(
                        self._monitor_segments_for_feedback_with_queue(
                            current_segments_dir,
                            session_id,
                            stop_event,
                            processed_segments
                        )
                    )
                
                # Verificar se há eventos de feedback na queue (non-blocking)
                try:
                    while True:
                        feedback_event = self.feedback_queue.get_nowait()
                        yield feedback_event
                        segment_counter += 1
                except asyncio.QueueEmpty:
                    pass  # Nenhum evento de feedback pendente
                
                # Se a gravação for finalizada, processar últimos segmentos
                if recording_event.get("event") in ["recording_completed", "recording_stopped"]:
                    logger.info(f"🏁 Gravação finalizada, processando segmentos finais...")
                    
                    # Aguardar conclusão do monitoramento
                    if segment_monitor_task and not segment_monitor_task.done():
                        try:
                            await asyncio.wait_for(segment_monitor_task, timeout=10.0)
                        except asyncio.TimeoutError:
                            logger.warning("Timeout ao aguardar finalização do monitoramento de segmentos")
                    
                    # Consumir eventos finais de feedback
                    try:
                        while True:
                            feedback_event = self.feedback_queue.get_nowait()
                            yield feedback_event
                            segment_counter += 1
                    except asyncio.QueueEmpty:
                        pass
                    
                    # Parar task de consumo de feedback
                    if feedback_consumer_task and not feedback_consumer_task.done():
                        feedback_consumer_task.cancel()
                    
                    # Enviar link do arquivo final se disponível
                    file_url = recording_event.get("file_url")
                    if file_url:
                        yield {
                            "event": "feedback_final_recording",
                            "session_id": session_id,
                            "file_url": file_url,
                            "public_url": file_url,  # Compatibilidade
                            "message": "Gravação com feedback PNL finalizada - Link disponível",
                            "total_segments": len(processed_segments)
                        }
                        logger.info(f"📹 URL final da gravação: {file_url}")
                    
                    yield {
                        "event": "feedback_analysis_complete",
                        "session_id": session_id,
                        "message": "Análise de feedback PNL concluída",
                        "total_segments": len(processed_segments)
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

    async def _consume_feedback_events(self):
        """
        Consome eventos de feedback da queue em background.
        Esta função roda em paralelo ao generator principal.
        """
        logger.info("🎯 Iniciando consumidor de eventos de feedback PNL")
        
        while True:
            try:
                # Aguardar por novos eventos de feedback (com timeout para não bloquear indefinidamente)
                feedback_event = await asyncio.wait_for(self.feedback_queue.get(), timeout=30.0)
                
                # O evento será processado pelo generator principal
                # Esta função apenas garante que a queue não fique bloqueada
                
            except asyncio.TimeoutError:
                # Timeout normal, continuar aguardando
                continue
            except asyncio.CancelledError:
                logger.info("🛑 Consumidor de eventos de feedback cancelado")
                break
            except Exception as e:
                logger.error(f"❌ Erro no consumidor de eventos de feedback: {e}")
                await asyncio.sleep(5)

    async def _monitor_segments_for_feedback(
        self,
        segments_dir: str,
        session_id: str,
        stop_event: asyncio.Event,
        processed_segments: set
    ):
        """
        Monitora diretório de segmentos e processa novos arquivos para análise PNL.
        Executa em background durante a gravação.
        """
        segment_counter = 0
        logger.info(f"🔍 Iniciando monitoramento de segmentos para PNL em: {segments_dir}")
        
        while not stop_event.is_set():
            try:
                # Buscar novos arquivos .ts no diretório
                pattern = os.path.join(segments_dir, "*.ts")
                segment_files = glob.glob(pattern)
                
                for segment_path in segment_files:
                    segment_name = os.path.basename(segment_path)
                    
                    # Processar apenas segmentos novos e com tamanho mínimo
                    if (segment_name not in processed_segments and 
                        os.path.exists(segment_path) and
                        os.path.getsize(segment_path) > 1024):  # Arquivo com pelo menos 1KB
                        
                        try:
                            # Aguardar um pouco para garantir que o arquivo está completo
                            await asyncio.sleep(2)
                            
                            # Verificar se ainda existe e tem tamanho adequado
                            if not os.path.exists(segment_path) or os.path.getsize(segment_path) < 1024:
                                continue
                            
                            # Extrair áudio do segmento .ts para análise PNL
                            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_audio:
                                temp_audio_path = temp_audio.name
                            
                            try:
                                # Extrair áudio do segmento .ts usando FFmpeg
                                cmd = [
                                    "ffmpeg", "-i", segment_path,
                                    "-vn",  # Sem vídeo
                                    "-acodec", "pcm_s16le",  # Codec de áudio WAV
                                    "-ar", "16000",  # Taxa de amostragem 16kHz
                                    "-ac", "1",  # Mono
                                    "-y",  # Sobrescrever arquivo
                                    temp_audio_path
                                ]
                                
                                result = subprocess.run(
                                    cmd, 
                                    capture_output=True, 
                                    text=True, 
                                    timeout=30
                                )
                                
                                if result.returncode == 0 and os.path.exists(temp_audio_path):
                                    # Verificar se o arquivo de áudio tem conteúdo
                                    if os.path.getsize(temp_audio_path) > 1024:  # Pelo menos 1KB de áudio
                                        # Ler arquivo de áudio extraído
                                        with open(temp_audio_path, 'rb') as audio_file:
                                            audio_data = audio_file.read()
                                        
                                        # Processar para análise PNL
                                        analysis_result = await self.feedback_service.process_feedback_audio_with_context(
                                            session_id, audio_data, "audio/wav"
                                        )
                                        
                                        if analysis_result:
                                            segment_counter += 1
                                            logger.info(f"🧠 Análise PNL concluída para segmento {segment_counter}: {segment_name}")
                                            
                                            # Evento será propagado pelo generator principal via queue interna se necessário
                                        
                                        processed_segments.add(segment_name)
                                    else:
                                        logger.warning(f"⚠️ Áudio extraído muito pequeno para {segment_name}")
                                else:
                                    logger.error(f"❌ Falha na extração de áudio de {segment_name}: {result.stderr}")
                            
                            finally:
                                # Remover arquivo temporário
                                if os.path.exists(temp_audio_path):
                                    os.unlink(temp_audio_path)
                        
                        except Exception as e:
                            logger.error(f"❌ Erro ao processar segmento {segment_name} para análise PNL: {e}")
                
                # Aguardar antes da próxima verificação
                await asyncio.sleep(10)  # Verificar a cada 10 segundos
                
            except Exception as e:
                logger.error(f"❌ Erro no monitoramento de segmentos: {e}")
                await asyncio.sleep(5)  # Aguardar um pouco antes de tentar novamente
        
        logger.info(f"✅ Monitoramento de segmentos finalizado. Total processados: {len(processed_segments)}")

    async def _monitor_segments_for_feedback_with_queue(
        self,
        segments_dir: str,
        session_id: str,
        stop_event: asyncio.Event,
        processed_segments: set
    ):
        """
        Versão melhorada do monitoramento que processa segmentos e gera eventos.
        Usando uma approach mais robusta para garantir processamento contínuo.
        """
        segment_counter = 0
        logger.info(f"🔍 Iniciando monitoramento otimizado de segmentos PNL em: {segments_dir}")
        
        # Criar queue para comunicação com o generator principal
        self.feedback_queue = asyncio.Queue() if not hasattr(self, 'feedback_queue') else self.feedback_queue
        
        while not stop_event.is_set():
            try:
                # Buscar novos arquivos .ts no diretório
                pattern = os.path.join(segments_dir, "*.ts")
                segment_files = sorted(glob.glob(pattern))  # Ordenar para processar em sequência
                
                for segment_path in segment_files:
                    if stop_event.is_set():
                        break
                        
                    segment_name = os.path.basename(segment_path)
                    
                    # Processar apenas segmentos novos, completos e estáveis
                    if (segment_name not in processed_segments and 
                        os.path.exists(segment_path)):
                        
                        # Verificar se o arquivo é estável (não está sendo escrito)
                        initial_size = os.path.getsize(segment_path)
                        await asyncio.sleep(3)  # Aguardar estabilização
                        
                        if not os.path.exists(segment_path):
                            continue
                            
                        current_size = os.path.getsize(segment_path)
                        
                        # Se o tamanho mudou, aguardar mais um pouco
                        if initial_size != current_size:
                            await asyncio.sleep(2)
                            current_size = os.path.getsize(segment_path)
                        
                        # Verificar tamanho mínimo
                        if current_size < 1024:  # Menor que 1KB
                            continue
                        
                        try:
                            logger.info(f"🎵 Processando segmento estável: {segment_name} ({current_size} bytes)")
                            
                            # Extrair áudio do segmento .ts para análise PNL
                            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_audio:
                                temp_audio_path = temp_audio.name
                            
                            try:
                                # Extrair áudio usando FFmpeg com configurações otimizadas
                                cmd = [
                                    "ffmpeg", "-i", segment_path,
                                    "-vn",  # Sem vídeo
                                    "-acodec", "pcm_s16le",  # Codec WAV
                                    "-ar", "16000",  # 16kHz
                                    "-ac", "1",  # Mono
                                    "-y",  # Sobrescrever
                                    temp_audio_path
                                ]
                                
                                result = subprocess.run(
                                    cmd, 
                                    capture_output=True, 
                                    text=True, 
                                    timeout=45  # Timeout mais generoso
                                )
                                
                                if result.returncode == 0 and os.path.exists(temp_audio_path):
                                    # Verificar se o áudio extraído tem conteúdo válido
                                    audio_size = os.path.getsize(temp_audio_path)
                                    if audio_size > 1024:  # Pelo menos 1KB de áudio
                                        # Ler arquivo de áudio extraído
                                        with open(temp_audio_path, 'rb') as audio_file:
                                            audio_data = audio_file.read()
                                        
                                        # Processar análise PNL
                                        logger.info(f"🧠 Enviando {len(audio_data)} bytes de áudio para análise PNL...")
                                        analysis_result = await self.feedback_service.process_feedback_audio_with_context(
                                            session_id, audio_data, "audio/wav"
                                        )
                                        
                                        if analysis_result:
                                            segment_counter += 1
                                            logger.info(f"✅ Análise PNL concluída para segmento {segment_counter}: {segment_name}")
                                            
                                            # Criar evento de feedback que será capturado pelo generator principal
                                            feedback_event = {
                                                "event": "feedback_analysis",
                                                "session_id": session_id,
                                                "segment_number": segment_counter,
                                                "segment_path": segment_path,
                                                "segment_name": segment_name,
                                                "analysis": analysis_result.model_dump(),
                                                "message": f"🧠 Análise PNL do segmento {segment_counter} concluída",
                                                "timestamp": datetime.now().isoformat()
                                            }
                                            
                                            # Adicionar à queue para o generator principal
                                            await self.feedback_queue.put(feedback_event)
                                        else:
                                            logger.warning(f"⚠️ Análise PNL falhou para segmento {segment_name}")
                                        
                                        processed_segments.add(segment_name)
                                    else:
                                        logger.warning(f"⚠️ Áudio extraído muito pequeno para {segment_name}: {audio_size} bytes")
                                else:
                                    logger.error(f"❌ Falha na extração de áudio de {segment_name}: {result.stderr}")
                            
                            finally:
                                # Limpar arquivo temporário
                                if os.path.exists(temp_audio_path):
                                    os.unlink(temp_audio_path)
                        
                        except Exception as e:
                            logger.error(f"❌ Erro ao processar segmento {segment_name} para análise PNL: {e}")
                
                # Aguardar antes da próxima verificação
                await asyncio.sleep(15)  # Verificar a cada 15 segundos
                
            except Exception as e:
                logger.error(f"❌ Erro no monitoramento otimizado de segmentos: {e}")
                await asyncio.sleep(10)  # Aguardar mais tempo em caso de erro
        
        logger.info(f"✅ Monitoramento otimizado finalizado. Total processados: {len(processed_segments)}")


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
