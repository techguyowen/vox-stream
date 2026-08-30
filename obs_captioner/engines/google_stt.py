"""Google Cloud Speech-to-Text Streaming Engine."""

import asyncio
import logging
import os
import time
from typing import AsyncGenerator, Optional

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig

logger = logging.getLogger("obs_captioner.engine.google")

# Google Cloud STT has a strict 305-second streaming limit per gRPC connection
STREAMING_LIMIT_SECONDS = 290


class GoogleSTTEngine(BaseSTTEngine):
    """Streaming recognition using Google Cloud Speech-to-Text v1 / v2."""

    def __init__(self, config: AppConfig):
        super().__init__("Google Cloud STT")
        self.config = config
        self.client = None
        self._speech_module = None
        self._types_module = None

    async def initialize(self) -> bool:
        """Verify credentials and create Google Speech client."""
        cred_path = self.config.google_stt.credentials_path
        if cred_path and os.path.isfile(cred_path):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path
            logger.info(f"Using Google credentials from: {cred_path}")
        elif not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            logger.warning(
                "GOOGLE_APPLICATION_CREDENTIALS is not set in config.json or environment! "
                "Google Cloud STT will attempt default application credentials."
            )

        try:
            from google.cloud import speech_v1 as speech
            self._speech_module = speech
            self.client = speech.SpeechAsyncClient()
            logger.info("Google SpeechAsyncClient initialized successfully.")
            return True
        except ImportError:
            logger.error("google-cloud-speech is not installed. Install with: pip install google-cloud-speech")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Google Speech client: {e}")
            return False

    def _build_streaming_config(self):
        """Construct the RecognitionConfig and StreamingRecognitionConfig."""
        speech = self._speech_module

        # Build speech contexts / hints
        speech_contexts = []
        if self.config.google_stt.speech_contexts:
            speech_contexts.append(
                speech.SpeechContext(phrases=self.config.google_stt.speech_contexts, boost=15.0)
            )

        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.config.audio.sample_rate,
            language_code=self.config.general.language,
            model=self.config.google_stt.model,
            enable_automatic_punctuation=self.config.google_stt.enable_automatic_punctuation,
            enable_word_time_offsets=self.config.google_stt.enable_word_time_offsets,
            profanity_filter=self.config.google_stt.profanity_filter,
            speech_contexts=speech_contexts,
        )

        return speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=True,
            single_utterance=False,
        )

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Stream audio chunks to Google STT, automatically reconnecting at time limits."""
        self.is_running = True
        speech = self._speech_module

        while self.is_running:
            logger.info("Starting Google Cloud STT streaming session...")
            streaming_config = self._build_streaming_config()
            session_start_time = time.time()

            async def request_generator():
                # First message must contain streaming_config
                yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
                
                async for chunk in audio_stream:
                    if not self.is_running:
                        break
                    # If session is approaching 300s limit, break generator to cleanly reconnect
                    if time.time() - session_start_time > STREAMING_LIMIT_SECONDS:
                        logger.info("Streaming limit approached (~5 mins). Reconnecting session...")
                        break
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)

            try:
                responses = await self.client.streaming_recognize(requests=request_generator())
                async for response in responses:
                    if not self.is_running:
                        break
                    for result in response.results:
                        if not result.alternatives:
                            continue
                        alt = result.alternatives[0]
                        transcript = alt.transcript.strip()
                        if not transcript:
                            continue

                        event = TranscriptEvent(
                            text=transcript,
                            is_final=result.is_final,
                            confidence=alt.confidence if alt.confidence > 0 else 1.0,
                        )
                        await on_transcript(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_running:
                    logger.error(f"Google STT streaming error: {e}. Reconnecting in 2 seconds...")
                    await asyncio.sleep(2.0)

    async def stop(self) -> None:
        """Stop Google STT engine."""
        self.is_running = False
        logger.info("Google STT engine stopped.")
