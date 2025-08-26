-- Tabela para armazenar sessões de gravação com feedback PNL
CREATE TABLE public.recording_sessions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  meeting_session_id uuid NOT NULL,
  session_id character varying NOT NULL UNIQUE, -- ID interno da sessão de feedback
  teams_url character varying NOT NULL,
  status character varying NOT NULL DEFAULT 'active',
  source_type character varying NOT NULL DEFAULT 'live_recording',
  
  -- Configurações da gravação
  segment_time integer DEFAULT 60,
  record_video boolean DEFAULT true,
  upload_dest character varying DEFAULT 'recordings-segments',
  
  -- Contexto de feedback PNL
  feedback_context jsonb DEFAULT '{}'::jsonb,
  total_chunks integer DEFAULT 0,
  total_segments_processed integer DEFAULT 0,
  
  -- URLs finais dos arquivos
  final_recording_url character varying NULL,
  recording_metadata jsonb DEFAULT '{}'::jsonb,
  
  -- Estatísticas da sessão
  session_stats jsonb DEFAULT '{}'::jsonb,
  error_message text NULL,
  
  -- Timestamps
  started_at timestamp with time zone DEFAULT now(),
  ended_at timestamp with time zone NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  
  CONSTRAINT recording_sessions_pkey PRIMARY KEY (id),
  CONSTRAINT recording_sessions_meeting_session_fkey FOREIGN KEY (meeting_session_id) 
    REFERENCES meeting_sessions (id) ON DELETE CASCADE,
  CONSTRAINT recording_sessions_session_id_unique UNIQUE (session_id),
  CONSTRAINT recording_sessions_status_check CHECK (
    (status)::text = ANY (
      ARRAY[
        'active'::character varying,
        'paused'::character varying,
        'completed'::character varying,
        'failed'::character varying,
        'cancelled'::character varying
      ]::text[]
    )
  )
) TABLESPACE pg_default;

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_recording_sessions_meeting_session_id 
  ON public.recording_sessions USING btree (meeting_session_id) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_recording_sessions_session_id 
  ON public.recording_sessions USING btree (session_id) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_recording_sessions_status 
  ON public.recording_sessions USING btree (status) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_recording_sessions_started_at 
  ON public.recording_sessions USING btree (started_at) TABLESPACE pg_default;

-- Trigger para atualizar updated_at automaticamente
CREATE OR REPLACE FUNCTION update_recording_sessions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_recording_sessions_updated_at
    BEFORE UPDATE ON public.recording_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_recording_sessions_updated_at();

-- Comentários para documentação
COMMENT ON TABLE public.recording_sessions IS 'Sessões de gravação do Teams com análise de feedback PNL em tempo real';
COMMENT ON COLUMN public.recording_sessions.session_id IS 'ID interno único da sessão de feedback (UUID gerado pela aplicação)';
COMMENT ON COLUMN public.recording_sessions.meeting_session_id IS 'Referência para a sessão de reunião principal';
COMMENT ON COLUMN public.recording_sessions.feedback_context IS 'Contexto acumulado da análise de feedback PNL em formato JSON';
COMMENT ON COLUMN public.recording_sessions.total_chunks IS 'Número total de chunks de áudio processados';
COMMENT ON COLUMN public.recording_sessions.session_stats IS 'Estatísticas detalhadas da sessão (padrões identificados, evolução emocional, etc.)';
