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


PARAKEET_MODELS = {
    "parakeet-fastconformer-large-24500": {
        "repo_id": "csukuangfj/sherpa-onnx-nemo-fast-conformer-ctc-en-24500",
        "type": "ctc",
    },
    "parakeet-tdt-0.6b": {
        "repo_id": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "type": "transducer",
    },
    "parakeet-ctc-large": {
        "repo_id": "csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-large",
        "type": "ctc",
    },
    "parakeet-ctc-medium": {
        "repo_id": "csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-medium",
        "type": "ctc",
    },
    # Backwards compatibility aliases
    "parakeet-ctc-0.6b": {
        "repo_id": "csukuangfj/sherpa-onnx-nemo-fast-conformer-ctc-en-24500",
        "type": "ctc",
    },
    "parakeet-tdt-1.1b": {
        "repo_id": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "type": "transducer",
    },
    "parakeet-nemo": {
        "repo_id": "csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-medium",
        "type": "ctc",
    },
}


def resolve_parakeet_repo(model_name: str) -> str:
    cleaned = (model_name or "").strip()
    if cleaned in PARAKEET_MODELS:
        return PARAKEET_MODELS[cleaned]["repo_id"]
    if "/" in cleaned:
        return cleaned
    if "sherpa-onnx" in cleaned:
        return f"csukuangfj/{cleaned}"
    return PARAKEET_MODELS["parakeet-fastconformer-large-24500"]["repo_id"]


class ParakeetEngine(BaseSTTEngine):
    """SOTA accuracy speech recognition using NVIDIA NeMo Parakeet FastConformer TDT/CTC."""

    DEFAULT_MODEL_NAME = "parakeet-fastconformer-large-24500"

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

    @staticmethod
    def _is_valid_model_dir(dir_path: Path) -> bool:
        if not dir_path.is_dir():
            return False
        if not (dir_path / "tokens.txt").exists():
            return False
        has_ctc = (dir_path / "model.onnx").exists() or (dir_path / "model.int8.onnx").exists()
        has_tdt = (
            any(dir_path.glob("encoder*.onnx"))
            and any(dir_path.glob("decoder*.onnx"))
            and any(dir_path.glob("joiner*.onnx"))
        )
        return has_ctc or has_tdt

    def _find_model_dir(self) -> Optional[Path]:
        """Search for Parakeet model files in standard cache locations."""
        custom_path = (self.config.parakeet.model_path or "").strip()
        if custom_path and Path(custom_path).exists():
            cp = Path(custom_path)
            if self._is_valid_model_dir(cp):
                return cp

        model_name = self.config.parakeet.model_name or self.DEFAULT_MODEL_NAME
        repo_id = resolve_parakeet_repo(model_name)
        repo_slug = repo_id.replace("/", "--")
        short_name = repo_id.split("/")[-1]

        candidates = [
            Path.home() / ".cache" / "parakeet" / model_name,
            Path.home() / ".cache" / "parakeet" / short_name,
            Path("models") / model_name,
            Path("models") / short_name,
            Path.home() / "AppData" / "Local" / "parakeet" / model_name,
            Path.home() / "AppData" / "Local" / "parakeet" / short_name,
            Path.home() / ".cache" / "nemo" / model_name,
        ]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "parakeet" / model_name)
            candidates.append(Path(local_app_data) / "parakeet" / short_name)
            candidates.append(Path(local_app_data) / "nemo" / model_name)
        app_data = os.environ.get("APPDATA")
        if app_data:
            candidates.append(Path(app_data) / "parakeet" / model_name)
            candidates.append(Path(app_data) / "parakeet" / short_name)
            candidates.append(Path(app_data) / "nemo" / model_name)

        hf_cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
        hf_dirs = [
            hf_cache / f"models--{repo_slug}" / "snapshots",
            hf_cache / f"models--csukuangfj--{short_name}" / "snapshots",
            hf_cache / f"models--csukuangfj--{model_name}" / "snapshots",
            hf_cache / "models--csukuangfj--sherpa-onnx-nemo-ctc-en-conformer-medium" / "snapshots",
        ]
        for hf_base in hf_dirs:
            if hf_base.is_dir():
                for snap in hf_base.iterdir():
                    if self._is_valid_model_dir(snap):
                        return snap

        for c in candidates:
            if self._is_valid_model_dir(c):
                return c
        return None

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Initialize Parakeet model on GPU or CPU."""
        from ..hardware import get_gpu_info, get_torch_device
        gpu_info = get_gpu_info()
        self._device, device_label = get_torch_device()

        model_name = self.config.parakeet.model_name or self.DEFAULT_MODEL_NAME
        repo_id = resolve_parakeet_repo(model_name)
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
                        status_callback(f"Downloading NVIDIA Parakeet model ({repo_id}) from HuggingFace...")
                    dl_path = snapshot_download(
                        repo_id=repo_id,
                        allow_patterns=["*.onnx", "tokens.txt"],
                    )
                    model_dir = Path(dl_path)
                except Exception as dl_err:
                    logger.debug(f"Parakeet auto-download note: {dl_err}")

            if model_dir and self._is_valid_model_dir(model_dir):
                loop = asyncio.get_event_loop()

                cfg_threads = int(getattr(self.config.parakeet, "num_threads", 4) or 4)
                configured_device = getattr(self.config.parakeet, "device", "auto") or "auto"
                provider = "cuda" if (configured_device == "cuda" or (configured_device == "auto" and self._device == "cuda")) else "cpu"
                tokens = model_dir / "tokens.txt"

                # A. Transducer (encoder, decoder, joiner)
                encoder_files = list(model_dir.glob("encoder*.onnx"))
                decoder_files = list(model_dir.glob("decoder*.onnx"))
                joiner_files = list(model_dir.glob("joiner*.onnx"))

                if encoder_files and decoder_files and joiner_files and tokens.exists():
                    encoder_path = sorted(encoder_files, key=lambda p: (0 if p.name.endswith(".int8.onnx") else 1, p.name))[0]
                    decoder_path = sorted(decoder_files, key=lambda p: (0 if p.name.endswith(".int8.onnx") else 1, p.name))[0]
                    joiner_path = sorted(joiner_files, key=lambda p: (0 if p.name.endswith(".int8.onnx") else 1, p.name))[0]

                    def _load_transducer():
                        return sherpa_onnx.OfflineRecognizer.from_transducer(
                            encoder=str(encoder_path),
                            decoder=str(decoder_path),
                            joiner=str(joiner_path),
                            tokens=str(tokens),
                            num_threads=cfg_threads,
                            sample_rate=self.config.audio.sample_rate,
                            provider=provider,
                        )

                    self.model = await loop.run_in_executor(None, _load_transducer)
                    if status_callback:
                        status_callback(f"✅ NVIDIA Parakeet TDT ready on {device_label} ({provider.upper()})!")
                    logger.info("Parakeet Transducer ONNX model loaded successfully.")
                    return True

                # B. CTC (model.onnx or model.int8.onnx)
                onnx_model = model_dir / "model.int8.onnx"
                if not onnx_model.exists():
                    onnx_model = model_dir / "model.onnx"

                if onnx_model.exists() and tokens.exists():
                    def _load_onnx():
                        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                            model=str(onnx_model),
                            tokens=str(tokens),
                            num_threads=cfg_threads,
                            sample_rate=self.config.audio.sample_rate,
                            provider=provider,
                        )

                    self.model = await loop.run_in_executor(None, _load_onnx)
                    if status_callback:
                        status_callback(f"✅ NVIDIA Parakeet ready on {device_label} ({provider.upper()})!")
                    logger.info("Parakeet CTC ONNX model loaded successfully.")
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
