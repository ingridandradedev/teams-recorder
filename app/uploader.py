from google.cloud import storage
from google.oauth2 import service_account
import os

BUCKET_NAME = "projeto-maria-1-0-pecege"
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "maria-457717-9fa8d402e552.json")

def enviar_para_gcs(nome_arquivo: str, destino: str = "") -> tuple[str, str]:
    """
    Se destino for ex.: "screenshot-logs", ele fará upload para 
    gs://…/screenshot-logs/nome_arquivo
    """
    print("📤 Iniciando upload para o Google Cloud Storage...")

    try:
        blob_name = f"{destino.rstrip('/')}/{nome_arquivo}" if destino else nome_arquivo

        # Carrega as credenciais do arquivo JSON
        credentials = service_account.Credentials.from_service_account_file(CREDENTIALS_PATH)
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