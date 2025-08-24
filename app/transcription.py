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
    def __init__(self, recording_id: str = None):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não encontrada nas variáveis de ambiente")
        
        self.client = genai.Client(api_key=self.api_key)
        self.processed_segments = {}  # Para evitar processamento duplicado
        self.recording_id = recording_id  # Para rastreamento no tracker
    
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
        
    async def upload_video_segment(self, video_path: str) -> Optional[types.File]:
        """
        Faz upload de um segmento de vídeo para o Gemini.
        Converte .ts para .mp4 se necessário para compatibilidade.
        Retorna o objeto File do Gemini ou None em caso de erro.
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
            
            return video_file  # Retornar o objeto File completo
            
        except Exception as e:
            logger.error(f"❌ Erro ao enviar segmento para Gemini: {e}")
            return None
    
    async def wait_for_file_active(self, file: types.File, max_wait_time: int = 60) -> bool:
        """
        Aguarda o arquivo ficar no estado ACTIVE.
        Retorna True se o arquivo ficou ativo, False se timeout ou erro.
        """
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            try:
                # Verificar o estado atual do arquivo
                file_info = await self.client.aio.files.get(name=file.name)
                
                logger.info(f"📋 Estado do arquivo {file.name}: {file_info.state}")
                
                if file_info.state == types.FileState.ACTIVE:
                    logger.info(f"✅ Arquivo {file.name} está ATIVO e pronto para uso")
                    return True
                elif file_info.state == types.FileState.FAILED:
                    logger.error(f"❌ Arquivo {file.name} falhou no processamento")
                    return False
                
                # Aguardar antes da próxima verificação
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"❌ Erro ao verificar estado do arquivo: {e}")
                await asyncio.sleep(2)
        
        logger.error(f"⏰ Timeout aguardando arquivo {file.name} ficar ativo")
        return False
    
    async def transcribe_segment(self, video_file: types.File, segment_number: int) -> Optional[Dict]:
        """
        Transcreve um segmento de vídeo usando Gemini 2.5 Pro.
        Recebe o objeto File do Gemini diretamente.
        Retorna a transcrição estruturada ou None em caso de erro.
        """
        try:
            logger.info(f"🤖 Iniciando transcrição com Gemini para segmento #{segment_number}")
            
            # AGUARDAR O ARQUIVO FICAR ATIVO
            logger.info(f"⏳ Aguardando arquivo ficar ativo: {video_file.name}")
            if not await self.wait_for_file_active(video_file):
                logger.error(f"❌ Arquivo {video_file.name} não ficou ativo a tempo")
                return None
            
            # Prompt otimizado para transcrição contínua
            prompt = f"""
            Transcreva este segmento de vídeo (segmento #{segment_number}) identificando falas humanas.
            
            INSTRUÇÕES:
            1. FALANTES: Use o nome real se visível na tela ou mencionado. Se não identificável, use "Participante 1", "Participante 2", etc.
            2. TIMESTAMPS: Formato HH:MM:SS (baseado no tempo do segmento)
            3. TEXTO: Transcrição exata do que foi falado
            4. Mantenha consistência nos nomes dos falantes entre segmentos
            5. Ignore ruídos de fundo e sons técnicos
            
            FORMATO JSON OBRIGATÓRIO (sempre retorne este formato, mesmo se não houver falas):
            {{
                "segmentos": [
                    {{
                        "falante": "Nome do falante",
                        "timestamp_inicial": "00:00:00",
                        "timestamp_final": "00:00:05",
                        "texto": "Texto transcrito exato"
                    }}
                ]
            }}
            
            Se NÃO houver falas humanas identificáveis, retorne:
            {{
                "segmentos": []
            }}
            """
            
            logger.info(f"📡 Enviando solicitação para Gemini 2.5 Pro...")
            
            response = await self.client.aio.models.generate_content(
                model='gemini-2.5-pro',
                contents=[
                    video_file,  # Usar o objeto File diretamente
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
                await self.client.aio.files.delete(name=video_file.name)
                logger.info(f"🗑️ Arquivo temporário removido do Gemini: {video_file.name}")
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
            video_file = await self.upload_video_segment(segment_path)
            if not video_file:
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
                "gemini_file": video_file.name  # Usar .name para logging
            }
            
            yield {
                "event": "transcription_start",
                "segment": segment_number
            }
            
            yield {
                "event": "waiting_file_active",
                "segment": segment_number,
                "message": "Aguardando arquivo ficar ativo no Gemini"
            }
            
            # Transcrição
            transcricao = await self.transcribe_segment(video_file, segment_number)  # Passar o objeto File
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
                result = {
                    "event": "transcription_segment",
                    "segment_number": segment_number,
                    "segment_index": idx,
                    "falante": segmento.get('falante', 'Desconhecido'),
                    "timestamp_inicial": segmento.get('timestamp_inicial', '00:00:00'),
                    "timestamp_final": segmento.get('timestamp_final', '00:00:00'),
                    "texto": segmento.get('texto', '')
                }
                
                # Armazena no tracker para stream limpo
                if hasattr(self, 'recording_id') and self.recording_id:
                    transcription_tracker.add_transcription_result(self.recording_id, result)
                
                yield result
            
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
    
    async def stream_clean_transcription_data(self, recording_id: str):
        """
        Stream limpo de dados de transcrição - apenas os resultados do Gemini.
        Retorna somente os objetos JSON gerados pelo Gemini, sem eventos intermediários.
        """
        if recording_id not in self.active_transcriptions:
            yield {
                "event": "error",
                "message": f"Transcrição {recording_id} não encontrada"
            }
            return
        
        # Contador de resultados já enviados
        sent_count = 0
        
        while True:
            current_results = self.transcription_results.get(recording_id, [])
            transcription_status = self.active_transcriptions.get(recording_id, {})
            
            # Envia novos resultados se houver
            if len(current_results) > sent_count:
                for result in current_results[sent_count:]:
                    # Apenas envia resultados de transcrição (segmentos do Gemini)
                    if result.get("event") == "transcription_segment":
                        yield {
                            "event": "gemini_transcription",
                            "segment_number": result.get("segment_number"),
                            "segment_index": result.get("segment_index"),
                            "falante": result.get("falante"),
                            "timestamp_inicial": result.get("timestamp_inicial"),
                            "timestamp_final": result.get("timestamp_final"),
                            "texto": result.get("texto"),
                            "timestamp": time.time()
                        }
                
                sent_count = len(current_results)
            
            # Verifica se a transcrição foi finalizada
            if transcription_status.get("status") == "completed":
                yield {
                    "event": "transcription_finished",
                    "recording_id": recording_id,
                    "total_segments": sent_count,
                    "timestamp": time.time()
                }
                break
            
            # Aguarda um pouco antes de verificar novamente
            await asyncio.sleep(0.5)

# Instância global para gerenciar transcrições
transcription_tracker = TranscriptionTracker()
