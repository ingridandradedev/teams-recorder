from google.cloud import storage
import os
import logging
from app.gcp_secrets import get_credentials_robust, get_credentials_from_secret  # Importar ambas as funções

# Configurar logging
logger = logging.getLogger(__name__)

BUCKET_NAME = "projeto-maria-1-0-pecege"

def enviar_para_gcs(nome_arquivo: str, destino: str = "") -> tuple[str, str]:
    """
    Envia arquivo para Google Cloud Storage usando autenticação robusta.
    Se destino for ex.: "screenshot-logs", ele fará upload para 
    gs://…/screenshot-logs/nome_arquivo
    
    Args:
        nome_arquivo: Nome do arquivo local para upload
        destino: Pasta de destino no bucket (opcional)
    
    Returns:
        Tuple com (public_url, gs_uri)
    """
    logger.info("📤 Iniciando upload para o Google Cloud Storage...")

    try:
        blob_name = f"{destino.rstrip('/')}/{nome_arquivo}" if destino else nome_arquivo

        # Tentar obter credenciais usando o método robusto
        credentials = get_credentials_robust()
        
        if not credentials:
            # Fallback para o método original se o robusto falhar
            logger.warning("⚠️ Método robusto falhou, tentando método original...")
            try:
                credentials = get_credentials_from_secret()
                logger.info("✅ Credenciais obtidas via método original")
            except Exception as fallback_error:
                raise RuntimeError(
                    f"❌ Todos os métodos de autenticação falharam. "
                    f"Erro do método original: {fallback_error}. "
                    f"Verifique as seguintes variáveis de ambiente:\n"
                    f"- GOOGLE_CREDENTIALS_JSON (JSON completo das credenciais)\n"
                    f"- GOOGLE_SECRET_NAME (nome do secret no Secret Manager)\n"
                    f"- GOOGLE_APPLICATION_CREDENTIALS (caminho para arquivo de chave)\n"
                    f"Ou configure ADC se estiver no Google Cloud."
                )
        
        # Criar cliente do Storage com as credenciais obtidas
        storage_client = storage.Client(credentials=credentials)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(blob_name)

        # Verificar se o arquivo existe antes do upload
        if not os.path.exists(nome_arquivo):
            raise FileNotFoundError(f"Arquivo não encontrado: {nome_arquivo}")

        # Fazer o upload do arquivo
        logger.info(f"🔄 Fazendo upload do arquivo: {nome_arquivo} -> {blob_name}")
        blob.upload_from_filename(nome_arquivo)

        # Gerar URLs
        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"
        gs_uri = f"gs://{BUCKET_NAME}/{blob_name}"

        logger.info(f"✅ Upload concluído. URL pública: {public_url}, URI GS: {gs_uri}")
        return public_url, gs_uri
        
    except Exception as e:
        logger.error(f"❌ Erro durante o upload para o Google Cloud Storage: {e}")
        raise