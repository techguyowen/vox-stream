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

            device = (self.config.local_whisper.device or "auto").strip().lower()
            compute_type = (self.config.local_whisper.compute_type or "auto").strip().lower()

            # CTranslate2 supports NVIDIA CUDA and CPU (Apple Accelerate / NEON). It does not support MPS.
            if device == "mps":
                logger.info("CTranslate2 / Faster-Whisper does not support Apple MPS. Using high-performance CPU (Accelerate/NEON).")
                device = "cpu"
            elif device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"

            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"

            model_size = self.config.local_whisper.model_size or "base.en"
            device_label = "NVIDIA CUDA GPU" if device == "cuda" else "CPU (Accelerate)"
            logger.info(f"Faster-Whisper loading on {device_label} [device={device}, compute_type={compute_type}]")
            if status_callback:
                status_callback(f"Loading Faster-Whisper '{model_size}' on {device_label} ({compute_type})...")

            def _load_whisper():
                try:
                    return WhisperModel(model_size, device=device, compute_type=compute_type)
                except Exception as first_err:
                    if device == "cuda":
                        logger.warning(f"CUDA initialization failed ({first_err}). Falling back to CPU...")
                        return WhisperModel(model_size, device="cpu", compute_type="int8")
                    raise first_err

            loop = asyncio.get_event_loop()
            self.model = await loop.run_in_executor(None, _load_whisper)
            if status_callback:
                status_callback(f"✅ Faster-Whisper ({model_size}) ready on {device_label}!")
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
        """Construct natural prompt to establish capitalization, punctuation, and domain style without prompting hallucinations."""
        if getattr(self.config.general, "church_mode", True):
            return "The following is a live church sermon and scripture reading with accurate transcription, proper punctuation, and capitalization."
        return "The following is a live speech stream with accurate transcription, proper punctuation, and capitalization."

    def _build_hotwords(self) -> str:
        """Construct domain-optimized hotwords string for faster-whisper vocabulary biasing."""
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
        return " ".join(terms[:60])

    def _transcribe_buffer(self, audio_float32: np.ndarray) -> str:
        """Run whisper transcription synchronously in worker thread with hallucination rejection."""
        try:
            prompt = self._build_initial_prompt()
            hotwords = self._build_hotwords()
            beam_sz = self.config.local_whisper.beam_size or 1
            segments, _ = self.model.transcribe(
                audio_float32,
                language=self.config.local_whisper.language or "en",
                beam_size=beam_sz,
                vad_filter=True,
                condition_on_previous_text=False,
                temperature=0.0,
                repetition_penalty=1.1,
                no_speech_threshold=0.6,
                suppress_blank=True,
                initial_prompt=prompt,
                hotwords=hotwords,
                without_timestamps=True,
            )
            valid_segments = [
                s.text.strip()
                for s in segments
                if s.text.strip() and getattr(s, "no_speech_prob", 0.0) < 0.6
            ]
            return " ".join(valid_segments)
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
        # Max buffer length before forced finalization
        max_sentence_seconds = getattr(self.config.audio, "max_sentence_duration_seconds", 4.5)
        max_bytes = int(self.config.audio.sample_rate * 2 * max_sentence_seconds)

        async for chunk in audio_stream:
            if not self.is_running:
                break

            if not chunk:
                continue

            has_speech = self.vad.is_speech(chunk)
            now = time.time()

            pause_break_seconds = getattr(self.config.audio, "sentence_break_ms", 450) / 1000.0
            max_sentence_seconds = getattr(self.config.audio, "max_sentence_duration_seconds", 4.5)
            max_bytes = int(self.config.audio.sample_rate * 2 * max_sentence_seconds)

            if has_speech:
                buffer.extend(chunk)
                silence_start_time = None
            else:
                if len(buffer) > 0:
                    buffer.extend(chunk)
                    if silence_start_time is None:
                        silence_start_time = now

            # Determine if we should transcribe (every ~350ms or when silence detected)
            is_silence_timeout = silence_start_time is not None and (now - silence_start_time >= pause_break_seconds)
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
