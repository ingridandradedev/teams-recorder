from google.cloud import storage
from datetime import timedelta
import os

BUCKET_NAME = "maria-1-0-pecege"

def enviar_para_gcs(nome_arquivo: str) -> str:
    print("📤 Iniciando upload para o Google Cloud Storage...")

    try:
        # Inicializa o cliente do Google Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(nome_arquivo)

        # Faz o upload do arquivo
        print(f"🔄 Fazendo upload do arquivo: {nome_arquivo}")
        blob.upload_from_filename(nome_arquivo)

        # Gera uma URL assinada válida por 1 hora usando o método V4
        url_assinada = blob.generate_signed_url(
            version="v4",  # Use a versão 4 do método de URL assinada
            expiration=timedelta(hours=1),
            method="GET"
        )
        print(f"✅ Upload concluído. URL assinada gerada: {url_assinada}")
        return url_assinada
    except Exception as e:
        print(f"❌ Erro durante o upload para o Google Cloud Storage: {e}")
        raise