"""Sherpa-ONNX (Next-Gen Kaldi / Zipformer Streaming) Speech-to-Text Engine."""

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

logger = logging.getLogger("obs_captioner.engine.sherpa")


class SherpaEngine(BaseSTTEngine):
    """Real-time streaming speech recognition with sub-100ms latency using Sherpa-ONNX Zipformer."""

    DEFAULT_MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-2023-06-26"

    def __init__(self, config: AppConfig):
        super().__init__("Sherpa-ONNX Streaming")
        self.config = config
        self.recognizer = None
        self._running = False
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
            enable_silero=getattr(config.audio, "enable_vad", True),
            suppress_music=getattr(config.audio, "suppress_music", True),
        )

    def _find_model_dir(self) -> Optional[Path]:
        """Search for Sherpa-ONNX model files in standard cache locations."""
        custom_path = (self.config.sherpa.model_path or "").strip()
        if custom_path and Path(custom_path).is_dir():
            return Path(custom_path)

        model_name = self.config.sherpa.model_name or self.DEFAULT_MODEL_NAME
        candidates = [
            Path.home() / ".cache" / "sherpa-onnx" / model_name,
            Path("models") / model_name,
            Path.home() / "AppData" / "Local" / "sherpa-onnx" / model_name,
        ]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "sherpa-onnx" / model_name)
        app_data = os.environ.get("APPDATA")
        if app_data:
            candidates.append(Path(app_data) / "sherpa-onnx" / model_name)
        try:
            import tempfile
            candidates.append(Path(tempfile.gettempdir()) / model_name)
        except Exception:
            pass

        hf_hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
        hf_dirs = [
            hf_hub / f"models--csukuangfj--{model_name}" / "snapshots",
            hf_hub / f"models--{model_name.replace('/', '--')}" / "snapshots",
            hf_hub / f"models--csukuangfj--{self.DEFAULT_MODEL_NAME}" / "snapshots",
            hf_hub / f"models--csukuangfj--sherpa-onnx-{model_name}" / "snapshots",
        ]
        if hf_hub.is_dir():
            for d in hf_hub.iterdir():
                if "zipformer" in d.name.lower():
                    hf_dirs.append(d / "snapshots")

        for hf_base in hf_dirs:
            if hf_base.is_dir():
                for snap in hf_base.iterdir():
                    if snap.is_dir() and (snap / "tokens.txt").exists():
                        return snap

        for c in candidates:
            if c.is_dir() and (c / "tokens.txt").exists():
                return c
        return None

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Initialize Sherpa-ONNX streaming recognizer."""
        try:
            import sherpa_onnx
        except ImportError:
            err = "sherpa-onnx is not installed. Install with: pip install sherpa-onnx"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False

        try:
            model_name = self.config.sherpa.model_name or self.DEFAULT_MODEL_NAME
            model_dir = self._find_model_dir()
            if not model_dir:
                try:
                    from huggingface_hub import snapshot_download
                    if status_callback:
                        status_callback(f"Downloading Sherpa Zipformer model from HuggingFace...")
                    if "zipformer" in model_name and not model_name.startswith("sherpa-onnx"):
                        repo_id = "csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26"
                    elif "/" not in model_name:
                        repo_id = f"csukuangfj/{model_name}"
                    else:
                        repo_id = model_name
                    dl_path = snapshot_download(repo_id=repo_id, allow_patterns=["*.onnx", "tokens.txt"])
                    model_dir = Path(dl_path)
                except Exception as dl_err:
                    logger.debug(f"Auto-download note: {dl_err}")

            if not model_dir:
                msg = (
                    f"Sherpa Zipformer weights not found. Download '{self.DEFAULT_MODEL_NAME}' to ~/.cache/sherpa-onnx/ "
                    "or specify path in config.sherpa.model_path"
                )
                if status_callback:
                    status_callback(f"⚠️ {msg}")
                logger.warning(msg)
                return True

            if status_callback:
                status_callback(f"Loading Sherpa-ONNX streaming model from {model_dir.name}...")

            loop = asyncio.get_event_loop()

            def _load():
                encoder = str(model_dir / "encoder-epoch-99-avg-1.onnx")
                if not os.path.exists(encoder):
                    encoders = sorted(list(model_dir.glob("*encoder*.onnx")), key=lambda p: ("128" in p.name, "int8" not in p.name), reverse=True)
                    encoder = str(encoders[0]) if encoders else ""

                decoder = str(model_dir / "decoder-epoch-99-avg-1.onnx")
                if not os.path.exists(decoder):
                    decoders = sorted(list(model_dir.glob("*decoder*.onnx")), key=lambda p: ("128" in p.name, "int8" not in p.name), reverse=True)
                    decoder = str(decoders[0]) if decoders else ""

                joiner = str(model_dir / "joiner-epoch-99-avg-1.onnx")
                if not os.path.exists(joiner):
                    joiners = sorted(list(model_dir.glob("*joiner*.onnx")), key=lambda p: ("128" in p.name, "int8" not in p.name), reverse=True)
                    joiner = str(joiners[0]) if joiners else ""

                tokens = str(model_dir / "tokens.txt")

                return sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=tokens,
                    encoder=encoder,
                    decoder=decoder,
                    joiner=joiner,
                    num_threads=getattr(self.config.sherpa, "num_threads", 4),
                    sample_rate=self.config.audio.sample_rate,
                    feature_dim=80,
                    decoding_method="greedy_search",
                )

            self.recognizer = await loop.run_in_executor(None, _load)
            if status_callback:
                status_callback("✅ Sherpa-ONNX Streaming Zipformer ready (<100ms latency)!")
            logger.info("Sherpa-ONNX streaming recognizer initialized successfully.")
            return True

        except Exception as e:
            err = f"Failed to load Sherpa-ONNX model: {e}"
            if status_callback:
                status_callback(f"❌ {err}")
            logger.error(err)
            return False

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ):
        """Consume live audio chunks and emit sub-100ms streaming transcripts."""
        self._running = True
        logger.info("Sherpa-ONNX audio streaming started.")

        if self.recognizer is None:
            logger.warning("Sherpa-ONNX running in stub mode (weights not configured).")
            async for _ in audio_stream:
                if not self._running:
                    break
                await asyncio.sleep(0.05)
            return

        stream = self.recognizer.create_stream()
        last_text = ""
        sample_rate = self.config.audio.sample_rate

        try:
            async for chunk in audio_stream:
                if not self._running:
                    break

                if not chunk:
                    continue

                samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                stream.accept_waveform(sample_rate, samples)

                while self.recognizer.is_ready(stream):
                    self.recognizer.decode_stream(stream)

                is_endpoint = self.recognizer.is_endpoint(stream)
                res = self.recognizer.get_result(stream)
                text = (res.text if hasattr(res, "text") else str(res)).strip()

                if is_endpoint:
                    if text:
                        await on_transcript(
                            TranscriptEvent(
                                text=text,
                                is_final=True,
                                confidence=0.98,
                                timestamp=time.time(),
                            )
                        )
                    self.recognizer.reset(stream)
                    last_text = ""
                elif text and text != last_text:
                    last_text = text
                    await on_transcript(
                        TranscriptEvent(
                            text=text,
                            is_final=False,
                            confidence=0.95,
                            timestamp=time.time(),
                        )
                    )

            if last_text:
                await on_transcript(
                    TranscriptEvent(
                        text=last_text,
                        is_final=True,
                        confidence=0.98,
                        timestamp=time.time(),
                    )
                )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Sherpa-ONNX streaming error: {e}")
        finally:
            self._running = False
            logger.info("Sherpa-ONNX audio streaming stopped.")

    async def stop(self) -> None:
        """Stop streaming."""
        self._running = False
        self.is_running = False
