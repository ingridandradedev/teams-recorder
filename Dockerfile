# Use a imagem oficial do Playwright com navegadores instalados
FROM mcr.microsoft.com/playwright/python:v1.51.0-jammy

# Instale pacotes adicionais necessários para áudio e gráficos
RUN apt-get update && apt-get install -y \
    pulseaudio \
    xvfb \
    ffmpeg \
    && apt-get clean

# Defina o diretório de trabalho dentro do container
WORKDIR /app

# Copie os arquivos do projeto para o container
COPY . /app

# Instale as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Crie um usuário não-root para executar a aplicação
RUN useradd -m appuser
USER appuser

# Exponha a porta 8000 para a API
EXPOSE 8000

# Comando para iniciar a aplicação
CMD ["sh", "run.sh"]