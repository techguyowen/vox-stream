"""Speech-to-Text Engine Factory."""

from typing import Optional
from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from .google_stt import GoogleSTTEngine
from .gemini_live import GeminiLiveEngine
from .google_web import GoogleWebEngine
from .local_whisper import LocalWhisperEngine
from .vosk import VoskEngine
from .moonshine import MoonshineEngine
from .bandwidth import BandwidthEngine
from ..config import AppConfig


def create_engine(config: AppConfig) -> BaseSTTEngine:
    """Instantiate the configured Speech-to-Text engine."""
    engine_type = (config.general.engine or "").strip().lower()

    if engine_type in ("google_web", "free", "zero_setup", "web"):
        return GoogleWebEngine(config)
    elif engine_type in ("google", "google_stt", "gcp"):
        return GoogleSTTEngine(config)
    elif engine_type in ("gemini", "gemini_live", "gemini-live"):
        return GeminiLiveEngine(config)
    elif engine_type in ("local", "whisper", "local_whisper", "faster_whisper"):
        return LocalWhisperEngine(config)
    elif engine_type in ("vosk", "local_vosk", "kaldi"):
        return VoskEngine(config)
    elif engine_type in ("moonshine", "local_moonshine"):
        return MoonshineEngine(config)
    elif engine_type in ("bandwidth", "labs.bandwidth.com", "bandwidth_labs"):
        return BandwidthEngine(config)
    else:
        raise ValueError(
            f"Unknown engine '{engine_type}'. Supported engines are: 'google_web', 'gemini_live', 'google_stt', 'local_whisper', 'vosk', 'moonshine', 'bandwidth'"
        )


__all__ = [
    "BaseSTTEngine",
    "CaptionCallback",
    "TranscriptEvent",
    "GoogleWebEngine",
    "GoogleSTTEngine",
    "GeminiLiveEngine",
    "LocalWhisperEngine",
    "VoskEngine",
    "MoonshineEngine",
    "BandwidthEngine",
    "create_engine",
]
