import os
import json
import asyncio
import logging
import time
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
        
    async def upload_video_segment(self, video_path: str) -> Optional[str]:
        """
        Faz upload de um segmento de vídeo para o Gemini.
        Retorna o URI do arquivo ou None em caso de erro.
        """
        try:
            # Upload do arquivo de vídeo
            video_file = self.client.files.upload(file=video_path)
            logger.info(f"✅ Segmento enviado para Gemini: {os.path.basename(video_path)}")
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
            
            response = self.client.models.generate_content(
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
            logger.info(f"✅ Transcrição do segmento #{segment_number} concluída")
            
            # Limpar arquivo após processamento
            try:
                self.client.files.delete(name=video_file_name)
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
            # Verificar se arquivo existe
            if not os.path.exists(segment_path):
                logger.warning(f"⚠️ Segmento não encontrado: {segment_path}")
                return
            
            # Evitar processamento duplicado
            if segment_path in self.processed_segments:
                logger.info(f"🔄 Segmento já processado: {os.path.basename(segment_path)}")
                return
            
            self.processed_segments[segment_path] = True
            
            yield {
                "event": "segment_upload_start",
                "segment": segment_number,
                "file": os.path.basename(segment_path)
            }
            
            # Upload do segmento
            video_file_name = await self.upload_video_segment(segment_path)
            if not video_file_name:
                yield {
                    "event": "segment_upload_error",
                    "segment": segment_number,
                    "error": "Falha no upload"
                }
                return
            
            yield {
                "event": "segment_upload_complete",
                "segment": segment_number,
                "file": os.path.basename(segment_path)
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
            for segmento in transcricao.get('segmentos', []):
                yield {
                    "event": "transcription_segment",
                    "segment_number": segment_number,
                    "falante": segmento.get('falante', 'Desconhecido'),
                    "timestamp_inicial": segmento.get('timestamp_inicial', '00:00:00'),
                    "timestamp_final": segmento.get('timestamp_final', '00:00:00'),
                    "texto": segmento.get('texto', '')
                }
            
            yield {
                "event": "transcription_complete",
                "segment": segment_number,
                "total_falas": len(transcricao.get('segmentos', []))
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
