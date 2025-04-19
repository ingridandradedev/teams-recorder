#!/bin/bash
pulseaudio --start
xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" \
  venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000