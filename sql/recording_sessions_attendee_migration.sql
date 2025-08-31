-- Migração para suportar integração com Attendee API
-- Adicionar colunas necessárias para Attendee

ALTER TABLE public.recording_sessions 
ADD COLUMN IF NOT EXISTS attendee_bot_id character varying,
ADD COLUMN IF NOT EXISTS attendee_meeting_url character varying,
ADD COLUMN IF NOT EXISTS attendee_bot_state character varying DEFAULT 'not_started',
ADD COLUMN IF NOT EXISTS attendee_recording_state character varying DEFAULT 'not_started',
ADD COLUMN IF NOT EXISTS attendee_transcription_state character varying DEFAULT 'not_started',
ADD COLUMN IF NOT EXISTS transcription_data jsonb DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS feedback_analysis jsonb DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS speaker_data jsonb DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS last_transcript_check_at timestamp with time zone,
ADD COLUMN IF NOT EXISTS bot_monitoring_active boolean DEFAULT false,
ADD COLUMN IF NOT EXISTS attendee_metadata jsonb DEFAULT '{}'::jsonb;

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_recording_sessions_attendee_bot_id 
ON public.recording_sessions USING btree (attendee_bot_id);

CREATE INDEX IF NOT EXISTS idx_recording_sessions_bot_monitoring 
ON public.recording_sessions USING btree (bot_monitoring_active);

CREATE INDEX IF NOT EXISTS idx_recording_sessions_bot_state 
ON public.recording_sessions USING btree (attendee_bot_state);

-- Comentários para documentação
COMMENT ON COLUMN public.recording_sessions.attendee_bot_id IS 'Bot ID retornado pela API do Attendee';
COMMENT ON COLUMN public.recording_sessions.attendee_bot_state IS 'Estado atual do bot no Attendee (ready, joining, joined_recording, etc.)';
COMMENT ON COLUMN public.recording_sessions.attendee_recording_state IS 'Estado da gravação no Attendee (not_started, in_progress, complete, etc.)';
COMMENT ON COLUMN public.recording_sessions.attendee_transcription_state IS 'Estado da transcrição no Attendee';
COMMENT ON COLUMN public.recording_sessions.transcription_data IS 'Array de objetos com transcrições timestampadas do Attendee';
COMMENT ON COLUMN public.recording_sessions.feedback_analysis IS 'Análise de feedback gerada pelo Gemini baseada na transcrição';
COMMENT ON COLUMN public.recording_sessions.speaker_data IS 'Dados dos falantes identificados pelo Attendee';
COMMENT ON COLUMN public.recording_sessions.bot_monitoring_active IS 'Indica se o monitoramento em background está ativo';
