# 1. Base Image
# Usar uma imagem Python slim baseada no Debian Buster
FROM python:3.9-slim-bookworm

# Definir variáveis de ambiente para evitar prompts interativos durante a instalação de pacotes
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 2. Instalar Dependências do Sistema
# Inclui ferramentas básicas, FFmpeg, PulseAudio, Xvfb e dependências para o Playwright/Chromium
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    bash \
    curl \
    gnupg \
    ffmpeg \
    pulseaudio \
    pulseaudio-utils \
    xvfb \
    xauth \
    # Dependências comuns para navegadores e Playwright no Debian Buster
    libnss3 \
    libnspr4 \
    libdbus-glib-1-2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libasound2 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libxshmfence1 \
    libxkbcommon0 \
    libx11-xcb1 \
    libxrender1 \
    libxtst6 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libgtk-3-0 \
    fonts-liberation \
    # Limpeza
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Configurar PulseAudio para não sair quando ocioso
RUN sed -i 's/; exit-idle-time = 20/exit-idle-time = -1/' /etc/pulse/daemon.conf && \
    sed -i 's/; flat-volumes = yes/flat-volumes = no/' /etc/pulse/daemon.conf

# 3. Definir Diretório de Trabalho
WORKDIR /app

# 4. Copiar o arquivo requirements.txt
COPY requirements.txt ./

# 5. Instalar Dependências Python
# O --no-cache-dir é usado para reduzir o tamanho da imagem
RUN pip install --no-cache-dir -r requirements.txt

# 6. Instalar Navegadores Playwright
# As dependências de SO foram instaladas manualmente acima
RUN playwright install chromium

# 7. Copiar Código da Aplicação
# ATENÇÃO: O arquivo de credenciais JSON (maria-457717-9fa8d402e552.json) está sendo copiado.
# Para ambientes de produção no Google Cloud, é ALTAMENTE RECOMENDADO usar
# Application Default Credentials (ADC) com uma conta de serviço associada à VM,
# em vez de embutir arquivos de chave na imagem.
# Esta linha está incluída porque o app/uploader.py espera o arquivo neste local.
COPY app/ ./app/
COPY run.sh ./

# 8. Tornar o run.sh executável
RUN chmod +x ./run.sh

# 9. Expor a Porta
# A aplicação FastAPI, conforme o run.sh, roda na porta 8000
EXPOSE 8000

# 10. Comando para executar a aplicação
# O script run.sh lida com a inicialização do PulseAudio, Xvfb e Uvicorn.
CMD ["./run.sh"]