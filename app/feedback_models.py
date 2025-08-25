from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class Citacao(BaseModel):
    """Modelo para citações importantes com timestamp"""
    t: str  # Timestamp no formato MM:SS
    trecho: str  # Trecho da citação


class FeedbackRealTime(BaseModel):
    """Modelo para feedback em tempo real"""
    tipo: str  # Tipo: "sugestao", "alerta", "insight", "sugestao_fala"
    mensagem: str  # Mensagem do feedback
    id: str  # ID único do feedback


class TranscriptionResult(BaseModel):
    """Modelo principal do resultado da transcrição"""
    resumo_global: str  # Resumo da discussão
    topicos_abertos: List[str]  # Lista de ações pendentes
    citacoes: List[Citacao]  # Trechos importantes
    feedback_rt: List[FeedbackRealTime]  # Sugestões em tempo real


class ConversationChunk(BaseModel):
    """Modelo para um chunk de conversa processado"""
    chunk_id: str
    timestamp: datetime
    duration_seconds: float
    analise_parcial: Optional[TranscriptionResult] = None


class ConversationContext(BaseModel):
    """Modelo para contexto acumulado da conversa"""
    session_id: str
    chunks: List[ConversationChunk]
    contexto_acumulado: str  # Resumo cumulativo da conversa
    padroes_identificados: List[str]  # Padrões PNL identificados
    evolucao_emocional: List[str]  # Evolução dos estados emocionais
    temas_recorrentes: List[str]  # Temas que se repetem
    created_at: datetime
    last_updated: datetime
    teams_url: Optional[str] = None  # URL da reunião do Teams
    recording_id: Optional[str] = None  # ID da gravação


class FeedbackSession(BaseModel):
    """Modelo para uma sessão de feedback completa"""
    session_id: str
    context: ConversationContext
    current_analysis: Optional[TranscriptionResult] = None
    total_chunks: int = 0
    status: str = "active"  # active, paused, ended
    teams_url: Optional[str] = None  # URL do Teams se for integrado
    recording_id: Optional[str] = None  # ID da gravação se estiver gravando


class SessionInfo(BaseModel):
    """Informações básicas da sessão"""
    session_id: str
    created_at: datetime
    status: str


class SSEEvent(BaseModel):
    """Evento SSE para streaming"""
    type: str
    message: Optional[str] = None
    data: Optional[dict] = None
    timestamp: float
