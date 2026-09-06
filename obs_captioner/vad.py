"""Voice Activity Detection (VAD) and audio level gating."""

import logging
import math
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None

from .music import AcousticMusicDetector

logger = logging.getLogger("obs_captioner.vad")

# Module-level cache so Silero VAD is only loaded from disk once across all engine instances.
# Engine hot-switching creates multiple VoiceActivityDetector objects — caching eliminates
# the ~800ms repeated load cost.
_SILERO_CACHE: dict = {}


class VoiceActivityDetector:
    """Detects voice activity in audio chunks using energy gating, Silero VAD, and music tonality detection."""

    def __init__(
        self,
        sample_rate: int = 16000,
        noise_gate_db: float = -45.0,
        vad_threshold: float = 0.5,
        enable_silero: bool = True,
        suppress_music: bool = True,
    ):
        self.sample_rate = sample_rate
        self.noise_gate_db = noise_gate_db
        self.vad_threshold = vad_threshold
        self.suppress_music = suppress_music
        self.music_detector = AcousticMusicDetector()
        self.silero_model = None
        self.silero_mode = None  # "torch" or "onnx"
        self._onnx_state = None

        if enable_silero:
            try:
                if _SILERO_CACHE:
                    self.silero_model = _SILERO_CACHE["model"]
                    self.silero_mode = _SILERO_CACHE.get("mode", "torch")
                    if self.silero_mode == "onnx":
                        self._onnx_state = np.zeros((2, 1, 128), dtype=np.float32)
                    logger.debug("Silero VAD reused from module cache.")
                else:
                    from pathlib import Path
                    data_dir = Path(__file__).parent / "data"
                    jit_path = data_dir / "silero_vad.jit"
                    onnx_path = data_dir / "silero_vad.onnx"

                    # 1. Primary: Bundled JIT model (PyTorch native, zero torchaudio dependency)
                    if jit_path.exists():
                        try:
                            import torch
                            torch.set_num_threads(1)
                            model = torch.jit.load(str(jit_path), map_location="cpu")
                            model.eval()
                            _SILERO_CACHE["model"] = model
                            _SILERO_CACHE["mode"] = "torch"
                            self.silero_model = model
                            self.silero_mode = "torch"
                            logger.info("Silero VAD loaded successfully from bundled JIT model.")
                        except Exception as e:
                            logger.debug(f"Could not load JIT Silero model: {e}")

                    # 2. Fallback: Bundled ONNX model (pure onnxruntime, zero PyTorch/Torchaudio dependency)
                    if self.silero_model is None and onnx_path.exists():
                        try:
                            import onnxruntime as ort
                            opts = ort.SessionOptions()
                            opts.inter_op_num_threads = 1
                            opts.intra_op_num_threads = 1
                            session = ort.InferenceSession(str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"])
                            _SILERO_CACHE["model"] = session
                            _SILERO_CACHE["mode"] = "onnx"
                            self.silero_model = session
                            self.silero_mode = "onnx"
                            self._onnx_state = np.zeros((2, 1, 128), dtype=np.float32)
                            logger.info("Silero VAD loaded successfully from bundled ONNX model.")
                        except Exception as e:
                            logger.debug(f"Could not load ONNX Silero model: {e}")

                    if self.silero_model is None:
                        logger.debug("Silero VAD bundled models not found; using energy-based VAD.")

            except Exception as e:
                logger.debug(f"Silero VAD not available ({e}). Using energy-based VAD.")

    def update_config(self, audio_config) -> None:
        """Live update threshold, noise gate, and music suppression parameters."""
        self.sample_rate = getattr(audio_config, "sample_rate", self.sample_rate)
        self.noise_gate_db = getattr(audio_config, "noise_gate_db", self.noise_gate_db)
        self.vad_threshold = getattr(audio_config, "vad_threshold", self.vad_threshold)
        self.suppress_music = getattr(audio_config, "suppress_music", self.suppress_music)

    def is_music(self, audio_chunk_bytes: bytes) -> bool:
        """Return True if sustained acoustic music (chords, organ, worship pads) is detected."""
        return self.music_detector.process_chunk(
            audio_chunk_bytes,
            sample_rate=self.sample_rate,
            noise_gate_db=self.noise_gate_db,
        )

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
            self.music_detector.reset()
            return False

        # 2. Acoustic music detection if suppress_music is enabled
        is_music_active = False
        if self.suppress_music:
            is_music_active = self.is_music(audio_chunk_bytes)

        # 3. Silero VAD check if available
        if self.silero_model is not None:
            try:
                audio_array = np.frombuffer(audio_chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                window_size = 512 if self.sample_rate == 16000 else 256
                max_prob = 0.0

                if self.silero_mode == "onnx":
                    if self._onnx_state is None:
                        self._onnx_state = np.zeros((2, 1, 128), dtype=np.float32)
                    sr_val = np.array(self.sample_rate, dtype=np.int64)
                    for i in range(0, len(audio_array) - window_size + 1, window_size):
                        chunk_slice = audio_array[i : i + window_size].reshape(1, -1)
                        out, self._onnx_state = self.silero_model.run(
                            None, {"input": chunk_slice, "state": self._onnx_state, "sr": sr_val}
                        )
                        prob = float(out[0][0])
                        if prob > max_prob:
                            max_prob = prob
                else:
                    import torch
                    for i in range(0, len(audio_array) - window_size + 1, window_size):
                        slice_tensor = torch.from_numpy(audio_array[i : i + window_size])
                        prob = self.silero_model(slice_tensor, self.sample_rate).item()
                        if prob > max_prob:
                            max_prob = prob

                # If sustained music is detected and speech confidence is not decisively high, suppress speech
                if is_music_active and max_prob < 0.75:
                    return False

                return max_prob >= self.vad_threshold
            except Exception as e:
                logger.debug(f"Silero inference error: {e}")
                if is_music_active:
                    return False
                # Fallback to energy check if Silero fails
                return db >= self.noise_gate_db

        # If no Silero and music detected, suppress
        if is_music_active:
            return False

        # If no Silero, energy above noise gate is considered active
        return True
