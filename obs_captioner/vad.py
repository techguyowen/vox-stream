"""Voice Activity Detection (VAD) and audio level gating."""

import logging
import math
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger("obs_captioner.vad")


class VoiceActivityDetector:
    """Detects voice activity in audio chunks using energy gating and optional Silero VAD."""

    def __init__(self, sample_rate: int = 16000, noise_gate_db: float = -45.0, vad_threshold: float = 0.5, enable_silero: bool = True):
        self.sample_rate = sample_rate
        self.noise_gate_db = noise_gate_db
        self.vad_threshold = vad_threshold
        self.silero_model = None
        self.silero_utils = None

        if enable_silero:
            try:
                import torch
                # Suppress torch / hub download noise
                torch.set_num_threads(1)
                model, utils = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    force_reload=False,
                    onnx=False,
                    trust_repo=True,
                    verbose=False,
                )
                self.silero_model = model
                self.silero_utils = utils
                logger.info("Silero VAD model loaded successfully.")
            except Exception as e:
                logger.debug(f"Silero VAD not available ({e}). Using energy-based VAD.")

    def calculate_rms_db(self, audio_chunk_bytes: bytes) -> float:
        """Calculate Root Mean Square (RMS) energy in decibels (dBFS) for 16-bit linear PCM."""
        if not audio_chunk_bytes:
            return -100.0
        
        try:
            audio_array = np.frombuffer(audio_chunk_bytes, dtype=np.int16)
            if len(audio_array) == 0:
                return -100.0
            rms = np.sqrt(np.mean(audio_array.astype(np.float64) ** 2))
        except Exception:
            import struct
            count = len(audio_chunk_bytes) // 2
            if count == 0:
                return -100.0
            shorts = struct.unpack(f"<{count}h", audio_chunk_bytes[: count * 2])
            sum_sq = sum(s * s for s in shorts)
            rms = math.sqrt(sum_sq / count)

        if rms <= 0:
            return -100.0
        
        # Max amplitude for 16-bit is 32767
        db = 20 * math.log10(rms / 32767.0)
        return db

    def is_speech(self, audio_chunk_bytes: bytes) -> bool:
        """Return True if speech is detected in the audio chunk."""
        # 1. Noise gate check
        db = self.calculate_rms_db(audio_chunk_bytes)
        if db < self.noise_gate_db:
            return False

        # 2. Silero VAD check if available
        if self.silero_model is not None:
            try:
                import torch
                audio_array = np.frombuffer(audio_chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                tensor = torch.from_numpy(audio_array)
                speech_prob = self.silero_model(tensor, self.sample_rate).item()
                return speech_prob >= self.vad_threshold
            except Exception as e:
                logger.debug(f"Silero inference error: {e}")
                # Fallback to energy check if Silero fails
                return True

        # If no Silero, energy above noise gate is considered active
        return True
