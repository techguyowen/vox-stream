"""SenseVoice (Alibaba FunASR with Audio Event Detection) Speech-to-Text Engine."""

from __future__ import annotations

import asyncio
import logging
import re
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

logger = logging.getLogger("obs_captioner.engine.sensevoice")


class SenseVoiceEngine(BaseSTTEngine):
    """Ultra-fast non-autoregressive speech recognition with Audio Event Detection (applause, laughter, music)."""

    DEFAULT_MODEL_NAME = "sensevoice-small"

    # SenseVoice special token patterns
    EVENT_MAP = {
        r"<\|APPLAUSE\|>": "[Applause]",
        r"<\|LAUGHTER\|>": "[Laughter]",
        r"<\|CRY\|>": "[Crying]",
        r"<\|SNEEZE\|>": "[Sneeze]",
        r"<\|COUGH\|>": "[Cough]",
        r"<\|MUSIC\|>": "[Music]",
        r"<\|BGM\|>": "[Music]",
    }

    def __init__(self, config: AppConfig):
        super().__init__("SenseVoice")
        self.config = config
        self.model = None
        self._running = False
        self._device = "cpu"
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
        )

    def _find_model_dir(self) -> Optional[Path]:
        """Search for SenseVoice model files in standard cache locations."""
        custom_path = (self.config.sensevoice.model_path or "").strip()
        if custom_path and Path(custom_path).exists():
            return Path(custom_path)

        model_name = self.config.sensevoice.model_name or self.DEFAULT_MODEL_NAME
        candidates = [
            Path.home() / ".cache" / "sensevoice" / model_name,
            Path("models") / model_name,
            Path.home() / "AppData" / "Local" / "sensevoice" / model_name,
            Path.home() / ".cache" / "funasr" / model_name,
        ]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "sensevoice" / model_name)
            candidates.append(Path(local_app_data) / "funasr" / model_name)
        app_data = os.environ.get("APPDATA")
        if app_data:
            candidates.append(Path(app_data) / "sensevoice" / model_name)
            candidates.append(Path(app_data) / "funasr" / model_name)
        for c in candidates:
            if c.exists():
                return c
        return None

    def _clean_audio_events(self, raw_text: str) -> str:
        """Parse SenseVoice event tags into user-friendly broadcast captions."""
        if not raw_text:
            return ""

        text = raw_text
        detect_events = getattr(self.config.sensevoice, "detect_events", True)

        if detect_events:
            for pat, repl in self.EVENT_MAP.items():
                text = re.sub(pat, f" {repl} ", text, flags=re.IGNORECASE)
        else:
            for pat in self.EVENT_MAP:
                text = re.sub(pat, " ", text, flags=re.IGNORECASE)

        # Strip remaining emotion and language meta tokens (e.g. <|HAPPY|>, <|en|>, <|NEUTRAL|>)
        text = re.sub(r"<\|[^>|]+\|>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Initialize SenseVoice model via FunASR / ONNX Runtime."""
        from ..hardware import get_torch_device
        self._device, device_label = get_torch_device()

        model_name = self.config.sensevoice.model_name or self.DEFAULT_MODEL_NAME
        if status_callback:
            status_callback(f"Loading SenseVoice ({model_name}) on {device_label}...")

        # 1. Try loading via funasr / SenseVoiceSmall
        try:
            from funasr import AutoModel
            loop = asyncio.get_event_loop()

            def _load_funasr():
                return AutoModel(
                    model="iic/SenseVoiceSmall",
                    trust_remote_code=True,
                    device=self._device,
                )

            self.model = await loop.run_in_executor(None, _load_funasr)
            if status_callback:
                status_callback(f"✅ SenseVoice ({model_name}) ready on {device_label}!")
            logger.info("SenseVoice AutoModel loaded successfully.")
            return True
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"FunASR loading note: {e}")

        # 2. Try loading via sherpa-onnx SenseVoice integration
        try:
            import sherpa_onnx
            model_dir = self._find_model_dir()
            if model_dir and (model_dir / "model.onnx").exists():
                loop = asyncio.get_event_loop()

                def _load_sherpa_sv():
                    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
                        model=str(model_dir / "model.onnx"),
                        tokens=str(model_dir / "tokens.txt"),
                        num_threads=4,
                        sample_rate=self.config.audio.sample_rate,
                    )

                self.model = await loop.run_in_executor(None, _load_sherpa_sv)
                if status_callback:
                    status_callback(f"✅ SenseVoice ONNX ready on {device_label}!")
                logger.info("SenseVoice ONNX loaded successfully via sherpa.")
                return True
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Sherpa SenseVoice loading note: {e}")

        # 3. Graceful standby message if weights or package not yet provisioned
        msg = (
            f"SenseVoice engine configured ({model_name}). "
            "For full neural execution, install: pip install funasr or sherpa-onnx"
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
        """Buffer voice chunks using VAD and transcribe using SenseVoice."""
        self._running = True
        logger.info("SenseVoice audio streaming started.")

        audio_buffer = bytearray()
        silence_start_time = None
        sentence_break_s = 0.65

        try:
            async for chunk in audio_stream:
                if not self._running:
                    break

                if not chunk:
                    continue

                is_speech = self.vad.is_speech(chunk)
                now = time.time()

                if is_speech:
                    silence_start_time = None
                    audio_buffer.extend(chunk)
                else:
                    if audio_buffer:
                        if silence_start_time is None:
                            silence_start_time = now
                        elif now - silence_start_time >= sentence_break_s:
                            await self._process_utterance(bytes(audio_buffer), on_transcript)
                            audio_buffer.clear()
                            silence_start_time = None

            if audio_buffer and self._running:
                await self._process_utterance(bytes(audio_buffer), on_transcript)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"SenseVoice streaming error: {e}")
        finally:
            self._running = False
            logger.info("SenseVoice audio streaming stopped.")

    async def _process_utterance(self, audio_bytes: bytes, on_transcript: CaptionCallback):
        """Transcribe completed speech utterance and extract audio events."""
        if not audio_bytes or len(audio_bytes) < 3200:
            return

        if self.model is None:
            return

        try:
            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            loop = asyncio.get_event_loop()

            def _transcribe():
                if hasattr(self.model, "generate"):
                    res = self.model.generate(
                        input=samples,
                        cache={},
                        language=self.config.sensevoice.language or "auto",
                        use_itn=True,
                    )
                    if res and len(res) > 0:
                        return res[0].get("text", "")
                elif hasattr(self.model, "create_stream"):
                    s = self.model.create_stream()
                    s.accept_waveform(self.config.audio.sample_rate, samples)
                    self.model.decode_stream(s)
                    return s.result.text
                return ""

            raw_text = await loop.run_in_executor(None, _transcribe)
            cleaned_text = self._clean_audio_events(raw_text)

            if cleaned_text:
                await on_transcript(
                    TranscriptEvent(
                        text=cleaned_text,
                        is_final=True,
                        confidence=0.96,
                        timestamp=time.time(),
                    )
                )
        except Exception as e:
            logger.debug(f"SenseVoice utterance error: {e}")

    async def stop(self) -> None:
        """Stop streaming."""
        self._running = False
        self.is_running = False
