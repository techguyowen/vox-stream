"""Useful Sensors Moonshine Local Offline Speech-to-Text Engine."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncGenerator, Callable, Optional

# Force Keras to use PyTorch backend before any imports
os.environ["KERAS_BACKEND"] = "torch"

try:
    import numpy as np
except ImportError:
    np = None

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig
from ..vad import VoiceActivityDetector

logger = logging.getLogger("obs_captioner.engine.moonshine")


class MoonshineEngine(BaseSTTEngine):
    """Local, 5x faster variable-length neural speech recognition using Moonshine."""

    def __init__(self, config: AppConfig):
        super().__init__("Local Moonshine")
        self.config = config
        self.model = None
        self.tokenizer = None
        self._device = "cpu"
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
        )

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Load Moonshine model into memory with progress updates."""
        try:
            model_name = (self.config.moonshine.model_name or "moonshine/tiny").strip()
            if not model_name.startswith("moonshine/"):
                model_name = f"moonshine/{model_name}"

            if status_callback:
                status_callback(f"Checking Moonshine cache / downloading {model_name}...")
            logger.info(f"Loading Moonshine model '{model_name}' (PyTorch CPU/GPU)...")

            import tokenizers
            import moonshine

            loop = asyncio.get_event_loop()

            def _load():
                if status_callback:
                    status_callback(f"Downloading/loading neural weights for {model_name}...")
                m = moonshine.load_model(model_name)
                if status_callback:
                    status_callback("Loading neural tokenizer...")
                tok_file = moonshine.ASSETS_DIR / "tokenizer.json"
                tok = tokenizers.Tokenizer.from_file(str(tok_file))
                return m, tok

            self.model, self.tokenizer = await loop.run_in_executor(None, _load)

            # Select best available device: NVIDIA CUDA → Apple MPS → CPU
            import torch
            if torch.cuda.is_available():
                self._device = "cuda"
            elif torch.backends.mps.is_available():
                self._device = "mps"
            else:
                self._device = "cpu"
            try:
                self.model = self.model.to(self._device)
                logger.info(f"Moonshine model moved to device: {self._device}")
            except Exception as dev_err:
                logger.warning(f"Could not move Moonshine to {self._device}, using cpu: {dev_err}")
                self._device = "cpu"

            if status_callback:
                device_label = self._device.upper()
                status_callback(f"✅ Moonshine ({model_name}) ready on {device_label}!")
            logger.info("Moonshine model and tokenizer loaded successfully.")
            return True
        except ImportError as ie:
            err = f"useful-moonshine dependencies missing: {ie}. Install with: pip install useful-moonshine"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False
        except Exception as e:
            err = f"Failed to load Moonshine model: {e}"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False

    def _transcribe_buffer(self, audio_float32: np.ndarray) -> str:
        """Run Moonshine forward inference synchronously in worker thread with torch inference mode."""
        try:
            import torch
            with torch.inference_mode():
                # Moonshine expects shape (1, N) of 16kHz float32
                if audio_float32.ndim == 1:
                    audio_input = np.expand_dims(audio_float32, axis=0)
                else:
                    audio_input = audio_float32

                # Move tensor to GPU (CUDA/MPS) if available for accelerated inference
                audio_tensor = torch.from_numpy(audio_input).to(self._device)
                tokens = self.model.generate(audio_tensor)
                decoded = self.tokenizer.decode_batch(tokens)
                if decoded and len(decoded) > 0:
                    return decoded[0].strip()
                return ""
        except Exception as e:
            logger.debug(f"Moonshine transcription error: {e}")
            return ""

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Accumulate voice phrases using VAD and transcribe dynamically."""
        self.is_running = True
        loop = asyncio.get_running_loop()

        buffer = bytearray()
        silence_start_time = None
        last_transcribe_time = time.time()

        # Min audio before first transcription (~250ms)
        min_bytes = int(self.config.audio.sample_rate * 2 * 0.25)
        # Max buffer length before forced finalization
        max_sentence_seconds = getattr(self.config.audio, "max_sentence_duration_seconds", 4.5)
        max_bytes = int(self.config.audio.sample_rate * 2 * max_sentence_seconds)

        logger.info("Moonshine streaming recognition pipeline active.")

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

            # Determine if we should transcribe (speech pause detected or live interim interval ~1.2s)
            is_silence_timeout = silence_start_time is not None and (now - silence_start_time >= pause_break_seconds)
            is_interval = (now - last_transcribe_time > 1.2) and len(buffer) >= min_bytes
            is_full = len(buffer) >= max_bytes

            if (is_silence_timeout or is_interval or is_full) and len(buffer) >= min_bytes:
                even_len = len(buffer) & ~1
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

        # Flush remaining buffer at stream end
        if len(buffer) >= min_bytes:
            even_len = len(buffer) & ~1
            audio_i16 = np.frombuffer(buffer[:even_len], dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32768.0
            text = await loop.run_in_executor(None, self._transcribe_buffer, audio_f32)
            if text and text.strip():
                await on_transcript(TranscriptEvent(text=text.strip(), is_final=True))
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
