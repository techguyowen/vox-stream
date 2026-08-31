"""Gemini 3.5 Transcribe Live Multimodal Streaming API Engine."""

import asyncio
import json
import logging
import os
from typing import AsyncGenerator, Callable, Optional

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig

logger = logging.getLogger("obs_captioner.engine.gemini")


class GeminiLiveEngine(BaseSTTEngine):
    """Real-time streaming speech transcription using Gemini 3.5 Transcribe Live."""

    def __init__(self, config: AppConfig):
        super().__init__("Gemini 3.5 Transcribe Live")
        self.config = config
        self.api_key = config.gemini_live.api_key or os.environ.get("GEMINI_API_KEY", "")
        self.client = None

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Validate API key and initialize Gemini Live client."""
        if not self.api_key:
            logger.warning("GEMINI_API_KEY is not set in config.json or environment variables!")
            return False

        try:
            from google import genai
            self.client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})
            logger.info("Gemini 3.5 Transcribe client initialized successfully.")
            return True
        except ImportError:
            logger.error("google-genai is not installed. Install with: pip install google-genai")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            return False

    def _build_system_instruction(self) -> str:
        """Build optimized system instructions with custom vocabulary & smart transcription."""
        base = self.config.gemini_live.system_instruction or (
            "You are Gemini 3.5 Transcribe. Transcribe the incoming audio stream into text verbatim."
        )

        extras = []
        if self.config.gemini_live.smart_transcription:
            extras.append(
                "Clean up speech disfluencies (such as 'ums' and 'ahs'), handle natural self-corrections, "
                "and format proper capitalization and punctuation."
            )

        if self.config.gemini_live.custom_vocabulary:
            vocab_list = ", ".join(f'"{v}"' for v in self.config.gemini_live.custom_vocabulary if v.strip())
            if vocab_list:
                extras.append(f"Adapt accurately to this custom specialized vocabulary: [{vocab_list}].")

        if extras:
            return f"{base} {' '.join(extras)}"
        return base

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Stream real-time PCM audio to Gemini 3.5 Transcribe Live session and yield tokens."""
        self.is_running = True
        model = self.config.gemini_live.model or "gemini-3.5-transcribe-live"

        while self.is_running:
            logger.info(f"Opening Gemini Live connection (model: {model})...")
            try:
                from google.genai import types

                instruction_text = self._build_system_instruction()

                config = types.LiveConnectConfig(
                    response_modalities=[types.LiveServerContentModality.TEXT],
                    system_instruction=types.Content(
                        parts=[types.Part.from_text(instruction_text)]
                    ),
                )

                async with self.client.aio.live.connect(model=model, config=config) as session:
                    logger.info(f"Gemini 3.5 Transcribe Live session connected with model '{model}'.")

                    async def send_audio():
                        async for chunk in audio_stream:
                            if not self.is_running:
                                break
                            # Send 16kHz PCM audio chunk
                            await session.send(
                                input={"data": chunk, "mime_type": f"audio/pcm;rate={self.config.audio.sample_rate}"}
                            )

                    async def receive_transcripts():
                        current_line = ""
                        async for response in session.receive():
                            if not self.is_running:
                                break
                            server_content = response.server_content
                            if server_content is not None and server_content.model_turn is not None:
                                for part in server_content.model_turn.parts:
                                    if part.text:
                                        text_piece = part.text
                                        current_line += text_piece
                                        # Yield interim update
                                        await on_transcript(
                                            TranscriptEvent(
                                                text=current_line.strip(),
                                                is_final=False,
                                            )
                                        )
                            
                            # Check turn complete
                            if server_content is not None and server_content.turn_complete:
                                if current_line.strip():
                                    await on_transcript(
                                        TranscriptEvent(
                                            text=current_line.strip(),
                                            is_final=True,
                                        )
                                    )
                                current_line = ""

                    # Run both sender and receiver concurrently
                    send_task = asyncio.create_task(send_audio())
                    recv_task = asyncio.create_task(receive_transcripts())

                    done, pending = await asyncio.wait(
                        [send_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_running:
                    logger.error(f"Gemini Live error: {e}. Reconnecting in 3s...")
                    await asyncio.sleep(3)

    async def stop(self) -> None:
        """Stop Gemini Live engine."""
        self.is_running = False
        logger.info("Gemini 3.5 Transcribe engine stopped.")
