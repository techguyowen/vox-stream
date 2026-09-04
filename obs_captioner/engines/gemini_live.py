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
        """Validate API key, library installation, and verify Gemini Live client connection."""
        self.api_key = self.config.gemini_live.api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            msg = "Missing GEMINI_API_KEY. Please provide your Google AI Studio API key in Audio & Engine settings."
            logger.warning(msg)
            if status_callback:
                status_callback(f"❌ {msg}")
            return False

        try:
            from google import genai
            from google.genai import types

            self.client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})

            model = self.config.gemini_live.model or "gemini-3.5-transcribe-live"
            if status_callback:
                status_callback(f"Verifying connection to Gemini Live ({model})...")

            # Validate connectivity with a fast session probe
            config = types.LiveConnectConfig(
                response_modalities=[types.Modality.TEXT],
                system_instruction="Transcribe speech verbatim.",
            )
            async with self.client.aio.live.connect(model=model, config=config) as _:
                pass

            logger.info(f"Gemini 3.5 Transcribe client initialized and verified successfully with model '{model}'.")
            if status_callback:
                status_callback(f"✅ Gemini Live ({model}) ready!")
            return True
        except ImportError:
            msg = "google-genai library is not installed. Install with: pip install google-genai"
            logger.error(msg)
            if status_callback:
                status_callback(f"❌ {msg}")
            return False
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Failed to initialize Gemini Live client: {err_msg}", exc_info=True)
            if status_callback:
                status_callback(f"❌ Gemini connection failed: {err_msg}")
            return False

    def _build_system_instruction(self) -> str:
        """Build optimized system instructions with custom vocabulary & smart transcription."""
        base = self.config.gemini_live.system_instruction or (
            "You are Gemini 3.5 Transcribe. Transcribe the incoming audio stream into text verbatim. Output ONLY the transcribed words."
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
                    response_modalities=[types.Modality.TEXT],
                    input_audio_transcription=types.AudioTranscriptionConfig(),
                    system_instruction=instruction_text,
                )

                async with self.client.aio.live.connect(model=model, config=config) as session:
                    logger.info(f"Gemini 3.5 Transcribe Live session connected with model '{model}'.")

                    async def send_audio():
                        async for chunk in audio_stream:
                            if not self.is_running:
                                break
                            # Send 16kHz PCM audio chunk via realtime input
                            await session.send_realtime_input(
                                media=types.Blob(
                                    data=chunk,
                                    mime_type=f"audio/pcm;rate={self.config.audio.sample_rate}",
                                )
                            )

                    async def receive_transcripts():
                        current_line = ""
                        async for response in session.receive():
                            if not self.is_running:
                                break

                            # 1. Check direct message text
                            text_piece = getattr(response, "text", None)

                            # 2. Check server_content model turn parts
                            server_content = getattr(response, "server_content", None)
                            if not text_piece and server_content and getattr(server_content, "model_turn", None):
                                for part in server_content.model_turn.parts:
                                    if getattr(part, "text", None):
                                        text_piece = (text_piece or "") + part.text

                            # 3. Check server_content input_transcription
                            if not text_piece and server_content and getattr(server_content, "input_transcription", None):
                                text_piece = server_content.input_transcription.text

                            if text_piece:
                                current_line += text_piece
                                await on_transcript(
                                    TranscriptEvent(
                                        text=current_line.strip(),
                                        is_final=False,
                                    )
                                )

                            # 4. Check turn completion
                            if server_content is not None and getattr(server_content, "turn_complete", False):
                                if current_line.strip():
                                    await on_transcript(
                                        TranscriptEvent(
                                            text=current_line.strip(),
                                            is_final=True,
                                        )
                                    )
                                current_line = ""

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
