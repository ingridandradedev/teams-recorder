import os
import asyncio
import tempfile
import subprocess
import time
from typing import Optional
from google import genai
from app.async_recorder import gravar_reuniao_stream_async
import logging

logger = logging.getLogger(__name__)


class AudioTranscriptionService:
    """Serviço para gravação de áudio e transcrição completa após finalização"""
    
    def __init__(self, api_key: str):
        """Inicializa o cliente Gemini para transcrição"""
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-2.0-flash-001"
        
    async def record_and_transcribe_meeting(
        self,
        teams_url: str,
        stop_event: asyncio.Event,
        segment_time: int = 60,
        upload_dest: str = "audio-recordings"
    ) -> dict:
        """
        Grava áudio da reunião e faz transcrição completa após finalização.
        
        Args:
            teams_url: URL da reunião do Teams
            stop_event: Evento para parar a gravação
            segment_time: Tempo por segmento em segundos
            upload_dest: Destino do upload dos segmentos
        
        Returns:
            Dict com resultado da gravação e transcrição
        """
        recording_result = {
            "recording_completed": False,
            "audio_file_path": None,
            "transcription": None,
            "segments_uploaded": [],
            "error": None
        }
        
        try:
            logger.info(f"Iniciando gravação de áudio para transcrição: {teams_url}")
            
            # Inicia gravação do Teams (apenas áudio para economizar recursos)
            segments_info = []
            segments_dir = None
            
            async for event in gravar_reuniao_stream_async(
                teams_url, 
                stop_event, 
                segment_time=segment_time, 
                upload_dest=upload_dest, 
                record_video=False  # Apenas áudio para transcrição
            ):
                # Capturar informações dos segmentos
                if event.get("event") == "recording_started":
                    segments_dir = event.get("segments_dir")
                    recording_result["segments_dir"] = segments_dir
                    logger.info(f"Gravação iniciada, segmentos em: {segments_dir}")
                
                elif event.get("event") == "segment_uploaded":
                    segment_info = {
                        "path": event.get("segment_path"),
                        "url": event.get("public_url", ""),
                        "gs_uri": event.get("gs_uri", "")
                    }
                    segments_info.append(segment_info)
                    recording_result["segments_uploaded"].append(segment_info)
                    logger.info(f"Segmento processado: {segment_info}")
                
                elif event.get("event") == "recording_complete":
                    recording_result["recording_completed"] = True
                    logger.info("Gravação concluída, iniciando processamento para transcrição")
                    break
            
            # Após gravação, processar segmentos para criar arquivo único de áudio
            if recording_result["recording_completed"] and segments_dir:
                audio_file_path = await self._merge_segments_to_audio(segments_dir)
                recording_result["audio_file_path"] = audio_file_path
                
                # Fazer transcrição com Gemini
                if audio_file_path and os.path.exists(audio_file_path):
                    transcription = await self._transcribe_audio_with_gemini(audio_file_path)
                    recording_result["transcription"] = transcription
                    logger.info("Transcrição concluída com sucesso")
                    
                    # Limpar arquivo temporário
                    try:
                        os.remove(audio_file_path)
                        logger.info(f"Arquivo temporário removido: {audio_file_path}")
                    except Exception as e:
                        logger.warning(f"Erro ao remover arquivo temporário: {e}")
                
                else:
                    recording_result["error"] = "Não foi possível criar arquivo de áudio para transcrição"
            
            else:
                recording_result["error"] = "Gravação não foi concluída adequadamente"
        
        except Exception as e:
            logger.error(f"Erro na gravação e transcrição: {e}")
            recording_result["error"] = str(e)
        
        return recording_result
    
    async def _merge_segments_to_audio(self, segments_dir: str) -> Optional[str]:
        """
        Mescla segmentos em um único arquivo MP3 para transcrição.
        
        Args:
            segments_dir: Diretório com os segmentos .ts
            
        Returns:
            Caminho do arquivo MP3 mesclado ou None se falhou
        """
        try:
            import glob
            
            # Buscar arquivos .ts no diretório
            ts_files = glob.glob(os.path.join(segments_dir, "*.ts"))
            ts_files.sort()  # Ordenar por nome para sequência correta
            
            if not ts_files:
                logger.error(f"Nenhum arquivo .ts encontrado em {segments_dir}")
                return None
            
            logger.info(f"Encontrados {len(ts_files)} segmentos para mesclar")
            
            # Criar arquivo temporário para o áudio final
            temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            output_audio_path = temp_audio.name
            temp_audio.close()
            
            # Criar lista de arquivos para concatenação FFmpeg
            concat_list_path = os.path.join(segments_dir, "concat_audio_list.txt")
            with open(concat_list_path, 'w') as f:
                for ts_file in ts_files:
                    f.write(f"file '{os.path.abspath(ts_file)}'\n")
            
            # Comando FFmpeg para concatenar e converter para MP3
            ffmpeg_command = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_list_path,
                '-vn',  # Sem vídeo
                '-acodec', 'libmp3lame',
                '-ar', '16000',  # Sample rate 16kHz (recomendado para Gemini)
                '-ac', '1',      # Mono
                '-b:a', '128k',  # Bitrate 128k
                output_audio_path
            ]
            
            # Executar FFmpeg
            result = subprocess.run(
                ffmpeg_command,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutos timeout
            )
            
            if result.returncode == 0:
                # Verificar se arquivo foi criado e tem tamanho válido
                if os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 1024:
                    logger.info(f"Áudio mesclado criado com sucesso: {output_audio_path}")
                    return output_audio_path
                else:
                    logger.error("Arquivo de áudio criado mas está vazio ou muito pequeno")
                    return None
            else:
                logger.error(f"Erro no FFmpeg: {result.stderr}")
                return None
        
        except Exception as e:
            logger.error(f"Erro ao mesclar segmentos de áudio: {e}")
            return None
        
        finally:
            # Limpar arquivo de lista de concatenação
            try:
                if 'concat_list_path' in locals() and os.path.exists(concat_list_path):
                    os.remove(concat_list_path)
            except:
                pass
    
    async def _transcribe_audio_with_gemini(self, audio_file_path: str) -> Optional[str]:
        """
        Transcreve arquivo de áudio usando Gemini 2.0 Flash com diarização.
        
        Args:
            audio_file_path: Caminho do arquivo MP3
            
        Returns:
            Texto da transcrição ou None se falhou
        """
        try:
            logger.info(f"Iniciando upload do áudio para Gemini: {audio_file_path}")
            
            # Upload do arquivo para Gemini
            uploaded_file = self.client.files.upload(file=audio_file_path)
            logger.info(f"Arquivo carregado no Gemini: {uploaded_file.name}")
            
            # Aguardar arquivo ficar ACTIVE
            for attempt in range(30):  # 30 tentativas = ~1 minuto
                try:
                    file_info = self.client.files.get(name=uploaded_file.name)
                    if file_info.state == "ACTIVE":
                        logger.info("Arquivo está ACTIVE, iniciando transcrição")
                        break
                    elif file_info.state == "FAILED":
                        logger.error("Upload do arquivo falhou no Gemini")
                        return None
                except Exception as e:
                    logger.warning(f"Erro ao verificar status do arquivo (tentativa {attempt + 1}): {e}")
                
                await asyncio.sleep(2)  # Aguardar 2 segundos
            else:
                logger.error("Arquivo não ficou ACTIVE dentro do tempo limite")
                return None
            
            # Prompt para transcrição com diarização
            prompt = """Generate a detailed transcript of the meeting audio with speaker diarization in Portuguese. 
            Please:
            1. Identify different speakers in the audio and label them as "Falante 1:", "Falante 2:", etc.
            2. Separate each speaker's speech clearly with timestamps when possible
            3. Include natural conversation flow and pauses
            4. If you can determine characteristics about speakers (gender, tone, role), mention briefly
            5. Identify main topics discussed and key decisions made
            6. Format as a clean, readable transcript
            
            Format the output like this:
            [Timestamp if available] Falante 1: [texto da fala]
            [Timestamp if available] Falante 2: [texto da fala]
            
            After the transcript, add a brief summary section:
            
            === RESUMO DA REUNIÃO ===
            - Principais tópicos discutidos:
            - Decisões tomadas:
            - Próximos passos:
            
            Provide the complete transcript and summary in Portuguese."""
            
            # Gerar transcrição
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt, uploaded_file]
            )
            
            # Limpar arquivo do Gemini
            try:
                self.client.files.delete(name=uploaded_file.name)
                logger.info("Arquivo removido do Gemini")
            except Exception as e:
                logger.warning(f"Erro ao remover arquivo do Gemini: {e}")
            
            if response.text:
                logger.info("Transcrição gerada com sucesso")
                return response.text
            else:
                logger.error("Resposta vazia do Gemini")
                return None
        
        except Exception as e:
            logger.error(f"Erro na transcrição com Gemini: {e}")
            return None
    
    def validate_api_key(self) -> bool:
        """Valida se a API key do Gemini está funcionando"""
        try:
            # Teste simples com o cliente
            self.client.models.list()
            return True
        except Exception as e:
            logger.error(f"API key inválida ou erro de conexão: {e}")
            return False
