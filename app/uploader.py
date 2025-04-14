import os
import json
from google.cloud import storage

BUCKET_NAME = "maria-1-0-pecege"
PASTA = "maria-1-0-pecege"

CREDENCIAL_EMBUTIDA = {
  "type": "service_account",
  "project_id": "maria-456618",
  "private_key_id": "871b8f622168f17cd1b863d59f46657e977ee091",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...SNIP...\n-----END PRIVATE KEY-----\n",
  "client_email": "maria-31@maria-456618.iam.gserviceaccount.com",
  "client_id": "103963023317629107070",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/maria-31%40maria-456618.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}

def enviar_para_gcs(nome_arquivo: str) -> str:
    print("📤 Enviando para o Google Cloud Storage...")

    cred_path = "embedded-gcp-credentials.json"
    with open(cred_path, "w") as f:
        json.dump(CREDENCIAL_EMBUTIDA, f)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path

    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(f"{PASTA}/{nome_arquivo}")
    blob.upload_from_filename(nome_arquivo)
    blob.make_public()

    print(f"✅ Arquivo disponível em: {blob.public_url}")
    return blob.public_url
