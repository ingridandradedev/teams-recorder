import os
import json
from google.cloud import secretmanager
from google.oauth2 import service_account

def get_credentials_from_secret():
    secret_name = os.getenv("GOOGLE_SECRET_NAME")
    if not secret_name:
        raise RuntimeError("GOOGLE_SECRET_NAME não definido no ambiente")
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": secret_name})
    payload = response.payload.data.decode("utf-8")
    info = json.loads(payload)
    return service_account.Credentials.from_service_account_info(info)