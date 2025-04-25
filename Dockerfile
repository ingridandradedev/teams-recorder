# Dockerfile
FROM python:3.10-slim

# 1) Instala dependências de SO
RUN apt-get update && apt-get install -y \
    ffmpeg \
    pulseaudio \
    xvfb \
    wget \
    gnupg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2) Instala o Chromium requerido pelo Playwright
RUN wget -qO- https://deb.nodesource.com/setup_16.x | bash - \
    && apt-get update && apt-get install -y chromium \
    && rm -rf /var/lib/apt/lists/*

# 3) Copia e instala requisitos Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4) Instala browsers do Playwright
RUN pip install playwright && \
    playwright install --with-deps chromium

# 5) Copia o código da app
COPY app ./app
COPY run.sh .

# 6) Expor porta e definir entrypoint
EXPOSE 8000
ENTRYPOINT ["bash", "run.sh"]