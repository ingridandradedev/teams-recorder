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

# Instala dependências do Python com override de ambiente protegido (PEP 668)
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

# Instala navegadores do Playwright para uso com Python
RUN python3 -m playwright install

# Define credenciais do Google (caso esteja embutida ou presente no projeto)
ENV GOOGLE_APPLICATION_CREDENTIALS="/app/maria-456618-871b8f622168.json"

# Expõe a porta usada pelo Uvicorn (também lida com PORT variável de ambientes como Railway)
EXPOSE 8000

# Comando final: inicia áudio e display virtual, respeita $PORT com fallback
CMD ["bash", "-c", "pulseaudio --start && sleep 1 && echo '✅ Pulseaudio iniciado' && echo '🚀 Iniciando Uvicorn...' && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]