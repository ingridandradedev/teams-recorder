import os
import sys
import asyncio
import subprocess
import signal
import time
import json
import logging
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from app.uploader import enviar_para_gcs

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
        partes = linha.split()
        if len(partes) >= 2 and partes[1].endswith('.monitor'):
            return partes[1]
    raise RuntimeError("Nenhum dispositivo '.monitor' encontrado em pactl")

def gerar_link_anonimo_direto(link_original):
    base = "https://teams.microsoft.com"
    path = link_original.replace(base, "")
    final_url = f"{base}/v2/?meetingjoin=true#{path}"
    if "anon=true" not in final_url:
        final_url += "&anon=true"
    if "deeplinkId=" not in final_url:
        final_url += "&deeplinkId=" + str(int(time.time() * 1000))
    return final_url

def iniciar_gravacao_ffmpeg(nome_arquivo):
    """Inicia o processo FFmpeg para gravação de áudio."""
    logger.info(f"🎙️ Iniciando gravação com FFmpeg: {nome_arquivo}")
    comando = [
        "ffmpeg",
        "-y",
        "-f", "pulse",
        "-i", DISPOSITIVO_AUDIO,
        "-acodec", "libmp3lame",
        nome_arquivo
    ]
    return subprocess.Popen(comando)

async def tirar_screenshot_e_upload_async(page, etapa):
    """Tira screenshot e faz upload de forma assíncrona."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    nome = f"screenshot_{etapa}_{ts}.png"
    try:
        await page.screenshot(path=nome, full_page=False)
        logger.info(f"📸 Screenshot salvo: {nome}")
        
        # Upload assíncrono em thread separada
        def upload_sync():
            try:
                enviar_para_gcs(nome, "screenshot-logs")
                if os.path.exists(nome):
                    os.remove(nome)
            except Exception as e:
                logger.error(f"❌ Erro no upload do screenshot: {e}")
        
        # Executar upload em thread para não bloquear
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, upload_sync)
        
    except Exception as e:
        logger.error(f"❌ Erro ao tirar screenshot: {e}")

async def verificar_condicoes_encerramento_async(page):
    """Verifica se a reunião terminou usando seletores assíncronos."""
    try:
        # Verificar se a reunião terminou
        selectors_fim = [
            'text="A reunião terminou"',
            'text="The meeting has ended"',
            'text="A chamada foi encerrada"',
            'text="Call ended"',
            '[data-tid="call-ended"]'
        ]
        
        for selector in selectors_fim:
            try:
                element = await page.wait_for_selector(selector, timeout=1000)
                if element:
                    logger.info(f"🏁 Reunião terminou - Selector: {selector}")
                    return True
            except PlaywrightTimeoutError:
                continue
                
    except Exception as e:
        logger.warning(f"⚠️ Erro ao verificar condições de encerramento: {e}")
    return False

async def tentar_reingressar_async(page):
    """Tenta reingressar na chamada de forma assíncrona."""
    try:
        reingressar_button = await page.wait_for_selector(
            'button:has-text("Reingressar na chamada")', 
            timeout=5000
        )
        if reingressar_button:
            logger.info("🔄 Tentando reingressar na chamada...")
            await reingressar_button.click()
            await page.wait_for_timeout(3000)
            return True
    except PlaywrightTimeoutError:
        pass
    except Exception as e:
        logger.error(f"❌ Erro ao tentar reingressar: {e}")
    return False

def terminar_ffmpeg_robusto(proc):
    """
    Termina o processo FFmpeg de forma robusta.
    Primeiro tenta SIGTERM (graceful), depois SIGKILL (forçado).
    """
    if not proc or proc.poll() is not None:
        logger.info("ℹ️ Processo FFmpeg já estava terminado")
        return proc.poll() if proc else 0

    logger.info(f"🛑 Terminando processo FFmpeg (PID: {proc.pid})...")
    
    try:
        # Primeiro: terminação graceful
        logger.info("📤 Enviando SIGTERM (terminação graceful)...")
        proc.terminate()
        
        # Aguardar até 10 segundos pela terminação graceful
        try:
            exit_code = proc.wait(timeout=10)
            logger.info(f"✅ FFmpeg terminou graciosamente (código: {exit_code})")
            return exit_code
        except subprocess.TimeoutExpired:
            logger.warning("⏰ Timeout na terminação graceful, forçando...")
            
            # Segundo: kill forçado
            logger.info("🔨 Enviando SIGKILL (terminação forçada)...")
            proc.kill()
            
            try:
                exit_code = proc.wait(timeout=5)
                logger.info(f"🔨 FFmpeg terminado forçadamente (código: {exit_code})")
                return exit_code
            except subprocess.TimeoutExpired:
                logger.error("❌ FFmpeg não respondeu nem ao SIGKILL!")
                return -1
                
    except ProcessLookupError:
        logger.info("ℹ️ Processo já havia terminado")
        return 0
    except Exception as e:
        logger.error(f"❌ Erro inesperado ao terminar FFmpeg: {e}")
        return -1

async def gravar_reuniao_stream_async(link_reuniao_original: str, stop_event: asyncio.Event):
    """
    Função principal de gravação usando Playwright Async API.
    Suporta múltiplas gravações simultâneas sem conflitos.
    """
    nome_arquivo = f"gravacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    yield {"event": "start_entry", "detail": "Gerando link anônimo"}
    
    LINK = gerar_link_anonimo_direto(link_reuniao_original)
    logger.info(f"🔗 Link gerado: {LINK}")

    playwright_instance = None
    browser = None
    context = None
    page = None
    proc = None
    auto_stopped_conditions_were_met = False
    ffmpeg_exit_code = None

    try:
        # Usar Playwright Async API
        playwright_instance = await async_playwright().start()
        browser = await playwright_instance.chromium.launch(
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
        
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="pt-BR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.5735.90 Safari/537.36"
        )
        
        await context.grant_permissions(["microphone", "camera"])
        page = await context.new_page()

        yield {"event": "opening_browser"}
        await tirar_screenshot_e_upload_async(page, "opening_browser")

        yield {"event": "navigating", "url": LINK}
        await page.goto(LINK, timeout=90000, wait_until="domcontentloaded")
        await tirar_screenshot_e_upload_async(page, "navigated")

        yield {"event": "filling_name", "name": NOME_USUARIO}
        await page.wait_for_selector('[data-tid="prejoin-display-name-input"]', timeout=60000)
        await page.fill('[data-tid="prejoin-display-name-input"]', NOME_USUARIO)

        yield {"event": "disabling_camera_mic"}
        # Desabilitar câmera e microfone
        try:
            await page.click('[data-tid="toggle-camera"]', timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("⚠️ Não conseguiu clicar no botão da câmera")

        try:
            await page.click('[data-tid="toggle-microphone"]', timeout=10000)
        except PlaywrightTimeoutError:
            logger.warning("⚠️ Não conseguiu clicar no botão do microfone")

        yield {"event": "clicking_continue"}
        await page.click('button:has-text("Continuar sem áudio ou vídeo")', timeout=30000)
        await tirar_screenshot_e_upload_async(page, "after_continue_click")

        # === CORREÇÃO DO TRAVAMENTO - MÚLTIPLOS SELETORES ===
        yield {"event": "waiting_join_button"}
        logger.info("🔍 Procurando botão 'Ingressar agora'...")
        await page.wait_for_timeout(3000)  # Aguardar página estabilizar
        await tirar_screenshot_e_upload_async(page, "before_join_search")

        # Seletores em ordem de prioridade
        join_selectors = [
            ('button:has-text("Ingressar agora")', "Ingressar agora (Portuguese)"),
            ('button:has-text("Join now")', "Join now (English)"),
            ('button[data-tid="prejoin-join-button"]', "Botão join via data-tid"),
            ('button:has-text("Entrar")', "Botão Entrar"),
            ('button:has-text("Enter")', "Botão Enter"),
            ('[data-tid*="join"]', "Elemento com data-tid contendo join")
        ]

        join_success = False
        for selector, description in join_selectors:
            try:
                logger.info(f"🔍 Procurando botão: {description}")
                element = await page.wait_for_selector(selector, timeout=30000)
                if element:
                    logger.info(f"✅ Botão encontrado: {description}")
                    await element.click()
                    logger.info(f"✅ Botão clicado com sucesso: {description}")
                    join_success = True
                    break
            except PlaywrightTimeoutError:
                logger.warning(f"⏰ Timeout aguardando botão: {description}")
                continue
            except Exception as e:
                logger.error(f"❌ Erro ao clicar no botão {description}: {e}")
                continue

        if not join_success:
            # Busca desesperada por qualquer botão relevante
            logger.warning("🆘 Tentando busca desesperada por botões...")
            try:
                buttons = await page.query_selector_all("button")
                for button in buttons:
                    try:
                        text = await button.inner_text()
                        if any(word in text.lower() for word in ["join", "ingressar", "entrar", "enter"]):
                            logger.info(f"🎯 Encontrado seletor alternativo: {text}")
                            await button.click()
                            join_success = True
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.error(f"❌ Erro na busca desesperada: {e}")

        if not join_success:
            raise Exception("Não foi possível encontrar o botão de ingresso após todas as tentativas")

        await tirar_screenshot_e_upload_async(page, "after_join_click")

        # Verificar se está no lobby com timeout otimizado
        yield {"event": "checking_lobby"}
        logger.info("🚪 Verificando se está no lobby...")
        
        lobby_selectors = [
            'text="Aguardando para ser admitido"',
            'text="Waiting to be admitted"',
            '[data-tid="lobby-screen"]',
            'text="Aguarde"'
        ]

        in_lobby = False
        for selector in lobby_selectors[:2]:  # Verificar principais primeiro
            try:
                element = await page.wait_for_selector(selector, timeout=3000)
                if element:
                    logger.info(f"🚪 Detectado lobby com seletor: {selector}")
                    in_lobby = True
                    break
            except PlaywrightTimeoutError:
                continue

        if in_lobby:
            yield {"event": "in_lobby"}
            logger.info("🚪 No lobby, aguardando admissão (máximo 5 minutos)...")
            
            # Aguardar saída do lobby com timeout máximo
            lobby_timeout = 300  # 5 minutos
            start_time = time.time()
            
            while time.time() - start_time < lobby_timeout:
                if stop_event.is_set():
                    yield {"event": "stop_requested"}
                    return
                
                # Verificar se ainda está no lobby
                still_in_lobby = False
                for selector in lobby_selectors:
                    try:
                        element = await page.wait_for_selector(selector, timeout=2000)
                        if element:
                            still_in_lobby = True
                            break
                    except PlaywrightTimeoutError:
                        continue
                
                if not still_in_lobby:
                    logger.info("✅ Saiu do lobby!")
                    break
                
                await asyncio.sleep(5)  # Aguardar 5s antes de verificar novamente
            
            if time.time() - start_time >= lobby_timeout:
                logger.warning("⏰ Timeout no lobby, continuando mesmo assim (pode estar na reunião)")
        else:
            logger.info("✅ Não está no lobby, provavelmente na reunião")

        # Iniciar gravação FFmpeg
        yield {"event": "starting_recording"}
        proc = iniciar_gravacao_ffmpeg(nome_arquivo)
        logger.info(f"🎙️ Gravação iniciada (PID: {proc.pid})")

        # Loop principal de gravação com verificações assíncronas
        inicio_gravacao = time.time()
        yield {"event": "recording_started", "duration_limit": DURACAO_MAXIMA}

        while True:
            # Verificar sinal de parada
            if stop_event.is_set():
                yield {"event": "stop_requested"}
                logger.info("⏹️ Parada solicitada pelo usuário")
                break

            # Verificar se atingiu duração máxima
            tempo_decorrido = time.time() - inicio_gravacao
            if tempo_decorrido >= DURACAO_MAXIMA:
                yield {"event": "max_duration_reached"}
                logger.info(f"⏰ Duração máxima atingida: {DURACAO_MAXIMA}s")
                auto_stopped_conditions_were_met = True
                break

            # Verificar se a reunião terminou
            if await verificar_condicoes_encerramento_async(page):
                yield {"event": "meeting_ended"}
                logger.info("🏁 Reunião terminou automaticamente")
                auto_stopped_conditions_were_met = True
                break

            # Verificar se FFmpeg ainda está rodando
            if proc.poll() is not None:
                yield {"event": "ffmpeg_stopped"}
                logger.warning("⚠️ FFmpeg parou inesperadamente")
                break

            # Tentar reingressar se necessário
            if await tentar_reingressar_async(page):
                yield {"event": "rejoined"}

            # Aguardar antes da próxima verificação
            await asyncio.sleep(10)

    except PlaywrightTimeoutError as pte:
        error_detail = f"Timeout do Playwright: {str(pte)}"
        logger.error(f"⏰ {error_detail}")
        yield {"event": "error", "type": "playwright_timeout", "detail": error_detail}
    except Exception as e:
        error_detail = f"Erro inesperado: {str(e)}"
        logger.error(f"❌ {error_detail}")
        yield {"event": "error", "type": "unexpected_error", "detail": error_detail}
    
    finally:
        # Limpeza robusta
        logger.info("🧹 Iniciando limpeza...")
        
        # Terminar FFmpeg primeiro
        if proc:
            ffmpeg_exit_code = terminar_ffmpeg_robusto(proc)
            logger.info(f"🎙️ FFmpeg terminado com código: {ffmpeg_exit_code}")

        # Fechar recursos do Playwright
        try:
            if page:
                await page.close()
                logger.info("🌐 Página fechada")
        except Exception as e:
            logger.error(f"❌ Erro ao fechar página: {e}")

        try:
            if context:
                await context.close()
                logger.info("🌐 Contexto fechado")
        except Exception as e:
            logger.error(f"❌ Erro ao fechar contexto: {e}")

        try:
            if browser:
                await browser.close()
                logger.info("🌐 Browser fechado")
        except Exception as e:
            logger.error(f"❌ Erro ao fechar browser: {e}")

        try:
            if playwright_instance:
                await playwright_instance.stop()
                logger.info("🌐 Playwright instance parada")
        except Exception as e:
            logger.error(f"❌ Erro ao parar Playwright: {e}")

        # Upload do arquivo final se existir
        if os.path.exists(nome_arquivo):
            try:
                logger.info(f"📤 Fazendo upload do arquivo: {nome_arquivo}")
                
                # Upload em thread separada para não bloquear
                def upload_final():
                    try:
                        public_url, gs_uri = enviar_para_gcs(nome_arquivo)
                        logger.info(f"✅ Upload concluído: {public_url}")
                        
                        # Remover arquivo local após upload
                        if os.path.exists(nome_arquivo):
                            os.remove(nome_arquivo)
                            logger.info(f"🗑️ Arquivo local removido: {nome_arquivo}")
                        
                        return public_url, gs_uri
                    except Exception as e:
                        logger.error(f"❌ Erro no upload final: {e}")
                        return None, None
                
                loop = asyncio.get_event_loop()
                public_url, gs_uri = await loop.run_in_executor(None, upload_final)
                
                if public_url:
                    yield {
                        "event": "recording_completed" if auto_stopped_conditions_were_met else "recording_stopped",
                        "file_url": public_url,
                        "gs_uri": gs_uri,
                        "filename": nome_arquivo,
                        "auto_stopped": auto_stopped_conditions_were_met,
                        "ffmpeg_exit_code": ffmpeg_exit_code
                    }
                else:
                    yield {"event": "upload_failed", "filename": nome_arquivo}
                    
            except Exception as e:
                logger.error(f"❌ Erro no processo de upload: {e}")
                yield {"event": "upload_error", "detail": str(e)}
        else:
            logger.warning(f"⚠️ Arquivo não encontrado para upload: {nome_arquivo}")
            yield {"event": "no_file_to_upload", "filename": nome_arquivo}

        yield {"event": "cleanup_completed"}
        logger.info("✅ Limpeza concluída")
