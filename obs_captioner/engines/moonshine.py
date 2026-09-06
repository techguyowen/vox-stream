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

            # Select optimal device: DirectML (AMD Radeon RX 580 / Intel Arc) → NVIDIA CUDA → Apple MPS → CPU
            from ..hardware import get_torch_device
            self._device, device_label = get_torch_device()
            try:
                self.model = self.model.to(self._device)
                logger.info(f"Moonshine model moved to device: {device_label}")
            except Exception as dev_err:
                logger.warning(f"Could not move Moonshine to {self._device}, using cpu: {dev_err}")
                self._device = "cpu"
                device_label = "CPU"

            if status_callback:
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
        # Max buffer length before forced finalization (default 7.0s for natural preaching flow)
        max_sentence_seconds = getattr(self.config.audio, "max_sentence_duration_seconds", 7.0) or 7.0
        max_bytes = int(self.config.audio.sample_rate * 2 * max_sentence_seconds)
        # Overlap buffer (~350ms of 16kHz PCM audio) to prevent slicing words mid-syllable across chunk cuts
        overlap_bytes = int(self.config.audio.sample_rate * 2 * 0.35) & ~1

        logger.info(f"Moonshine streaming recognition pipeline active (sentence max: {max_sentence_seconds}s).")

        async for chunk in audio_stream:
            if not self.is_running:
                break

            if not chunk:
                continue

            has_speech = self.vad.is_speech(chunk)
            now = time.time()

            pause_break_seconds = (getattr(self.config.audio, "sentence_break_ms", 550) or 550) / 1000.0
            max_sentence_seconds = getattr(self.config.audio, "max_sentence_duration_seconds", 7.0) or 7.0
            max_bytes = int(self.config.audio.sample_rate * 2 * max_sentence_seconds)

            if has_speech:
                buffer.extend(chunk)
                silence_start_time = None
            else:
                if len(buffer) > 0:
                    buffer.extend(chunk)
                    if silence_start_time is None:
                        silence_start_time = now

            # Determine if we should finalize:
            # 1. Natural breath/pause detected (speaker stopped speaking)
            is_silence_timeout = silence_start_time is not None and (now - silence_start_time >= pause_break_seconds)
            # 2. Live interim update every ~1.2s while speaker is talking
            is_interval = (now - last_transcribe_time > 1.2) and len(buffer) >= min_bytes
            # 3. Buffer length ceiling reached: prefer waiting for at least a brief silence dip before forcing cut
            is_soft_full = len(buffer) >= max_bytes and (silence_start_time is not None or not has_speech)
            is_hard_full = len(buffer) >= int(max_bytes * 1.25)  # Hard ceiling if preacher speaks without any breath
            is_full = is_soft_full or is_hard_full

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
                    if is_silence_timeout:
                        # Full breath/silence: safe to clear entire audio buffer
                        buffer.clear()
                    else:
                        # Split occurred during continuous speech: retain trailing ~350ms overlap
                        # so the next chunk starts with the complete transitioning syllable
                        if len(buffer) > overlap_bytes:
                            tail = buffer[-overlap_bytes:]
                            buffer.clear()
                            buffer.extend(tail)
                        else:
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
        from ..hardware import release_stt_memory
        release_stt_memory()
        logger.info("Moonshine engine stopped and memory freed.")
