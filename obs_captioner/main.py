"""Main entry point and orchestrator for OBS Real-Time Live Captioner."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from .config import load_config, AppConfig
from .audio_capture import AudioCapture, list_audio_devices
from .engines import create_engine
from .history import TranscriptHistory
from .obs import OBSWebSocketClient, CaptionSink
from .twitch_bot import TwitchCaptionBot
from .web import WebOverlayServer


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
    pipeline_task = None
    loop = asyncio.get_running_loop()
    is_paused = False

    def get_app_status():
        return {
            "is_running": not is_paused,
            "obs_connected": obs_client.is_connected if obs_client else False,
            "audio_level_db": audio_capture.current_rms_db if audio_capture else -100.0,
            "engine_name": engine.name if engine else config.general.engine,
        }

    def on_config_updated(new_cfg: AppConfig):
        nonlocal config
        config = new_cfg
        if sink:
            sink.update_config(new_cfg)
        logger.info("Configuration hot-reloaded.")

    def on_start_requested():
        nonlocal is_paused
        is_paused = False
        logger.info("Captioning started via API.")

    def on_stop_requested():
        nonlocal is_paused
        is_paused = True
        logger.info("Captioning paused via API.")

    # 3. Initialize Web Overlay & Dashboard Server
    web_server = None
    if config.overlay.enabled:
        web_server = WebOverlayServer(
            config=config,
            history=history,
            on_config_updated=on_config_updated,
            on_start_requested=on_start_requested,
            on_stop_requested=on_stop_requested,
            get_app_status=get_app_status,
            obs_client=obs_client,
        )
        await web_server.start()

    # Hook VU level meter to broadcast over WebSockets
    if web_server:
        last_meter_broadcast = 0.0

        def on_vu_level(db_level: float):
            nonlocal last_meter_broadcast
            now = asyncio.get_event_loop().time()
            if now - last_meter_broadcast > 0.08:  # ~12 FPS meter updates
                last_meter_broadcast = now
                asyncio.run_coroutine_threadsafe(
                    web_server.broadcast_vu_level(db_level),
                    loop,
                )

        audio_capture.on_level_meter = on_vu_level

    # 4. Initialize Twitch Chat Bot
    twitch_bot = None
    if config.twitch.enabled:
        twitch_bot = TwitchCaptionBot(config.twitch)
        await twitch_bot.start()

    # 5. Initialize Caption Sink
    sink = CaptionSink(config, obs_client=obs_client, web_server=web_server, history=history, twitch_bot=twitch_bot)

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
    shutdown_event = asyncio.Event()

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
            if is_paused or engine is None or not initialized:
                await asyncio.sleep(0.2)
                continue
            try:
                await engine.start_streaming(
                    audio_stream=audio_capture.stream_generator(),
                    on_transcript=sink.handle_transcript,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Pipeline error: {e}", exc_info=True)
                await asyncio.sleep(2.0)

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
        if engine:
            await engine.stop()
        if audio_capture:
            audio_capture.stop()
        if obs_client:
            await obs_client.close()
        if web_server:
            await web_server.stop()
        logger.info("OBS Live Captioner stopped gracefully.")


def main():
    """CLI parsing entry point."""
    parser = argparse.ArgumentParser(description="OBS Real-Time Live Captioner")
    parser.add_argument("--config", "-c", type=str, default=None, help="Path to config.json")
    parser.add_argument("--engine", "-e", type=str, choices=["google_web", "gemini_live", "google_stt", "local_whisper"], help="Override STT engine")
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

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
