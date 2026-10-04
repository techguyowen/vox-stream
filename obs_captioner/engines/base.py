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
    translated_text: Optional[str] = None
    seq: int = 0
    utterance_id: int = 0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


CaptionCallback = Callable[[TranscriptEvent], Coroutine[any, any, None]]
StatusCallback = Callable[[str], None]


class PreRollBuffer:
    """Rolling audio window that protects word onsets from VAD false negatives.

    VAD-gated engines discard non-speech chunks while idle, so a weak onset
    chunk misclassified as silence clips the first phoneme. Keeping the most
    recent ~300ms and prepending it when speech starts preserves the onset;
    utterance gaps always exceed the window, so only silence (or the missed
    onset itself) is ever prepended.
    """

    def __init__(self, sample_rate: int = 16000, preroll_seconds: float = 0.3):
        self.max_bytes = max(2, int(sample_rate * 2 * preroll_seconds)) & ~1
        self._buf = bytearray()

    def push(self, chunk: bytes) -> None:
        """Retain a chunk in the rolling window."""
        if not chunk:
            return
        self._buf.extend(chunk)
        overflow = len(self._buf) - self.max_bytes
        if overflow > 0:
            del self._buf[:overflow]

    def take(self) -> bytes:
        """Drain and return the retained window (called on speech onset)."""
        data = bytes(self._buf)
        self._buf.clear()
        # Keep 16-bit alignment so downstream np.frombuffer never fails.
        if len(data) & 1:
            data = data[:-1]
        return data


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

    def trim_memory(self) -> None:
        """Optional hook to flush internal memory buffers or model caches."""
        pass
