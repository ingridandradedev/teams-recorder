# Implementação de Persistência com Supabase

## Visão Geral

Sistema de persistência integrado ao feedback PNL que permite armazenar e consultar dados das sessões de gravação no banco Supabase PostgreSQL.

## Arquitetura

### 1. Tabela `recording_sessions`

```sql
CREATE TABLE recording_sessions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    meeting_session_id UUID NOT NULL,
    session_id VARCHAR(255) NOT NULL UNIQUE,
    teams_url TEXT NOT NULL,
    status recording_status DEFAULT 'created',
    feedback_context JSONB,
    recording_url TEXT,
    public_url TEXT,
    total_segments INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT fk_meeting_session 
        FOREIGN KEY (meeting_session_id) 
        REFERENCES meeting_sessions(id) 
        ON DELETE CASCADE
);
```

### 2. Service Layer - `supabase_persistence.py`

**Principais métodos:**
- `create_recording_session()` - Cria nova sessão no banco
- `update_feedback_context()` - Atualiza contexto de análise PNL
- `save_final_recording()` - Salva URLs da gravação final
- `get_recording_session_by_session_id()` - Busca sessão por session_id
- `get_recordings_by_meeting_session()` - Lista gravações de uma reunião

### 3. API Endpoints Atualizados

#### POST `/api/feedback/start`
**Mudanças:**
- Aceita `FeedbackSessionRequest` no body (não mais query params)
- Inclui `meeting_session_id` obrigatório
- Retorna `FeedbackSessionResponse` com `recording_session_id`
- Cria registro no banco automaticamente

**Request Body:**
```json
{
    "meeting_session_id": "uuid-da-reuniao",
    "teams_url": "https://teams.microsoft.com/...",
    "segment_time": 60,
    "upload_dest": "recordings-segments", 
    "record_video": true
}
```

**Response:**
```json
{
    "session_id": "uuid-gerado",
    "recording_session_id": "uuid-no-banco",
    "status": "started",
    "type": "feedback_pnl_with_teams_recording",
    "teams_url": "https://teams.microsoft.com/...",
    "meeting_session_id": "uuid-da-reuniao",
    "feedback_stream_url": "/api/feedback/stream/uuid-gerado",
    "stop_url": "/api/feedback/session/uuid-gerado",
    "context_url": "/api/feedback/context/uuid-gerado",
    "message": "Sessão iniciada e persistida no banco"
}
```

#### GET `/api/feedback/session/{session_id}/recording`
**Novo endpoint** para consultar dados da sessão armazenados no banco.

**Response:**
```json
{
    "recording_session_id": "uuid-no-banco",
    "meeting_session_id": "uuid-da-reuniao", 
    "session_id": "uuid-da-sessao",
    "teams_url": "https://teams.microsoft.com/...",
    "status": "completed",
    "feedback_context": {
        "analise_completa": "...",
        "total_chunks": 5
    },
    "recording_url": "gs://bucket/final.mp4",
    "public_url": "https://public-url.com/final.mp4",
    "total_segments": 12,
    "metadata": {},
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T11:45:00Z"
}
```

#### GET `/api/feedback/meeting/{meeting_session_id}/recordings`
**Novo endpoint** para listar todas as gravações de uma reunião.

**Response:**
```json
{
    "meeting_session_id": "uuid-da-reuniao",
    "recordings": [
        {
            "id": "uuid-no-banco",
            "session_id": "uuid-da-sessao",
            "status": "completed",
            "recording_url": "gs://bucket/final.mp4",
            "total_segments": 12,
            "created_at": "2024-01-15T10:30:00Z"
        }
    ],
    "total": 1
}
```

## Fluxo de Persistência

### 1. Início da Sessão
1. Cliente envia POST `/api/feedback/start` com `meeting_session_id`
2. Sistema cria registro em `recording_sessions` com status `created`
3. Inicia gravação e análise PNL em background
4. Atualiza status para `recording`

### 2. Durante a Gravação
1. A cada análise de feedback, atualiza `feedback_context` no banco
2. Logs de persistência são não-bloqueantes (warnings se falharem)
3. Contexto PNL é armazenado como JSONB para flexibilidade

### 3. Finalização
1. Ao concluir gravação, salva URLs finais no banco
2. Atualiza `total_segments` e status para `completed`
3. Em caso de erro, status fica como `error`

## Configuração

### Variáveis de Ambiente
```bash
SUPABASE_DB_HOST=aws-1-sa-east-1.pooler.supabase.com
SUPABASE_DB_PORT=6543
SUPABASE_DB_NAME=postgres
SUPABASE_DB_USER=postgres.xxxxx
SUPABASE_DB_PASSWORD=sua-senha
```

### Pool de Conexões
- Min: 1 conexão
- Max: 10 conexões
- Timeout: 30 segundos
- Retry automático em caso de falha

## Integração com Sistema Existente

### Relacionamentos
- `recording_sessions.meeting_session_id` → `meeting_sessions.id`
- Permite vincular gravações de feedback a reuniões existentes
- CASCADE DELETE remove gravações quando reunião é deletada

### Compatibilidade
- Sistema funciona mesmo se persistência falhar (graceful degradation)
- Logs detalhados para debugging
- APIs antigas continuam funcionando

## Testes

Execute o script de teste:
```bash
python test_persistence_integration.py
```

**Validações:**
- ✅ Criação de sessão com persistência
- ✅ Consulta de dados da sessão 
- ✅ Listagem por meeting_session_id
- ✅ Parada e cleanup da sessão
- ✅ Conexão com banco de dados

## Monitoramento

### Logs Importantes
```
✅ Sessão criada no banco: recording_session_id=uuid
⚠️ Erro ao salvar contexto de feedback: erro-detalhado
📹 URL da gravação final: gs://bucket/final.mp4
🧹 Gravação finalizada | Total ativo: 2
```

### Status da Sessão
- `created` - Sessão criada, aguardando início
- `recording` - Gravação ativa
- `completed` - Finalizada com sucesso 
- `error` - Erro durante processamento

## Benefícios

1. **Rastreabilidade:** Histórico completo das sessões
2. **Integração:** Vinculação com sistema de reuniões existente
3. **Confiabilidade:** Pool de conexões e retry automático
4. **Flexibilidade:** JSONB para contexto dinâmico
5. **Escalabilidade:** Suporte a múltiplas sessões simultâneas
