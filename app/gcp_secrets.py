import os
import json
import logging
from typing import Optional
from google.cloud import secretmanager
from google.oauth2 import service_account
from google.auth import default
from google.auth.exceptions import DefaultCredentialsError

# Configurar logging
logger = logging.getLogger(__name__)

def get_credentials_robust() -> Optional[service_account.Credentials]:
    """
    Obtém credenciais do Google Cloud de forma robusta, tentando múltiplos métodos:
    1. Application Default Credentials (ADC) - funciona no Google Cloud
    2. Variável de ambiente GOOGLE_APPLICATION_CREDENTIALS com JSON inline
    3. Secret Manager (se disponível)
    4. Arquivo de chave local (para desenvolvimento)
    
    Returns:
        service_account.Credentials ou None se nenhum método funcionar
    """
    
    # Método 1: Tentar Application Default Credentials (ADC)
    # Funciona automaticamente no Google Cloud (Cloud Run, GKE, etc.)
    try:
        logger.info("🔐 Tentando Application Default Credentials (ADC)...")
        credentials, project = default()
        if credentials:
            logger.info("✅ ADC configurado com sucesso")
            return credentials
    except DefaultCredentialsError as e:
        logger.info(f"⚠️ ADC não disponível: {e}")
    except Exception as e:
        logger.warning(f"⚠️ Erro inesperado com ADC: {e}")
    
    # Método 2: Credenciais inline via variável de ambiente
    # Útil para Railway, Heroku, etc. onde você pode colar o JSON completo
    try:
        google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if google_credentials_json:
            logger.info("🔐 Tentando credenciais inline via GOOGLE_CREDENTIALS_JSON...")
            credentials_info = json.loads(google_credentials_json)
            credentials = service_account.Credentials.from_service_account_info(credentials_info)
            logger.info("✅ Credenciais inline configuradas com sucesso")
            return credentials
    except json.JSONDecodeError as e:
        logger.error(f"❌ Erro ao decodificar JSON das credenciais inline: {e}")
    except Exception as e:
        logger.warning(f"⚠️ Erro com credenciais inline: {e}")
    
    # Método 3: Secret Manager (método original)
    # Funciona quando já temos credenciais básicas e acesso ao Secret Manager
    try:
        secret_name = os.getenv("GOOGLE_SECRET_NAME")
        if secret_name:
            logger.info("🔐 Tentando Secret Manager...")
            return get_credentials_from_secret_manager(secret_name)
    except Exception as e:
        logger.warning(f"⚠️ Erro com Secret Manager: {e}")
    
    # Método 4: Arquivo de chave local (desenvolvimento)
    try:
        service_account_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if service_account_file and os.path.exists(service_account_file):
            logger.info("🔐 Tentando arquivo de chave local...")
            credentials = service_account.Credentials.from_service_account_file(service_account_file)
            logger.info("✅ Arquivo de chave local configurado com sucesso")
            return credentials
    except Exception as e:
        logger.warning(f"⚠️ Erro com arquivo de chave local: {e}")
    
    logger.error("❌ Nenhum método de autenticação funcionou!")
    return None

def get_credentials_from_secret_manager(secret_name: str) -> service_account.Credentials:
    """
    Método original mantido para compatibilidade.
    Obtém credenciais do Secret Manager.
    """
    logger.info(f"🔐 Acessando Secret Manager: {secret_name}")
    
    # Tentar usar credenciais existentes ou ADC para acessar Secret Manager
    try:
        client = secretmanager.SecretManagerServiceClient()
    except DefaultCredentialsError:
        # Se não temos ADC, tentar criar client com credenciais explícitas
        try:
            # Tentar primeiro com credenciais inline se disponível
            google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if google_credentials_json:
                credentials_info = json.loads(google_credentials_json)
                credentials = service_account.Credentials.from_service_account_info(credentials_info)
                client = secretmanager.SecretManagerServiceClient(credentials=credentials)
            else:
                raise RuntimeError("Não foi possível criar cliente do Secret Manager sem credenciais")
        except Exception as inner_e:
            raise RuntimeError(f"Erro ao criar cliente do Secret Manager: {inner_e}")
    
    response = client.access_secret_version(request={"name": secret_name})
    payload = response.payload.data.decode("utf-8")
    info = json.loads(payload)
    credentials = service_account.Credentials.from_service_account_info(info)
    logger.info("✅ Credenciais obtidas do Secret Manager com sucesso")
    return credentials

def get_credentials_from_secret():
    """
    Função original mantida para compatibilidade total.
    """
    secret_name = os.getenv("GOOGLE_SECRET_NAME")
    if not secret_name:
        raise RuntimeError("GOOGLE_SECRET_NAME não definido no ambiente")
    return get_credentials_from_secret_manager(secret_name)