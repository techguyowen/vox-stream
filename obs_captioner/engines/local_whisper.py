"""Local Faster-Whisper Offline Streaming STT Engine."""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import AsyncGenerator, Optional

try:
    import numpy as np
except ImportError:
    np = None

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig
from ..vad import VoiceActivityDetector

logger = logging.getLogger("obs_captioner.engine.local_whisper")


class LocalWhisperEngine(BaseSTTEngine):
    """Local, offline speech recognition using faster-whisper (CTranslate2)."""

    def __init__(self, config: AppConfig):
        super().__init__("Local Faster-Whisper")
        self.config = config
        self.model = None
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
        )

    async def initialize(self) -> bool:
        """Load Faster-Whisper model into memory (GPU or CPU)."""
        try:
            from faster_whisper import WhisperModel
            import torch

            device = self.config.local_whisper.device
            compute_type = self.config.local_whisper.compute_type

            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"

            model_size = self.config.local_whisper.model_size
            logger.info(f"Loading faster-whisper model '{model_size}' on {device} ({compute_type})...")

            loop = asyncio.get_event_loop()
            self.model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(model_size, device=device, compute_type=compute_type),
            )
            logger.info("Faster-Whisper model loaded successfully.")
            return True
        except ImportError:
            logger.error("faster-whisper is not installed. Install with: pip install faster-whisper")
            return False
        except Exception as e:
            logger.error(f"Failed to load Faster-Whisper model: {e}")
            return False

    def _transcribe_buffer(self, audio_float32: np.ndarray) -> str:
        """Run whisper transcription synchronously in worker thread."""
        try:
            segments, _ = self.model.transcribe(
                audio_float32,
                language=self.config.local_whisper.language or "en",
                beam_size=self.config.local_whisper.beam_size,
                vad_filter=True,
                without_timestamps=True,
            )
            text = " ".join(s.text.strip() for s in segments if s.text.strip())
            return text
        except Exception as e:
            logger.debug(f"Whisper transcription error: {e}")
            return ""

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Accumulate audio chunks and trigger transcription with VAD windowing."""
        self.is_running = True
        loop = asyncio.get_event_loop()

        buffer = bytearray()
        silence_start_time = None
        last_transcribe_time = time.time()
        last_text = ""

        # Min audio before first transcription (~400ms)
        min_bytes = int(self.config.audio.sample_rate * 2 * 0.4)
        # Max buffer length (~10s) before forced finalization
        max_bytes = int(self.config.audio.sample_rate * 2 * 10.0)

        async for chunk in audio_stream:
            if not self.is_running:
                break

            has_speech = self.vad.is_speech(chunk)
            buffer.extend(chunk)

            now = time.time()

            if not has_speech:
                if silence_start_time is None:
                    silence_start_time = now
            else:
                silence_start_time = None

            # Determine if we should transcribe (every ~350ms or when silence detected)
            is_silence_timeout = silence_start_time is not None and (now - silence_start_time > 0.6)
            is_interval = (now - last_transcribe_time > 0.35) and len(buffer) >= min_bytes
            is_full = len(buffer) >= max_bytes

            if (is_interval or is_silence_timeout or is_full) and len(buffer) >= min_bytes:
                # Convert buffer to float32 normalized [-1.0, 1.0]
                audio_i16 = np.frombuffer(buffer, dtype=np.int16)
                audio_f32 = audio_i16.astype(np.float32) / 32768.0

                text = await loop.run_in_executor(None, self._transcribe_buffer, audio_f32)
                last_transcribe_time = now

                if text:
                    last_text = text
                    is_final = is_silence_timeout or is_full
                    await on_transcript(
                        TranscriptEvent(
                            text=text,
                            is_final=is_final,
                        )
                    )

                if is_silence_timeout or is_full:
                    # Clear buffer on sentence pause
                    buffer.clear()
                    silence_start_time = None
                    last_text = ""

    async def stop(self) -> None:
        """Stop local Whisper engine."""
        self.is_running = False
        logger.info("Local Whisper engine stopped.")
