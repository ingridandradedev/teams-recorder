import subprocess
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from app.uploader import enviar_para_gcs
import os

NOME_USUARIO = "MarIA"  # Nome do bot
DURACAO_MAXIMA = 10800  # 3 horas em segundos
DISPOSITIVO_AUDIO = "default"  # Dispositivo de áudio padrão

def detectar_monitor_pulse() -> str:
    """
    Retorna o primeiro source que termina em '.monitor' via pactl
    """
    res = subprocess.run(
        ["pactl", "list", "short", "sources"],
        capture_output=True, text=True, check=True
    )
    for linha in res.stdout.splitlines():
        # formato: idx    nome.do.source    …
        nome = linha.split()[1]
        if nome.endswith(".monitor"):
            return nome
    raise RuntimeError("Nenhum dispositivo '.monitor' encontrado em pactl")

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

def verificar_condicoes_encerramento(page):
    try:
        if page.is_visible("text='Você foi removido desta reunião'"):
            print("❌ Bot foi removido da reunião.")
            return True
        if page.is_visible("text='As reuniões são apenas uma de nossas ferramentas.'"):
            print("❌ Reunião encerrada para todos.")
            return True
        participantes = page.locator('[data-tid="toolbar-item-badge"]').inner_text()
        if participantes == "1":
            print("❌ Bot está sozinho na reunião.")
            return True
    except Exception as e:
        print(f"⚠️ Erro ao verificar condições de encerramento: {e}")
    return False

def gravar_reuniao(link_reuniao_original):
    print("📡 Iniciando processo de gravação da reunião. Versão corrigida")
    LINK_REUNIAO = gerar_link_anonimo_direto(link_reuniao_original)
    nome_arquivo = f"gravacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"

    try:
        with sync_playwright() as p:
            print("🌐 Abrindo navegador...")
            browser = p.chromium.launch(headless=False, args=["--use-fake-ui-for-media-stream"])
            print("✅ Navegador iniciado.")
            
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
            url = enviar_para_gcs(nome_arquivo)
            print(f"✅ Gravação enviada para o Google Cloud Storage: {url}")
            return {"status": "finalizado", "arquivo": nome_arquivo, "url_bucket": url}
    except Exception as e:
        print(f"❌ Erro geral no processo: {e}")
        return {"status": "erro", "detalhes": str(e)}