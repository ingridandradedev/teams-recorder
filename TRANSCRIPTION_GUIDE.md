# Exemplo de configuração para Teams Recorder com Transcrição

## Variáveis de Ambiente Necessárias

### Para autenticação Google Cloud Storage (escolha uma):
# GOOGLE_CREDENTIALS_JSON="{"type":"service_account",...}"  # Recomendado para Railway
# GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"  # Local development
# GOOGLE_SECRET_NAME="projects/PROJECT_ID/secrets/SECRET_NAME/versions/latest"  # Secret Manager

### Para transcrição com Gemini (obrigatório para novos endpoints):
GEMINI_API_KEY="your-gemini-api-key-here"

### Para autenticação da API (opcional, mas recomendado):
API_TOKEN="your-secure-api-token-here"

## Novos Endpoints

### 1. Iniciar Gravação com Transcrição
POST /record-and-transcribe
Headers:
  X-API-Token: your-api-token

Query Parameters:
  - url: URL da reunião do Teams
  - segment_time: Segundos por segmento (padrão: 60)
  - upload_dest: Pasta destino no bucket (padrão: "recordings-segments")
  - record_video: Capturar vídeo (padrão: true)

Response:
{
  "message": "Gravação com transcrição iniciada com sucesso",
  "recording_id": "uuid-here",
  "status": "started",
  "transcription_stream_url": "/transcription-stream/uuid-here",
  "stop_url": "/stop/uuid-here"
}

### 2. Stream de Transcrição em Tempo Real
GET /transcription-stream/{recording_id}
Headers:
  X-API-Token: your-api-token

Response: Server-Sent Events stream com eventos como:
{
  "event": "transcription_segment",
  "falante": "João Silva",
  "timestamp_inicial": "00:02:15",
  "timestamp_final": "00:02:18",
  "texto": "Boa tarde, pessoal. Vamos começar a reunião.",
  "recording_id": "uuid-here"
}

### 3. Status da Transcrição
GET /transcription-status/{recording_id}
Headers:
  X-API-Token: your-api-token

Response:
{
  "recording_id": "uuid-here",
  "status": "active",
  "started_at": 1692123456.789,
  "total_segments_processed": 5,
  "total_transcription_events": 25,
  "total_speech_segments": 12
}

## Como Usar

1. Configure as variáveis de ambiente
2. Inicie uma gravação com transcrição: POST /record-and-transcribe
3. Use o recording_id retornado para acessar o stream: GET /transcription-stream/{recording_id}
4. Monitore o progresso com: GET /transcription-status/{recording_id}
5. Para parar: POST /stop/{recording_id}

## Eventos de Transcrição

O stream retorna eventos no formato Server-Sent Events:

- `transcription_segment`: Nova fala transcrita
- `transcription_complete`: Segmento de vídeo processado
- `transcription_stream_complete`: Transcrição finalizada
- `transcription_error`: Erro na transcrição

## Observações

- A transcrição usa Gemini 2.5 Pro para identificar falantes e gerar timestamps
- Os segmentos são processados conforme são gravados (tempo real)
- A gravação continua funcionando mesmo se a transcrição falhar
- Os endpoints antigos (/gravar) continuam funcionando normalmente
