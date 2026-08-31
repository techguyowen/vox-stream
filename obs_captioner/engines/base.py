"""Base class and common types for Speech-to-Text engines."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Callable, Coroutine, Optional
import time


@dataclass
class TranscriptEvent:
    """Represents a real-time transcript update."""
    text: str
    is_final: bool = False
    confidence: float = 1.0
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


CaptionCallback = Callable[[TranscriptEvent], Coroutine[any, any, None]]
StatusCallback = Callable[[str], None]


class BaseSTTEngine(ABC):
    """Abstract interface for streaming Speech-to-Text engines."""

    def __init__(self, name: str):
        self.name = name
        self.is_running = False

    @abstractmethod
    async def initialize(self, status_callback: Optional[StatusCallback] = None) -> bool:
        """Initialize models, credentials, or network clients with live progress reporting."""
        pass

    @abstractmethod
    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Consume PCM audio chunks and trigger on_transcript for interim and final results."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop the recognition session."""
        pass
