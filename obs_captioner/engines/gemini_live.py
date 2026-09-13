"""Gemini 3.5 Transcribe Live Multimodal Streaming API Engine.

Implements official Google Gemini Live Transcribe specifications:
https://ai.google.dev/gemini-api/docs/live-api/live-transcribe#websockets
- Native SMART mode (automatic disfluency removal, formatting, and grammar polish) vs VERBATIM
- Native customVocabulary biasing (up to 1,000 domain-specific terms)
- Native languageCodes biasing and automatic language detection
- Dual-channel streaming: speculative interimInputTranscription vs authoritative inputTranscription
- Zero-latency Hybrid VAD signaling (audioStreamEnd) driven by local Silero VAD
- Resilient raw WebSockets transport over aiohttp with automatic reconnection
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import AsyncGenerator, Callable, Dict, List, Optional, Tuple, Any

import aiohttp

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig
from ..vad import VoiceActivityDetector

logger = logging.getLogger("obs_captioner.engine.gemini")

GEMINI_LIVE_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"


class GeminiLiveEngine(BaseSTTEngine):
    """Real-time streaming speech transcription using Gemini 3.5 Transcribe Live."""

    def __init__(self, config: AppConfig):
        super().__init__("Gemini 3.5 Transcribe Live")
        self.config = config
        self.api_key = config.gemini_live.api_key or os.environ.get("GEMINI_API_KEY", "")
        self.is_running = False
        self._active_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._active_session: Optional[aiohttp.ClientSession] = None

        # Client-side VAD for zero-latency Hybrid VAD turn finalization
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
            enable_silero=getattr(config.audio, "enable_vad", True),
            suppress_music=getattr(config.audio, "suppress_music", True),
        )

    def _build_system_instruction(self) -> str:
        """Build system instruction for backward compatibility."""
        base = getattr(self.config.gemini_live, "system_instruction", None) or (
            "You are Gemini 3.5 Transcribe. Transcribe the incoming audio stream into text verbatim. Output ONLY the transcribed words."
        )
        extras = []
        if getattr(self.config.gemini_live, "smart_transcription", True):
            extras.append(
                "Clean up speech disfluencies (such as 'ums' and 'ahs'), handle natural self-corrections, "
                "and format proper capitalization and punctuation."
            )
        vocab = getattr(self.config.gemini_live, "custom_vocabulary", []) or []
        vocab_list = ", ".join(f'"{v}"' for v in vocab if isinstance(v, str) and v.strip())
        if vocab_list:
            extras.append(f"Adapt accurately to this custom specialized vocabulary: [{vocab_list}].")
        if extras:
            return f"{base} {' '.join(extras)}"
        return base

    def _get_api_key(self) -> str:
        """Return the current active API key from config or environment."""
        return (self.config.gemini_live.api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

    def _get_ws_url(self) -> str:
        """Return WebSocket endpoint URL formatted with API key parameter."""
        key = self._get_api_key()
        return f"{GEMINI_LIVE_WS_URL}?key={key}"

    def build_setup_payload(self) -> Dict[str, Any]:
        """Construct the official Gemini Live Transcribe setup payload.

        Matches: https://ai.google.dev/gemini-api/docs/live-api/live-transcribe#websockets
        """
        model = (self.config.gemini_live.model or "gemini-3.5-transcribe-live").strip()
        if not model.startswith("models/"):
            model = f"models/{model}"

        # Transcription mode: SMART (disfluency removal, formatting) or VERBATIM
        mode = getattr(self.config.gemini_live, "mode", "SMART") or "SMART"
        if not getattr(self.config.gemini_live, "smart_transcription", True):
            mode = "VERBATIM"
        mode_str = "SMART" if str(mode).upper() == "SMART" else "VERBATIM"

        # Custom vocabulary speech biasing (up to 1,000 terms)
        raw_vocab = getattr(self.config.gemini_live, "custom_vocabulary", []) or []
        vocab = [v.strip() for v in raw_vocab if isinstance(v, str) and v.strip()]

        # Language code hints (empty list triggers automatic language detection)
        lang_codes = list(getattr(self.config.gemini_live, "language_codes", []) or [])
        if not lang_codes:
            glang = getattr(self.config.general, "language", None)
            if glang and glang.strip() and glang.strip().lower() not in ("auto", "default", "none"):
                lang_codes = [glang.strip()]

        input_audio_transcription: Dict[str, Any] = {
            "mode": mode_str,
        }
        if vocab:
            input_audio_transcription["customVocabulary"] = vocab[:1000]
        if lang_codes:
            input_audio_transcription["languageCodes"] = lang_codes

        is_live_translate = "translate" in model.lower()
        if is_live_translate:
            target_lang = getattr(getattr(self.config, "translation", None), "target_language", "es") or "es"
            gen_config: Dict[str, Any] = {
                "responseModalities": ["AUDIO"],
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
                "translationConfig": {
                    "targetLanguageCode": target_lang,
                    "echoTargetLanguage": True,
                },
            }
            return {
                "setup": {
                    "model": model,
                    "generationConfig": gen_config,
                }
            }

        gen_config = {
            "responseModalities": ["TEXT"],
        }
        setup_dict: Dict[str, Any] = {
            "setup": {
                "model": model,
                "generationConfig": gen_config,
                "inputAudioTranscription": input_audio_transcription,
            }
        }

        # Optional system instructions (supported on standard transcribe / agent models, not live-translate)
        instruction = (getattr(self.config.gemini_live, "system_instruction", "") or "").strip()
        if instruction:
            setup_dict["setup"]["systemInstruction"] = {
                "parts": [{"text": instruction}]
            }

        return setup_dict

    def parse_server_message(self, data: Dict[str, Any]) -> List[Tuple[str, bool]]:
        """Parse Gemini Live server JSON response and extract text events.

        Returns a list of (text, is_final) tuples.
        """
        events: List[Tuple[str, bool]] = []
        server_content = data.get("serverContent")
        if not isinstance(server_content, dict):
            return events

        # 0. Live translated output (from gemini-3.5-live-translate-preview)
        output_obj = server_content.get("outputTranscription")
        if isinstance(output_obj, dict) and output_obj.get("text"):
            out_text = str(output_obj["text"]).strip()
            if out_text:
                events.append((out_text, True))

        # 1. Authoritative finalized transcription (emitted on speech completion)
        final_obj = server_content.get("inputTranscription")
        if isinstance(final_obj, dict) and final_obj.get("text"):
            text = str(final_obj["text"]).strip()
            if text and not output_obj:
                events.append((text, True))

        # 2. Speculative interim hypothesis (updates rapidly while user speaks)
        interim_obj = server_content.get("interimInputTranscription")
        if isinstance(interim_obj, dict) and interim_obj.get("text"):
            text = str(interim_obj["text"]).strip()
            if text:
                events.append((text, False))

        # 3. Conversational fallback (modelTurn parts)
        model_turn = server_content.get("modelTurn")
        if isinstance(model_turn, dict):
            parts = model_turn.get("parts", [])
            part_texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
            full_part_text = "".join(part_texts).strip()
            if full_part_text:
                is_done = bool(server_content.get("turnComplete") or server_content.get("generationComplete"))
                events.append((full_part_text, is_done))

        return events

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Validate API key and verify Gemini Live WebSockets connectivity."""
        self.api_key = self._get_api_key()
        if not self.api_key:
            msg = "Missing GEMINI_API_KEY. Please enter your Google AI Studio key in Audio & Engine settings."
            logger.warning(msg)
            if status_callback:
                status_callback(f"❌ {msg}")
            return False

        model = self.config.gemini_live.model or "gemini-3.5-transcribe-live"
        if status_callback:
            status_callback(f"Connecting to Gemini Live ({model})...")

        # Fast connection handshake verification
        try:
            ws_url = self._get_ws_url()
            timeout = aiohttp.ClientTimeout(total=6.0, connect=3.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(ws_url) as ws:
                    setup_payload = self.build_setup_payload()
                    await ws.send_str(json.dumps(setup_payload))
                    msg = await ws.receive(timeout=4.0)

                    if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        raw_data = msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8")
                        res_json = json.loads(raw_data)
                        if "setupComplete" in res_json:
                            logger.info(f"✅ Gemini 3.5 Transcribe Live verified successfully with model '{model}'.")
                            if status_callback:
                                status_callback(f"✅ Gemini Live ({model}) ready!")
                            return True
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                        reason = getattr(msg, "extra", "") or f"Close code {msg.data}"
                        logger.error(f"Gemini WebSocket closed during handshake: {reason}")
                        if "API key not valid" in reason or msg.data == 1007:
                            err = "Invalid Gemini API key. Please check your key at aistudio.google.com."
                        else:
                            err = f"Gemini handshake failed: {reason}"
                        if status_callback:
                            status_callback(f"❌ {err}")
                        return False

            logger.info("Gemini 3.5 Transcribe client verified successfully.")
            if status_callback:
                status_callback(f"✅ Gemini Live ({model}) ready!")
            return True
        except aiohttp.ClientError as ce:
            err = f"Gemini network error: {ce}"
            logger.error(err)
            if status_callback:
                status_callback(f"❌ {err}")
            return False
        except asyncio.TimeoutError:
            err = "Connection to Gemini Live timed out (check internet connection)."
            logger.error(err)
            if status_callback:
                status_callback(f"❌ {err}")
            return False
        except Exception as e:
            err = f"Failed to connect to Gemini Live: {e}"
            logger.error(err, exc_info=True)
            if status_callback:
                status_callback(f"❌ {err}")
            return False

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Stream real-time PCM audio to Gemini 3.5 Transcribe Live over raw WebSockets."""
        self.is_running = True
        model = self.config.gemini_live.model or "gemini-3.5-transcribe-live"
        sample_rate = self.config.audio.sample_rate or 16000
        enable_hybrid_vad = getattr(self.config.gemini_live, "enable_hybrid_vad", True)
        pause_threshold = (getattr(self.config.audio, "sentence_break_ms", 600) or 600) / 1000.0

        while self.is_running:
            self.api_key = self._get_api_key()
            if not self.api_key:
                logger.error("Gemini Live cannot start: Missing API key. Please configure your key in settings.")
                return

            logger.info(f"Opening Gemini Live WebSockets connection (model: {model}, Hybrid VAD: {enable_hybrid_vad})...")
            ws_url = self._get_ws_url()

            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=5.0)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    self._active_session = session
                    async with session.ws_connect(ws_url, heartbeat=20.0) as ws:
                        self._active_ws = ws

                        # 1. Send official setup payload
                        setup_payload = self.build_setup_payload()
                        await ws.send_str(json.dumps(setup_payload))

                        # Await setupComplete confirmation
                        init_msg = await ws.receive(timeout=5.0)
                        if init_msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                            raw = init_msg.data if isinstance(init_msg.data, str) else init_msg.data.decode("utf-8")
                            try:
                                init_data = json.loads(raw)
                                if "setupComplete" in init_data:
                                    logger.info("Gemini 3.5 Transcribe Live session established and ready.")
                            except Exception:
                                pass
                        elif init_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                            logger.error(f"Gemini rejected connection during setup: {getattr(init_msg, 'extra', '')}")
                            await asyncio.sleep(3.0)
                            continue

                        # 2. Worker: stream audio chunks & signal Hybrid VAD end-of-speech
                        async def send_audio():
                            speech_active = False
                            last_speech_time = 0.0

                            async for chunk in audio_stream:
                                if not self.is_running or ws.closed:
                                    break
                                if not chunk:
                                    continue

                                even_len = len(chunk) & ~1
                                pcm_chunk = chunk[:even_len]

                                # Base64 encode raw PCM audio chunk
                                b64_audio = base64.b64encode(pcm_chunk).decode("utf-8")
                                audio_payload = {
                                    "realtimeInput": {
                                        "audio": {
                                            "data": b64_audio,
                                            "mimeType": f"audio/pcm;rate={sample_rate}",
                                        }
                                    }
                                }
                                await ws.send_str(json.dumps(audio_payload))

                                # Hybrid VAD detection
                                if enable_hybrid_vad:
                                    now = time.time()
                                    is_voice = self.vad.is_speech(pcm_chunk)
                                    if is_voice:
                                        speech_active = True
                                        last_speech_time = now
                                    elif speech_active:
                                        if now - last_speech_time >= pause_threshold:
                                            speech_active = False
                                            # Send audioStreamEnd to trigger zero-latency turn finalization
                                            end_signal = {
                                                "realtimeInput": {
                                                    "audioStreamEnd": True
                                                }
                                            }
                                            await ws.send_str(json.dumps(end_signal))

                            # End of stream signal upon loop exit
                            if not ws.closed:
                                try:
                                    await ws.send_str(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                                except Exception:
                                    pass

                        # 3. Worker: receive and dispatch interim and finalized transcripts
                        async def receive_transcripts():
                            async for msg in ws:
                                if not self.is_running:
                                    break

                                if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                                    raw_text = msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8")
                                    try:
                                        data = json.loads(raw_text)
                                    except Exception:
                                        continue

                                    events = self.parse_server_message(data)
                                    is_trans_model = "translate" in getattr(self.config.gemini_live, "model", "").lower()
                                    for text, is_final in events:
                                        await on_transcript(
                                            TranscriptEvent(
                                                text=text,
                                                is_final=is_final,
                                                translated_text=text if is_trans_model else None,
                                            )
                                        )

                                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                    logger.warning("Gemini Live WebSocket closed by remote server.")
                                    break

                        send_task = asyncio.create_task(send_audio())
                        recv_task = asyncio.create_task(receive_transcripts())

                        done, pending = await asyncio.wait(
                            [send_task, recv_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        if send_task in done:
                            # Audio capture stream completed
                            if recv_task in pending and self.is_running:
                                try:
                                    await asyncio.wait_for(asyncio.shield(recv_task), timeout=2.0)
                                except (asyncio.TimeoutError, asyncio.CancelledError):
                                    pass
                            for t in pending:
                                if not t.done():
                                    t.cancel()
                            break
                        else:
                            # Remote connection closed or errored
                            for t in pending:
                                if not t.done():
                                    t.cancel()
                            if not self.is_running:
                                break

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_running:
                    logger.error(f"Gemini Live error: {e}. Reconnecting in 3s...")
                    await asyncio.sleep(3.0)

    async def stop(self) -> None:
        """Stop Gemini Live streaming session cleanly."""
        self.is_running = False
        if self._active_ws and not self._active_ws.closed:
            try:
                await self._active_ws.close()
            except Exception:
                pass
        self._active_ws = None
        self._active_session = None
        logger.info("Gemini 3.5 Transcribe Live engine stopped.")

    def trim_memory(self) -> None:
        """No heavy local GPU/RAM model weights held for cloud STT."""
        pass
