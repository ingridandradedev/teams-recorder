#!/usr/bin/env bash
set -euo pipefail

# Garante que o script execute sempre do diretório raiz do repo
cd "$(dirname "$0")"

# Verifica se o PulseAudio está instalado
if ! command -v pulseaudio &> /dev/null; then
  echo "❌ PulseAudio não está instalado. Instale-o antes de continuar."
  exit 1
fi

# Inicia o PulseAudio para captura de áudio, se não estiver rodando
if ! pulseaudio --check &> /dev/null; then
  echo "🔊 Iniciando o PulseAudio..."
  pulseaudio --start
else
  echo "🔊 PulseAudio já está rodando."
fi

# Verifica se o Xvfb está instalado
if ! command -v xvfb-run &> /dev/null; then
  echo "❌ Xvfb não está instalado. Instale-o antes de continuar."
  exit 1
fi

# Roda o uvicorn via Xvfb para permitir o Playwright/Chromium headless
echo "🚀 Iniciando o servidor FastAPI com Xvfb..."
xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" \
  uvicorn app.main:app --host 0.0.0.0 --port 8000