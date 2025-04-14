FROM mcr.microsoft.com/playwright:v1.51.1-noble

# Instala dependências
RUN apt-get update && apt-get install -y \
    python3 python3-pip ffmpeg pulseaudio alsa-utils xvfb dbus \
    && apt-get clean

WORKDIR /app
COPY . .

# Instala dependências Python
RUN pip3 install --no-cache-dir -r requirements.txt

# Ativa Playwright
RUN python3 -m playwright install

ENV GOOGLE_APPLICATION_CREDENTIALS=/app/maria-456618-871b8f622168.json

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
