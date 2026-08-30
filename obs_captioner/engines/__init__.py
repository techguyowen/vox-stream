"""Speech-to-Text Engine Factory."""

from typing import Optional
from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from .google_stt import GoogleSTTEngine
from .gemini_live import GeminiLiveEngine
from .google_web import GoogleWebEngine
from .local_whisper import LocalWhisperEngine
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
    else:
        raise ValueError(
            f"Unknown engine '{engine_type}'. Supported engines are: 'google_web', 'gemini_live', 'google_stt', 'local_whisper'"
        )


__all__ = [
    "BaseSTTEngine",
    "CaptionCallback",
    "TranscriptEvent",
    "GoogleWebEngine",
    "GoogleSTTEngine",
    "GeminiLiveEngine",
    "LocalWhisperEngine",
    "create_engine",
]
