import asyncio
import logging
import json
import os
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import aiohttp
from google import genai
from app.supabase_persistence import get_persistence_service

logger = logging.getLogger(__name__)


class AttendeeIntegrationService:
    """Serviço para integração com Attendee API"""
    
    def __init__(self):
        self.api_key = os.getenv("ATTENDEE_API_KEY")
        self.base_url = "https://app.attendee.dev/api/v1"
        self.gemini_client = None
        
        if not self.api_key:
            raise ValueError("ATTENDEE_API_KEY não configurada")
        
        # Inicializar cliente Gemini para análise de feedback
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            self.gemini_client = genai.Client(api_key=gemini_key)
        
        logger.info("🤖 Serviço de integração Attendee inicializado")
    
    async def create_bot(
        self,
        meeting_url: str,
        bot_name: str,
        metadata: Dict[str, Any] = None
    ) -> Optional[Dict]:
        """
        Cria um bot no Attendee para gravar reunião.
        
        Args:
            meeting_url: URL da reunião (Teams, Zoom, etc.)
            bot_name: Nome do bot
            metadata: Metadados opcionais
        
        Returns:
            Dados do bot criado ou None se falhou
        """
        try:
            payload = {
                "meeting_url": meeting_url,
                "bot_name": bot_name,
                "transcription_settings": {
                    "deepgram": {
                        "language": "pt-BR",
                        "model": "nova-2"
                    }
                },
                "recording_settings": {
                    "format": "mp4",
                    "view": "speaker_view",
                    "resolution": "1080p"
                },
                "automatic_leave_settings": {
                    "wait_time_on_host_join_s": 120,
                    "silence_threshold_s": 300,
                    "alone_threshold_s": 120,
                    "max_meeting_length_s": 10800  # 3 horas
                }
            }
            
            if metadata:
                payload["metadata"] = metadata
            
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/bots",
                    json=payload,
                    headers=headers
                ) as response:
                    if response.status == 201:
                        bot_data = await response.json()
                        logger.info(f"✅ Bot criado no Attendee: {bot_data['id']}")
                        return bot_data
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Erro ao criar bot no Attendee: {response.status} - {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"❌ Exceção ao criar bot no Attendee: {e}")
            return None
    
    async def get_bot_status(self, bot_id: str) -> Optional[Dict]:
        """Obtém status atual do bot"""
        try:
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/bots/{bot_id}",
                    headers=headers
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"❌ Erro ao obter status do bot {bot_id}: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"❌ Exceção ao obter status do bot {bot_id}: {e}")
            return None
    
    async def get_transcript(self, bot_id: str, updated_after: str = None) -> Optional[List[Dict]]:
        """Obtém transcrição do bot"""
        try:
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.base_url}/bots/{bot_id}/transcript"
            if updated_after:
                url += f"?updated_after={updated_after}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"❌ Erro ao obter transcrição do bot {bot_id}: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"❌ Exceção ao obter transcrição do bot {bot_id}: {e}")
            return None
    
    async def get_recording_url(self, bot_id: str) -> Optional[Dict]:
        """Obtém URL de gravação do bot"""
        try:
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/bots/{bot_id}/recording",
                    headers=headers
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"❌ Erro ao obter URL de gravação do bot {bot_id}: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"❌ Exceção ao obter URL de gravação do bot {bot_id}: {e}")
            return None
    
    async def generate_feedback_analysis(self, transcript_data: List[Dict]) -> Optional[Dict]:
        """
        Gera análise de feedback usando Gemini baseada na transcrição.
        
        Args:
            transcript_data: Lista de objetos de transcrição do Attendee
        
        Returns:
            Análise de feedback ou None se falhou
        """
        if not self.gemini_client or not transcript_data:
            return None
        
        try:
            # Converter transcrição para texto estruturado
            transcript_text = self._format_transcript_for_analysis(transcript_data)
            
            if len(transcript_text.strip()) < 50:  # Muito pouco conteúdo
                return None
            
            prompt = f"""
Analise esta transcrição de reunião e forneça feedback estruturado em JSON:

TRANSCRIÇÃO:
{transcript_text}

Forneça uma análise no seguinte formato JSON:
{{
    "resumo_geral": "Resumo da discussão em 2-3 frases",
    "pontos_principais": ["Ponto 1", "Ponto 2", "..."],
    "acoes_pendentes": ["Ação 1", "Ação 2", "..."],
    "feedback_participacao": {{
        "distribuicao_fala": "Descrição de como foi a distribuição de fala",
        "engajamento": "Nível de engajamento observado",
        "dinamica": "Dinâmica da reunião"
    }},
    "insights_pnl": {{
        "tom_geral": "Tom predominante da reunião",
        "momentos_criticos": ["Momento 1", "Momento 2"],
        "oportunidades_melhoria": ["Oportunidade 1", "Oportunidade 2"]
    }},
    "metricas": {{
        "total_falantes": 0,
        "duracao_estimada_min": 0,
        "nivel_colaboracao": "Alto/Médio/Baixo"
    }}
}}

Responda APENAS com o JSON válido, sem texto adicional.
"""
            
            response = self.gemini_client.models.generate_content(
                model="gemini-1.5-pro",
                contents=[prompt]
            )
            
            # Tentar parsear o JSON da resposta
            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            feedback_analysis = json.loads(response_text.strip())
            
            # Adicionar timestamp da análise
            feedback_analysis["generated_at"] = datetime.now().isoformat()
            feedback_analysis["transcript_segments"] = len(transcript_data)
            
            logger.info(f"✅ Análise de feedback gerada com {len(transcript_data)} segmentos")
            return feedback_analysis
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Erro ao parsear JSON da análise de feedback: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Erro ao gerar análise de feedback: {e}")
            return None
    
    def _format_transcript_for_analysis(self, transcript_data: List[Dict]) -> str:
        """Formata dados de transcrição para análise"""
        formatted_lines = []
        
        for segment in transcript_data:
            speaker = segment.get("speaker_name", "Desconhecido")
            text = segment.get("transcription", {}).get("transcript", "")
            timestamp_ms = segment.get("timestamp_ms", 0)
            
            # Converter timestamp para formato legível
            timestamp_min = timestamp_ms // 60000
            timestamp_sec = (timestamp_ms % 60000) // 1000
            time_str = f"{timestamp_min:02d}:{timestamp_sec:02d}"
            
            if text.strip():
                formatted_lines.append(f"[{time_str}] {speaker}: {text.strip()}")
        
        return "\n".join(formatted_lines)


class AttendeeMonitoringService:
    """Serviço para monitoramento contínuo de bots do Attendee"""
    
    def __init__(self, attendee_service: AttendeeIntegrationService):
        self.attendee_service = attendee_service
        self.monitoring_tasks: Dict[str, asyncio.Task] = {}
        logger.info("📊 Serviço de monitoramento Attendee inicializado")
    
    async def start_monitoring(self, recording_session_id: str, bot_id: str):
        """Inicia monitoramento de um bot específico"""
        if recording_session_id in self.monitoring_tasks:
            logger.warning(f"⚠️ Monitoramento já ativo para recording_session {recording_session_id}")
            return
        
        logger.info(f"🔄 Iniciando monitoramento para bot {bot_id} (recording_session: {recording_session_id})")
        
        task = asyncio.create_task(
            self._monitor_bot_loop(recording_session_id, bot_id)
        )
        self.monitoring_tasks[recording_session_id] = task
    
    async def stop_monitoring(self, recording_session_id: str):
        """Para monitoramento de um bot específico"""
        if recording_session_id in self.monitoring_tasks:
            task = self.monitoring_tasks[recording_session_id]
            task.cancel()
            del self.monitoring_tasks[recording_session_id]
            logger.info(f"⏹️ Monitoramento parado para recording_session {recording_session_id}")
    
    async def _monitor_bot_loop(self, recording_session_id: str, bot_id: str):
        """Loop principal de monitoramento do bot"""
        check_interval = 30  # segundos
        last_transcript_check = None
        
        try:
            persistence = await get_persistence_service()
            
            while True:
                try:
                    # Obter status atual do bot
                    bot_status = await self.attendee_service.get_bot_status(bot_id)
                    if not bot_status:
                        logger.error(f"❌ Não foi possível obter status do bot {bot_id}")
                        await asyncio.sleep(check_interval)
                        continue
                    
                    # Atualizar status do bot no banco
                    await self._update_bot_status(persistence, recording_session_id, bot_status)
                    
                    # Verificar se precisa obter nova transcrição
                    current_time = datetime.now().isoformat()
                    transcript_data = await self.attendee_service.get_transcript(
                        bot_id, 
                        updated_after=last_transcript_check
                    )
                    
                    if transcript_data and len(transcript_data) > 0:
                        # Gerar análise de feedback
                        feedback_analysis = await self.attendee_service.generate_feedback_analysis(transcript_data)
                        
                        # Atualizar transcrição e feedback no banco
                        await self._update_transcript_and_feedback(
                            persistence, 
                            recording_session_id, 
                            transcript_data, 
                            feedback_analysis
                        )
                        
                        last_transcript_check = current_time
                        logger.info(f"📝 Transcrição atualizada para bot {bot_id}: {len(transcript_data)} novos segmentos")
                    
                    # Verificar se bot finalizou
                    bot_state = bot_status.get("state", "")
                    if bot_state in ["ended", "post_processing"]:
                        logger.info(f"🏁 Bot {bot_id} finalizou com estado: {bot_state}")
                        
                        # Obter URL de gravação se disponível
                        if bot_status.get("recording_state") == "complete":
                            recording_data = await self.attendee_service.get_recording_url(bot_id)
                            if recording_data:
                                await self._update_final_recording(
                                    persistence, 
                                    recording_session_id, 
                                    recording_data
                                )
                        
                        # Finalizar monitoramento
                        await self._finalize_monitoring(persistence, recording_session_id)
                        break
                    
                    # Aguardar próxima verificação
                    await asyncio.sleep(check_interval)
                    
                except asyncio.CancelledError:
                    logger.info(f"🛑 Monitoramento cancelado para bot {bot_id}")
                    break
                except Exception as e:
                    logger.error(f"❌ Erro no loop de monitoramento do bot {bot_id}: {e}")
                    await asyncio.sleep(check_interval)
                    
        except Exception as e:
            logger.error(f"❌ Erro fatal no monitoramento do bot {bot_id}: {e}")
        finally:
            # Limpar task do dicionário
            if recording_session_id in self.monitoring_tasks:
                del self.monitoring_tasks[recording_session_id]
    
    async def _update_bot_status(self, persistence, recording_session_id: str, bot_status: Dict):
        """Atualiza status do bot no banco"""
        try:
            await persistence.update_attendee_bot_status(
                recording_session_id=recording_session_id,
                bot_state=bot_status.get("state"),
                recording_state=bot_status.get("recording_state"),
                transcription_state=bot_status.get("transcription_state"),
                metadata=bot_status
            )
        except Exception as e:
            logger.error(f"❌ Erro ao atualizar status do bot: {e}")
    
    async def _update_transcript_and_feedback(
        self, 
        persistence, 
        recording_session_id: str, 
        transcript_data: List[Dict], 
        feedback_analysis: Dict
    ):
        """Atualiza transcrição e feedback no banco"""
        try:
            await persistence.update_transcript_and_feedback(
                recording_session_id=recording_session_id,
                transcript_data=transcript_data,
                feedback_analysis=feedback_analysis
            )
        except Exception as e:
            logger.error(f"❌ Erro ao atualizar transcrição e feedback: {e}")
    
    async def _update_final_recording(self, persistence, recording_session_id: str, recording_data: Dict):
        """Atualiza URL final de gravação"""
        try:
            await persistence.update_final_recording_url(
                recording_session_id=recording_session_id,
                recording_url=recording_data.get("url"),
                recording_metadata=recording_data
            )
        except Exception as e:
            logger.error(f"❌ Erro ao atualizar URL final de gravação: {e}")
    
    async def _finalize_monitoring(self, persistence, recording_session_id: str):
        """Finaliza monitoramento e atualiza status"""
        try:
            await persistence.finalize_attendee_recording(recording_session_id)
            logger.info(f"✅ Monitoramento finalizado para recording_session {recording_session_id}")
        except Exception as e:
            logger.error(f"❌ Erro ao finalizar monitoramento: {e}")


# Instâncias globais
attendee_service: Optional[AttendeeIntegrationService] = None
attendee_monitoring: Optional[AttendeeMonitoringService] = None


async def get_attendee_service() -> AttendeeIntegrationService:
    """Factory para serviço do Attendee"""
    global attendee_service
    if attendee_service is None:
        attendee_service = AttendeeIntegrationService()
    return attendee_service


async def get_attendee_monitoring() -> AttendeeMonitoringService:
    """Factory para monitoramento do Attendee"""
    global attendee_monitoring, attendee_service
    if attendee_monitoring is None:
        if attendee_service is None:
            attendee_service = AttendeeIntegrationService()
        attendee_monitoring = AttendeeMonitoringService(attendee_service)
    return attendee_monitoring


async def cleanup_attendee_services():
    """Cleanup dos serviços"""
    global attendee_monitoring
    if attendee_monitoring:
        # Parar todos os monitoramentos ativos
        for recording_session_id in list(attendee_monitoring.monitoring_tasks.keys()):
            await attendee_monitoring.stop_monitoring(recording_session_id)
