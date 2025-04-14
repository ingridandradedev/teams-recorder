# Base com Chromium + Playwright (v1.51.1) sobre Ubuntu 24.04 (noble)
FROM mcr.microsoft.com/playwright:v1.51.1-noble

# Instala dependências: Python completo, pip, ffmpeg, pulseaudio e X11 virtual
RUN apt-get update && apt-get install -y \
    python3-full python3-pip ffmpeg pulseaudio alsa-utils xvfb dbus \
    && apt-get clean

# Diretório principal da aplicação
WORKDIR /app

# Copia o conteúdo do projeto
COPY . .

# Instala dependências do Python com override de ambiente protegido
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

# Garante instalação dos navegadores do Playwright
RUN python3 -m playwright install

# Define credenciais do Google (opcional se estiver embutido no código)
ENV GOOGLE_APPLICATION_CREDENTIALS="/app/maria-456618-871b8f622168.json"

# Expõe a porta da FastAPI
EXPOSE 8000

# Comando final: inicia pulseaudio + servidor uvicorn com display virtual
CMD ["bash", "-c", "pulseaudio --start && xvfb-run --auto-servernum --server-args='-screen 0 1280x720x24' uvicorn app.main:app --host 0.0.0.0 --port 8000"]
