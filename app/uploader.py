from google.cloud import storage
from google.oauth2 import service_account
import os

BUCKET_NAME = "projeto-maria-1-0-pecege"
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "maria-457717-9fa8d402e552.json")

def enviar_para_gcs(nome_arquivo: str) -> tuple[str, str]:
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

        # Torna o objeto público e gera URLs
        blob.make_public()
        public_url = blob.public_url
        gs_uri = f"gs://{BUCKET_NAME}/{nome_arquivo}"

        print(f"✅ Upload concluído. URL pública: {public_url}, URI GS: {gs_uri}")
        return public_url, gs_uri
    except Exception as e:
        print(f"❌ Erro durante o upload para o Google Cloud Storage: {e}")
        raise