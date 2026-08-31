"""Caption Sink and Dispatcher with Content Filtering, Live Translation, and Twitch Broadcast."""

import asyncio
import logging
import time
from typing import Optional

from ..config import AppConfig
from ..engines.base import TranscriptEvent
from ..censor import ContentFilter
from ..formatter import TextFormatter
from ..history import TranscriptHistory
from ..translator import SubtitleTranslator
from ..twitch_bot import TwitchCaptionBot
from ..vocabulary import VocabularyReplacer
from .ws_client import OBSWebSocketClient

logger = logging.getLogger("obs_captioner.sink")


class CaptionSink:
    """Dispatches transcribed captions to OBS, Web Overlay, Translation, and Twitch Chat."""

    def __init__(
        self,
        config: AppConfig,
        obs_client: Optional[OBSWebSocketClient] = None,
        web_server=None,
        history: Optional[TranscriptHistory] = None,
        twitch_bot: Optional[TwitchCaptionBot] = None,
    ):
        self.config = config
        self.obs_client = obs_client
        self.web_server = web_server
        self.vocabulary = VocabularyReplacer(config.vocabulary)
        self.formatter = TextFormatter(
            auto_capitalization=getattr(config.general, "auto_capitalization", True),
            auto_punctuation=getattr(config.general, "auto_punctuation", True),
            church_mode=getattr(config.general, "church_mode", True),
        )
        self.content_filter = ContentFilter(config.censor)
        self.translator = SubtitleTranslator(config.translation)
        self.history = history or TranscriptHistory()
        self.twitch_bot = twitch_bot
        self._last_caption_time = 0.0
        self._sentence_start_time = time.time()
        self._auto_clear_task: Optional[asyncio.Task] = None

    def update_config(self, new_config: AppConfig):
        """Live update configuration, filter dictionary, and translation rules."""
        self.config = new_config
        self.vocabulary = VocabularyReplacer(new_config.vocabulary)
        self.formatter = TextFormatter(
            auto_capitalization=getattr(new_config.general, "auto_capitalization", True),
            auto_punctuation=getattr(new_config.general, "auto_punctuation", True),
            church_mode=getattr(new_config.general, "church_mode", True),
        )
        self.content_filter = ContentFilter(new_config.censor)
        self.translator = SubtitleTranslator(new_config.translation)

    async def handle_transcript(self, event: TranscriptEvent):
        """Process, filter, translate, record, and dispatch a new transcript event."""
        self._last_caption_time = time.time()
        raw_text = event.text.strip()
        if not raw_text:
            return

        # 1. Custom Vocabulary & Glossary Replacements
        vocab_text, _ = self.vocabulary.replace(raw_text)

        # 2. Capitalization & Punctuation Formatting
        formatted_text = self.formatter.format_text(vocab_text, is_final=event.is_final)

        # 3. Content and Profanity Filtering
        clean_text, was_censored = self.content_filter.filter_text(formatted_text)
        
        # If drop_sentence mode triggered on censored text
        if was_censored and self.config.censor.mode == "drop_sentence":
            clean_text = ""

        # 2. Live Translation if enabled
        translated_text = None
        if clean_text and self.config.translation.enabled:
            primary_text, translated_text = await self.translator.translate_text(clean_text)
            display_text = primary_text
        else:
            display_text = clean_text

        # 3. Record to history if finalized
        if event.is_final and clean_text:
            recorded_text = clean_text
            if translated_text:
                recorded_text = f"{clean_text} ({translated_text})"
            self.history.add_entry(
                text=recorded_text,
                start_time=self._sentence_start_time,
                end_time=time.time(),
                is_censored=was_censored,
            )
            self._sentence_start_time = time.time()

            # Broadcast to Twitch Chat if enabled
            if self.twitch_bot and self.twitch_bot.is_connected:
                await self.twitch_bot.send_caption(clean_text)

        # 4. Console display
        status = "✓ [FINAL]  " if event.is_final else "… [INTERIM]"
        censor_tag = " 🛡️ [CENSORED]" if was_censored else ""
        trans_tag = f" 🌐 [{translated_text}]" if translated_text else ""
        logger.info(f"{status}{censor_tag} {display_text or '[DROPPED]'}{trans_tag}")

        # 5. Dispatch to Web Overlay (Browser Source) and Dashboard Preview
        if self.web_server:
            await self.web_server.broadcast_caption(
                {
                    "text": display_text,
                    "translated_text": translated_text,
                    "is_final": event.is_final,
                    "is_censored": was_censored,
                    "timestamp": event.timestamp,
                }
            )

        # 6. Dispatch to OBS WebSocket (Text Source & CEA-608)
        if self.obs_client and self.obs_client.is_connected:
            obs_out_text = display_text
            if translated_text and self.config.translation.display_mode == "dual":
                obs_out_text = f"{display_text}\n{translated_text}"

            if self.config.obs.update_text_source and self.config.obs.text_source_name:
                await self.obs_client.update_text_source(
                    self.config.obs.text_source_name,
                    obs_out_text,
                )

            # Send Twitch/YouTube Closed Captions (only on finalized sentences)
            if self.config.obs.send_cea608_captions and event.is_final and clean_text:
                await self.obs_client.send_stream_caption(clean_text)

        # Reset auto-clear timer
        if self.config.overlay.auto_hide_seconds > 0:
            if self._auto_clear_task:
                self._auto_clear_task.cancel()
            self._auto_clear_task = asyncio.create_task(self._auto_clear_worker())

    async def _auto_clear_worker(self):
        """Clear OBS Text Source and web overlay after silence timeout."""
        try:
            await asyncio.sleep(self.config.overlay.auto_hide_seconds)
            # Clear OBS text source
            if self.obs_client and self.obs_client.is_connected:
                if self.config.obs.update_text_source and self.config.obs.text_source_name:
                    await self.obs_client.update_text_source(
                        self.config.obs.text_source_name,
                        "",
                    )
            # Clear web overlay browser source too
            if self.web_server:
                await self.web_server.broadcast_caption(
                    {"text": "", "is_final": True, "is_censored": False, "timestamp": 0}
                )
        except asyncio.CancelledError:
            pass
