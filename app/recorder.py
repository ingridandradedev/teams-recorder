import subprocess
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from app.uploader import enviar_para_gcs

NOME_USUARIO = "GravadorBot"
DURACAO_MAXIMA = 60 * 10  # segundos
DISPOSITIVO_AUDIO = "default"

def gerar_link_anonimo_direto(link_original):
    base = "https://teams.microsoft.com"
    path = link_original.replace(base, "")
    final_url = f"{base}/v2/?meetingjoin=true#{path}"
    if "anon=true" not in final_url:
        final_url += "&anon=true"
    if "deeplinkId=" not in final_url:
        final_url += "&deeplinkId=joinweb"
    return final_url

def iniciar_gravacao(nome_arquivo):
    print(f"🎙️ Iniciando gravação com FFmpeg: {nome_arquivo}")
    comando = [
        "ffmpeg",
        "-y",
        "-f", "pulse",
        "-i", DISPOSITIVO_AUDIO,
        "-acodec", "libmp3lame",
        nome_arquivo
    ]
    return subprocess.Popen(comando)

def tirar_screenshot(page, etapa):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    screenshot_path = f"screenshot_{etapa}_{timestamp}.png"
    page.screenshot(path=screenshot_path)
    print(f"📸 Screenshot salva: {screenshot_path}")

def gravar_reuniao(link_reuniao_original):
    print("📡 Iniciando processo de gravação da reunião...")
    LINK_REUNIAO = gerar_link_anonimo_direto(link_reuniao_original)
    nome_arquivo = f"gravacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"

    try:
        with sync_playwright() as p:
            print("🌐 Abrindo navegador...")
            browser = p.chromium.launch(headless=False, args=["--use-fake-ui-for-media-stream"])
            print("✅ Navegador iniciado.")
            
            # Configurando o idioma para português
            context = browser.new_context(
                permissions=["microphone", "camera"],
                locale="pt-BR",  # Define o idioma como português do Brasil
                extra_http_headers={"Accept-Language": "pt-BR"}  # Cabeçalho para idioma
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
                print("✅ Ingressou na reunião.")
            except Exception as e:
                print(f"❌ Erro ao ingressar na reunião: {e}")
                tirar_screenshot(page, "erro_ingressar")

            time.sleep(10)
            processo_ffmpeg = iniciar_gravacao(nome_arquivo)

            tempo_inicio = time.time()
            while True:
                if page.is_closed():
                    print("🛑 A aba foi fechada. Encerrando gravação.")
                    break
                if (time.time() - tempo_inicio) > DURACAO_MAXIMA:
                    print("⏱️ Tempo máximo de gravação atingido.")
                    break
                try:
                    if not page.locator('div:has-text("Você foi removido da reunião")').is_hidden():
                        print("❌ Bot foi removido da reunião. Encerrando gravação.")
                        tirar_screenshot(page, "removido_reuniao")
                        break
                except Exception:
                    pass
                print("🎧 Gravando...")
                time.sleep(5)

            processo_ffmpeg.terminate()
            browser.close()
            print("📤 Enviando para o Google Cloud Storage...")
            url = enviar_para_gcs(nome_arquivo)
            return {"status": "finalizado", "arquivo": nome_arquivo, "url_bucket": url}
    except Exception as e:
        print(f"❌ Erro geral no processo: {e}")
        return {"status": "erro", "detalhes": str(e)}