"""Local Faster-Whisper Offline Streaming STT Engine."""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import AsyncGenerator, Callable, Optional

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

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Load Faster-Whisper model into memory (GPU or CPU) with live progress reporting."""
        try:
            from faster_whisper import WhisperModel
            import torch

            device = self.config.local_whisper.device
            compute_type = self.config.local_whisper.compute_type

            if device == "auto":
                if torch.cuda.is_available():
                    device = "cuda"
                elif torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"
            if compute_type == "auto":
                if device == "cuda":
                    compute_type = "float16"
                elif device == "mps":
                    # CTranslate2 MPS backend requires float32
                    compute_type = "float32"
                else:
                    compute_type = "int8"

            model_size = self.config.local_whisper.model_size or "base.en"
            logger.info(f"Faster-Whisper device selected: {device} ({compute_type})")
            if status_callback:
                status_callback(f"Downloading/loading Faster-Whisper '{model_size}' model ({device}, {compute_type})...")
            logger.info(f"Loading faster-whisper model '{model_size}' on {device} ({compute_type})...")

            loop = asyncio.get_event_loop()
            self.model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(model_size, device=device, compute_type=compute_type),
            )
            if status_callback:
                status_callback(f"✅ Faster-Whisper ({model_size}) ready!")
            logger.info("Faster-Whisper model loaded successfully.")
            return True
        except ImportError:
            err = "faster-whisper is not installed. Install with: pip install faster-whisper torch"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False
        except Exception as e:
            err = f"Failed to load Faster-Whisper model: {e}"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False

    def _build_initial_prompt(self) -> str:
        """Construct domain-optimized prompt to prime Whisper's vocabulary and formatting."""
        terms = []
        if getattr(self.config, "gemini_live", None) and self.config.gemini_live.custom_vocabulary:
            terms.extend([v for v in self.config.gemini_live.custom_vocabulary if v.strip()])
        if getattr(self.config, "vocabulary", None) and getattr(self.config.vocabulary, "terms", None):
            terms.extend([v for v in self.config.vocabulary.terms.values() if v.strip()])
        if getattr(self.config, "censor", None) and getattr(self.config.censor, "custom_whitelist", None):
            terms.extend([v for v in self.config.censor.custom_whitelist if v.strip()])
        if getattr(self.config.general, "church_mode", True):
            church_terms = [
                "Scripture", "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
                "Matthew", "Mark", "Luke", "John", "Romans", "Corinthians", "Galatians",
                "Ephesians", "Philippians", "Colossians", "Thessalonians", "Timothy",
                "Hebrews", "Revelation", "Nebuchadnezzar", "Melchizedek", "Zephaniah",
                "Habakkuk", "Septuagint", "Golgotha", "propitiation", "sanctification",
                "justification", "covenant", "Jesus Christ", "Holy Spirit", "Hallelujah", "Amen"
            ]
            for ct in church_terms:
                if ct not in terms:
                    terms.append(ct)
        if terms:
            return f"Live captions and sermon transcription: {', '.join(terms[:60])}."
        return "Live captions: accurate speech transcription with proper capitalization and punctuation."

    def _transcribe_buffer(self, audio_float32: np.ndarray) -> str:
        """Run whisper transcription synchronously in worker thread."""
        try:
            prompt = self._build_initial_prompt()
            beam_sz = self.config.local_whisper.beam_size or 1
            segments, _ = self.model.transcribe(
                audio_float32,
                language=self.config.local_whisper.language or "en",
                beam_size=beam_sz,
                vad_filter=False,
                condition_on_previous_text=False,
                temperature=0.0,
                repetition_penalty=1.1,
                no_speech_threshold=0.6,
                suppress_blank=True,
                initial_prompt=prompt,
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

            if not chunk:
                continue

            has_speech = self.vad.is_speech(chunk)
            now = time.time()

            if has_speech:
                buffer.extend(chunk)
                silence_start_time = None
            else:
                if len(buffer) > 0:
                    buffer.extend(chunk)
                    if silence_start_time is None:
                        silence_start_time = now

            # Determine if we should transcribe (every ~350ms or when silence detected)
            is_silence_timeout = silence_start_time is not None and (now - silence_start_time > 0.5)
            is_interval = (now - last_transcribe_time > 0.35) and len(buffer) >= min_bytes
            is_full = len(buffer) >= max_bytes

            if (is_interval or is_silence_timeout or is_full) and len(buffer) >= min_bytes:
                # Ensure even number of bytes to prevent np.frombuffer ValueError
                even_len = len(buffer) & ~1
                # Convert buffer to float32 normalized [-1.0, 1.0]
                audio_i16 = np.frombuffer(buffer[:even_len], dtype=np.int16)
                audio_f32 = audio_i16.astype(np.float32) / 32768.0

                text = await loop.run_in_executor(None, self._transcribe_buffer, audio_f32)
                last_transcribe_time = now

                is_final = is_silence_timeout or is_full

                if text and text.strip():
                    await on_transcript(
                        TranscriptEvent(
                            text=text.strip(),
                            is_final=is_final,
                        )
                    )

                if is_final:
                    buffer.clear()
                    silence_start_time = None

        # Flush remaining audio buffer when stream terminates
        if len(buffer) >= min_bytes and self.is_running:
            even_len = len(buffer) & ~1
            audio_i16 = np.frombuffer(buffer[:even_len], dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32768.0
            text = await loop.run_in_executor(None, self._transcribe_buffer, audio_f32)
            if text and text.strip():
                await on_transcript(
                    TranscriptEvent(
                        text=text.strip(),
                        is_final=True,
                    )
                )
            buffer.clear()

    async def stop(self) -> None:
        """Stop STT engine and free memory."""
        self.is_running = False
        self.model = None
        if hasattr(self, 'tokenizer'):
            self.tokenizer = None
        if hasattr(self, 'processor'):
            self.processor = None
        logger.info(f"STT engine stopped and memory freed.")
