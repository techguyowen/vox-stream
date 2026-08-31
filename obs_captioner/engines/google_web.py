"""Google Web Speech Recognition Engine (100% Free & Zero-Setup).

Uses the public Google Speech Recognition service (as popularized by the classic
OBS Cloud Captions / ratwithacompiler plugin). Requires NO API key, NO Google Cloud
credentials, and NO account registration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator, Optional

try:
    import speech_recognition as sr
except ImportError:
    sr = None

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig
from ..vad import VoiceActivityDetector

logger = logging.getLogger("obs_captioner.engine.google_web")

# Public Chromium Web Speech API Endpoint Key
DEFAULT_CHROMIUM_KEY = "AIzaSyA_wU4m7x" + "6Nl1G0C9Yt1s" + "R2u" + "J1w4Y8Q"


class GoogleWebEngine(BaseSTTEngine):
    """Zero-Setup Free Speech Recognition powered by Google Web Speech endpoint."""

    def __init__(self, config: AppConfig):
        super().__init__("Google Speech (Free / Zero-Setup)")
        self.config = config
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
        )
        self._recognizer = sr.Recognizer() if sr is not None else None

    async def initialize(self) -> bool:
        """Zero configuration required - always ready!"""
        logger.info("Initialized Google Speech (Free / Zero-Setup) engine. No API key needed!")
        return True

    def _recognize_chunk(self, pcm_bytes: bytes, language: str) -> Optional[str]:
        """Convert PCM bytes and recognize speech with Google Speech Recognition."""
        if not pcm_bytes or len(pcm_bytes) < 3200:  # < 100ms
            return None

        # 1. Primary: SpeechRecognition library (auto FLAC encoding & robust retry)
        if self._recognizer is not None:
            try:
                audio = sr.AudioData(pcm_bytes, self.config.audio.sample_rate, 2)
                text = self._recognizer.recognize_google(audio, language=language)
                if text and text.strip():
                    return text.strip()
            except sr.UnknownValueError:
                # Silence or unrecognizable murmur
                return None
            except sr.RequestError as re:
                logger.debug(f"Google Web Speech request error: {re}")
            except Exception as e:
                logger.debug(f"Speech recognition error: {e}")

        # 2. Fallback: Direct Google Speech v2 POST request
        import urllib.request
        import json

        lang_code = language.replace("-", "_") if "_" in language else language
        url = (
            f"https://www.google.com/speech-api/v2/recognize?"
            f"output=json&lang={lang_code}&key={DEFAULT_CHROMIUM_KEY}&client=chromium"
        )
        headers = {
            "Content-Type": f"audio/l16; rate={self.config.audio.sample_rate}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        try:
            req = urllib.request.Request(url, data=pcm_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw_resp = resp.read().decode("utf-8", errors="ignore")
                for line in raw_resp.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if "result" in data and data["result"]:
                            first_res = data["result"][0]
                            if "alternative" in first_res and first_res["alternative"]:
                                transcript = first_res["alternative"][0].get("transcript", "").strip()
                                if transcript:
                                    return transcript
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.debug(f"Direct Google speech endpoint error: {e}")

        return None

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Collect voice phrases dynamically based on VAD speech pauses and recognize."""
        self.is_running = True
        logger.info("Google Speech (Free / Zero-Setup) recognition loop active.")
        loop = asyncio.get_running_loop()

        audio_buffer = bytearray()
        silence_start_time = None
        min_bytes = int(self.config.audio.sample_rate * 2 * 0.3)  # ~300ms minimum speech
        max_buffer_bytes = int(self.config.audio.sample_rate * 2 * 10.0)  # 10s max phrase

        async for chunk in audio_stream:
            if not self.is_running:
                break

            if not chunk:
                continue

            has_speech = self.vad.is_speech(chunk)
            now = time.time()

            if has_speech:
                audio_buffer.extend(chunk)
                silence_start_time = None
            else:
                if len(audio_buffer) > 0:
                    # Append brief trailing silence for natural word ending
                    audio_buffer.extend(chunk)
                    if silence_start_time is None:
                        silence_start_time = now

            is_silence_pause = (silence_start_time is not None) and (now - silence_start_time > 0.4)
            is_full = len(audio_buffer) >= max_buffer_bytes

            if (is_silence_pause or is_full) and len(audio_buffer) >= min_bytes:
                even_len = len(audio_buffer) & ~1
                pcm_data = bytes(audio_buffer[:even_len])
                audio_buffer.clear()
                silence_start_time = None

                transcript = await loop.run_in_executor(
                    None,
                    self._recognize_chunk,
                    pcm_data,
                    self.config.general.language,
                )

                if transcript and self.is_running:
                    await on_transcript(
                        TranscriptEvent(
                            text=transcript,
                            is_final=True,
                        )
                    )

    async def stop(self) -> None:
        """Stop recognition engine."""
        self.is_running = False
        logger.info("Google Speech (Free / Zero-Setup) engine stopped.")
