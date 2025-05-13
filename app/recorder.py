import subprocess
import time
import tempfile
import shutil
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from app.uploader import enviar_para_gcs
import threading
import os
import traceback

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

def tirar_screenshot_e_upload(page, etapa):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    nome = f"screenshot_{etapa}_{ts}.png"
    try:
        page.screenshot(path=nome, timeout=15000) # Added timeout for screenshot
        print(f"📸 Screenshot salva: {nome}")
        public_url, _ = enviar_para_gcs(nome, destino="screenshot-logs")
        print(f"✅ Screenshot enviada para GCS: {public_url}")
    except Exception as e:
        print(f"❌ Falha ao tirar/enviar screenshot '{nome}' na etapa '{etapa}': {e}")

def verificar_condicoes_encerramento(page):
    try:
        if page.is_visible("text='Você foi removido desta reunião'", timeout=5000):
            print("❌ Bot foi removido da reunião.")
            return True
        if page.is_visible("text='As reuniões são apenas uma de nossas ferramentas.'", timeout=5000): # This message might be generic
            print("❌ Reunião encerrada para todos (ou tela de saída detectada).")
            return True
        # Consider more robust checks for meeting end, e.g., lack of participants or specific UI changes.
        # The participant count check can be unreliable if the selector changes or isn't always present.
        # participantes_locator = page.locator('[data-tid="toolbar-item-badge"]')
        # if participantes_locator.is_visible(timeout=5000) and participantes_locator.inner_text(timeout=5000) == "1":
        #     print("❌ Bot está sozinho na reunião.")
        #     return True
    except Exception as e:
        print(f"⚠️ Erro ao verificar condições de encerramento: {e}")
    return False

def gravar_reuniao_stream(link_reuniao_original: str, stop_event: threading.Event):
    nome_arquivo = f"gravacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    yield {"event": "start_entry", "detail": "Gerando link anônimo"}
    LINK = gerar_link_anonimo_direto(link_reuniao_original)

    playwright_instance = None
    browser = None
    context = None
    page = None
    proc = None

    try:
        playwright_instance = sync_playwright().start()
        browser = playwright_instance.chromium.launch(
            headless=False, # Should work with Xvfb as per run.sh
            args=[
                "--use-fake-ui-for-media-stream",
                "--mute-audio",
                "--disable-infobars",
                "--no-sandbox", # Often needed in Docker/Linux environments
                "--disable-dev-shm-usage" # Often needed in Docker/Linux environments
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720}, # Matches Xvfb screen in run.sh
            locale="pt-BR" # Set locale if Teams UI might change language
        )
        context.grant_permissions(["microphone", "camera"])
        page = context.new_page()

        yield {"event": "opening_browser"}
        tirar_screenshot_e_upload(page, "opening_browser")

        yield {"event": "navigating", "url": LINK}
        page.goto(LINK, timeout=90000, wait_until="domcontentloaded") # Increased timeout, wait_until
        tirar_screenshot_e_upload(page, "navigated")

        yield {"event": "filling_name", "name": NOME_USUARIO}
        page.wait_for_selector('[data-tid="prejoin-display-name-input"]', timeout=60000)
        page.fill('[data-tid="prejoin-display-name-input"]', NOME_USUARIO)
        tirar_screenshot_e_upload(page, "after_filling_name")

        yield {"event": "checking_audio_video_prompt"}
        continue_button_selector = 'button:has-text("Continue without audio or video")'
        try:
            if page.is_visible(continue_button_selector, timeout=20000): # Check for 20s
                page.click(continue_button_selector, timeout=10000)
                yield {"event": "clicked_continue_without_audio_video"}
                tirar_screenshot_e_upload(page, "after_audio_video_prompt")
            else:
                yield {"event": "audio_video_prompt_not_found_or_not_visible"}
                tirar_screenshot_e_upload(page, "audio_video_prompt_not_visible")
        except PlaywrightTimeoutError:
            yield {"event": "audio_video_prompt_timeout", "detail": "Timeout clicking 'Continue without audio or video'"}
            tirar_screenshot_e_upload(page, "error_timeout_audio_video_prompt")
        except Exception as e_prompt:
            yield {"event": "audio_video_prompt_handling_error", "detail": str(e_prompt)}
            tirar_screenshot_e_upload(page, "error_audio_video_prompt")

        yield {"event": "waiting_for_join_button"}
        join_now_button_clicked = False
        # Using selectors that target data attributes if available is more robust than text.
        # For now, using text-based selectors as per user's info.
        selectors_to_try = [
            ('button:has-text("Join now")', "Join now (English)"), # From screenshot
            ('button:has-text("Ingressar agora")', "Ingressar agora (Portuguese)") # From previous logs
        ]

        for selector, description in selectors_to_try:
            try:
                # Ensure the button is not disabled: :not([disabled])
                full_selector = f"{selector}:not([disabled])"
                yield {"event": "attempting_join_button", "selector_description": description, "selector": full_selector}
                page.wait_for_selector(full_selector, timeout=45000) # Wait up to 45s for the button to be active
                page.click(full_selector, timeout=15000) # Click timeout
                join_now_button_clicked = True
                yield {"event": "clicked_join_button", "selector_used": description}
                tirar_screenshot_e_upload(page, f"after_clicking_join_button_{description.replace(' ', '_')}")
                break 
            except PlaywrightTimeoutError as e_join_timeout:
                yield {"event": "join_button_attempt_timeout", "selector_description": description, "error": str(e_join_timeout)}
                tirar_screenshot_e_upload(page, f"error_timeout_join_button_{description.replace(' ', '_')}")
            except Exception as e_join:
                yield {"event": "join_button_attempt_failed", "selector_description": description, "error": str(e_join)}
                tirar_screenshot_e_upload(page, f"error_failed_join_button_{description.replace(' ', '_')}")
        
        if not join_now_button_clicked:
            error_message = "Failed to find or click any suitable 'Join' button after trying all options."
            yield {"event": "error", "type": "join_button_error", "detail": error_message}
            tirar_screenshot_e_upload(page, "error_all_join_buttons_failed")
            return

        yield {"event": "waiting_for_organizer_permission"}
        waiting_message_selector_PT = "text='Oi, MarIA! Aguarde até que o organizador permita que você entre.'"
        # Add English version if known, e.g., "text='Hi MarIA! Waiting for the host to let you in.'"
        
        try:
            # Wait for the waiting message to disappear OR for a known element inside the meeting to appear.
            # This example waits for the Portuguese message to disappear.
            page.wait_for_selector(waiting_message_selector_PT, state="hidden", timeout=300000) # Wait up to 5 minutes
            yield {"event": "organizer_permission_granted_or_message_gone"}
        except PlaywrightTimeoutError:
            yield {"event": "lobby_timeout_extended_wait", "detail": "Timed out waiting for lobby message to disappear."}
            tirar_screenshot_e_upload(page, "error_lobby_timeout")
            # Fallback to periodic check if the above times out
            start_lobby_wait = time.time()
            max_lobby_wait_fallback = 180  # Additional 3 minutes
            in_lobby = True
            while in_lobby and (time.time() - start_lobby_wait) < max_lobby_wait_fallback:
                if stop_event.is_set():
                    yield {"event": "stopped_by_user", "stage": "lobby_fallback"}
                    return
                try:
                    if not page.is_visible(waiting_message_selector_PT, timeout=5000):
                        in_lobby = False
                        yield {"event": "exited_lobby_fallback_check"}
                        break
                except Exception: # If is_visible throws error (e.g. page closed), assume exited or error
                    in_lobby = False
                    yield {"event": "exited_lobby_fallback_check_exception"}
                    break
                yield {"event": "in_lobby_fallback_check", "detail": "Aguardando permissão do organizador (fallback)"}
                time.sleep(5)
            if in_lobby: # Still in lobby after fallback
                yield {"event": "error", "type": "lobby_timeout_final", "detail": "Timed out waiting in lobby after fallback."}
                tirar_screenshot_e_upload(page, "error_lobby_timeout_final")
                return
        except Exception as e_lobby:
            yield {"event": "error", "type": "lobby_error", "detail": f"Error waiting in lobby: {str(e_lobby)}"}
            tirar_screenshot_e_upload(page, "error_lobby_exception")
            return
            
        yield {"event": "joined_meeting_successfully_or_past_lobby"}
        tirar_screenshot_e_upload(page, "after_lobby_or_joined")

        time.sleep(10) # Settling time after joining
        
        yield {"event": "recording_starting_ffmpeg", "file": nome_arquivo}
        tirar_screenshot_e_upload(page, "before_ffmpeg_start")
        proc = iniciar_gravacao(nome_arquivo)
        inicio_gravacao_ts = time.time()
        yield {"event": "recording_started_ffmpeg_process_launched"}


        while True:
            if stop_event.is_set():
                yield {"event": "stopped_by_user", "stage": "recording"}
                break
            if (time.time() - inicio_gravacao_ts) > DURACAO_MAXIMA:
                yield {"event": "auto_stopped_max_duration", "stage": "recording"}
                break
            if verificar_condicoes_encerramento(page): # This check can be slow
                yield {"event": "auto_stopped_conditions_met", "stage": "recording"}
                tirar_screenshot_e_upload(page, "conditions_met_for_stop")
                break
            
            # Check if ffmpeg process is still running
            if proc.poll() is not None: # Process has terminated
                yield {"event": "error", "type": "ffmpeg_terminated_unexpectedly", "detail": f"FFmpeg process exited with code {proc.returncode}"}
                tirar_screenshot_e_upload(page, "error_ffmpeg_terminated")
                # Attempt to get FFmpeg logs if possible (more advanced)
                return # Stop if FFmpeg dies

            yield {"event": "recording", "elapsed": int(time.time() - inicio_gravacao_ts)}
            time.sleep(5) # Check conditions every 5 seconds

    except PlaywrightTimeoutError as pte:
        error_message = f"Playwright Timeout Error: {str(pte)}"
        yield {"event": "error", "type": "playwright_timeout_main", "detail": error_message, "traceback": traceback.format_exc()}
        if page: tirar_screenshot_e_upload(page, "error_playwright_timeout_main")
        return
    except Exception as e:
        error_message = f"An unexpected error occurred: {str(e)}"
        yield {"event": "error", "type": "unexpected_error_main", "detail": error_message, "traceback": traceback.format_exc()}
        if page: tirar_screenshot_e_upload(page, "error_unexpected_main")
        return
    finally:
        if proc: # Terminate FFmpeg if it's running
            if proc.poll() is None: # if process is still running
                print("Terminating FFmpeg process...")
                proc.terminate()
                try:
                    proc.wait(timeout=10) # Wait for graceful termination
                    print(f"FFmpeg terminated with code: {proc.returncode}")
                except subprocess.TimeoutExpired:
                    print("FFmpeg did not terminate gracefully, killing.")
                    proc.kill()
                    proc.wait()
                    print("FFmpeg killed.")
                except Exception as e_proc_term:
                    print(f"Error during FFmpeg termination: {e_proc_term}")
            else: # Process already terminated
                 print(f"FFmpeg process already terminated with code: {proc.returncode} before explicit stop.")
            yield {"event": "recording_process_handled"}

        if page:
            try:
                tirar_screenshot_e_upload(page, "before_browser_close")
            except Exception as e_screenshot_final:
                 print(f"Failed to take final screenshot: {e_screenshot_final}")
        if context:
            try:
                context.close()
            except Exception as e: print(f"Error closing context: {e}")
        if browser:
            try:
                browser.close()
            except Exception as e: print(f"Error closing browser: {e}")
        if playwright_instance:
            try:
                playwright_instance.stop()
            except Exception as e: print(f"Error stopping Playwright: {e}")
        yield {"event": "browser_resources_closed"}

    # Uploading logic (only if proc was started and nome_arquivo exists)
    if os.path.exists(nome_arquivo) and proc is not None: # Check if file was actually created
        yield {"event": "upload_start", "file": nome_arquivo}
        try:
            public_url, gs_uri = enviar_para_gcs(nome_arquivo)
            yield {
                "event": "completed",
                "file": nome_arquivo,
                "public_url": public_url,
                "gs_uri": gs_uri
            }
        except Exception as e_upload:
            yield {"event": "error", "type": "upload_error", "detail": f"Failed to upload {nome_arquivo}: {str(e_upload)}"}
    elif proc is None and not os.path.exists(nome_arquivo):
        # This means we likely errored out before even attempting to record
        yield {"event": "process_ended_before_recording_file_creation", "detail": f"Recording file {nome_arquivo} was not created."}
    elif proc is not None and not os.path.exists(nome_arquivo):
        # FFmpeg ran but didn't create the file
        yield {"event": "error", "type": "file_not_found_after_ffmpeg", "detail": f"Recording file {nome_arquivo} not found after FFmpeg process. FFmpeg might have failed to write."}