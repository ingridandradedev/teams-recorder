# 🎬💬 MarIA Recorder API - Feedback PNL Integrado

## Novos Endpoints de Feedback PNL com Gravação Teams

A API do MarIA Recorder agora inclui funcionalidades avançadas de análise de feedback PNL (Programação Neurolinguística) integradas à gravação do Microsoft Teams. Estes endpoints combinam a gravação automática do Teams com análise contextual em tempo real usando Gemini 2.5 Pro.

## 🚀 Endpoints Implementados

### 1. **POST** `/api/feedback/start`
Inicia uma nova sessão de feedback PNL integrada com gravação do Teams.

**Parâmetros Query:**
- `url` (obrigatório): URL da reunião do Teams
- `segment_time` (opcional): Segundos por segmento para análise (padrão: 60)
- `upload_dest` (opcional): Pasta destino no bucket (padrão: "recordings-segments")
- `record_video` (opcional): Gravar vídeo além do áudio (padrão: true)

**Headers:**
- `X-API-Token`: Seu token de autenticação

**Resposta:**
```json
{
  "sessionId": "uuid-da-sessao",
  "status": "started",
  "type": "feedback_pnl_with_teams_recording",
  "teams_url": "https://teams.microsoft.com/...",
  "feedback_stream_url": "/api/feedback/stream/{sessionId}",
  "stop_url": "/api/feedback/session/{sessionId}",
  "context_url": "/api/feedback/context/{sessionId}",
  "message": "Sessão iniciada com sucesso"
}
```

### 2. **POST** `/api/feedback/process-audio/{session_id}`
Processa áudio manualmente para análise de feedback PNL.

**Body:** Arquivo de áudio (multipart/form-data)
**Headers:** `X-API-Token`

**Resposta:**
```json
{
  "status": "processed",
  "type": "feedback_pnl_analysis",
  "session_id": "uuid",
  "timestamp": 1234567890,
  "audio_size": 102400,
  "context_segments": 5
}
```

### 3. **GET** `/api/feedback/stream/{session_id}`
Stream SSE de atualizações da sessão de feedback PNL.

**Headers:** `X-API-Token`

**Eventos SSE:**
- `feedback_analysis`: Nova análise de feedback
- `recording_update`: Eventos da gravação
- `feedback_session_started`: Sessão iniciada
- `feedback_recording_complete`: Gravação concluída
- `error`: Erros diversos
- `heartbeat`: Manter conexão viva

### 4. **DELETE** `/api/feedback/session/{session_id}`
Encerra sessão de feedback PNL e para gravação do Teams.

**Headers:** `X-API-Token`

**Resposta:**
```json
{
  "status": "session_ended",
  "sessionId": "uuid",
  "type": "feedback_pnl_with_teams_recording",
  "context_cleared": true,
  "recording_stopped": true,
  "timestamp": 1234567890
}
```

### 5. **GET** `/api/feedback/context/{session_id}`
Retorna o contexto acumulado da sessão de feedback.

**Headers:** `X-API-Token`

**Resposta:**
```json
{
  "sessionId": "uuid",
  "context": {
    "session_id": "uuid",
    "total_chunks": 10,
    "status": "active",
    "contexto_acumulado": "Resumo da conversa...",
    "padroes_identificados": ["padrão1", "padrão2"],
    "evolucao_emocional": ["estado1", "estado2"],
    "temas_recorrentes": ["tema1", "tema2"],
    "created_at": "2024-01-01T10:00:00",
    "last_updated": "2024-01-01T10:30:00",
    "teams_url": "https://teams.microsoft.com/...",
    "recording_id": "uuid"
  },
  "timestamp": 1234567890
}
```

### 6. **GET** `/api/feedback/sessions`
Lista todas as sessões de feedback PNL ativas.

**Headers:** `X-API-Token`

**Resposta:**
```json
{
  "active_feedback_sessions": ["uuid1", "uuid2"],
  "feedback_session_count": 2,
  "feedback_sessions_info": {
    "uuid1": {
      "session_id": "uuid1",
      "created_at": "2024-01-01T10:00:00",
      "status": "active"
    }
  },
  "context_info": {
    "uuid1": {
      "total_segments": 5,
      "patterns_count": 3,
      "themes_count": 2,
      "teams_url": "https://teams.microsoft.com/...",
      "recording_id": "uuid1"
    }
  },
  "service_available": true
}
```

### 7. **POST** `/api/feedback/session/{session_id}/pause`
Pausa análise de feedback PNL (mantém gravação).

### 8. **POST** `/api/feedback/session/{session_id}/resume`
Retoma análise de feedback PNL pausada.

## 🧠 Análise de Feedback PNL

### Estrutura da Análise
Cada análise de feedback retorna:

```json
{
  "resumo_global": "Como este momento se conecta com o contexto anterior",
  "topicos_abertos": [
    "Objetivo que evoluiu",
    "Novo ponto identificado"
  ],
  "citacoes": [
    {
      "t": "05:30",
      "trecho": "Frase importante que mostra evolução"
    }
  ],
  "feedback_rt": [
    {
      "tipo": "contexto",
      "mensagem": "Como se conecta com discussões anteriores",
      "id": "unique_id"
    },
    {
      "tipo": "evolucao",
      "mensagem": "Mudança observada desde o início",
      "id": "unique_id"
    },
    {
      "tipo": "sugestao_fala",
      "mensagem": "Frase específica sugerida: 'Exemplo de fala'",
      "id": "unique_id"
    }
  ]
}
```

### Tipos de Feedback
- **contexto**: Conexão com momentos anteriores
- **evolucao**: Mudanças observadas
- **rapport**: Qualidade da conexão
- **linguagem**: Padrões VAK identificados
- **estado**: Estados emocionais
- **ancoragem**: Ancoragens PNL
- **reframing**: Ressignificações
- **outcome**: Clareza dos objetivos
- **padrao**: Padrões comportamentais
- **sugestao_fala**: Sugestões específicas de fala

## 🔧 Configuração

### Variáveis de Ambiente Necessárias
```bash
# Obrigatório para feedback PNL
GEMINI_API_KEY=your_gemini_api_key_here

# Para autenticação da API
API_TOKEN=your_secure_api_token

# Para upload no Google Cloud Storage
GOOGLE_CREDENTIALS_JSON={"type":"service_account",...}
# OU
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

### Dependências Python
```bash
pip install google-genai fastapi uvicorn python-multipart
```

## 📋 Exemplo de Uso

### 1. Iniciar Sessão
```bash
curl -X POST "http://localhost:8000/api/feedback/start?url=https://teams.microsoft.com/l/meetup-join/example&segment_time=60" \
  -H "X-API-Token: your_token"
```

### 2. Conectar ao Stream
```javascript
const eventSource = new EventSource('/api/feedback/stream/session_id?api_key=your_token');
eventSource.onmessage = function(event) {
  const data = JSON.parse(event.data);
  console.log('Feedback recebido:', data);
};
```

### 3. Obter Contexto
```bash
curl -H "X-API-Token: your_token" \
  "http://localhost:8000/api/feedback/context/session_id"
```

### 4. Encerrar Sessão
```bash
curl -X DELETE -H "X-API-Token: your_token" \
  "http://localhost:8000/api/feedback/session/session_id"
```

## 🧪 Teste

Use o arquivo `test_feedback_teams.html` para testar a funcionalidade completa:

1. Abra o arquivo em um navegador
2. Configure a URL do Teams e API Token
3. Clique em "Iniciar Sessão de Feedback + Teams"
4. Acompanhe as análises em tempo real

## 🔄 Diferenças dos Endpoints Existentes

### Antes (Transcrição Simples)
- `POST /record-and-transcribe`: Gravação + transcrição básica
- `GET /transcription-stream/{recording_id}`: Stream de transcrição

### Agora (Feedback PNL Integrado)
- `POST /api/feedback/start`: Gravação + análise PNL contextual
- `GET /api/feedback/stream/{session_id}`: Stream de feedback e eventos
- Controle de pausa/retomada
- Contexto acumulativo com padrões PNL
- Sugestões de fala em tempo real

## 🎯 Principais Vantagens

1. **Análise Contextual**: Cada análise considera todo o histórico da conversa
2. **Padrões PNL**: Identifica rapport, ancoragens, estados emocionais
3. **Sugestões Práticas**: Recomendações específicas de fala para líderes
4. **Gravação Integrada**: Combinação automática com gravação do Teams
5. **Controle Granular**: Pause/retome análise sem afetar gravação
6. **Streaming Real-time**: Eventos SSE para atualizações instantâneas

## 🚨 Status dos Serviços

Verifique o status no endpoint `/health`:
```json
{
  "status": "healthy",
  "version": "2.3.0",
  "active_recordings": 1,
  "active_feedback_sessions": 1,
  "features": {
    "recording": true,
    "transcription": true,
    "feedback_pnl": true
  }
}
```
