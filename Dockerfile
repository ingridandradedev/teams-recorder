# Base oficial do Playwright com navegadores
FROM mcr.microsoft.com/playwright:v1.51.1-noble

# Instala Python completo, FFmpeg, Pulseaudio, Xvfb e dependências
RUN apt-get update && apt-get install -y \
    python3-full python3-pip ffmpeg pulseaudio alsa-utils xvfb dbus \
    && apt-get clean

# Diretório da aplicação
WORKDIR /app

# Copia tudo para o container
COPY . .

# Instala dependências Python
RUN pip3 install --no-cache-dir -r requirements.txt

# Instala navegadores do Playwright (caso a base não tenha instalado)
RUN python3 -m playwright install

# Define variáveis de ambiente (caso precise de PATH ou tokens)
ENV GOOGLE_APPLICATION_CREDENTIALS="/app/maria-456618-871b8f622168.json"

# Expõe a porta da API FastAPI
EXPOSE 8000

# Comando de inicialização da API com suporte a áudio e vídeo headless
CMD ["bash", "-c", "pulseaudio --start && xvfb-run --auto-servernum --server-args='-screen 0 1280x720x24' uvicorn app.main:app --host 0.0.0.0 --port 8000"]
