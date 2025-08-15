import subprocess
import time
import tempfile
import shutil
import signal
import sys
import logging
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from app.uploader import enviar_para_gcs
import threading
import os
import traceback

# Configurar logging
logger = logging.getLogger(__name__)

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

def tentar_reingressar(page):
    try:
        if page.is_visible('button:has-text("Reingressar na chamada")', timeout=5000):
            print("🔄 Tentando reingressar na chamada...")
            page.click('button:has-text("Reingressar na chamada")')
            page.wait_for_timeout(3000)
            return True
    except Exception as e:
        print(f"Erro ao tentar reingressar: {e}")
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
    auto_stopped_conditions_were_met = False # Flag para indicar parada automática por condições

    try:
        playwright_instance = sync_playwright().start()
        browser = playwright_instance.chromium.launch(
            headless=False,
            args=[
                "--use-fake-ui-for-media-stream",
                "--disable-infobars",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--window-size=1280,720",
                "--start-maximized",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="pt-BR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.5735.90 Safari/537.36"
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
                
                # Aguardar um pouco antes de procurar o botão
                print(f"🔍 Procurando botão: {description}")
                page.wait_for_timeout(3000)  # Aguarda 3 segundos para a página estabilizar
                
                # Tentar encontrar o botão com timeout menor e melhor tratamento
                try:
                    page.wait_for_selector(full_selector, timeout=30000)  # Reduzido de 45s para 30s
                    print(f"✅ Botão encontrado: {description}")
                    
                    # Tirar screenshot antes de clicar
                    tirar_screenshot_e_upload(page, f"before_clicking_join_button_{description.replace(' ', '_').lower()}")
                    
                    page.click(full_selector, timeout=15000) 
                    join_now_button_clicked = True
                    yield {"event": "clicked_join_button", "selector_used": description}
                    tirar_screenshot_e_upload(page, f"after_clicking_join_button_{description.replace(' ', '_').lower()}")
                    print(f"✅ Botão clicado com sucesso: {description}")
                    break
                    
                except PlaywrightTimeoutError as timeout_error:
                    print(f"⏰ Timeout aguardando botão: {description} - {timeout_error}")
                    yield {"event": "join_button_wait_timeout", "selector_description": description, "timeout": "30s"}
                    tirar_screenshot_e_upload(page, f"timeout_waiting_join_button_{description.replace(' ', '_').lower()}")
                    continue  # Tenta o próximo seletor
                    
            except PlaywrightTimeoutError as e_join_timeout:
                print(f"⏰ Timeout no botão {description}: {e_join_timeout}")
                yield {"event": "join_button_attempt_timeout", "selector_description": description, "error_detail": str(e_join_timeout)}
                tirar_screenshot_e_upload(page, f"error_timeout_join_button_{description.replace(' ', '_').lower()}")
            except Exception as e_join:
                print(f"❌ Erro no botão {description}: {e_join}")
                yield {"event": "join_button_attempt_failed", "selector_description": description, "error_detail": str(e_join)}
                tirar_screenshot_e_upload(page, f"error_failed_join_button_{description.replace(' ', '_').lower()}")
        
        if not join_now_button_clicked:
            error_message = "Failed to find or click any suitable 'Join' button after trying all options."
            print(f"❌ {error_message}")
            yield {"event": "error", "type": "join_button_error", "detail": error_message}
            tirar_screenshot_e_upload(page, "error_all_join_buttons_failed")
            
            # Tentar seletores alternativos antes de desistir
            alternative_selectors = [
                ('button[data-tid="prejoin-join-button"]', "Botão join via data-tid"),
                ('button:has-text("Entrar")', "Botão Entrar"),
                ('button:has-text("Enter")', "Botão Enter"),
                ('[data-tid*="join"]', "Elemento com data-tid contendo join")
            ]
            
            yield {"event": "trying_alternative_join_selectors"}
            print("🔄 Tentando seletores alternativos...")
            
            for alt_selector, alt_description in alternative_selectors:
                try:
                    if page.is_visible(alt_selector, timeout=5000):
                        print(f"🎯 Encontrado seletor alternativo: {alt_description}")
                        yield {"event": "alternative_join_button_found", "selector": alt_description}
                        page.click(alt_selector, timeout=10000)
                        join_now_button_clicked = True
                        yield {"event": "clicked_alternative_join_button", "selector_used": alt_description}
                        tirar_screenshot_e_upload(page, f"after_clicking_alternative_join_{alt_description.replace(' ', '_').lower()}")
                        break
                except Exception as e_alt:
                    print(f"⚠️ Seletor alternativo falhou {alt_description}: {e_alt}")
                    continue
            
            if not join_now_button_clicked:
                # Última tentativa: procurar qualquer botão que possa ser de join
                yield {"event": "desperate_join_button_search"}
                print("🆘 Busca desesperada por botão de join...")
                try:
                    # Pegar todos os botões visíveis e tentar encontrar um que faça sentido
                    buttons = page.query_selector_all('button:visible')
                    for i, button in enumerate(buttons):
                        try:
                            text = button.inner_text().lower()
                            if any(word in text for word in ['join', 'ingressar', 'entrar', 'participar']):
                                print(f"🎯 Tentando botão com texto: {text}")
                                button.click(timeout=5000)
                                join_now_button_clicked = True
                                yield {"event": "clicked_desperate_join_button", "button_text": text}
                                tirar_screenshot_e_upload(page, f"after_desperate_join_click_{i}")
                                break
                        except Exception:
                            continue
                except Exception as e_desperate:
                    print(f"❌ Busca desesperada falhou: {e_desperate}")
            
            if not join_now_button_clicked:
                yield {"event": "error", "type": "complete_join_button_failure", "detail": "Completely failed to find and click any join button"}
                return

        yield {"event": "waiting_for_organizer_permission"}
        print("🚪 Verificando se está no lobby...")
        
        lobby_message_selectors = [
            "text='Oi, MarIA! Aguarde até que o organizador permita que você entre.'", # PT
            "text='Hi, MarIA! Waiting for the host to let you in.'", # EN
            "text='Aguarde até que o organizador permita que você entre'", # PT variação
            "text='Waiting for the host to let you in'", # EN variação
            "[data-tid*='lobby']", # Qualquer elemento com lobby no data-tid
            "text*='aguarde'", # Qualquer texto contendo aguarde
            "text*='waiting'" # Qualquer texto contendo waiting
        ]
        
        in_lobby_or_failed_to_join = True
        lobby_timeout_seconds = 300  # 5 minutos máximo no lobby
        
        try:
            lobby_message_is_currently_visible = False
            visible_lobby_selector = None
            
            # Verificação inicial do lobby com timeout menor
            print("🔍 Verificação inicial do lobby...")
            for sel in lobby_message_selectors:
                try:
                    if page.is_visible(sel, timeout=3000):  # 3 segundos por seletor
                        lobby_message_is_currently_visible = True
                        visible_lobby_selector = sel
                        print(f"🚪 Detectado no lobby com seletor: {sel}")
                        yield {"event": "lobby_message_detected", "selector": sel}
                        tirar_screenshot_e_upload(page, "lobby_message_detected")
                        break
                except Exception as e_lobby_check:
                    print(f"⚠️ Erro verificando seletor de lobby {sel}: {e_lobby_check}")
                    continue
            
            if lobby_message_is_currently_visible and visible_lobby_selector:
                print(f"⏳ Aguardando liberação do lobby (máximo {lobby_timeout_seconds}s)...")
                yield {"event": "waiting_for_lobby_message_to_disappear", "selector": visible_lobby_selector, "max_wait": lobby_timeout_seconds}
                
                try:
                    page.wait_for_selector(visible_lobby_selector, state="hidden", timeout=lobby_timeout_seconds * 1000)
                    print("✅ Liberado do lobby!")
                    yield {"event": "lobby_message_disappeared"}
                    in_lobby_or_failed_to_join = False
                except PlaywrightTimeoutError:
                    print(f"⏰ Timeout no lobby após {lobby_timeout_seconds}s")
                    yield {"event": "lobby_timeout", "waited_seconds": lobby_timeout_seconds}
                    tirar_screenshot_e_upload(page, "lobby_timeout")
                    # Continua mesmo com timeout, talvez esteja na reunião
                    in_lobby_or_failed_to_join = False
                    
            else:
                print("🔍 Não detectado no lobby inicialmente, aguardando para verificar...")
                yield {"event": "no_immediate_lobby_message_checking_meeting_state"}
                page.wait_for_timeout(10000)  # Aguarda 10 segundos
                
                # Segunda verificação após aguardar
                still_in_lobby_after_wait = False
                print("🔍 Segunda verificação do lobby...")
                for sel in lobby_message_selectors:
                    try:
                        if page.is_visible(sel, timeout=2000):
                            print(f"🚪 Lobby detectado na segunda verificação: {sel}")
                            yield {"event": "lobby_message_appeared_late", "selector": sel}
                            tirar_screenshot_e_upload(page, "lobby_message_appeared_late")
                            still_in_lobby_after_wait = True
                            
                            # Aguardar liberação com timeout menor
                            try:
                                page.wait_for_selector(sel, state="hidden", timeout=180000)  # 3 minutos
                                print("✅ Liberado do lobby na segunda tentativa!")
                                yield {"event": "lobby_released_second_check"}
                                in_lobby_or_failed_to_join = False
                            except PlaywrightTimeoutError:
                                print("⏰ Timeout na segunda verificação do lobby")
                                yield {"event": "lobby_timeout_second_check"}
                                in_lobby_or_failed_to_join = False  # Continua mesmo assim
                            break
                    except Exception as e_second_check:
                        print(f"⚠️ Erro na segunda verificação: {e_second_check}")
                        continue
                        
                if not still_in_lobby_after_wait:
                    print("✅ Não está no lobby, provavelmente na reunião")
                    in_lobby_or_failed_to_join = False

            if in_lobby_or_failed_to_join and not page.is_closed():
                print("⚠️ Status do lobby incerto, prosseguindo...")
                yield {"event": "lobby_status_uncertain_proceeding_to_record"}
                tirar_screenshot_e_upload(page, "lobby_status_uncertain")

        except PlaywrightTimeoutError as pte_lobby: 
            yield {"event": "error", "type": "lobby_timeout", "detail": f"Timed out waiting for lobby message to change state: {str(pte_lobby)}"}
            tirar_screenshot_e_upload(page, "error_lobby_timeout")
            # Não retorna aqui, pois podemos ainda estar na reunião
        except Exception as e_lobby:
            yield {"event": "error", "type": "lobby_error", "detail": f"Error during lobby check: {str(e_lobby)}"}
            tirar_screenshot_e_upload(page, "error_lobby_exception")
            return # Retorna se houver um erro inesperado no lobby
            
        yield {"event": "assumed_joined_meeting_or_past_lobby"}
        tirar_screenshot_e_upload(page, "after_lobby_or_joined")

        time.sleep(10) # Pequena pausa para estabilizar a entrada na reunião
        
        yield {"event": "recording_starting_ffmpeg", "file": nome_arquivo}
        tirar_screenshot_e_upload(page, "before_ffmpeg_start")
        proc = iniciar_gravacao(nome_arquivo)
        inicio_gravacao_ts = time.time()
        yield {"event": "recording_started_ffmpeg_process_launched"}

        while True:
            # Verificar primeiro se foi parado pelo usuário
            if stop_event.is_set():
                print("🛑 Parada solicitada pelo usuário")
                yield {"event": "stopped_by_user", "stage": "recording"}
                break
                
            # Verificar duração máxima
            if (time.time() - inicio_gravacao_ts) > DURACAO_MAXIMA:
                print(f"🛑 Duração máxima atingida: {DURACAO_MAXIMA}s")
                yield {"event": "auto_stopped_max_duration", "stage": "recording"}
                auto_stopped_conditions_were_met = True
                break
            
            # Verificar se a página foi fechada
            if page.is_closed():
                print("🛑 Página do browser foi fechada inesperadamente")
                yield {"event": "error", "type": "page_closed_unexpectedly", "detail": "Browser page was closed during recording."}
                try:
                    if page and not page.is_closed(): 
                         tirar_screenshot_e_upload(page, "error_page_closed_during_recording")
                except Exception:
                    print("Não foi possível tirar screenshot, página já estava fechada.")
                auto_stopped_conditions_were_met = True
                break

            # Verificar condições de encerramento da reunião
            if verificar_condicoes_encerramento(page): 
                print("🛑 Condições de encerramento detectadas")
                yield {"event": "auto_stopped_conditions_met", "stage": "recording"}
                auto_stopped_conditions_were_met = True
                tirar_screenshot_e_upload(page, "conditions_met_for_stop")
                break
            
            # Verificar se o FFmpeg terminou inesperadamente
            if proc.poll() is not None: 
                print(f"🛑 Processo FFmpeg terminou inesperadamente com código: {proc.returncode}")
                yield {"event": "error", "type": "ffmpeg_terminated_unexpectedly", "detail": f"FFmpeg process exited with code {proc.returncode}"}
                if page and not page.is_closed(): 
                    tirar_screenshot_e_upload(page, "error_ffmpeg_terminated")
                # Não definimos auto_stopped_conditions_were_met aqui, pois é um erro do FFmpeg
                break  # Sai do loop, mas não retorna (vai para finally)

            # Tentar reingressar se necessário
            if tentar_reingressar(page):
                print("🔄 Tentativa de reingresso realizada")
                yield {"event": "tentou_reingressar"}
                continue
            
            # Status normal da gravação
            elapsed_time = int(time.time() - inicio_gravacao_ts)
            yield {"event": "recording", "elapsed": elapsed_time}
            
            # Aguarda antes da próxima verificação
            time.sleep(5) 

    except PlaywrightTimeoutError as pte:
        error_message = f"Playwright Timeout Error: {str(pte)}"
        yield {"event": "error", "type": "playwright_timeout_main", "detail": error_message, "traceback": traceback.format_exc()}
        if page and not page.is_closed(): tirar_screenshot_e_upload(page, "error_playwright_timeout_main")
        auto_stopped_conditions_were_met = True # Timeout pode ser considerado uma condição de parada
        # Não retorna aqui, prossegue para o finally para tentar salvar o que foi gravado
    except Exception as e:
        error_message = f"An unexpected error occurred: {str(e)}"
        yield {"event": "error", "type": "unexpected_error_main", "detail": error_message, "traceback": traceback.format_exc()}
        if page and not page.is_closed(): tirar_screenshot_e_upload(page, "error_unexpected_main")
        auto_stopped_conditions_were_met = True # Erro inesperado também pode ser condição de parada
        # Não retorna aqui, prossegue para o finally
    finally:
        ffmpeg_exit_code = None
        if proc: 
            if proc.poll() is None: 
                print("🛑 Terminando processo FFmpeg...")
                yield {"event": "terminating_ffmpeg_process"}
                
                # Primeiro tenta SIGTERM (terminação graceful)
                try:
                    proc.terminate()
                    print("📤 SIGTERM enviado ao FFmpeg, aguardando finalização...")
                    proc.wait(timeout=10)  # Espera 10 segundos para terminação graceful
                    ffmpeg_exit_code = proc.returncode
                    print(f"✅ FFmpeg terminou graciosamente com código: {ffmpeg_exit_code}")
                except subprocess.TimeoutExpired:
                    print("⚠️ FFmpeg não terminou graciosamente em 10s, forçando terminação...")
                    proc.kill()  # Força terminação com SIGKILL
                    try:
                        proc.wait(timeout=5)  # Espera mais 5 segundos após SIGKILL
                        ffmpeg_exit_code = proc.returncode 
                        print(f"🔨 FFmpeg foi forçado a terminar com código: {ffmpeg_exit_code}")
                    except subprocess.TimeoutExpired:
                        print("❌ FFmpeg não respondeu nem ao SIGKILL!")
                        # Processo pode estar em estado zumbi
                        ffmpeg_exit_code = -9  # Código artificial para indicar kill forçado
                except Exception as e_proc_term:
                    print(f"❌ Erro durante terminação do FFmpeg: {e_proc_term}")
                    # Tenta kill como último recurso
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                        ffmpeg_exit_code = proc.returncode
                        print(f"🔨 FFmpeg foi morto como último recurso, código: {ffmpeg_exit_code}")
                    except:
                        print("❌ Falha completa na terminação do FFmpeg")
                        ffmpeg_exit_code = -1
            else: 
                 ffmpeg_exit_code = proc.returncode
                 print(f"✅ Processo FFmpeg já havia terminado com código: {ffmpeg_exit_code}")
            
            yield {"event": "recording_process_handled", "ffmpeg_exit_code": ffmpeg_exit_code}
        else:
            print("ℹ️ Nenhum processo FFmpeg para terminar")
            yield {"event": "no_ffmpeg_process_to_terminate"}

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

    current_ffmpeg_exit_code = ffmpeg_exit_code

    if os.path.exists(nome_arquivo):
        # Condição modificada: FFmpeg saiu limpo OU foi parado pelo usuário OU parado por condições automáticas
        if current_ffmpeg_exit_code == 0 or \
           (current_ffmpeg_exit_code is not None and current_ffmpeg_exit_code != 0 and (stop_event.is_set() or auto_stopped_conditions_were_met)):
            yield {"event": "upload_start", "file": nome_arquivo}
            try:
                public_url, gs_uri = enviar_para_gcs(nome_arquivo)
                yield {
                    "event": "completed", # Evento unificado para paradas "controladas"
                    "file": nome_arquivo,
                    "public_url": public_url,
                    "gs_uri": gs_uri,
                    "ffmpeg_exit_code": current_ffmpeg_exit_code,
                    "stopped_by_user": stop_event.is_set(),
                    "auto_stopped_conditions": auto_stopped_conditions_were_met
                }
            except Exception as e_upload:
                yield {"event": "error", "type": "upload_error_after_controlled_stop", "detail": f"Failed to upload {nome_arquivo}: {str(e_upload)}", "ffmpeg_exit_code": current_ffmpeg_exit_code}
        elif current_ffmpeg_exit_code is not None and current_ffmpeg_exit_code != 0: # Erro do FFmpeg (e não devido a parada de usuário/auto) e arquivo existe
            yield {"event": "error", "type": "ffmpeg_error_with_file", "detail": f"FFmpeg process exited with code {current_ffmpeg_exit_code}, but a file {nome_arquivo} exists (may be incomplete). Uploading anyway."}
            try:
                public_url, gs_uri = enviar_para_gcs(nome_arquivo)
                yield {
                    "event": "completed_with_ffmpeg_error", # Mantém para erros genuínos do FFmpeg
                    "file": nome_arquivo,
                    "public_url": public_url,
                    "gs_uri": gs_uri,
                    "ffmpeg_exit_code": current_ffmpeg_exit_code
                }
            except Exception as e_upload_err:
                yield {"event": "error", "type": "upload_error_after_ffmpeg_error", "detail": f"Failed to upload {nome_arquivo} (after FFmpeg error {current_ffmpeg_exit_code}): {str(e_upload_err)}"}
        else: # Arquivo existe mas status do FFmpeg não é claro (ex: proc é None mas arquivo existe - incomum, ou current_ffmpeg_exit_code é None)
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
        if proc is None and not auto_stopped_conditions_were_met and not stop_event.is_set(): # FFmpeg nunca iniciou, e não foi uma parada "controlada" antes do FFmpeg
            yield {"event": "process_ended_before_recording_file_creation", "detail": f"Recording file {nome_arquivo} was not created, FFmpeg likely not started or process ended before FFmpeg could start."}
        else: # FFmpeg iniciou (ou deveria ter iniciado) mas nenhum arquivo foi criado, ou foi uma parada controlada antes da criação do arquivo
             yield {"event": "error", "type": "file_not_found_after_process_end", "detail": f"Recording file {nome_arquivo} not found after process end. FFmpeg might have failed or was stopped before/during creation (exit code: {current_ffmpeg_exit_code}).", "stopped_by_user": stop_event.is_set(), "auto_stopped_conditions": auto_stopped_conditions_were_met}