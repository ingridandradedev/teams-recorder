import subprocess
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from app.uploader import enviar_para_gcs
import os

NOME_USUARIO = "MarIA"  # Alterado de "GravadorBot" para "MarIA"
DURACAO_MAXIMA = 10800  # 3 horas em segundos
DISPOSITIVO_AUDIO = "default"
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "/app/gravacoes")

def gerar_link_anonimo_direto(link_original):
    base = "https://teams.microsoft.com"
    path = link_original.replace(base, "")
    final_url = f"{base}/v2/?meetingjoin=true#{path}"
    if "anon=true" not in final_url:
        final_url += "&anon=true"
    if "deeplinkId=" not in final_url:
        final_url += "&deeplinkId=joinweb"
    return final_url

def iniciar_gravacao(caminho_arquivo):
    print(f"🎙️ Iniciando gravação com FFmpeg: {caminho_arquivo}")
    comando = [
        "ffmpeg",
        "-y",
        "-f", "pulse",
        "-i", DISPOSITIVO_AUDIO,
        "-acodec", "libmp3lame",
        caminho_arquivo
    ]
    return subprocess.Popen(comando)

def tirar_screenshot(page, etapa):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    screenshot_path = f"screenshot_{etapa}_{timestamp}.png"
    page.screenshot(path=screenshot_path)
    print(f"📸 Screenshot salva: {screenshot_path}")

def verificar_condicoes_encerramento(page):
    try:
        # Verifica se o bot foi removido da reunião
        if page.is_visible("text='Você foi removido desta reunião'"):
            print("❌ Bot foi removido da reunião.")
            return True

        # Verifica se a reunião foi encerrada para todos
        if page.is_visible("text='As reuniões são apenas uma de nossas ferramentas.'"):
            print("❌ Reunião encerrada para todos.")
            return True

        # Verifica se o bot está sozinho na reunião
        participantes = page.locator('[data-tid="toolbar-item-badge"]').inner_text()
        if participantes == "1":
            print("❌ Bot está sozinho na reunião.")
            return True

    except Exception as e:
        print(f"⚠️ Erro ao verificar condições de encerramento: {e}")

    return False

def gravar_reuniao(link_reuniao_original):
    print("📡 Iniciando processo de gravação da reunião. Versão 1.6")
    LINK_REUNIAO = gerar_link_anonimo_direto(link_reuniao_original)

    os.makedirs(RECORDINGS_DIR, exist_ok=True)

    filename = f"gravacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    full_path = os.path.join(RECORDINGS_DIR, filename)

    try:
        with sync_playwright() as p:
            print("🌐 Abrindo navegador...")
            browser = p.chromium.launch(headless=True, args=["--use-fake-ui-for-media-stream"])
            print("✅ Navegador iniciado.")
            
            # Configurando o idioma para português
            context = browser.new_context(
                permissions=["microphone", "camera"],
                locale="pt-BR",
                extra_http_headers={"Accept-Language": "pt-BR"}
            )
            page = context.new_page()

            print(f"🔗 Acessando o link: {LINK_REUNIAO}")
            page.goto(LINK_REUNIAO, timeout=60000)
            tirar_screenshot(page, "pagina_carregada")
            print("✅ Página carregada.")

            try:
                print("⌨️ Preenchendo nome...")
                page.wait_for_selector('[data-tid="prejoin-display-name-input"]', timeout=20000)
                page.fill('[data-tid="prejoin-display-name-input"]', NOME_USUARIO)
                tirar_screenshot(page, "nome_preenchido")
                print(f"✅ Nome preenchido como: {NOME_USUARIO}")
            except Exception as e:
                print(f"❌ Não conseguiu preencher nome: {e}")

            time.sleep(2)

            try:
                print("🚪 Clicando em 'Ingressar agora'...")
                page.wait_for_selector('button:has-text("Ingressar agora")', timeout=20000)
                page.click('button:has-text("Ingressar agora")', force=True)
                tirar_screenshot(page, "ingressar_agora")
                print("✅ Tentando ingressar na reunião.")
            except Exception as e:
                print(f"❌ Erro ao ingressar na reunião: {e}")
                tirar_screenshot(page, "erro_ingressar")

            # Aguarda até que o bot seja aceito na reunião
            print("⏳ Aguardando aceitação na reunião...")
            while True:
                if not page.is_visible("text='Oi, MarIA! Aguarde até que o organizador permita que você entre.'"):
                    print("✅ Bot aceito na reunião. Iniciando gravação.")
                    break
                print("⌛ Ainda aguardando aceitação...")
                time.sleep(5)

            time.sleep(10)
            processo_ffmpeg = iniciar_gravacao(full_path)

            tempo_inicio = time.time()
            while True:
                if page.is_closed():
                    print("🛑 A aba foi fechada. Encerrando gravação.")
                    break
                if (time.time() - tempo_inicio) > DURACAO_MAXIMA:
                    print("⏱️ Tempo máximo de gravação atingido. Encerrando gravação.")
                    break
                if verificar_condicoes_encerramento(page):
                    print("❌ Condição de encerramento atendida. Encerrando gravação.")
                    break
                print("🎧 Gravando...")
                time.sleep(5)

            processo_ffmpeg.terminate()
            browser.close()
            print("📤 Enviando para o Google Cloud Storage...")
            url = enviar_para_gcs(full_path, blob_name=filename)
            print(f"✅ Gravação enviada para o Google Cloud Storage: {url}")
            return {"status": "finalizado", "arquivo": filename, "url_bucket": url}
    except Exception as e:
        print(f"❌ Erro geral no processo: {e}")
        return {"status": "erro", "detalhes": str(e)}