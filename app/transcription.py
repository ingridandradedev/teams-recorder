import os
import json
import asyncio
import logging
import time
import subprocess
from typing import List, Dict, Optional, AsyncGenerator
from pydantic import BaseModel
from google import genai
from google.genai import types

# Configurar logging
logger = logging.getLogger(__name__)

class TranscricaoSegmento(BaseModel):
    falante: str
    timestamp_inicial: str
    timestamp_final: str
    texto: str

class TranscricaoCompleta(BaseModel):
    segmentos: List[TranscricaoSegmento]

class TranscriptionManager:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não encontrada nas variáveis de ambiente")
        
        self.client = genai.Client(api_key=self.api_key)
        self.processed_segments = {}  # Para evitar processamento duplicado
    
    def convert_ts_to_mp4(self, ts_path: str) -> str:
        """
        Converte arquivo .ts para .mp4 para compatibilidade com Gemini.
        Retorna o caminho do arquivo .mp4 convertido.
        """
        # Gerar nome do arquivo .mp4
        mp4_path = ts_path.replace('.ts', '_converted.mp4')
        
        try:
            logger.info(f"🔄 Convertendo {os.path.basename(ts_path)} para MP4...")
            
            # Comando FFmpeg para conversão rápida
            comando = [
                'ffmpeg',
                '-y',  # Sobrescrever arquivo se existir
                '-i', ts_path,  # Arquivo de entrada .ts
                '-c', 'copy',  # Copiar streams sem re-encoding (mais rápido)
                '-f', 'mp4',  # Formato de saída
                mp4_path  # Arquivo de saída
            ]
            
            # Executar conversão
            result = subprocess.run(
                comando,
                capture_output=True,
                text=True,
                timeout=30  # Timeout de 30 segundos
            )
            
            if result.returncode == 0:
                logger.info(f"✅ Conversão concluída: {os.path.basename(mp4_path)} ({os.path.getsize(mp4_path)} bytes)")
                return mp4_path
            else:
                logger.error(f"❌ Erro na conversão FFmpeg: {result.stderr}")
                raise RuntimeError(f"FFmpeg falhou: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error(f"⏰ Timeout na conversão do arquivo {ts_path}")
            raise RuntimeError("Timeout na conversão de vídeo")
        except Exception as e:
            logger.error(f"❌ Erro na conversão {ts_path}: {e}")
            raise
        
    async def upload_video_segment(self, video_path: str) -> Optional[str]:
        """
        Faz upload de um segmento de vídeo para o Gemini.
        Converte .ts para .mp4 se necessário para compatibilidade.
        Retorna o URI do arquivo ou None em caso de erro.
        """
        try:
            # Verificar se precisa converter .ts para .mp4
            if video_path.endswith('.ts'):
                logger.info(f"🔄 Arquivo .ts detectado, convertendo para MP4...")
                mp4_path = self.convert_ts_to_mp4(video_path)
                upload_path = mp4_path
                mime_type = 'video/mp4'
            else:
                upload_path = video_path
                # Determinar mime_type baseado na extensão
                if video_path.endswith('.mp4'):
                    mime_type = 'video/mp4'
                elif video_path.endswith('.webm'):
                    mime_type = 'video/webm'
                elif video_path.endswith('.mpeg') or video_path.endswith('.mpg'):
                    mime_type = 'video/mpeg'
                else:
                    mime_type = 'video/mp4'  # Default
            
            logger.info(f"📤 Iniciando upload para Gemini: {os.path.basename(upload_path)} ({os.path.getsize(upload_path)} bytes, {mime_type})")
            
            # Upload do arquivo de vídeo usando a API assíncrona correta
            video_file = await self.client.aio.files.upload(
                file=upload_path,
                config=types.UploadFileConfig(
                    display_name=f"video_segment_{os.path.basename(upload_path)}",
                    mime_type=mime_type
                )
            )
            logger.info(f"✅ Segmento enviado para Gemini: {os.path.basename(upload_path)} -> {video_file.name}")
            
            # Limpar arquivo .mp4 temporário se foi convertido
            if upload_path != video_path and os.path.exists(upload_path):
                try:
                    os.remove(upload_path)
                    logger.info(f"🗑️ Arquivo temporário MP4 removido: {os.path.basename(upload_path)}")
                except:
                    pass  # Ignorar erros de limpeza
            
            return video_file.name
            
        except Exception as e:
            logger.error(f"❌ Erro ao enviar segmento para Gemini: {e}")
            return None
    
    async def transcribe_segment(self, video_file_name: str, segment_number: int) -> Optional[Dict]:
        """
        Transcreve um segmento de vídeo usando Gemini 2.5 Pro.
        Retorna a transcrição estruturada ou None em caso de erro.
        """
        try:
            logger.info(f"🤖 Iniciando transcrição com Gemini para segmento #{segment_number}")
            
            # Prompt otimizado para transcrição contínua
            prompt = f"""
            Transcreva este segmento de vídeo (segmento #{segment_number}) identificando:
            
            1. FALANTES: Use o nome real se visível na tela ou mencionado. Se não identificável, use "Participante 1", "Participante 2", etc.
            2. TIMESTAMPS: Formato HH:MM:SS (baseado no tempo do segmento)
            3. TEXTO: Transcrição exata do que foi falado
            
            IMPORTANTE:
            - Mantenha consistência nos nomes dos falantes entre segmentos
            - Se for continuação de uma fala anterior, indique isso
            - Ignore ruídos de fundo e sons técnicos
            - Retorne apenas falas humanas relevantes
            
            Retorne no formato JSON estruturado.
            """
            
            logger.info(f"📡 Enviando solicitação para Gemini 2.5 Pro...")
            
            response = await self.client.aio.models.generate_content(
                model='gemini-2.5-pro',
                contents=[
                    types.Part.from_uri(
                        file_uri=video_file_name,
                        mime_type='video/mp4'
                    ),
                    prompt
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TranscricaoCompleta
                )
            )
            
            # Processar resposta
            transcricao_data = json.loads(response.text)
            logger.info(f"✅ Transcrição do segmento #{segment_number} concluída: {len(transcricao_data.get('segmentos', []))} falas encontradas")
            
            # Limpar arquivo após processamento
            try:
                await self.client.aio.files.delete(name=video_file_name)
                logger.info(f"🗑️ Arquivo temporário removido do Gemini: {video_file_name}")
            except:
                pass  # Ignorar erros de limpeza
                
            return transcricao_data
            
        except Exception as e:
            logger.error(f"❌ Erro na transcrição do segmento #{segment_number}: {e}")
            return None
    
    async def process_segment_for_transcription(self, segment_path: str, segment_number: int) -> AsyncGenerator[Dict, None]:
        """
        Processa um segmento completo: upload + transcrição + yield dos resultados.
        """
        try:
            logger.info(f"🎬 Iniciando processamento de segmento: {os.path.basename(segment_path)}")
            
            # Verificar se arquivo existe
            if not os.path.exists(segment_path):
                logger.warning(f"⚠️ Segmento não encontrado: {segment_path}")
                yield {
                    "event": "segment_file_not_found",
                    "segment": segment_number,
                    "file": os.path.basename(segment_path)
                }
                return
            
            # Verificar tamanho do arquivo
            file_size = os.path.getsize(segment_path)
            logger.info(f"📏 Tamanho do segmento: {file_size} bytes")
            
            if file_size < 1024:  # Menor que 1KB
                logger.warning(f"⚠️ Arquivo muito pequeno, ignorando: {os.path.basename(segment_path)}")
                yield {
                    "event": "segment_too_small",
                    "segment": segment_number,
                    "file": os.path.basename(segment_path),
                    "size": file_size
                }
                return
            
            # Evitar processamento duplicado
            if segment_path in self.processed_segments:
                logger.info(f"🔄 Segmento já processado: {os.path.basename(segment_path)}")
                return
            
            self.processed_segments[segment_path] = True
            
            yield {
                "event": "segment_upload_start",
                "segment": segment_number,
                "file": os.path.basename(segment_path),
                "size": file_size
            }
            
            # Upload do segmento
            video_file_name = await self.upload_video_segment(segment_path)
            if not video_file_name:
                yield {
                    "event": "segment_upload_error",
                    "segment": segment_number,
                    "error": "Falha no upload para Gemini"
                }
                return
            
            yield {
                "event": "segment_upload_complete",
                "segment": segment_number,
                "file": os.path.basename(segment_path),
                "gemini_file": video_file_name
            }
            
            yield {
                "event": "transcription_start",
                "segment": segment_number
            }
            
            # Transcrição
            transcricao = await self.transcribe_segment(video_file_name, segment_number)
            if not transcricao:
                yield {
                    "event": "transcription_error",
                    "segment": segment_number,
                    "error": "Falha na transcrição"
                }
                return
            
            # Yield dos resultados de transcrição
            segmentos_encontrados = transcricao.get('segmentos', [])
            logger.info(f"📝 Processando {len(segmentos_encontrados)} falas do segmento #{segment_number}")
            
            for idx, segmento in enumerate(segmentos_encontrados):
                yield {
                    "event": "transcription_segment",
                    "segment_number": segment_number,
                    "segment_index": idx,
                    "falante": segmento.get('falante', 'Desconhecido'),
                    "timestamp_inicial": segmento.get('timestamp_inicial', '00:00:00'),
                    "timestamp_final": segmento.get('timestamp_final', '00:00:00'),
                    "texto": segmento.get('texto', '')
                }
            
            yield {
                "event": "transcription_complete",
                "segment": segment_number,
                "total_falas": len(segmentos_encontrados)
            }
            
        except Exception as e:
            logger.error(f"❌ Erro no processamento do segmento {segment_number}: {e}")
            yield {
                "event": "segment_error",
                "segment": segment_number,
                "error": str(e)
            }

class TranscriptionTracker:
    """
    Gerencia o estado de transcrições ativas.
    """
    def __init__(self):
        self.active_transcriptions: Dict[str, Dict] = {}
        self.transcription_results: Dict[str, List[Dict]] = {}
    
    def start_transcription(self, recording_id: str, metadata: Dict):
        """Inicia tracking de uma nova transcrição."""
        self.active_transcriptions[recording_id] = {
            "status": "active",
            "started_at": time.time(),
            "metadata": metadata,
            "total_segments": 0,
            "processed_segments": 0
        }
        self.transcription_results[recording_id] = []
    
    def add_transcription_result(self, recording_id: str, result: Dict):
        """Adiciona um resultado de transcrição."""
        if recording_id not in self.transcription_results:
            self.transcription_results[recording_id] = []
        self.transcription_results[recording_id].append(result)
    
    def update_segment_progress(self, recording_id: str, processed: int, total: int):
        """Atualiza progresso de segmentos."""
        if recording_id in self.active_transcriptions:
            self.active_transcriptions[recording_id]["processed_segments"] = processed
            self.active_transcriptions[recording_id]["total_segments"] = total
    
    def finish_transcription(self, recording_id: str):
        """Marca transcrição como finalizada."""
        if recording_id in self.active_transcriptions:
            self.active_transcriptions[recording_id]["status"] = "completed"
            self.active_transcriptions[recording_id]["finished_at"] = time.time()
    
    def get_transcription_status(self, recording_id: str) -> Optional[Dict]:
        """Retorna status de uma transcrição."""
        return self.active_transcriptions.get(recording_id)
    
    def get_transcription_results(self, recording_id: str) -> List[Dict]:
        """Retorna resultados de transcrição."""
        return self.transcription_results.get(recording_id, [])

# Instância global para gerenciar transcrições
transcription_tracker = TranscriptionTracker()
