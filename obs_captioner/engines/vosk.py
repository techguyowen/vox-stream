"""Vosk (Kaldi) Local Offline Speech-to-Text Engine."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncGenerator, Optional

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig

logger = logging.getLogger("obs_captioner.engine.vosk")


class VoskEngine(BaseSTTEngine):
    """Local, ultra-low latency continuous offline speech recognition using Vosk (Kaldi)."""

    def __init__(self, config: AppConfig):
        super().__init__("Local Vosk / Kaldi")
        self.config = config
        self.model = None

    async def initialize(self) -> bool:
        """Load or download Vosk model into memory."""
        try:
            import vosk

            # Suppress noisy C-level Kaldi log spam
            vosk.SetLogLevel(-1)

            loop = asyncio.get_event_loop()

            def _load_model():
                # 1. Custom model path if specified
                custom_path = (self.config.vosk.model_path or "").strip()
                if custom_path and Path(custom_path).exists():
                    logger.info(f"Loading custom Vosk model from: {custom_path}")
                    return vosk.Model(model_path=custom_path)

                # 2. Named model presets
                model_name = (self.config.vosk.model_name or "small").strip().lower()
                if model_name in ("accurate", "large", "en-us-0.22", "vosk-model-en-us-0.22"):
                    logger.info("Loading accurate Vosk model (vosk-model-en-us-0.22)...")
                    return vosk.Model(model_name="vosk-model-en-us-0.22")
                else:
                    logger.info("Loading lightweight Vosk model (vosk-model-small-en-us-0.15)...")
                    small_cache = Path.home() / ".cache" / "vosk" / "vosk-model-small-en-us-0.15"
                    if small_cache.exists():
                        return vosk.Model(model_path=str(small_cache))
                    return vosk.Model(model_name="vosk-model-small-en-us-0.15")

            self.model = await loop.run_in_executor(None, _load_model)
            logger.info("Vosk model loaded successfully.")
            return True
        except ImportError:
            logger.error("vosk is not installed. Install with: pip install vosk")
            return False
        except Exception as e:
            logger.error(f"Failed to load Vosk model: {e}")
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

        logger.info("Vosk continuous streaming recognition loop active.")

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
                    if partial_text:
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
        """Stop Vosk engine."""
        self.is_running = False
        logger.info("Vosk STT engine stopped.")
