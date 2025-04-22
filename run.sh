#!/usr/bin/env bash
set -euo pipefail

# garante que o script execute sempre do diretório raiz do repo
cd "$(dirname "$0")"

# ativa o virtualenv (opcional, pois chamaremos o uvicorn dentro dele)
source venv/bin/activate

# inicia o PulseAudio para captura de áudio
pulseaudio --start

# roda o uvicorn via Xvfb para permitir o Playwright/Chromium headless
xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" \
  uvicorn app.main:app --host 0.0.0.0 --port 8000