import argparse
import asyncio
import logging
import os
import signal
import sys
import time
if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "obs_captioner"

from .config import load_config, AppConfig
from .audio_capture import AudioCapture, list_audio_devices
from .engines import create_engine
from .history import TranscriptHistory
from .obs import OBSWebSocketClient, CaptionSink
from .twitch_bot import TwitchCaptionBot
from .web import WebOverlayServer
from .updater import UpdateManager
from .subtitle_recorder import SubtitleRecorder

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

    # Synchronized Subtitle Sidecar Recorder (.srt / .vtt)
    subtitle_recorder = SubtitleRecorder(
        enabled=getattr(config.obs, "auto_record_subtitles", True),
        output_format=getattr(config.obs, "record_subtitles_format", "srt"),
        output_dir=getattr(config.obs, "record_subtitles_directory", ""),
    )

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
                return "Vosk Accurate (1.8 GB)"
            return "Vosk Small (~40 MB)"
        elif eng == "local_whisper":
            w_size = cfg.local_whisper.model_size or "base.en"
            if w_size == "distil-large-v3":
                w_label = "Distil-Large-v3 (Anti-Hallucination)"
            elif w_size == "large-v3-turbo":
                w_label = "Large-v3-Turbo (SOTA Multilingual)"
            elif w_size == "base.en":
                w_label = "Base.en (Punctuation & Caps)"
            else:
                w_label = w_size
            return f"Faster-Whisper ({w_label})"
        elif eng == "moonshine":
            return f"Moonshine ONNX ({cfg.moonshine.model_name})"
        elif eng == "google_web":
            return "Google Web Speech (Real-Time Cloud • Free)"
        elif eng == "google_stt":
            return f"Google Cloud Speech v2 ({cfg.google_stt.model or 'latest_long'})"
        elif eng == "gemini_live":
            return f"Gemini 3.5 Live ({cfg.gemini_live.model})"
        elif eng in ("bandwidth", "labs.bandwidth.com", "bandwidth_labs"):
            return "Bandwidth Labs Live STT (Real-Time Cloud)"
        elif eng in ("sherpa", "sherpa_onnx", "zipformer"):
            return f"Sherpa-ONNX Zipformer ({cfg.sherpa.model_name})"
        elif eng in ("parakeet", "nemo", "nemo_parakeet", "fastconformer"):
            p_model = cfg.parakeet.model_name or "parakeet-fastconformer-large-24500"
            if "24500" in p_model:
                p_name = "FastConformer Large (24,500h CTC)"
            elif "tdt" in p_model:
                p_name = "Parakeet-TDT 0.6B (Transducer)"
            elif "large" in p_model:
                p_name = "Conformer Large int8"
            elif "medium" in p_model or "nemo" in p_model:
                p_name = "Conformer Medium int8"
            else:
                p_name = p_model
            return f"NVIDIA Parakeet ({p_name})"
        elif eng in ("sensevoice", "funasr"):
            return f"SenseVoice ({cfg.sensevoice.model_name} • Device: {cfg.sensevoice.device})"
        return eng

    engine_switch_status = ""
    engine_switch_target = ""
    engine_switch_error = None

    def get_app_status():
        from .hardware import get_ram_usage_mb, get_gpu_info
        sink_obj = sink if 'sink' in locals() else None
        hist_obj = history if 'history' in locals() else None
        return {
            "is_running": not is_paused,
            "obs_connected": obs_client.is_connected if obs_client else False,
            "audio_level_db": audio_capture.current_rms_db if audio_capture else -100.0,
            "engine": config.general.engine,
            "engine_name": engine.name if engine else config.general.engine,
            "model_detail": get_model_detail(config),
            "engine_initialized": bool(initialized and (engine is not None)),
            "is_streaming": bool(engine and getattr(engine, "is_running", False) and not is_paused),
            "is_switching_engine": is_switching_engine,
            "engine_switch_status": engine_switch_status,
            "engine_switch_target": engine_switch_target,
            "engine_switch_error": engine_switch_error,
            "last_caption_time": getattr(sink_obj, "_last_caption_time", 0.0) if sink_obj else 0.0,
            "total_captions": len(hist_obj.entries) if hist_obj else 0,
            "ram_usage_mb": get_ram_usage_mb(),
            "gpu_info": get_gpu_info(),
        }

    async def switch_engine_async(new_cfg: AppConfig):
        nonlocal engine, initialized, is_switching_engine, engine_switch_status, engine_switch_target, engine_switch_error
        nonlocal active_engine_type, active_vosk_model, active_whisper_model, active_moonshine_model, active_gemini_key, active_gemini_model, active_bandwidth_key
        nonlocal active_sherpa_model, active_sherpa_path, active_sherpa_threads, active_sherpa_device
        nonlocal active_parakeet_model, active_parakeet_device, active_parakeet_threads
        nonlocal active_sensevoice_model, active_sensevoice_device, active_sensevoice_threads, active_sensevoice_events, active_sensevoice_language
        async with engine_lock:
            is_switching_engine = True
            engine_switch_error = None
            target_name = get_model_detail(new_cfg)
            engine_switch_target = target_name
            engine_switch_status = f"Preparing to load {target_name}..."

            async def broadcast_status(text: str, is_err: bool = False):
                nonlocal engine_switch_status, engine_switch_error
                engine_switch_status = text
                if is_err:
                    engine_switch_error = text
                if web_server:
                    await web_server.broadcast_control({
                        "type": "engine_switching_status",
                        "is_switching": is_switching_engine,
                        "status_text": text,
                        "target_engine": new_cfg.general.engine,
                        "target_name": target_name,
                        "is_error": is_err,
                    })

            def status_callback(msg: str):
                logger.info(f"Engine status update: {msg}")
                asyncio.run_coroutine_threadsafe(broadcast_status(msg, is_err="❌" in msg), loop)

            try:
                await broadcast_status("Stopping previous recognition engine & releasing RAM...")
                if engine:
                    logger.info(f"Stopping active engine: {engine.name}...")
                    await engine.stop()
                    engine = None
                    from .hardware import release_stt_memory
                    release_stt_memory()
                    await asyncio.sleep(0.1)

                await broadcast_status(f"Initializing {target_name} (checking cache / downloading weights)...")
                logger.info(f"Instantiating new STT engine for: {new_cfg.general.engine}...")
                new_eng = create_engine(new_cfg)
                init_ok = await new_eng.initialize(status_callback=status_callback)
                if init_ok:
                    engine = new_eng
                    initialized = True
                    engine_switch_error = None
                    engine_switch_status = f"✅ {engine.name} ready!"
                    active_engine_type = new_cfg.general.engine
                    active_vosk_model = new_cfg.vosk.model_name
                    active_whisper_model = new_cfg.local_whisper.model_size
                    active_moonshine_model = new_cfg.moonshine.model_name
                    active_gemini_key = new_cfg.gemini_live.api_key
                    active_gemini_model = new_cfg.gemini_live.model
                    active_bandwidth_key = new_cfg.bandwidth.api_key
                    active_sherpa_model = new_cfg.sherpa.model_name
                    active_sherpa_path = new_cfg.sherpa.model_path
                    active_sherpa_threads = new_cfg.sherpa.num_threads
                    active_sherpa_device = new_cfg.sherpa.device
                    active_parakeet_model = new_cfg.parakeet.model_name
                    active_parakeet_device = new_cfg.parakeet.device
                    active_parakeet_threads = new_cfg.parakeet.num_threads
                    active_sensevoice_model = new_cfg.sensevoice.model_name
                    active_sensevoice_device = new_cfg.sensevoice.device
                    active_sensevoice_threads = new_cfg.sensevoice.num_threads
                    active_sensevoice_events = new_cfg.sensevoice.detect_events
                    active_sensevoice_language = new_cfg.sensevoice.language
                    logger.info(f"✅ STT engine switched to: {engine.name} ({get_model_detail(new_cfg)})")
                    if web_server:
                        await web_server.broadcast_control({
                            "type": "engine_changed",
                            "engine": new_cfg.general.engine,
                            "engine_name": engine.name,
                            "model_detail": get_model_detail(new_cfg),
                        })
                else:
                    err_msg = engine_switch_error or f"Failed to initialize '{target_name}'. See server logs for details."
                    await broadcast_status(err_msg, is_err=True)
                    logger.error(f"Failed to initialize engine '{new_eng.name}'. Restoring previous working engine...")
                    await new_eng.stop()
                    new_eng = None
                    from .hardware import release_stt_memory
                    release_stt_memory()
                    try:
                        fallback_eng = create_engine(config)
                        if await fallback_eng.initialize():
                            engine = fallback_eng
                            initialized = True
                            logger.info(f"Restored previous engine: {engine.name}")
                    except Exception as fe:
                        logger.warning(f"Could not restore previous engine: {fe}")
            except Exception as e:
                err_msg = f"Error during engine switch: {e}"
                await broadcast_status(err_msg, is_err=True)
                logger.error(f"Error during engine switch: {e}", exc_info=True)
            finally:
                is_switching_engine = False
                if web_server:
                    await web_server.broadcast_control({
                        "type": "engine_switching_status",
                        "is_switching": False,
                        "status_text": engine_switch_status,
                        "target_engine": new_cfg.general.engine,
                        "target_name": target_name,
                        "is_error": bool(engine_switch_error),
                    })

    active_engine_type = config.general.engine
    active_vosk_model = config.vosk.model_name
    active_whisper_model = config.local_whisper.model_size
    active_moonshine_model = config.moonshine.model_name
    active_gemini_key = config.gemini_live.api_key
    active_gemini_model = config.gemini_live.model
    active_bandwidth_key = config.bandwidth.api_key
    active_sherpa_model = config.sherpa.model_name
    active_sherpa_path = config.sherpa.model_path
    active_sherpa_threads = config.sherpa.num_threads
    active_sherpa_device = config.sherpa.device
    active_parakeet_model = config.parakeet.model_name
    active_parakeet_device = config.parakeet.device
    active_parakeet_threads = config.parakeet.num_threads
    active_sensevoice_model = config.sensevoice.model_name
    active_sensevoice_device = config.sensevoice.device
    active_sensevoice_threads = config.sensevoice.num_threads
    active_sensevoice_events = config.sensevoice.detect_events
    active_sensevoice_language = config.sensevoice.language

    def on_config_updated(new_cfg: AppConfig):
        nonlocal config, active_engine_type, active_vosk_model, active_whisper_model, active_moonshine_model, active_gemini_key, active_gemini_model, active_bandwidth_key
        nonlocal active_sherpa_model, active_sherpa_path, active_sherpa_threads, active_sherpa_device
        nonlocal active_parakeet_model, active_parakeet_device, active_parakeet_threads
        nonlocal active_sensevoice_model, active_sensevoice_device, active_sensevoice_threads, active_sensevoice_events, active_sensevoice_language

        config = new_cfg
        if sink:
            sink.update_config(new_cfg)
        if subtitle_recorder:
            subtitle_recorder.update_config(new_cfg.obs)
        if audio_capture:
            audio_capture.update_device(new_cfg.audio)
        if engine:
            if hasattr(engine, "config"):
                engine.config = new_cfg
            if hasattr(engine, "vad"):
                engine.vad.update_config(new_cfg.audio)
        logger.info("Configuration hot-reloaded.")

        needs_engine_reload = (
            new_cfg.general.engine != active_engine_type
            or (new_cfg.general.engine == "vosk" and new_cfg.vosk.model_name != active_vosk_model)
            or (new_cfg.general.engine == "local_whisper" and new_cfg.local_whisper.model_size != active_whisper_model)
            or (new_cfg.general.engine == "moonshine" and new_cfg.moonshine.model_name != active_moonshine_model)
            or (new_cfg.general.engine == "gemini_live" and (new_cfg.gemini_live.api_key != active_gemini_key or new_cfg.gemini_live.model != active_gemini_model))
            or (new_cfg.general.engine == "bandwidth" and new_cfg.bandwidth.api_key != active_bandwidth_key)
            or (new_cfg.general.engine in ("sherpa", "sherpa_onnx", "zipformer") and (new_cfg.sherpa.model_name != active_sherpa_model or new_cfg.sherpa.model_path != active_sherpa_path or new_cfg.sherpa.num_threads != active_sherpa_threads or new_cfg.sherpa.device != active_sherpa_device))
            or (new_cfg.general.engine in ("parakeet", "nemo", "nemo_parakeet", "fastconformer") and (new_cfg.parakeet.model_name != active_parakeet_model or new_cfg.parakeet.device != active_parakeet_device or new_cfg.parakeet.num_threads != active_parakeet_threads))
            or (new_cfg.general.engine in ("sensevoice", "funasr") and (new_cfg.sensevoice.model_name != active_sensevoice_model or new_cfg.sensevoice.device != active_sensevoice_device or new_cfg.sensevoice.num_threads != active_sensevoice_threads or new_cfg.sensevoice.detect_events != active_sensevoice_events or new_cfg.sensevoice.language != active_sensevoice_language))
            or (engine_switch_error is not None)
        )

        if needs_engine_reload:
            logger.info(f"STT engine change requested: {active_engine_type} -> {new_cfg.general.engine}. Hot-switching engine...")
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

    # 3. Initialize Web Overlay, Dashboard Server, and GitHub Updater
    web_server = None
    updater = UpdateManager(on_restart_requested=on_restart_requested)
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
            updater=updater,
            subtitle_recorder=subtitle_recorder,
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

        def on_recovery_status(status: str, device_name: str):
            logger.info(f"Audio recovery status changed: status={status}, device='{device_name}'")
            if web_server:
                try:
                    asyncio.run_coroutine_threadsafe(
                        web_server.broadcast_control({
                            "type": "audio_recovery_status",
                            "status": status,
                            "device": device_name,
                        }),
                        loop,
                    )
                except Exception:
                    pass

        audio_capture.on_recovery_status = on_recovery_status

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
        subtitle_recorder=subtitle_recorder,
    )

    # 6. Initialize STT Engine
    try:
        engine = create_engine(config)
    except Exception as e:
        logger.error(f"Failed to create STT engine: {e}")
        return

    logger.info(f"Initializing {engine.name}...")
    def initial_status_cb(msg: str):
        nonlocal engine_switch_status
        engine_switch_status = msg
        logger.info(f"Engine startup status: {msg}")
        if web_server:
            asyncio.run_coroutine_threadsafe(
                web_server.broadcast_control({
                    "type": "engine_switching_status",
                    "is_switching": True,
                    "status_text": msg,
                    "target_engine": config.general.engine,
                    "target_name": engine.name,
                }),
                loop,
            )

    initialized = await engine.initialize(status_callback=initial_status_cb)
    if not initialized:
        logger.error(f"Failed to initialize engine '{engine.name}'. Please check API keys / credentials in Dashboard or config.json.")
    else:
        engine_switch_status = f"✅ {engine.name} ready!"
        if web_server:
            asyncio.run_coroutine_threadsafe(
                web_server.broadcast_control({
                    "type": "engine_switching_status",
                    "is_switching": False,
                    "status_text": engine_switch_status,
                    "target_engine": config.general.engine,
                    "target_name": engine.name,
                }),
                loop,
            )

    # 6. Initialize Audio Capture
    if not audio_capture.start(loop=loop):
        logger.error("Failed to start audio capture stream.")

    # Hook OBS auto-start / auto-stop events & recording sync
    if obs_client:
        def on_stream_state(active: bool):
            nonlocal is_paused
            if active and config.obs.auto_start_on_stream:
                logger.info("OBS Streaming started -> Captioner active.")
                is_paused = False
            elif not active and config.obs.auto_start_on_stream:
                logger.info("OBS Streaming stopped.")

        def on_record_state(active: bool, output_path: str = ""):
            nonlocal is_paused
            if active and config.obs.auto_start_on_record:
                logger.info(f"OBS Recording started (output: '{output_path}') -> Captioner active.")
                is_paused = False
            elif not active and config.obs.auto_start_on_record:
                logger.info("OBS Recording stopped.")

            # Auto synchronized subtitle sidecar (.srt / .vtt)
            if config.obs.auto_record_subtitles and subtitle_recorder:
                if active:
                    subtitle_recorder.start_recording(video_path=output_path)
                else:
                    subtitle_recorder.stop_recording()

        def on_scene_changed(scene_name: str):
            nonlocal is_paused
            if not config.obs.scene_auto_mute_enabled or not scene_name:
                return
            clean_name = scene_name.strip()
            cur_lower = clean_name.lower()
            muted_scenes = [s.strip().lower() for s in config.obs.scene_muted_names if s.strip()]
            active_scenes = [s.strip().lower() for s in config.obs.scene_active_names if s.strip()]

            should_pause = None
            if muted_scenes and not active_scenes:
                # If only muted scenes specified: pause if in muted scenes, unpause if not
                should_pause = (cur_lower in muted_scenes)
            elif active_scenes and not muted_scenes:
                # If only active scenes specified: unpause if in active scenes, pause if not
                should_pause = (cur_lower not in active_scenes)
            elif muted_scenes and active_scenes:
                # If both specified: prioritize explicit lists
                if cur_lower in muted_scenes:
                    should_pause = True
                elif cur_lower in active_scenes:
                    should_pause = False

            if should_pause is not None and should_pause != is_paused:
                is_paused = should_pause
                action = "Auto-Muted (Paused)" if is_paused else "Auto-Resumed (Active)"
                logger.info(f"🎬 OBS Scene changed to '{clean_name}' -> Captions {action}.")
                if web_server:
                    asyncio.run_coroutine_threadsafe(
                        web_server.broadcast_control({
                            "type": "obs_scene_auto_mute",
                            "scene_name": clean_name,
                            "is_paused": is_paused,
                        }),
                        loop,
                    )

        obs_client.on_stream_state_changed = on_stream_state
        obs_client.on_record_state_changed = on_record_state
        obs_client.on_scene_changed = on_scene_changed

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
                if getattr(e, "__class__", None).__name__ == "AudioStreamError":
                    logger.error("Audio device lost. Attempting to re-initialize audio capture in 3 seconds...")
                    audio_capture.stop()
                    await asyncio.sleep(3.0)
                    if not audio_capture.start(loop=loop):
                        logger.error("Failed to recover audio capture stream. Will retry.")
                elif not is_switching_engine:
                    logger.error(f"Pipeline error: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    pipeline_task = asyncio.create_task(run_pipeline())

    async def cleanup_all():
        logger.info("Cleaning up resources...")
        if pipeline_task:
            pipeline_task.cancel()
            try:
                await asyncio.wait_for(pipeline_task, timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        if 'obs_reconnect_task' in locals():
            try:
                obs_reconnect_task.cancel()
            except Exception:
                pass
        if twitch_bot:
            try:
                await asyncio.wait_for(twitch_bot.stop(), timeout=0.8)
            except Exception as e:
                logger.debug(f"Error stopping Twitch bot: {e}")
        if subtitle_recorder and getattr(subtitle_recorder, "is_recording", False):
            try:
                subtitle_recorder.stop_recording()
            except Exception as e:
                logger.debug(f"Error stopping subtitle recorder: {e}")
        if engine:
            try:
                await asyncio.wait_for(engine.stop(), timeout=0.8)
            except Exception as e:
                logger.debug(f"Error stopping engine: {e}")
        if audio_capture:
            try:
                audio_capture.stop()
            except Exception as e:
                logger.debug(f"Error stopping audio capture: {e}")
        if obs_client:
            try:
                await asyncio.wait_for(obs_client.close(), timeout=0.8)
            except Exception as e:
                logger.debug(f"Error closing OBS client: {e}")
        if web_server:
            try:
                await asyncio.wait_for(web_server.stop(), timeout=1.5)
            except Exception as e:
                logger.debug(f"Error stopping web server: {e}")
        logger.info("OBS Live Captioner stopped gracefully.")

    # Wait until shutdown requested
    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
    finally:
        try:
            await asyncio.wait_for(cleanup_all(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("Resource cleanup timed out after 3.0s, proceeding with exit.")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        return 42 if is_restart else 0


def main():
    """CLI parsing entry point."""
    parser = argparse.ArgumentParser(description="OBS Real-Time Live Captioner")
    parser.add_argument("--config", "-c", type=str, default=None, help="Path to config.json")
    parser.add_argument("--engine", "-e", type=str, choices=["google_web", "gemini_live", "google_stt", "local_whisper", "vosk", "moonshine", "bandwidth", "sherpa", "parakeet", "sensevoice"], help="Override STT engine")
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

    exit_code = 0
    try:
        exit_code = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        exit_code = 0
    except Exception as e:
        logger.error(f"Fatal error during execution: {e}", exc_info=True)
        exit_code = 1

    if exit_code == 42:
        logger.info("🔄 [VoxStream] Application restart requested. Handing over to launcher...")
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

        restart_cmd = [sys.executable, "-m", "obs_captioner.main"]
        if len(sys.argv) > 1:
            restart_cmd.extend(sys.argv[1:])

        # If running under batch/shell wrapper, exit with code 42 so launcher loops
        is_runner = os.environ.get("VOXSTREAM_RUNNER") in ("bat", "sh")
        if is_runner:
            os._exit(42)
        elif sys.platform == "win32":
            # Standalone Windows execution without wrapper: spawn replacement process and exit
            try:
                subprocess.Popen(restart_cmd)
            except Exception as restart_err:
                logger.error(f"Failed to spawn replacement restart process: {restart_err}")
            os._exit(42)
        else:
            # Standalone POSIX execution: re-exec Python process in-place
            try:
                os.execv(sys.executable, restart_cmd)
            except Exception:
                os._exit(42)
    else:
        logger.info("🛑 [VoxStream] Application shutdown complete.")
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(exit_code or 0)


if __name__ == "__main__":
    main()
