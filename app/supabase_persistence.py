import asyncio
import logging
import os
import json
from typing import Optional, Dict, Any
from datetime import datetime
import asyncpg
from app.feedback_models import ConversationContext, FeedbackSession

logger = logging.getLogger(__name__)


class SupabasePersistenceService:
    """Serviço para persistência de sessões de feedback PNL no Supabase"""
    
    def __init__(self):
        # Construir connection string a partir de variáveis individuais
        self.host = os.getenv("SUPABASE_DB_HOST")
        self.port = os.getenv("SUPABASE_DB_PORT", "6543")
        self.database = os.getenv("SUPABASE_DB_NAME", "postgres")
        self.user = os.getenv("SUPABASE_DB_USER")
        self.password = os.getenv("SUPABASE_DB_PASSWORD")
        
        if not all([self.host, self.user, self.password]):
            raise ValueError(
                "Variáveis de ambiente do Supabase não configuradas: "
                "SUPABASE_DB_HOST, SUPABASE_DB_USER, SUPABASE_DB_PASSWORD são obrigatórias"
            )
        
        # Construir URL de conexão
        self.database_url = f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"
        
        self.pool = None
        logger.info("🗄️ Serviço de persistência Supabase inicializado")
    
    async def initialize_pool(self):
        """Inicializa o pool de conexões do banco"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
                command_timeout=30,
                statement_cache_size=0  # Disable prepared statements for pgbouncer compatibility
            )
            logger.info("✅ Pool de conexões Supabase criado com sucesso")
        except Exception as e:
            logger.error(f"❌ Erro ao criar pool de conexões Supabase: {e}")
            raise
    
    async def close_pool(self):
        """Fecha o pool de conexões"""
        if self.pool:
            await self.pool.close()
            logger.info("🔒 Pool de conexões Supabase fechado")
    
    async def create_recording_session(
        self,
        meeting_session_id: str,
        session_id: str,
        teams_url: str,
        segment_time: int = 60,
        record_video: bool = True,
        upload_dest: str = "recordings-segments"
    ) -> Optional[str]:
        """
        Cria uma nova sessão de gravação no banco.
        
        Returns:
            ID da recording_session criada ou None se falhou
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                # Verificar se já existe uma sessão ativa para este meeting_session_id
                existing = await conn.fetchrow(
                    """
                    SELECT id, session_id, status 
                    FROM recording_sessions 
                    WHERE meeting_session_id = $1 AND status IN ('active', 'paused')
                    ORDER BY created_at DESC 
                    LIMIT 1
                    """,
                    meeting_session_id
                )
                
                if existing:
                    logger.warning(f"⚠️ Sessão ativa já existe para meeting_session_id {meeting_session_id}: {existing['session_id']}")
                    return str(existing['id'])
                
                # Criar nova sessão
                recording_id = await conn.fetchval(
                    """
                    INSERT INTO recording_sessions (
                        meeting_session_id, session_id, teams_url, 
                        source_type, segment_time, record_video, upload_dest,
                        status, feedback_context, recording_metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                    """,
                    meeting_session_id, session_id, teams_url,
                    'live_recording', segment_time, record_video, upload_dest,
                    'active', '{}', '{}'
                )
                
                logger.info(f"✅ Sessão de gravação criada: {recording_id} (session_id: {session_id})")
                return str(recording_id)
                
        except Exception as e:
            logger.error(f"❌ Erro ao criar sessão de gravação: {e}")
            return None
    
    async def update_feedback_context(
        self,
        recording_session_id: str,
        analysis_data: Dict[str, Any]
    ) -> bool:
        """
        Atualiza o contexto de feedback PNL na sessão.
        
        Args:
            recording_session_id: ID da recording_session no banco
            analysis_data: Dados da análise de feedback
        
        Returns:
            True se atualizou com sucesso, False caso contrário
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            logger.info(f"🔄 Tentando atualizar contexto de feedback para recording_session_id: {recording_session_id}")
            logger.info(f"📊 Dados da análise (tamanho): {len(json.dumps(analysis_data))} characters")
            
            async with self.pool.acquire() as conn:
                # Primeiro verificar se a sessão existe
                session_check = await conn.fetchrow(
                    "SELECT id, session_id FROM recording_sessions WHERE id = $1",
                    recording_session_id
                )
                
                if not session_check:
                    logger.error(f"❌ Recording session não encontrada: {recording_session_id}")
                    return False
                
                logger.info(f"✅ Recording session encontrada: {session_check['session_id']}")
                
                result = await conn.execute(
                    """
                    UPDATE recording_sessions 
                    SET 
                        feedback_context = $1,
                        total_chunks = COALESCE(total_chunks, 0) + 1,
                        updated_at = now()
                    WHERE id = $2
                    """,
                    json.dumps(analysis_data),
                    recording_session_id
                )
                
                if result == "UPDATE 1":
                    logger.info(f"✅ Contexto de feedback atualizado para recording_session {recording_session_id}")
                    logger.info(f"📈 Total chunks incrementado para sessão {session_check['session_id']}")
                    return True
                else:
                    logger.warning(f"⚠️ Update não afetou nenhuma linha para recording_session: {recording_session_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Erro ao atualizar contexto de feedback: {e}")
            import traceback
            logger.error(f"❌ Stack trace: {traceback.format_exc()}")
            return False
    
    async def update_session_status(
        self,
        session_id: str,
        status: str,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Atualiza o status da sessão de gravação.
        
        Args:
            session_id: ID da sessão
            status: Novo status ('active', 'paused', 'completed', 'failed', 'cancelled')
            error_message: Mensagem de erro se aplicável
        
        Returns:
            True se atualizou com sucesso
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                # Se estiver finalizando a sessão, definir ended_at
                if status in ['completed', 'failed', 'cancelled']:
                    result = await conn.execute(
                        """
                        UPDATE recording_sessions 
                        SET status = $1, error_message = $2, ended_at = now(), updated_at = now()
                        WHERE session_id = $3
                        """,
                        status, error_message, session_id
                    )
                else:
                    result = await conn.execute(
                        """
                        UPDATE recording_sessions 
                        SET status = $1, error_message = $2, updated_at = now()
                        WHERE session_id = $3
                        """,
                        status, error_message, session_id
                    )
                
                if result == "UPDATE 1":
                    logger.info(f"✅ Status da sessão {session_id} atualizado para: {status}")
                    return True
                else:
                    logger.warning(f"⚠️ Sessão não encontrada para atualizar status: {session_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Erro ao atualizar status da sessão: {e}")
            return False
    
    async def save_final_recording(
        self,
        session_id: str,
        recording_url: str,
        recording_metadata: Dict[str, Any]
    ) -> bool:
        """
        Salva informações do arquivo final de gravação.
        
        Args:
            session_id: ID da sessão
            recording_url: URL final da gravação
            recording_metadata: Metadados do arquivo (tamanho, duração, etc.)
        
        Returns:
            True se salvou com sucesso
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                # Atualizar sessão com URL final
                await conn.execute(
                    """
                    UPDATE recording_sessions 
                    SET 
                        final_recording_url = $1,
                        recording_metadata = $2,
                        updated_at = now()
                    WHERE session_id = $3
                    """,
                    recording_url,
                    json.dumps(recording_metadata),
                    session_id
                )
                
                # Buscar meeting_session_id para criar entrada em meeting_media
                meeting_session_id = await conn.fetchval(
                    "SELECT meeting_session_id FROM recording_sessions WHERE session_id = $1",
                    session_id
                )
                
                if meeting_session_id:
                    # Determinar tipo de arquivo e metadados
                    file_type = 'video' if recording_metadata.get('has_video', True) else 'audio'
                    file_size = recording_metadata.get('file_size_bytes')
                    duration = recording_metadata.get('duration_seconds')
                    filename = recording_metadata.get('filename')
                    gcs_uri = recording_metadata.get('gcs_uri', recording_url)
                    
                    # Inserir em meeting_media
                    media_id = await conn.fetchval(
                        """
                        INSERT INTO meeting_media (
                            session_id, filename, file_type, file_size_bytes,
                            duration_seconds, gcs_uri, public_url
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (session_id, gcs_uri) DO UPDATE SET
                            public_url = EXCLUDED.public_url,
                            file_size_bytes = EXCLUDED.file_size_bytes,
                            duration_seconds = EXCLUDED.duration_seconds
                        RETURNING id
                        """,
                        meeting_session_id, filename, file_type, file_size,
                        duration, gcs_uri, recording_url
                    )
                    
                    logger.info(f"✅ Arquivo final salvo para sessão {session_id}: {recording_url}")
                    logger.info(f"✅ Entrada em meeting_media criada: {media_id}")
                    return True
                else:
                    logger.error(f"❌ meeting_session_id não encontrado para sessão {session_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Erro ao salvar gravação final: {e}")
            return False
    
    async def get_recording_session_by_session_id(self, session_id: str) -> Optional[Dict]:
        """
        Busca uma sessão de gravação pelo session_id.
        
        Args:
            session_id: ID da sessão
        
        Returns:
            Dados da sessão ou None se não encontrada
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM recording_sessions 
                    WHERE session_id = $1
                    ORDER BY created_at DESC 
                    LIMIT 1
                    """,
                    session_id
                )
                
                if row:
                    return dict(row)
                return None
                
        except Exception as e:
            logger.error(f"❌ Erro ao buscar sessão por session_id {session_id}: {e}")
            return None

    async def get_recordings_by_meeting_session(self, meeting_session_id: str) -> list:
        """
        Busca todas as gravações de uma reunião específica.
        
        Args:
            meeting_session_id: ID da reunião
        
        Returns:
            Lista de gravações
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM recording_sessions 
                    WHERE meeting_session_id = $1
                    ORDER BY created_at DESC
                    """,
                    meeting_session_id
                )
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Erro ao buscar gravações da reunião {meeting_session_id}: {e}")
            return []

    async def update_recording_session_status(self, recording_session_id: str, status: str) -> bool:
        """
        Atualiza o status de uma sessão de gravação pelo recording_session_id.
        
        Args:
            recording_session_id: ID da recording_session no banco
            status: Novo status
        
        Returns:
            True se atualizou com sucesso
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE recording_sessions 
                    SET status = $1, updated_at = now()
                    WHERE id = $2
                    """,
                    status, recording_session_id
                )
                
                if result == "UPDATE 1":
                    logger.info(f"✅ Status atualizado para {status} na sessão {recording_session_id}")
                    return True
                else:
                    logger.warning(f"⚠️ Nenhuma sessão encontrada para atualizar: {recording_session_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Erro ao atualizar status da sessão: {e}")
            return False

    async def save_final_recording(
        self, 
        recording_session_id: str, 
        recording_url: str, 
        public_url: str = None,
        total_segments: int = 0
    ) -> bool:
        """
        Salva as URLs da gravação final.
        
        Args:
            recording_session_id: ID da recording_session
            recording_url: URL da gravação (gs://)
            public_url: URL pública da gravação
            total_segments: Total de segmentos processados
        
        Returns:
            True se salvou com sucesso
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE recording_sessions 
                    SET 
                        recording_url = $1,
                        public_url = $2,
                        total_segments = $3,
                        status = 'completed',
                        ended_at = now(),
                        updated_at = now()
                    WHERE id = $4
                    """,
                    recording_url, public_url or recording_url, total_segments, recording_session_id
                )
                
                if result == "UPDATE 1":
                    logger.info(f"✅ Gravação final salva para sessão {recording_session_id}: {recording_url}")
                    return True
                else:
                    logger.warning(f"⚠️ Nenhuma sessão encontrada para salvar gravação: {recording_session_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Erro ao salvar gravação final: {e}")
            return False

    async def get_recording_session(self, session_id: str) -> Optional[Dict]:
        """
        Recupera informações de uma sessão de gravação.
        
        Args:
            session_id: ID da sessão
        
        Returns:
            Dicionário com dados da sessão ou None se não encontrada
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM recording_sessions 
                    WHERE session_id = $1
                    """,
                    session_id
                )
                
                if row:
                    return dict(row)
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Erro ao buscar sessão de gravação: {e}")
            return None
    
    async def get_active_sessions_for_meeting(self, meeting_session_id: str) -> list:
        """
        Recupera sessões ativas para uma meeting_session.
        
        Args:
            meeting_session_id: ID da sessão de reunião
        
        Returns:
            Lista de sessões ativas
        """
        try:
            if not self.pool:
                await self.initialize_pool()
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM recording_sessions 
                    WHERE meeting_session_id = $1 AND status IN ('active', 'paused')
                    ORDER BY created_at DESC
                    """,
                    meeting_session_id
                )
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Erro ao buscar sessões ativas: {e}")
            return []


# Instância global do serviço
supabase_persistence: Optional[SupabasePersistenceService] = None


async def get_persistence_service() -> SupabasePersistenceService:
    """Factory function para o serviço de persistência"""
    global supabase_persistence
    
    if supabase_persistence is None:
        supabase_persistence = SupabasePersistenceService()
        await supabase_persistence.initialize_pool()
    
    return supabase_persistence


async def cleanup_persistence_service():
    """Cleanup do serviço de persistência"""
    global supabase_persistence
    
    if supabase_persistence:
        await supabase_persistence.close_pool()
        supabase_persistence = None
