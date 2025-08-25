from google import genai
from google.genai import types
import os
import json
import logging
import uuid
from typing import Optional, Dict, List
from datetime import datetime
from app.feedback_models import (
    TranscriptionResult, ConversationContext, ConversationChunk, 
    FeedbackSession, Citacao, FeedbackRealTime
)

logger = logging.getLogger(__name__)


class FeedbackService:
    """Serviço para análise de feedback 1:1 com PNL e contexto acumulativo integrado com gravação do Teams"""
    
    def __init__(self, api_key: str):
        """Inicializa o cliente Gemini para feedback"""
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-2.5-pro"
        
        # Armazena contexto das sessões ativas
        self.active_sessions: Dict[str, FeedbackSession] = {}
        
    def create_feedback_session(self, session_id: str, teams_url: Optional[str] = None, recording_id: Optional[str] = None) -> FeedbackSession:
        """Cria uma nova sessão de feedback com contexto"""
        now = datetime.now()
        
        context = ConversationContext(
            session_id=session_id,
            chunks=[],
            contexto_acumulado="",
            padroes_identificados=[],
            evolucao_emocional=[],
            temas_recorrentes=[],
            created_at=now,
            last_updated=now
        )
        
        session = FeedbackSession(
            session_id=session_id,
            context=context,
            current_analysis=None,
            total_chunks=0,
            status="active",
            teams_url=teams_url,
            recording_id=recording_id
        )
        
        self.active_sessions[session_id] = session
        logger.info(f"Nova sessão de feedback PNL criada: {session_id}")
        
        return session
    
    def get_feedback_session(self, session_id: str) -> Optional[FeedbackSession]:
        """Recupera sessão de feedback existente"""
        return self.active_sessions.get(session_id)
    
    def end_feedback_session(self, session_id: str) -> bool:
        """Encerra sessão de feedback"""
        if session_id in self.active_sessions:
            self.active_sessions[session_id].status = "ended"
            del self.active_sessions[session_id]
            logger.info(f"Sessão de feedback encerrada: {session_id}")
            return True
        return False
    
    def pause_feedback_session(self, session_id: str) -> bool:
        """Pausa sessão de feedback"""
        if session_id in self.active_sessions:
            self.active_sessions[session_id].status = "paused"
            logger.info(f"Sessão de feedback pausada: {session_id}")
            return True
        return False
    
    def resume_feedback_session(self, session_id: str) -> bool:
        """Resume sessão de feedback"""
        if session_id in self.active_sessions:
            self.active_sessions[session_id].status = "active"
            logger.info(f"Sessão de feedback resumida: {session_id}")
            return True
        return False
    
    def _create_contextual_prompt(self, context: ConversationContext, chunk_number: int) -> str:
        """Cria prompt com contexto acumulado da conversa"""
        
        # Calcula tempo aproximado baseado nos chunks (30s cada)
        current_minute = (chunk_number * 30) // 60
        current_second = (chunk_number * 30) % 60
        tempo_atual = f"{current_minute:02d}:{current_second:02d}"
        
        base_prompt = f"""
        Você é um especialista em Programação Neurolinguística (PNL) analisando uma REUNIÃO DE FEEDBACK 1:1.
        
        **IMPORTANTE**: Esta é uma análise CONTEXTUAL. Você está recebendo um segmento de áudio da conversa em andamento (aproximadamente aos {tempo_atual} minutos).
        
        **CONTEXTO DA CONVERSA ATÉ AGORA:**
        {context.contexto_acumulado or "Início da conversa"}
        
        **PADRÕES PNL IDENTIFICADOS ANTERIORMENTE:**
        {", ".join(context.padroes_identificados) or "Nenhum padrão identificado ainda"}
        
        **EVOLUÇÃO EMOCIONAL OBSERVADA:**
        {", ".join(context.evolucao_emocional) or "Observando estados iniciais"}
        
        **TEMAS RECORRENTES:**
        {", ".join(context.temas_recorrentes) or "Aguardando temas recorrentes"}
        
        **INSTRUÇÃO PARA ESTE MOMENTO:**
        Analise este novo áudio considerando TODO O CONTEXTO ANTERIOR. 
        Identifique como este momento se conecta com o que foi discutido antes.
        Observe evoluções, padrões que se repetem ou se modificam.
        
        **IMPORTANTE**: Nas suas análises, ao referenciar momentos anteriores, use sempre referências temporais (ex: "aos 2 minutos", "no início da conversa", "há alguns minutos") NUNCA mencione "chunk".
        
        **ANÁLISE PNL CONTEXTUAL PARA FEEDBACK 1:1:**
        
        1. **resumo_global**: 
           - Como este momento se conecta com o contexto anterior
           - Progressão da conversa até agora
           - Principais insights acumulados
        
        2. **topicos_abertos**: 
           - Metas/objetivos que evoluíram ou se clarificaram
           - Novos pontos de desenvolvimento identificados
           - Ações que se tornaram mais específicas
        
        3. **citacoes**: Frases importantes DESTE chunk que:
           - Demonstram evolução em relação ao contexto anterior
           - Revelam novos padrões PNL
           - Mostram mudanças de perspectiva ou breakthrough
        
        4. **feedback_rt**: Observações PNL específicas DESTE momento:
           - **contexto**: Como se conecta com discussões anteriores
           - **evolucao**: Mudanças observadas em relação ao início
           - **rapport**: Qualidade atual da conexão
           - **linguagem**: Padrões VAK identificados neste momento
           - **estado**: Estados emocionais atuais vs anteriores
           - **ancoragem**: Novas ancoragens ou reforço de anteriores
           - **reframing**: Ressignificações que emergiram
           - **outcome**: Clareza crescente dos objetivos
           - **padrao**: Padrões comportamentais/linguísticos recorrentes
           - **sugestao_fala**: Sugestão específica de fala para o líder neste momento
        
        **FOQUE NA CONTINUIDADE**: Sempre considere como este momento constrói sobre o que veio antes.
        **REFERÊNCIAS TEMPORAIS**: Use "no início da conversa", "há alguns minutos", "aos X minutos" para se referir ao histórico.
        
        **IMPORTANTE PARA SUGESTÕES DE FALA**: 
        - Sempre inclua pelo menos UMA sugestão de fala específica e prática
        - A sugestão deve considerar o contexto atual e estado emocional identificado
        - Use linguagem PNL apropriada (VAK, reframing, rapport)
        - Seja específico e acionável para o líder
        
        **RETORNE APENAS O JSON no formato exato:**
        
        {{
          "resumo_global": "como este momento se conecta e evolui a partir do contexto anterior da sessão",
          "topicos_abertos": [
            "objetivo/meta que evoluiu ou se clarificou",
            "novo ponto identificado considerando o contexto"
          ],
          "citacoes": [
            {{"t": "MM:SS", "trecho": "frase importante DESTE momento que mostra evolução"}}
          ],
          "feedback_rt": [
            {{"tipo": "contexto", "mensagem": "como este momento se conecta com discussões anteriores", "id": "unique_id"}},
            {{"tipo": "evolucao", "mensagem": "mudança observada em relação ao início da sessão", "id": "unique_id"}},
            {{"tipo": "padrao", "mensagem": "padrão recorrente ou nova variação identificada", "id": "unique_id"}},
            {{"tipo": "sugestao_fala", "mensagem": "Sugestão específica: 'Frase exata sugerida para o líder dizer agora'", "id": "unique_id"}}
          ]
        }}
        """
        
        return base_prompt
    
    def _update_conversation_context(
        self, 
        context: ConversationContext, 
        new_analysis: TranscriptionResult,
        chunk_info: ConversationChunk
    ) -> ConversationContext:
        """Atualiza o contexto da conversa com nova análise"""
        
        # Adiciona o chunk atual
        context.chunks.append(chunk_info)
        
        # Calcula tempo aproximado deste momento
        total_chunks = len(context.chunks)
        current_minute = (total_chunks * 30) // 60
        current_second = (total_chunks * 30) % 60
        tempo_ref = f"{current_minute:02d}:{current_second:02d}"
        
        # Atualiza contexto acumulado
        if context.contexto_acumulado:
            context.contexto_acumulado += f" | Aos {tempo_ref}: {new_analysis.resumo_global}"
        else:
            context.contexto_acumulado = f"Início da conversa (00:00): {new_analysis.resumo_global}"
        
        # Extrai e acumula padrões PNL do feedback
        for feedback in new_analysis.feedback_rt:
            if feedback.tipo in ["rapport", "linguagem", "estado", "ancoragem", "padrao"]:
                pattern_info = f"{feedback.tipo}: {feedback.mensagem}"
                if pattern_info not in context.padroes_identificados:
                    context.padroes_identificados.append(pattern_info)
        
        # Extrai evolução emocional
        for feedback in new_analysis.feedback_rt:
            if feedback.tipo in ["estado", "evolucao"]:
                emotional_info = f"Aos {tempo_ref}: {feedback.mensagem}"
                context.evolucao_emocional.append(emotional_info)
        
        # Identifica temas recorrentes
        current_topics = set(new_analysis.topicos_abertos)
        for chunk in context.chunks[:-1]:  # Momentos anteriores
            if chunk.analise_parcial:
                previous_topics = set(chunk.analise_parcial.topicos_abertos)
                recurring = current_topics.intersection(previous_topics)
                for topic in recurring:
                    if topic not in context.temas_recorrentes:
                        context.temas_recorrentes.append(topic)
        
        # Mantém apenas os últimos 50 padrões para não sobrecarregar
        if len(context.padroes_identificados) > 50:
            context.padroes_identificados = context.padroes_identificados[-50:]
        
        if len(context.evolucao_emocional) > 30:
            context.evolucao_emocional = context.evolucao_emocional[-30:]
        
        # Atualiza timestamp
        context.last_updated = datetime.now()
        
        return context

    async def process_feedback_audio_with_context(
        self, 
        session_id: str,
        audio_bytes: bytes, 
        mime_type: str = "audio/webm"
    ) -> Optional[TranscriptionResult]:
        """
        Processa áudio de feedback 1:1 com contexto acumulativo
        
        Args:
            session_id: ID da sessão
            audio_bytes: Dados binários do áudio
            mime_type: Tipo MIME do áudio
            
        Returns:
            TranscriptionResult ou None em caso de erro
        """
        try:
            # Recupera ou cria sessão
            session = self.get_feedback_session(session_id)
            if not session:
                logger.warning(f"Sessão não encontrada: {session_id}")
                return None
            
            # Verifica se a sessão está ativa
            if session.status != "active":
                logger.warning(f"Sessão não está ativa: {session_id} (status: {session.status})")
                return None
            
            chunk_number = len(session.context.chunks) + 1
            
            # Calcula tempo aproximado
            current_minute = (chunk_number * 30) // 60
            current_second = (chunk_number * 30) % 60
            tempo_atual = f"{current_minute:02d}:{current_second:02d}"
            
            logger.info(f"Processando áudio da sessão {session_id} (tempo ~{tempo_atual}): {len(audio_bytes)} bytes")
            
            # Cria prompt contextual
            prompt = self._create_contextual_prompt(session.context, chunk_number)
            
            # Cria o conteúdo para o Gemini
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
                    ]
                )
            ]
            
            # Configuração para resposta JSON
            config = types.GenerateContentConfig(
                response_mime_type='application/json',
                thinking_config=types.ThinkingConfig(thinking_budget=-1)
            )
            
            # Chama o Gemini
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )
            
            logger.info(f"Resposta contextual de feedback PNL recebida (tempo ~{tempo_atual})")
            
            # Parse da resposta JSON
            try:
                result_dict = json.loads(response.text)
                new_analysis = TranscriptionResult(**result_dict)
                
                # Cria info do chunk atual
                chunk_info = ConversationChunk(
                    chunk_id=str(uuid.uuid4()),
                    timestamp=datetime.now(),
                    duration_seconds=30.0,  # Estimativa
                    analise_parcial=new_analysis
                )
                
                # Atualiza contexto da conversa
                session.context = self._update_conversation_context(
                    session.context, 
                    new_analysis, 
                    chunk_info
                )
                
                # Atualiza sessão
                session.current_analysis = new_analysis
                session.total_chunks = len(session.context.chunks)
                
                # Salva sessão atualizada
                self.active_sessions[session_id] = session
                
                logger.info(f"Contexto atualizado para sessão {session_id}: {session.total_chunks} segmentos, {len(session.context.padroes_identificados)} padrões")
                
                return new_analysis
                
            except json.JSONDecodeError as e:
                logger.error(f"Erro ao fazer parse do JSON de feedback contextual: {e}")
                logger.error(f"Resposta recebida: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Erro ao processar áudio contextual de feedback: {str(e)}")
            return None
    
    def get_conversation_summary(self, session_id: str) -> Optional[dict]:
        """Retorna resumo completo da conversa"""
        session = self.get_feedback_session(session_id)
        if not session:
            return None
            
        return {
            "session_id": session_id,
            "total_chunks": session.total_chunks,
            "status": session.status,
            "teams_url": session.teams_url,
            "recording_id": session.recording_id,
            "contexto_acumulado": session.context.contexto_acumulado,
            "padroes_identificados": session.context.padroes_identificados,
            "evolucao_emocional": session.context.evolucao_emocional,
            "temas_recorrentes": session.context.temas_recorrentes,
            "created_at": session.context.created_at.isoformat(),
            "last_updated": session.context.last_updated.isoformat()
        }
    
    def validate_audio_format(self, mime_type: str) -> bool:
        """Valida se o formato de áudio é suportado"""
        supported_formats = {
            "audio/webm",
            "audio/wav", 
            "audio/mp3",
            "audio/mpeg",
            "audio/mp4",
            "audio/aac",
            "audio/pcm"
        }
        
        # Verifica formato base (ignorando parâmetros como codecs)
        base_type = mime_type.split(';')[0].strip()
        return base_type in supported_formats
