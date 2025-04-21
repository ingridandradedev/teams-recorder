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

# Crie um usuário não-root antes de copiar / ajustar permissões
RUN useradd -m appuser

# Copie o código e ajuste dono para o appuser
COPY . /app
RUN chown -R appuser:appuser /app

# Instale as dependências do Python (ainda como root)
RUN pip install --no-cache-dir -r requirements.txt

# Passe a executar como appuser
USER appuser

# Exponha a porta 8000 para a API
EXPOSE 8000

# Comando para iniciar a aplicação
CMD ["sh", "run.sh"]