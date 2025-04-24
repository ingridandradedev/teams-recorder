from google.cloud import storage
from google.oauth2 import service_account
from datetime import timedelta
import os

BUCKET_NAME = "projeto-maria-1-0-pecege"
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "maria-457717-9fa8d402e552.json")

def enviar_para_gcs(nome_arquivo: str) -> str:
    print("📤 Iniciando upload para o Google Cloud Storage...")

    try:
        # Carrega as credenciais do arquivo JSON
        credentials = service_account.Credentials.from_service_account_file(CREDENTIALS_PATH)
        storage_client = storage.Client(credentials=credentials)
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