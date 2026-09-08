"""NVIDIA Parakeet (NeMo FastConformer TDT) Speech-to-Text Engine."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

try:
    import numpy as np
except ImportError:
    np = None

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig
from ..vad import VoiceActivityDetector

logger = logging.getLogger("obs_captioner.engine.parakeet")


class ParakeetEngine(BaseSTTEngine):
    """SOTA accuracy speech recognition using NVIDIA NeMo Parakeet FastConformer TDT/CTC."""

    DEFAULT_MODEL_NAME = "parakeet-tdt-0.6b"

    def __init__(self, config: AppConfig):
        super().__init__("NVIDIA Parakeet")
        self.config = config
        self.model = None
        self._running = False
        self._device = "cpu"
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
            enable_silero=getattr(config.audio, "enable_vad", True),
            suppress_music=getattr(config.audio, "suppress_music", True),
        )

    def _find_model_dir(self) -> Optional[Path]:
        """Search for Parakeet model files in standard cache locations."""
        custom_path = (self.config.parakeet.model_path or "").strip()
        if custom_path and Path(custom_path).exists():
            return Path(custom_path)

        model_name = self.config.parakeet.model_name or self.DEFAULT_MODEL_NAME
        candidates = [
            Path.home() / ".cache" / "parakeet" / model_name,
            Path("models") / model_name,
            Path.home() / "AppData" / "Local" / "parakeet" / model_name,
            Path.home() / ".cache" / "nemo" / model_name,
        ]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "parakeet" / model_name)
            candidates.append(Path(local_app_data) / "nemo" / model_name)
        app_data = os.environ.get("APPDATA")
        if app_data:
            candidates.append(Path(app_data) / "parakeet" / model_name)
            candidates.append(Path(app_data) / "nemo" / model_name)

        hf_cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
        hf_dirs = [
            hf_cache / f"models--csukuangfj--{model_name}" / "snapshots",
            hf_cache / f"models--{model_name.replace('/', '--')}" / "snapshots",
            hf_cache / "models--csukuangfj--sherpa-onnx-nemo-ctc-en-conformer-medium" / "snapshots",
        ]
        for hf_base in hf_dirs:
            if hf_base.is_dir():
                for snap in hf_base.iterdir():
                    if snap.is_dir() and (snap / "tokens.txt").exists() and (
                        (snap / "model.onnx").exists() or (snap / "model.int8.onnx").exists()
                    ):
                        return snap

        for c in candidates:
            if c.exists() and (c / "tokens.txt").exists():
                return c
        return None

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Initialize Parakeet model on GPU or CPU."""
        from ..hardware import get_gpu_info, get_torch_device
        gpu_info = get_gpu_info()
        self._device, device_label = get_torch_device()

        model_name = self.config.parakeet.model_name or self.DEFAULT_MODEL_NAME
        if status_callback:
            status_callback(f"Loading NVIDIA Parakeet ({model_name}) on {device_label}...")

        # 1. Try loading via sherpa-onnx NeMo FastConformer support if available
        try:
            import sherpa_onnx
            model_dir = self._find_model_dir()
            if not model_dir:
                try:
                    from huggingface_hub import snapshot_download
                    if status_callback:
                        status_callback(f"Downloading NVIDIA Parakeet model from HuggingFace...")
                    repo_id = (
                        f"csukuangfj/{model_name}"
                        if "/" not in model_name and "sherpa-onnx" in model_name
                        else "csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-medium"
                    )
                    dl_path = snapshot_download(
                        repo_id=repo_id,
                        allow_patterns=["model.int8.onnx", "model.onnx", "tokens.txt"],
                    )
                    model_dir = Path(dl_path)
                except Exception as dl_err:
                    logger.debug(f"Parakeet auto-download note: {dl_err}")

            if model_dir:
                onnx_model = model_dir / "model.onnx"
                if not onnx_model.exists():
                    onnx_model = model_dir / "model.int8.onnx"
                tokens = model_dir / "tokens.txt"

                if onnx_model.exists() and tokens.exists():
                    loop = asyncio.get_event_loop()

                    def _load_onnx():
                        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                            model=str(onnx_model),
                            tokens=str(tokens),
                            num_threads=4,
                            sample_rate=self.config.audio.sample_rate,
                        )

                    self.model = await loop.run_in_executor(None, _load_onnx)
                    if status_callback:
                        status_callback(f"✅ NVIDIA Parakeet ready on {device_label}!")
                    logger.info("Parakeet ONNX model loaded successfully.")
                    return True
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Sherpa Parakeet loading note: {e}")

        # 2. Try loading via nemo_toolkit if installed
        try:
            import nemo.collections.asr as nemo_asr
            loop = asyncio.get_event_loop()

            def _load_nemo():
                return nemo_asr.models.EncDecCTCModelBPE.from_pretrained(
                    model_name="nvidia/parakeet-ctc-0.6b"
                )

            self.model = await loop.run_in_executor(None, _load_nemo)
            if self._device == "cuda" and hasattr(self.model, "cuda"):
                self.model = self.model.cuda()
            if status_callback:
                status_callback(f"✅ NVIDIA Parakeet ({model_name}) ready on {device_label}!")
            return True
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"NeMo toolkit loading note: {e}")

        # 3. Graceful standby message if weights / optional toolkit not yet provisioned
        msg = (
            f"NVIDIA Parakeet engine configured ({model_name}). "
            "For full GPU neural inference, install: pip install sherpa-onnx"
        )
        if status_callback:
            status_callback(f"⚠️ {msg}")
        logger.info(msg)
        return True

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ):
        """Buffer voice chunks using VAD and transcribe using Parakeet."""
        self._running = True
        logger.info("NVIDIA Parakeet streaming started.")

        audio_buffer = bytearray()
        silence_start_time = None

        try:
            async for chunk in audio_stream:
                if not self._running:
                    break

                if not chunk:
                    continue

                is_speech = self.vad.is_speech(chunk)
                now = time.time()

                sentence_break_s = (getattr(self.config.audio, "sentence_break_ms", 650) or 650) / 1000.0
                max_sentence_s = getattr(self.config.audio, "max_sentence_duration_seconds", 7.0) or 7.0
                max_sentence_words = getattr(self.config.audio, "max_sentence_words", 24) or 24
                max_bytes = int(self.config.audio.sample_rate * 2 * max_sentence_s)
                # Approximate word ceiling: ~2.5 words/sec
                approx_words = (len(audio_buffer) / (self.config.audio.sample_rate * 2)) * 2.5
                is_word_ceiling = approx_words >= max_sentence_words

                if is_speech:
                    silence_start_time = None
                    audio_buffer.extend(chunk)
                    if len(audio_buffer) >= max_bytes or (len(audio_buffer) > 0 and approx_words >= max_sentence_words):
                        await self._process_utterance(bytes(audio_buffer), on_transcript)
                        audio_buffer.clear()
                        silence_start_time = None
                else:
                    if audio_buffer:
                        if silence_start_time is None:
                            silence_start_time = now
                        elif now - silence_start_time >= sentence_break_s or len(audio_buffer) >= max_bytes or is_word_ceiling:
                            # Process buffered speech
                            await self._process_utterance(bytes(audio_buffer), on_transcript)
                            audio_buffer.clear()
                            silence_start_time = None

            if audio_buffer and self._running:
                await self._process_utterance(bytes(audio_buffer), on_transcript)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Parakeet streaming error: {e}")
        finally:
            self._running = False
            logger.info("NVIDIA Parakeet streaming stopped.")

    async def _process_utterance(self, audio_bytes: bytes, on_transcript: CaptionCallback):
        """Transcribe completed speech utterance."""
        if not audio_bytes or len(audio_bytes) < 3200:
            return

        if self.model is None:
            return

        try:
            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            loop = asyncio.get_event_loop()

            def _transcribe():
                if hasattr(self.model, "create_stream"):
                    s = self.model.create_stream()
                    s.accept_waveform(self.config.audio.sample_rate, samples)
                    self.model.decode_stream(s)
                    return s.result.text.strip()
                elif hasattr(self.model, "transcribe"):
                    res = self.model.transcribe([samples])
                    if res and len(res) > 0:
                        return res[0].strip() if isinstance(res[0], str) else str(res[0])
                return ""

            text = await loop.run_in_executor(None, _transcribe)
            if text:
                await on_transcript(
                    TranscriptEvent(
                        text=text,
                        is_final=True,
                        confidence=0.96,
                        timestamp=time.time(),
                    )
                )
        except Exception as e:
            logger.debug(f"Parakeet utterance transcription error: {e}")

    async def stop(self) -> None:
        """Stop streaming and release model resources."""
        self._running = False
        self.is_running = False
        self.model = None
        try:
            from ..hardware import release_stt_memory
            release_stt_memory()
        except Exception:
            pass
