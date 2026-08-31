"""Bandwidth Labs Speech-to-Text Engine."""

import asyncio
import json
import logging
import os
import time
import urllib.parse
from typing import AsyncGenerator, Callable, Optional

import websockets

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig

logger = logging.getLogger("obs_captioner.engine.bandwidth")


class BandwidthEngine(BaseSTTEngine):
    """
    Connects to Bandwidth Labs' live streaming Speech-to-Text WebSocket API.
    Docs: https://labs.bandwidth.com/docs/speech-to-text
    """

    def __init__(self, config: AppConfig):
        super().__init__("Bandwidth Labs Live STT")
        self.config = config
        self.ws_url = "wss://api.labs.bandwidth.com/audio/v1/listen"
        self.api_key = config.bandwidth.api_key or os.getenv("BANDWIDTH_API_KEY", "")

    async def _flush_loop(self, on_transcript: CaptionCallback):
        """Background task to flush the interim buffer on punctuation, silence, or max length."""
        while self.is_running:
            await asyncio.sleep(0.1)

            async with self._lock:
                if not self._buffer:
                    continue

                now = time.time()
                silence_duration = now - self._last_update

                trimmed = self._buffer.strip()
                has_punctuation = trimmed.endswith((".", "?", "!"))

                # Flush conditions:
                # 1. Terminal punctuation with a brief pause (>300ms)
                # 2. Natural pause in speech (>1.2s)
                # 3. Buffer length exceeds comfortable reading limit (~200 chars)
                if (has_punctuation and silence_duration > 0.3) or (silence_duration > 1.2) or len(self._buffer) > 200:
                    text_to_flush = self._buffer.strip()
                    self._buffer = ""
                    if text_to_flush:
                        try:
                            await on_transcript(TranscriptEvent(text=text_to_flush, is_final=True))
                        except Exception as e:
                            logger.error(f"Error flushing final transcript: {e}")

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        if not self.api_key:
            logger.warning("Bandwidth API Key is not set in config.json or BANDWIDTH_API_KEY environment variable.")
            return False
        return True

    async def start_streaming(self, audio_stream: AsyncGenerator[bytes, None], on_transcript: CaptionCallback) -> None:
        self.is_running = True
        self._buffer = ""
        self._last_update = time.time()
        self._lock = asyncio.Lock()

        params = {
            "encoding": "linear16",
            "sample_rate": str(self.config.audio.sample_rate),
        }
        headers = {
            "X-BW-LABS-API-KEY": self.api_key,
        }
        url = f"{self.ws_url}?{urllib.parse.urlencode(params)}"

        flush_task = asyncio.create_task(self._flush_loop(on_transcript))

        try:
            async with websockets.connect(url, extra_headers=headers) as ws:
                logger.info("Connected to Bandwidth Labs real-time STT WebSocket.")

                async def send_audio():
                    try:
                        async for chunk in audio_stream:
                            if not self.is_running:
                                break
                            await ws.send(chunk)
                    except Exception as e:
                        if self.is_running:
                            logger.error(f"Error sending audio to Bandwidth: {e}")
                    finally:
                        if self.is_running:
                            try:
                                await ws.send(json.dumps({"type": "CloseStream"}))
                            except Exception:
                                pass

                async def receive_transcripts():
                    try:
                        async for message in ws:
                            if not self.is_running:
                                break
                            try:
                                msg = json.loads(message)
                                mtype = msg.get("type")

                                if mtype == "Segment":
                                    text_delta = msg.get("text", "")
                                    if text_delta:
                                        async with self._lock:
                                            self._buffer += text_delta
                                            self._last_update = time.time()
                                            await on_transcript(TranscriptEvent(text=self._buffer.strip(), is_final=False))

                                elif mtype == "SessionClosed":
                                    break
                                elif mtype == "Error":
                                    logger.error(f"Bandwidth Labs API Error: {msg}")
                            except Exception as e:
                                logger.error(f"Error parsing Bandwidth message: {e}")
                    except Exception as e:
                        if self.is_running:
                            logger.error(f"Error receiving from Bandwidth: {e}")

                await asyncio.gather(send_audio(), receive_transcripts())
        except Exception as e:
            if self.is_running:
                logger.error(f"Bandwidth STT connection failed: {e}")
        finally:
            self.is_running = False
            flush_task.cancel()

    async def stop(self) -> None:
        self.is_running = False
        logger.info("Bandwidth STT engine stopped.")
