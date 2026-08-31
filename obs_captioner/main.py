import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

from .config import load_config, AppConfig
from .audio_capture import AudioCapture, list_audio_devices
from .engines import create_engine
from .history import TranscriptHistory
from .obs import OBSWebSocketClient, CaptionSink
from .twitch_bot import TwitchCaptionBot
from .web import WebOverlayServer

logger = logging.getLogger("obs_captioner")


def setup_logging(log_level: str = "INFO"):
    """Configure structured logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%H:%M:%S",
    )


async def main_async(args):
    """Main async execution loop."""
    config: AppConfig = load_config(args.config)

    # CLI Overrides
    if args.engine:
        config.general.engine = args.engine
    if args.device_index is not None:
        config.audio.device_index = args.device_index
    if args.device_name:
        config.audio.device_name_filter = args.device_name
    if args.no_obs:
        config.obs.enabled = False
    if args.no_overlay:
        config.overlay.enabled = False

    setup_logging(config.general.log_level)
    logger = logging.getLogger("obs_captioner")
    logger.info("==================================================")
    logger.info("   OBS Real-Time Live Captioner Suite")
    logger.info(f"   Engine: {config.general.engine}")
    logger.info(f"   Censor Mode: {config.censor.mode} (Enabled: {config.censor.enabled})")
    logger.info(f"   Language: {config.general.language}")
    logger.info("==================================================")

    # Shared Transcript History
    history = TranscriptHistory()

    # 1. Initialize OBS WebSocket Client
    obs_client = None
    if config.obs.enabled:
        obs_client = OBSWebSocketClient(config.obs)
        await obs_client.connect()

    # 2. State & Control references
    audio_capture = AudioCapture(config.audio)
    sink = None
    engine = None
    initialized = False
    pipeline_task = None
    loop = asyncio.get_running_loop()
    is_paused = False
    is_restart = False
    is_switching_engine = False
    engine_lock = asyncio.Lock()
    shutdown_event = asyncio.Event()

    def get_model_detail(cfg: AppConfig) -> str:
        eng = cfg.general.engine
        if eng == "vosk":
            model_variant = (cfg.vosk.model_name or "small").strip().lower()
            if model_variant in ("accurate", "large", "en-us-0.22", "vosk-model-en-us-0.22"):
                return "Vosk Accurate (vosk-model-en-us-0.22 • ~1.8 GB)"
            return "Vosk Small (vosk-model-small-en-us-0.15 • ~40 MB)"
        elif eng == "local_whisper":
            return f"Faster-Whisper ({cfg.local_whisper.model_size} • Device: {cfg.local_whisper.device})"
        elif eng == "moonshine":
            return f"Moonshine ONNX ({cfg.moonshine.model_name})"
        elif eng == "google_web":
            return "Google Web Speech (Real-Time Cloud • Free)"
        elif eng == "google_stt":
            return f"Google Cloud Speech v2 ({cfg.google_stt.model or 'latest_long'})"
        elif eng == "gemini_live":
            return f"Gemini 3.5 Live ({cfg.gemini_live.model})"
        return eng

    def get_app_status():
        return {
            "is_running": not is_paused,
            "obs_connected": obs_client.is_connected if obs_client else False,
            "audio_level_db": audio_capture.current_rms_db if audio_capture else -100.0,
            "engine": config.general.engine,
            "engine_name": engine.name if engine else config.general.engine,
            "model_detail": get_model_detail(config),
            "is_switching_engine": is_switching_engine,
        }

    async def switch_engine_async(new_cfg: AppConfig):
        nonlocal engine, initialized, is_switching_engine
        async with engine_lock:
            is_switching_engine = True
            try:
                if engine:
                    logger.info(f"Stopping active engine: {engine.name}...")
                    await engine.stop()
                    await asyncio.sleep(0.1)

                logger.info(f"Instantiating new STT engine for: {new_cfg.general.engine}...")
                new_eng = create_engine(new_cfg)
                init_ok = await new_eng.initialize()
                if init_ok:
                    engine = new_eng
                    initialized = True
                    logger.info(f"✅ STT engine switched to: {engine.name} ({get_model_detail(new_cfg)})")
                    if web_server:
                        await web_server.broadcast_control({
                            "type": "engine_changed",
                            "engine": new_cfg.general.engine,
                            "engine_name": engine.name,
                            "model_detail": get_model_detail(new_cfg),
                        })
                else:
                    logger.error(f"Failed to initialize engine '{new_eng.name}'.")
            except Exception as e:
                logger.error(f"Error during engine switch: {e}", exc_info=True)
            finally:
                is_switching_engine = False

    active_engine_type = config.general.engine
    active_vosk_model = config.vosk.model_name
    active_whisper_model = config.local_whisper.model_size
    active_moonshine_model = config.moonshine.model_name

    def on_config_updated(new_cfg: AppConfig):
        nonlocal config, active_engine_type, active_vosk_model, active_whisper_model, active_moonshine_model

        config = new_cfg
        if sink:
            sink.update_config(new_cfg)
        logger.info("Configuration hot-reloaded.")

        needs_engine_reload = (
            new_cfg.general.engine != active_engine_type
            or (new_cfg.general.engine == "vosk" and new_cfg.vosk.model_name != active_vosk_model)
            or (new_cfg.general.engine == "local_whisper" and new_cfg.local_whisper.model_size != active_whisper_model)
            or (new_cfg.general.engine == "moonshine" and new_cfg.moonshine.model_name != active_moonshine_model)
        )

        if needs_engine_reload:
            logger.info(f"STT engine change requested: {active_engine_type} -> {new_cfg.general.engine}. Hot-switching engine...")
            active_engine_type = new_cfg.general.engine
            active_vosk_model = new_cfg.vosk.model_name
            active_whisper_model = new_cfg.local_whisper.model_size
            active_moonshine_model = new_cfg.moonshine.model_name
            asyncio.run_coroutine_threadsafe(switch_engine_async(new_cfg), loop)

    def on_start_requested():
        nonlocal is_paused
        is_paused = False
        logger.info("Captioning started via API.")

    def on_stop_requested():
        nonlocal is_paused
        is_paused = True
        logger.info("Captioning paused via API.")

    def on_restart_requested():
        nonlocal is_restart
        is_restart = True
        logger.info("Application restart triggered via API/Dashboard.")
        shutdown_event.set()

    def on_shutdown_requested():
        nonlocal is_restart
        is_restart = False
        logger.info("Application shutdown triggered via API/Dashboard.")
        shutdown_event.set()

    # 3. Initialize Web Overlay & Dashboard Server
    web_server = None
    if config.overlay.enabled:
        web_server = WebOverlayServer(
            config=config,
            history=history,
            on_config_updated=on_config_updated,
            on_start_requested=on_start_requested,
            on_stop_requested=on_stop_requested,
            on_restart_requested=on_restart_requested,
            on_shutdown_requested=on_shutdown_requested,
            get_app_status=get_app_status,
            obs_client=obs_client,
            audio_capture=audio_capture,
        )
        await web_server.start()

    # Hook VU level meter to broadcast over WebSockets
    if web_server:
        last_meter_broadcast = 0.0

        def on_vu_level(db_level: float):
            nonlocal last_meter_broadcast
            now = loop.time()
            if now - last_meter_broadcast > 0.06:  # ~16 FPS meter updates
                last_meter_broadcast = now
                try:
                    asyncio.run_coroutine_threadsafe(
                        web_server.broadcast_vu_level(db_level),
                        loop,
                    )
                except Exception:
                    pass

        audio_capture.on_level_meter = on_vu_level

    # 4. Initialize Twitch Chat Bot
    twitch_bot = None
    if config.twitch.enabled:
        twitch_bot = TwitchCaptionBot(config.twitch)
        await twitch_bot.start()

    # 5. Initialize Caption Sink (is_paused gates dispatch so Stop works even
    # while a continuous engine's streaming loop is still running)
    sink = CaptionSink(
        config,
        obs_client=obs_client,
        web_server=web_server,
        history=history,
        twitch_bot=twitch_bot,
        is_paused=lambda: is_paused,
    )

    # 6. Initialize STT Engine
    try:
        engine = create_engine(config)
    except Exception as e:
        logger.error(f"Failed to create STT engine: {e}")
        return

    logger.info(f"Initializing {engine.name}...")
    initialized = await engine.initialize()
    if not initialized:
        logger.error(f"Failed to initialize engine '{engine.name}'. Please check API keys / credentials in Dashboard or config.json.")

    # 6. Initialize Audio Capture
    if not audio_capture.start(loop=loop):
        logger.error("Failed to start audio capture stream.")

    # Hook OBS auto-start / auto-stop events
    if obs_client and obs_client.is_connected:
        def on_stream_state(active: bool):
            nonlocal is_paused
            if active and config.obs.auto_start_on_stream:
                logger.info("OBS Streaming started -> Captioner active.")
                is_paused = False
            elif not active and config.obs.auto_start_on_stream:
                logger.info("OBS Streaming stopped.")

        obs_client.on_stream_state_changed = on_stream_state

    # 7. Run streaming pipeline
    def signal_handler():
        logger.info("Shutdown signal received. Stopping captioner...")
        shutdown_event.set()

    try:
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, signal_handler)
    except Exception:
        pass

    logger.info("OBS Live Captioner ready!")
    logger.info(f"👉 Web Control Panel & OBS Dock: http://127.0.0.1:{config.overlay.port}/dashboard")
    logger.info(f"👉 OBS Browser Source URL: http://127.0.0.1:{config.overlay.port}/")

    async def run_pipeline():
        while not shutdown_event.is_set():
            if is_paused or engine is None or not initialized or is_switching_engine:
                await asyncio.sleep(0.1)
                continue
            try:
                await engine.start_streaming(
                    audio_stream=audio_capture.stream_generator(),
                    on_transcript=sink.handle_transcript,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not is_switching_engine:
                    logger.error(f"Pipeline error: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    pipeline_task = asyncio.create_task(run_pipeline())

    # Wait until shutdown requested
    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
    finally:
        logger.info("Cleaning up resources...")
        if pipeline_task:
            pipeline_task.cancel()
            try:
                await pipeline_task
            except asyncio.CancelledError:
                pass
        if engine:
            await engine.stop()
        if audio_capture:
            audio_capture.stop()
        if obs_client:
            await obs_client.close()
        if web_server:
            await web_server.stop()
        logger.info("OBS Live Captioner stopped gracefully.")
        return 42 if is_restart else 0


def main():
    """CLI parsing entry point."""
    parser = argparse.ArgumentParser(description="OBS Real-Time Live Captioner")
    parser.add_argument("--config", "-c", type=str, default=None, help="Path to config.json")
    parser.add_argument("--engine", "-e", type=str, choices=["google_web", "gemini_live", "google_stt", "local_whisper", "vosk", "moonshine"], help="Override STT engine")
    parser.add_argument("--device-index", "-d", type=int, default=None, help="Audio input device index")
    parser.add_argument("--device-name", "-n", type=str, default=None, help="Audio input device name filter")
    parser.add_argument("--list-devices", "-l", action="store_true", help="List all available audio input devices and exit")
    parser.add_argument("--no-obs", action="store_true", help="Disable OBS WebSocket client")
    parser.add_argument("--no-overlay", action="store_true", help="Disable Browser Source web overlay")

    args = parser.parse_args()

    if args.list_devices:
        devices = list_audio_devices()
        print("\nAvailable Audio Input Devices:")
        print("--------------------------------------------------------------------------------")
        print(f"{'Idx':<4} | {'Host API':<18} | {'Channels':<8} | {'Sample Rate':<11} | {'Device Name'}")
        print("--------------------------------------------------------------------------------")
        for d in devices:
            print(f"{d['index']:<4} | {d['hostapi']:<18} | {d['channels']:<8} | {int(d['default_samplerate']):<11} | {d['name']}")
        print("--------------------------------------------------------------------------------\n")
        return

    while True:
        exit_code = 0
        try:
            exit_code = asyncio.run(main_async(args))
        except KeyboardInterrupt:
            exit_code = 0
            break

        if exit_code == 42:
            logger.info("🔄 [VoxStream] Application restart requested. Re-launching pipeline in 0.5s...")
            time.sleep(0.5)
            continue
        else:
            sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
