from google.cloud import storage
import os
from app.gcp_secrets import get_credentials_from_secret  # ADICIONE ESTA LINHA

BUCKET_NAME = "projeto-maria-1-0-pecege"

def enviar_para_gcs(nome_arquivo: str, destino: str = "") -> tuple[str, str]:
    """
    Se destino for ex.: "screenshot-logs", ele fará upload para 
    gs://…/screenshot-logs/nome_arquivo
    """
    print("📤 Iniciando upload para o Google Cloud Storage...")

    try:
        blob_name = f"{destino.rstrip('/')}/{nome_arquivo}" if destino else nome_arquivo

        # Carrega as credenciais do Secret Manager
        credentials = get_credentials_from_secret()  # ALTERADO AQUI
        storage_client = storage.Client(credentials=credentials)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(blob_name)

        # Faz o upload do arquivo
        print(f"🔄 Fazendo upload do arquivo: {nome_arquivo}")
        blob.upload_from_filename(nome_arquivo)

        # Gera URLs
        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"
        gs_uri = f"gs://{BUCKET_NAME}/{blob_name}"

        print(f"✅ Upload concluído. URL pública: {public_url}, URI GS: {gs_uri}")
        return public_url, gs_uri
    except Exception as e:
        print(f"❌ Erro durante o upload para o Google Cloud Storage: {e}")
        raise