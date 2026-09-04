"""Voice Activity Detection (VAD) and audio level gating."""

import logging
import math
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger("obs_captioner.vad")

# Module-level cache so Silero VAD is only loaded from disk once across all engine instances.
# Engine hot-switching creates multiple VoiceActivityDetector objects — caching eliminates
# the ~800ms repeated load cost.
_SILERO_CACHE: dict = {}


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
                if _SILERO_CACHE:
                    # Reuse already-loaded model — avoids repeated disk I/O on engine switches
                    self.silero_model = _SILERO_CACHE["model"]
                    self.silero_utils = _SILERO_CACHE["utils"]
                    logger.debug("Silero VAD reused from module cache (no reload needed).")
                else:
                    model, utils = torch.hub.load(
                        repo_or_dir="snakers4/silero-vad",
                        model="silero_vad",
                        force_reload=False,
                        onnx=False,
                        trust_repo=True,
                        verbose=False,
                    )
                    _SILERO_CACHE["model"] = model
                    _SILERO_CACHE["utils"] = utils
                    self.silero_model = model
                    self.silero_utils = utils
                    logger.info("Silero VAD model loaded and cached successfully.")
            except Exception as e:
                logger.debug(f"Silero VAD not available ({e}). Using energy-based VAD.")

    def update_config(self, audio_config) -> None:
        """Live update threshold and noise gate parameters."""
        self.sample_rate = getattr(audio_config, "sample_rate", self.sample_rate)
        self.noise_gate_db = getattr(audio_config, "noise_gate_db", self.noise_gate_db)
        self.vad_threshold = getattr(audio_config, "vad_threshold", self.vad_threshold)

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
                window_size = 512 if self.sample_rate == 16000 else 256
                max_prob = 0.0
                for i in range(0, len(audio_array) - window_size + 1, window_size):
                    slice_tensor = torch.from_numpy(audio_array[i : i + window_size])
                    prob = self.silero_model(slice_tensor, self.sample_rate).item()
                    if prob > max_prob:
                        max_prob = prob
                return max_prob >= self.vad_threshold
            except Exception as e:
                logger.debug(f"Silero inference error: {e}")
                # Fallback to energy check if Silero fails
                return db >= self.noise_gate_db

        # If no Silero, energy above noise gate is considered active
        return True
