#!/bin/bash
# Inicia o servidor de áudio
pulseaudio --start

# Executa o servidor Uvicorn com suporte ao Xvfb
xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" \
  uvicorn app.main:app --host 0.0.0.0 --port 8000