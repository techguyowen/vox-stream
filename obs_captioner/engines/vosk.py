"""Vosk (Kaldi) Local Offline Speech-to-Text Engine."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig

logger = logging.getLogger("obs_captioner.engine.vosk")


class VoskEngine(BaseSTTEngine):
    """Local, ultra-low latency continuous offline speech recognition using Vosk (Kaldi)."""

    def __init__(self, config: AppConfig):
        super().__init__("Local Vosk / Kaldi")
        self.config = config
        self.model = None

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Load or download Vosk model into memory with progress updates."""
        try:
            import vosk

            # Suppress noisy C-level Kaldi log spam
            vosk.SetLogLevel(-1)

            model_name = (self.config.vosk.model_name or "small").strip().lower()
            is_accurate = model_name in ("accurate", "large", "en-us-0.22", "vosk-model-en-us-0.22")
            model_desc = "vosk-model-en-us-0.22 (1.8GB)" if is_accurate else "vosk-model-small-en-us-0.15 (40MB)"

            if status_callback:
                status_callback(f"Checking Vosk cache for {model_desc}...")
            logger.info(f"Loading Vosk model ({model_desc})...")

            loop = asyncio.get_event_loop()

            def _load_model():
                # 1. Custom model path if specified
                custom_path = (self.config.vosk.model_path or "").strip()
                if custom_path and Path(custom_path).exists():
                    if status_callback:
                        status_callback(f"Loading custom Vosk model from: {custom_path}")
                    logger.info(f"Loading custom Vosk model from: {custom_path}")
                    return vosk.Model(model_path=custom_path)

                # 2. Named model presets
                if is_accurate:
                    acc_cache = Path.home() / ".cache" / "vosk" / "vosk-model-en-us-0.22"
                    if acc_cache.exists():
                        if status_callback:
                            status_callback("Loading cached Vosk accurate model (~1.8GB)...")
                        return vosk.Model(model_path=str(acc_cache))
                    if status_callback:
                        status_callback("Downloading Vosk accurate model (vosk-model-en-us-0.22, ~1.8GB, first-time setup)...")
                    logger.info("Loading accurate Vosk model (vosk-model-en-us-0.22)...")
                    return vosk.Model(model_name="vosk-model-en-us-0.22")
                else:
                    small_cache = Path.home() / ".cache" / "vosk" / "vosk-model-small-en-us-0.15"
                    if small_cache.exists():
                        if status_callback:
                            status_callback("Loading cached Vosk small model (~40MB)...")
                        return vosk.Model(model_path=str(small_cache))
                    if status_callback:
                        status_callback("Downloading Vosk small model (vosk-model-small-en-us-0.15, ~40MB)...")
                    logger.info("Loading lightweight Vosk model (vosk-model-small-en-us-0.15)...")
                    return vosk.Model(model_name="vosk-model-small-en-us-0.15")

            self.model = await loop.run_in_executor(None, _load_model)
            if status_callback:
                status_callback(f"✅ Vosk model loaded ready!")
            logger.info("Vosk model loaded successfully.")
            return True
        except ImportError:
            err = "vosk is not installed. Install with: pip install vosk"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False
        except Exception as e:
            err = f"Failed to load Vosk model: {e}"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Continuously process audio chunks and yield token-by-token interim and final transcripts."""
        import vosk

        self.is_running = True
        sample_rate = self.config.audio.sample_rate or 16000
        rec = vosk.KaldiRecognizer(self.model, sample_rate)
        rec.SetWords(True)
        if hasattr(rec, "SetPartialWords"):
            rec.SetPartialWords(True)

        logger.info("Vosk continuous streaming recognition loop active.")
        last_partial_text = ""

        async for chunk in audio_stream:
            if not self.is_running:
                break

            if not chunk:
                continue

            even_len = len(chunk) & ~1
            pcm_chunk = chunk[:even_len]

            # AcceptWaveform returns True when Kaldi detects silence/endpoint (final sentence)
            if rec.AcceptWaveform(pcm_chunk):
                res_json = rec.Result()
                try:
                    res_data = json.loads(res_json)
                    text = res_data.get("text", "").strip()
                    if text:
                        last_partial_text = ""
                        await on_transcript(
                            TranscriptEvent(
                                text=text,
                                is_final=True,
                            )
                        )
                except Exception:
                    pass
            else:
                # PartialResult returns interim words as you speak
                partial_json = rec.PartialResult()
                try:
                    part_data = json.loads(partial_json)
                    partial_text = part_data.get("partial", "").strip()
                    if partial_text and partial_text != last_partial_text:
                        last_partial_text = partial_text
                        await on_transcript(
                            TranscriptEvent(
                                text=partial_text,
                                is_final=False,
                            )
                        )
                except Exception:
                    pass

        # Final cleanup on stream stop
        if self.is_running:
            try:
                final_json = rec.FinalResult()
                final_data = json.loads(final_json)
                text = final_data.get("text", "").strip()
                if text:
                    await on_transcript(
                        TranscriptEvent(
                            text=text,
                            is_final=True,
                        )
                    )
            except Exception:
                pass

    async def stop(self) -> None:
        """Stop STT engine and free memory."""
        self.is_running = False
        self.model = None
        if hasattr(self, 'tokenizer'):
            self.tokenizer = None
        if hasattr(self, 'processor'):
            self.processor = None
        logger.info(f"STT engine stopped and memory freed.")
