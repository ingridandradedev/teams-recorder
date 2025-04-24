import subprocess
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from app.uploader import enviar_para_gcs
import threading
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

def gravar_reuniao_stream(link_reuniao_original: str, stop_event: threading.Event):
    nome_arquivo = f"gravacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    yield {"event": "start_entry", "detail": "Gerando link anônimo"}
    LINK = gerar_link_anonimo_direto(link_reuniao_original)

    with sync_playwright() as p:
        yield {"event": "opening_browser"}
        browser = p.chromium.launch(headless=False, args=["--use-fake-ui-for-media-stream"])
        context = browser.new_context(permissions=["microphone", "camera"], locale="pt-BR")
        page = context.new_page()

        yield {"event": "navigating", "url": LINK}
        page.goto(LINK, timeout=60000)
        yield {"event": "page_loaded"}

        # preenche nome e entra
        yield {"event": "filling_name", "name": NOME_USUARIO}
        page.fill('[data-tid="prejoin-display-name-input"]', NOME_USUARIO)
        yield {"event": "requesting_entry"}
        page.click('button:has-text("Ingressar agora")', force=True)
        yield {"event": "joined"}

        time.sleep(10)
        yield {"event": "recording_started", "file": nome_arquivo}
        proc = iniciar_gravacao(nome_arquivo)
        inicio = time.time()

        # loop de gravação
        while True:
            if stop_event.is_set():
                yield {"event": "stopped_by_user"}
                break
            if (time.time() - inicio) > DURACAO_MAXIMA or verificar_condicoes_encerramento(page):
                yield {"event": "auto_stopped"}
                break
            yield {"event": "recording", "elapsed": int(time.time() - inicio)}
            time.sleep(5)

        proc.terminate()
        browser.close()

    yield {"event": "upload_start", "file": nome_arquivo}
    public_url, gs_uri = enviar_para_gcs(nome_arquivo)
    yield {
        "event": "completed",
        "file": nome_arquivo,
        "public_url": public_url,
        "gs_uri": gs_uri
    }