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
        # Check for "You've been removed" message
        removed_selectors = [
            "text='Você foi removido desta reunião'", # Portuguese
            "text='You have been removed from this meeting'" # English
        ]
        for selector in removed_selectors:
            if page.is_visible(selector, timeout=1000): # Quick check
                print("❌ Bot foi removido da reunião.")
                return True

        # Check for "Meeting has ended" or similar messages
        ended_selectors = [
            "text='As reuniões são apenas uma de nossas ferramentas.'", # Portuguese generic exit screen
            "text='This meeting has ended'", # English
            "text='A reunião terminou'" # Portuguese
        ]
        for selector in ended_selectors:
            if page.is_visible(selector, timeout=1000):
                print("❌ Reunião encerrada (ou tela de saída detectada).")
                return True
        
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
            headless=False, 
            args=[
                "--use-fake-ui-for-media-stream",
                # "--mute-audio", # REMOVED THIS LINE - LIKELY CAUSE OF MUTE AUDIO
                "--disable-infobars",
                "--no-sandbox", 
                "--disable-dev-shm-usage"
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720}, 
            locale="pt-BR" 
        )
        context.grant_permissions(["microphone", "camera"])
        page = context.new_page()

        yield {"event": "opening_browser"}
        tirar_screenshot_e_upload(page, "opening_browser")

        yield {"event": "navigating", "url": LINK}
        page.goto(LINK, timeout=90000, wait_until="domcontentloaded")
        tirar_screenshot_e_upload(page, "navigated")

        yield {"event": "filling_name", "name": NOME_USUARIO}
        page.wait_for_selector('[data-tid="prejoin-display-name-input"]', timeout=60000)
        page.fill('[data-tid="prejoin-display-name-input"]', NOME_USUARIO)
        tirar_screenshot_e_upload(page, "after_filling_name")

        # --- Handle "Continue without audio/video" pop-up ---
        yield {"event": "checking_audio_video_prompt_explicitly"}
        popup_button_selectors = [
            ('button:has-text("Continuar sem áudio ou vídeo")', "Continuar sem áudio ou vídeo (Portuguese)"),
            ('button:has-text("Continue without audio or video")', "Continue without audio or video (English)")
        ]
        popup_clicked_successfully = False
        for selector, description in popup_button_selectors:
            try:
                yield {"event": "attempting_to_find_audio_video_popup_button", "description": description}
                if page.is_visible(selector, timeout=15000): 
                    yield {"event": "audio_video_popup_button_found", "description": description}
                    page.click(selector, timeout=10000)
                    yield {"event": "clicked_audio_video_popup_button", "description": description}
                    tirar_screenshot_e_upload(page, f"after_clicking_audio_video_popup_{description.replace(' ', '_').lower()}")
                    page.wait_for_timeout(2000) 
                    popup_clicked_successfully = True
                    break 
                else:
                    yield {"event": "audio_video_popup_button_not_visible_within_timeout", "description": description}
            except PlaywrightTimeoutError as pte_popup:
                yield {"event": "audio_video_popup_button_timeout_exception", "description": description, "detail": str(pte_popup)}
                tirar_screenshot_e_upload(page, f"error_timeout_audio_video_popup_{description.replace(' ', '_').lower()}")
            except Exception as e_popup:
                yield {"event": "audio_video_popup_handling_error", "description": description, "detail": str(e_popup)}
                tirar_screenshot_e_upload(page, f"error_handling_audio_video_popup_{description.replace(' ', '_').lower()}")
        
        if not popup_clicked_successfully:
            yield {"event": "audio_video_popup_not_handled_or_not_found"}
            tirar_screenshot_e_upload(page, "audio_video_popup_not_handled")
        # --- End of pop-up handling ---

        yield {"event": "waiting_for_join_button"}
        join_now_button_clicked = False
        
        join_button_selectors = [
            ('button:has-text("Ingressar agora")', "Ingressar agora (Portuguese)"),
            ('button:has-text("Join now")', "Join now (English)")
        ]

        for selector, description in join_button_selectors:
            try:
                full_selector = f"{selector}:not([disabled])"
                yield {"event": "attempting_join_button", "selector_description": description, "selector": full_selector}
                page.wait_for_selector(full_selector, timeout=45000) 
                page.click(full_selector, timeout=15000) 
                join_now_button_clicked = True
                yield {"event": "clicked_join_button", "selector_used": description}
                tirar_screenshot_e_upload(page, f"after_clicking_join_button_{description.replace(' ', '_').lower()}")
                break 
            except PlaywrightTimeoutError as e_join_timeout:
                yield {"event": "join_button_attempt_timeout", "selector_description": description, "error_detail": str(e_join_timeout)}
                tirar_screenshot_e_upload(page, f"error_timeout_join_button_{description.replace(' ', '_').lower()}")
            except Exception as e_join:
                yield {"event": "join_button_attempt_failed", "selector_description": description, "error_detail": str(e_join)}
                tirar_screenshot_e_upload(page, f"error_failed_join_button_{description.replace(' ', '_').lower()}")
        
        if not join_now_button_clicked:
            error_message = "Failed to find or click any suitable 'Join' button after trying all options."
            yield {"event": "error", "type": "join_button_error", "detail": error_message}
            tirar_screenshot_e_upload(page, "error_all_join_buttons_failed")
            return

        yield {"event": "waiting_for_organizer_permission"}
        lobby_message_selectors = [
            "text='Oi, MarIA! Aguarde até que o organizador permita que você entre.'", # PT
            "text='Hi, MarIA! Waiting for the host to let you in.'" # EN (example)
        ]
        
        in_lobby_or_failed_to_join = True
        try:
            lobby_message_is_currently_visible = False
            visible_lobby_selector = None
            for sel in lobby_message_selectors:
                if page.is_visible(sel, timeout=5000): 
                    lobby_message_is_currently_visible = True
                    visible_lobby_selector = sel
                    yield {"event": "lobby_message_detected", "selector": sel}
                    tirar_screenshot_e_upload(page, "lobby_message_detected")
                    break
            
            if lobby_message_is_currently_visible and visible_lobby_selector:
                yield {"event": "waiting_for_lobby_message_to_disappear", "selector": visible_lobby_selector}
                page.wait_for_selector(visible_lobby_selector, state="hidden", timeout=300000) 
                yield {"event": "lobby_message_disappeared_or_timed_out"}
                in_lobby_or_failed_to_join = False 
            else:
                yield {"event": "no_immediate_lobby_message_checking_meeting_state"}
                page.wait_for_timeout(15000) 
                
                still_in_lobby_after_wait = False
                for sel in lobby_message_selectors:
                    if page.is_visible(sel, timeout=5000):
                        yield {"event": "lobby_message_appeared_late", "selector": sel}
                        tirar_screenshot_e_upload(page, "lobby_message_appeared_late")
                        still_in_lobby_after_wait = True 
                        break
                if not still_in_lobby_after_wait:
                     in_lobby_or_failed_to_join = False

            if in_lobby_or_failed_to_join and not page.is_closed():
                 yield {"event": "lobby_status_uncertain_proceeding_to_record"}
                 tirar_screenshot_e_upload(page, "lobby_status_uncertain")

        except PlaywrightTimeoutError as pte_lobby: 
            yield {"event": "error", "type": "lobby_timeout", "detail": f"Timed out waiting for lobby message to change state: {str(pte_lobby)}"}
            tirar_screenshot_e_upload(page, "error_lobby_timeout")
        except Exception as e_lobby:
            yield {"event": "error", "type": "lobby_error", "detail": f"Error during lobby check: {str(e_lobby)}"}
            tirar_screenshot_e_upload(page, "error_lobby_exception")
            return 
            
        yield {"event": "assumed_joined_meeting_or_past_lobby"}
        tirar_screenshot_e_upload(page, "after_lobby_or_joined")

        time.sleep(10) 
        
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
            
            if page.is_closed():
                yield {"event": "error", "type": "page_closed_unexpectedly", "detail": "Browser page was closed during recording."}
                # Screenshot might fail if page is already closed, but attempt it.
                try:
                    if page and not page.is_closed(): # Re-check, though likely closed
                         tirar_screenshot_e_upload(page, "error_page_closed_during_recording")
                except Exception:
                    print("Could not take screenshot, page was already closed.")
                break

            if verificar_condicoes_encerramento(page): 
                yield {"event": "auto_stopped_conditions_met", "stage": "recording"}
                tirar_screenshot_e_upload(page, "conditions_met_for_stop")
                break
            
            if proc.poll() is not None: 
                yield {"event": "error", "type": "ffmpeg_terminated_unexpectedly", "detail": f"FFmpeg process exited with code {proc.returncode}"}
                if page and not page.is_closed(): tirar_screenshot_e_upload(page, "error_ffmpeg_terminated")
                return 

            yield {"event": "recording", "elapsed": int(time.time() - inicio_gravacao_ts)}
            time.sleep(5) 

    except PlaywrightTimeoutError as pte:
        error_message = f"Playwright Timeout Error: {str(pte)}"
        yield {"event": "error", "type": "playwright_timeout_main", "detail": error_message, "traceback": traceback.format_exc()}
        if page and not page.is_closed(): tirar_screenshot_e_upload(page, "error_playwright_timeout_main")
        return
    except Exception as e:
        error_message = f"An unexpected error occurred: {str(e)}"
        yield {"event": "error", "type": "unexpected_error_main", "detail": error_message, "traceback": traceback.format_exc()}
        if page and not page.is_closed(): tirar_screenshot_e_upload(page, "error_unexpected_main")
        return
    finally:
        ffmpeg_exit_code = None
        if proc: 
            if proc.poll() is None: 
                print("Terminating FFmpeg process...")
                proc.terminate()
                try:
                    proc.wait(timeout=10) 
                    ffmpeg_exit_code = proc.returncode
                    print(f"FFmpeg terminated with code: {ffmpeg_exit_code}")
                except subprocess.TimeoutExpired:
                    print("FFmpeg did not terminate gracefully, killing.")
                    proc.kill()
                    proc.wait()
                    ffmpeg_exit_code = proc.returncode # Capture exit code after kill
                    print(f"FFmpeg killed, exit code: {ffmpeg_exit_code}")
                except Exception as e_proc_term:
                    print(f"Error during FFmpeg termination: {e_proc_term}")
            else: 
                 ffmpeg_exit_code = proc.returncode
                 print(f"FFmpeg process already terminated with code: {ffmpeg_exit_code} before explicit stop.")
            yield {"event": "recording_process_handled", "ffmpeg_exit_code": ffmpeg_exit_code}

        if page and not page.is_closed():
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

    # Ensure ffmpeg_exit_code is defined for the conditions below
    # It would be None if proc was None (i.e., FFmpeg never started)
    current_ffmpeg_exit_code = ffmpeg_exit_code if proc else None

    if os.path.exists(nome_arquivo):
        if current_ffmpeg_exit_code == 0 or (current_ffmpeg_exit_code is not None and current_ffmpeg_exit_code != 0 and stop_event.is_set()): # FFmpeg exited cleanly OR was stopped by user (non-zero code is expected)
            yield {"event": "upload_start", "file": nome_arquivo}
            try:
                public_url, gs_uri = enviar_para_gcs(nome_arquivo)
                yield {
                    "event": "completed",
                    "file": nome_arquivo,
                    "public_url": public_url,
                    "gs_uri": gs_uri,
                    "ffmpeg_exit_code": current_ffmpeg_exit_code
                }
            except Exception as e_upload:
                yield {"event": "error", "type": "upload_error", "detail": f"Failed to upload {nome_arquivo}: {str(e_upload)}", "ffmpeg_exit_code": current_ffmpeg_exit_code}
        elif current_ffmpeg_exit_code is not None and current_ffmpeg_exit_code != 0: # FFmpeg errored and file exists
            yield {"event": "error", "type": "ffmpeg_error_with_file", "detail": f"FFmpeg process exited with code {current_ffmpeg_exit_code}, but a file {nome_arquivo} exists (may be incomplete). Uploading anyway."}
            try:
                public_url, gs_uri = enviar_para_gcs(nome_arquivo)
                yield {
                    "event": "completed_with_ffmpeg_error",
                    "file": nome_arquivo,
                    "public_url": public_url,
                    "gs_uri": gs_uri,
                    "ffmpeg_exit_code": current_ffmpeg_exit_code
                }
            except Exception as e_upload_err:
                yield {"event": "error", "type": "upload_error_after_ffmpeg_error", "detail": f"Failed to upload {nome_arquivo} (after FFmpeg error {current_ffmpeg_exit_code}): {str(e_upload_err)}"}
        else: # File exists but FFmpeg process info is unclear (e.g. proc is None but file exists - unusual)
             yield {"event": "error", "type": "file_exists_ffmpeg_status_unclear", "detail": f"File {nome_arquivo} exists, but FFmpeg status is unclear (exit code: {current_ffmpeg_exit_code}). Attempting upload."}
             try:
                public_url, gs_uri = enviar_para_gcs(nome_arquivo)
                yield {
                    "event": "completed_with_ffmpeg_status_unclear",
                    "file": nome_arquivo,
                    "public_url": public_url,
                    "gs_uri": gs_uri,
                    "ffmpeg_exit_code": current_ffmpeg_exit_code
                }
             except Exception as e_upload_unclear:
                yield {"event": "error", "type": "upload_error_ffmpeg_status_unclear", "detail": f"Failed to upload {nome_arquivo} (FFmpeg status unclear): {str(e_upload_unclear)}"}

    elif not os.path.exists(nome_arquivo):
        if proc is None: # FFmpeg never started, and no file
            yield {"event": "process_ended_before_recording_file_creation", "detail": f"Recording file {nome_arquivo} was not created, FFmpeg likely not started."}
        else: # FFmpeg started but no file created
             yield {"event": "error", "type": "file_not_found_after_ffmpeg", "detail": f"Recording file {nome_arquivo} not found after FFmpeg process. FFmpeg might have failed (exit code: {current_ffmpeg_exit_code})."}