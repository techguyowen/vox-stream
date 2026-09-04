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

        if self.client is None:
            self.api_key = self.config.gemini_live.api_key or os.environ.get("GEMINI_API_KEY", "")
            if not self.api_key:
                logger.error("Gemini Live cannot start: Missing API key. Please configure your API key.")
                return
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})
            except Exception as e:
                logger.error(f"Failed to initialize GenAI client: {e}")
                return

        while self.is_running:
            logger.info(f"Opening Gemini Live connection (model: {model})...")
            try:
                from google.genai import types

                instruction_text = self._build_system_instruction()
                silence_ms = getattr(self.config.audio, "sentence_break_ms", None) or getattr(self.config.gemini_live, "silence_duration_ms", 600) or 600

                config = types.LiveConnectConfig(
                    response_modalities=[types.Modality.TEXT],
                    input_audio_transcription=types.AudioTranscriptionConfig(),
                    system_instruction=instruction_text,
                    realtime_input_config=types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            silence_duration_ms=silence_ms,
                        )
                    ),
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

                            server_content = getattr(response, "server_content", None)
                            it_text = None
                            model_text = None

                            # 1. Check direct message text
                            direct_text = getattr(response, "text", None)

                            # 2. Check server_content model turn parts
                            if server_content and getattr(server_content, "model_turn", None):
                                for part in server_content.model_turn.parts:
                                    if getattr(part, "text", None):
                                        model_text = (model_text or "") + part.text

                            # 3. Check server_content input_transcription
                            if server_content and getattr(server_content, "input_transcription", None):
                                it = server_content.input_transcription
                                if getattr(it, "text", None):
                                    it_text = it.text

                            # 4. Check completion flags
                            it_finished = False
                            if server_content and getattr(server_content, "input_transcription", None):
                                it_finished = bool(getattr(server_content.input_transcription, "finished", False))
                            gen_complete = bool(getattr(server_content, "generation_complete", False)) if server_content else False
                            turn_complete = bool(getattr(server_content, "turn_complete", False)) if server_content else False

                            is_completed = it_finished or gen_complete or turn_complete

                            # Update current_line
                            if it_text:
                                # For input_transcription, text is the cumulative utterance
                                current_line = it_text.strip()
                            elif model_text:
                                current_line += model_text
                            elif direct_text:
                                current_line += direct_text

                            if is_completed:
                                if current_line.strip():
                                    await on_transcript(
                                        TranscriptEvent(
                                            text=current_line.strip(),
                                            is_final=True,
                                        )
                                    )
                                    current_line = ""
                            elif current_line.strip() and (it_text or model_text or direct_text):
                                await on_transcript(
                                    TranscriptEvent(
                                        text=current_line.strip(),
                                        is_final=False,
                                    )
                                )

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

                    if send_task in done:
                        # Audio stream ended normally; allow recv_task to finish receiving remaining server responses
                        if recv_task in pending and self.is_running:
                            try:
                                await asyncio.wait_for(asyncio.shield(recv_task), timeout=2.0)
                            except (asyncio.TimeoutError, asyncio.CancelledError):
                                pass
                        for task in pending:
                            if not task.done():
                                task.cancel()
                        # Stream completed, exit loop
                        break
                    else:
                        # Server disconnected or recv_task errored while send_task still active
                        for task in pending:
                            if not task.done():
                                task.cancel()
                        if not self.is_running:
                            break

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
