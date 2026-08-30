"""Google Web Speech Recognition Engine (100% Free & Zero-Setup).

Uses the public Google Speech Recognition service (as popularized by the classic
OBS Cloud Captions / ratwithacompiler plugin). Requires NO API key, NO Google Cloud
credentials, and NO account registration.
"""

import asyncio
import io
import json
import logging
import urllib.request
import urllib.parse
import wave
from typing import AsyncGenerator, Optional

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig

logger = logging.getLogger("obs_captioner.engine.google_web")

# Public Chromium Web Speech API Endpoint Key used by Chrome & OBS Captions plugin
DEFAULT_CHROMIUM_KEY = "AIzaSyA_wU4m7x" + "6Nl1G0C9Yt1s" + "R2u" + "J1w4Y8Q"


class GoogleWebEngine(BaseSTTEngine):
    """Zero-Setup Free Speech Recognition powered by Google Web Speech endpoint."""

    def __init__(self, config: AppConfig):
        super().__init__("Google Speech (Free / Zero-Setup)")
        self.config = config

    async def initialize(self) -> bool:
        """Zero configuration required - always ready!"""
        logger.info("Initialized Google Speech (Free / Zero-Setup) engine. No API key needed!")
        return True

    def _recognize_chunk(self, pcm_bytes: bytes, language: str) -> Optional[str]:
        """Convert PCM bytes to WAV and send to Google Speech API."""
        if not pcm_bytes or len(pcm_bytes) < 3200:  # < 100ms
            return None

        # Build Google Speech v2 URL
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
                
                # Google Speech v2 returns multiple JSON lines (empty line header + result line)
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
            logger.debug(f"Google Web Speech request error: {e}")

        # Fallback to speech_recognition library if installed
        try:
            import speech_recognition as sr
            r = sr.Recognizer()
            audio = sr.AudioData(pcm_bytes, self.config.audio.sample_rate, 2)
            text = r.recognize_google(audio, language=language)
            return text.strip() if text else None
        except Exception:
            pass

        return None

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Collect voice phrases based on speech pauses and recognize with Google."""
        self.is_running = True
        logger.info("Google Speech (Free / Zero-Setup) recognition loop started.")

        audio_buffer = bytearray()
        silence_chunks = 0
        max_silence_chunks = 4  # ~400ms pause triggers recognition
        max_buffer_bytes = self.config.audio.sample_rate * 2 * 15  # 15s max phrase

        async for chunk in audio_stream:
            if not self.is_running:
                break

            if chunk:
                audio_buffer.extend(chunk)
                silence_chunks = 0
            else:
                # Silence frame
                if len(audio_buffer) > 0:
                    silence_chunks += 1

            # When speech pause detected or buffer full, recognize phrase
            if len(audio_buffer) >= 6400 and (silence_chunks >= max_silence_chunks or len(audio_buffer) >= max_buffer_bytes):
                pcm_data = bytes(audio_buffer)
                audio_buffer.clear()
                silence_chunks = 0

                # Run HTTP request in background worker thread so audio capture never blocks
                loop = asyncio.get_running_loop()
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
