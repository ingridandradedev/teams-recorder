import os
import json
from datetime import timedelta
from google.cloud import storage
from google.oauth2 import service_account

BUCKET_NAME = "maria-1-0-pecege"
# carrega JSON das credenciais via env var
CRED_JSON = os.getenv("GCP_SERVICE_ACCOUNT_JSON")

def enviar_para_gcs(nome_arquivo: str) -> str:
    print("📤 Iniciando upload para o Google Cloud Storage...")
    try:
        if CRED_JSON:
            info = json.loads(CRED_JSON)
            credentials = service_account.Credentials.from_service_account_info(info)
            client = storage.Client(credentials=credentials, project=info.get("project_id"))
        else:
            # fallback para ADC (se configurado)
            client = storage.Client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(nome_arquivo)

        print(f"🔄 Fazendo upload do arquivo: {nome_arquivo}")
        blob.upload_from_filename(nome_arquivo)

        url_assinada = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET"
        )
        print(f"✅ Upload concluído. URL gerada: {url_assinada}")
        return url_assinada

    except Exception as e:
        print(f"❌ Erro durante o upload para o Google Cloud Storage: {e}")
        raise