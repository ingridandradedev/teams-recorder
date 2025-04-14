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
    comando = [
        "ffmpeg",
        "-y",
        "-f", "pulse",
        "-i", DISPOSITIVO_AUDIO,
        "-acodec", "libmp3lame",
        nome_arquivo
    ]
    return subprocess.Popen(comando)

def gravar_reuniao(link_reuniao_original):
    print("📡 Gravando reunião...")
    LINK_REUNIAO = gerar_link_anonimo_direto(link_reuniao_original)
    nome_arquivo = f"gravacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--use-fake-ui-for-media-stream"])
        context = browser.new_context(permissions=["microphone", "camera"])
        page = context.new_page()
        page.goto(LINK_REUNIAO)

        try:
            page.wait_for_selector('[data-tid="prejoin-display-name-input"]', timeout=20000)
            page.fill('[data-tid="prejoin-display-name-input"]', NOME_USUARIO)
        except:
            print("❌ Nome não preenchido.")

        time.sleep(2)

        try:
            mic = page.locator('[aria-label^="Microfone"]')
            if mic.get_attribute("aria-pressed") == "true":
                mic.click()
        except:
            pass

        try:
            cam = page.locator('[aria-label^="Câmera"]')
            if cam.get_attribute("aria-pressed") == "true":
                cam.click()
        except:
            pass

        try:
            page.wait_for_selector('button:has-text("Ingressar agora")', timeout=15000)
            page.click('button:has-text("Ingressar agora")', force=True)
        except:
            pass

        time.sleep(10)
        processo_ffmpeg = iniciar_gravacao(nome_arquivo)
        print(f"🎙️ Gravando em {nome_arquivo}...")

        tempo_inicio = time.time()
        while True:
            if page.is_closed():
                print("🛑 Reunião encerrada.")
                break

            if (time.time() - tempo_inicio) > DURACAO_MAXIMA:
                print("⏱️ Tempo máximo atingido.")
                break

            time.sleep(5)

        processo_ffmpeg.terminate()
        browser.close()
        print("✅ Finalizando e enviando para o bucket...")
        url = enviar_para_gcs(nome_arquivo)
        return {"status": "finalizado", "arquivo": nome_arquivo, "url_bucket": url}
