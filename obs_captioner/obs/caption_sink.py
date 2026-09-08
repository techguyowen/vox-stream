"""Caption Sink and Dispatcher with Content Filtering, Live Translation, and Twitch Broadcast."""

import asyncio
import logging
import re
import time
from typing import Optional, Tuple

from ..config import AppConfig
from ..engines.base import TranscriptEvent
from ..censor import ContentFilter
from ..formatter import TextFormatter
from ..history import TranscriptHistory
from ..music import is_music_text
from ..translator import SubtitleTranslator
from ..twitch_bot import TwitchCaptionBot
from ..vocabulary import VocabularyReplacer
from .ws_client import OBSWebSocketClient

logger = logging.getLogger("obs_captioner.sink")


class CaptionSink:
    """Dispatches transcribed captions to OBS, Web Overlay, Translation, and Twitch Chat."""

    BOUNDARY_STITCH_PAIRS = {
        ("waypoint", "point"): ("Waypoint", ""),
        ("way point", "point"): ("Waypoint", ""),
        ("way poi", "point"): ("Waypoint", ""),
        ("dog", "solid g"): ("Doxology", ""),
        ("dog.", "solid g"): ("Doxology", ""),
        ("author", "forty"): ("authority", ""),
        ("corin", "thians"): ("Corinthians", ""),
        ("dis", "ciple"): ("disciple", ""),
        ("dis", "ciples"): ("disciples", ""),
    }

    def __init__(
        self,
        config: AppConfig,
        obs_client: Optional[OBSWebSocketClient] = None,
        web_server=None,
        history: Optional[TranscriptHistory] = None,
        twitch_bot: Optional[TwitchCaptionBot] = None,
        is_paused=None,
    ):
        self.config = config
        self.obs_client = obs_client
        self.web_server = web_server
        self.is_paused = is_paused  # optional callable; engines keep running, but events are dropped while paused
        self.vocabulary = VocabularyReplacer(config.vocabulary)
        church_mode = getattr(config.general, "church_mode", True)
        church_name = getattr(config.general, "church_name", "Waypoint Church")
        self.formatter = TextFormatter(
            auto_capitalization=getattr(config.general, "auto_capitalization", True),
            auto_punctuation=getattr(config.general, "auto_punctuation", True),
            church_mode=church_mode,
            church_name=church_name,
        )
        self.content_filter = ContentFilter(config.censor, church_mode=church_mode)
        self.translator = SubtitleTranslator(config.translation)
        self.history = history or TranscriptHistory()
        self.twitch_bot = twitch_bot
        self._last_caption_time = 0.0
        self._sentence_start_time = time.time()
        self._utterance_active = False
        self._last_partial_text: Optional[str] = None
        self._auto_clear_task: Optional[asyncio.Task] = None
        self._last_final_time = 0.0

    def update_config(self, new_config: AppConfig):
        """Live update configuration, filter dictionary, and translation rules."""
        self.config = new_config
        self.vocabulary = VocabularyReplacer(new_config.vocabulary)
        church_mode = getattr(new_config.general, "church_mode", True)
        church_name = getattr(new_config.general, "church_name", "Waypoint Church")
        self.formatter = TextFormatter(
            auto_capitalization=getattr(new_config.general, "auto_capitalization", True),
            auto_punctuation=getattr(new_config.general, "auto_punctuation", True),
            church_mode=church_mode,
            church_name=church_name,
        )
        self.content_filter = ContentFilter(new_config.censor, church_mode=church_mode)
        self.translator = SubtitleTranslator(new_config.translation)

    def _attempt_boundary_stitch(self, clean_text: str) -> Tuple[str, bool]:
        """Stitch mid-word chunk boundary splits between consecutive finalized utterances."""
        now = time.time()
        if not self.history.entries or (now - self._last_final_time > 3.5):
            return clean_text, False

        last_entry = self.history.entries[-1]
        last_text = last_entry.text

        clean_new = clean_text.strip()
        for (prefix, suffix), (stitched_word, _) in self.BOUNDARY_STITCH_PAIRS.items():
            # Check if last entry ends with prefix or stitched word (ignoring trailing punctuation)
            tail_pat = rf"(?:\b|_)(?:{re.escape(prefix)}|{re.escape(stitched_word)})[.,!?:;\-_]*$"
            tail_match = re.search(tail_pat, last_text, re.IGNORECASE)
            if not tail_match:
                continue

            # Check if clean_text starts with suffix or stitched word (ignoring leading punctuation)
            head_pat = rf"^[.,!?:;\-_\s]*(?:{re.escape(suffix)}|{re.escape(stitched_word)})\b[.,!?:;\-_]*"
            head_match = re.search(head_pat, clean_new, re.IGNORECASE)
            if not head_match:
                continue

            # Found split boundary! Update last history entry
            prefix_span = tail_match.span()
            last_entry.text = last_text[:prefix_span[0]] + stitched_word + ("." if last_text.endswith(".") else "")

            # Remainder of new chunk
            remainder = clean_new[head_match.end():].strip()
            if remainder:
                remainder = remainder[0].upper() + remainder[1:]
                return remainder, False
            else:
                return "", True

        return clean_text, False

    async def handle_transcript(self, event: TranscriptEvent):
        """Process, filter, translate, record, and dispatch a new transcript event."""
        if self.is_paused and self.is_paused():
            return

        self._last_caption_time = time.time()
        raw_text = event.text.strip()
        if not raw_text:
            return

        # Music Suppression Check
        if getattr(self.config.audio, "suppress_music", True) and is_music_text(raw_text):
            if event.is_final:
                logger.info(f"✓ [FINAL]   🎵 [MUSIC SUPPRESSED] {raw_text}")
                self._utterance_active = False
                self._last_partial_text = None
                if self.web_server:
                    await self.web_server.broadcast_caption(
                        {"text": "", "is_final": False, "is_censored": False, "timestamp": event.timestamp}
                    )
            return

        if event.is_final:
            self._last_partial_text = None
        else:
            # Continuous engines (Vosk) re-emit identical partials every audio
            # chunk; skip re-broadcasting unchanged interim text.
            if raw_text == self._last_partial_text:
                return
            self._last_partial_text = raw_text
            # First partial after a final marks the start of a new utterance
            if not self._utterance_active:
                self._utterance_active = True
                self._sentence_start_time = time.time()

        # 1. Custom Vocabulary & Glossary Replacements
        vocab_text, _ = self.vocabulary.replace(raw_text)

        # 2. Capitalization & Punctuation Formatting
        formatted_text = self.formatter.format_text(vocab_text, is_final=event.is_final)

        # 3. Content and Profanity Filtering
        clean_text, was_censored = self.content_filter.filter_text(formatted_text)

        # A dropped sentence should vanish quietly: clear the interim line on
        # connected views without wiping their visible caption history.
        if event.is_final and was_censored and self.config.censor.mode == "drop_sentence":
            self._utterance_active = False
            if self.web_server:
                await self.web_server.broadcast_caption(
                    {"text": "", "is_final": False, "is_censored": True, "timestamp": event.timestamp}
                )
            logger.info("✓ [FINAL]   🛡️ [DROPPED]")
            return

        # Boundary Stitcher for split chunks
        if event.is_final and clean_text:
            stitched_text, is_absorbed = self._attempt_boundary_stitch(clean_text)
            if is_absorbed:
                logger.info(f"✓ [STITCHED & ABSORBED] '{clean_text}' into previous entry")
                self._utterance_active = False
                self._sentence_start_time = time.time()
                self._last_final_time = time.time()
                return
            clean_text = stitched_text

        # 4. Live Translation if enabled
        translated_text = None
        if clean_text and self.config.translation.enabled:
            primary_text, translated_text = await self.translator.translate_text(clean_text)
            display_text = primary_text
        else:
            display_text = clean_text

        # 5. Record to history if finalized
        if event.is_final and clean_text:
            self._last_final_time = time.time()
            recorded_text = clean_text
            if translated_text:
                recorded_text = f"{clean_text} ({translated_text})"
            self.history.add_entry(
                text=recorded_text,
                start_time=self._sentence_start_time,
                end_time=time.time(),
                is_censored=was_censored,
            )
            self._utterance_active = False
            self._sentence_start_time = time.time()

            # Auto Scripture Lookup & Broadcast Trigger
            if self.web_server and getattr(self.config, "bible", None) and self.config.bible.enabled:
                if self.config.bible.display_mode == "auto":
                    asyncio.create_task(self.web_server.trigger_scripture_lookup(clean_text))

            # Broadcast to Twitch Chat if enabled
            if self.twitch_bot and self.twitch_bot.is_connected:
                await self.twitch_bot.send_caption(clean_text)

        # 6. Console display
        status = "✓ [FINAL]  " if event.is_final else "… [INTERIM]"
        censor_tag = " 🛡️ [CENSORED]" if was_censored else ""
        trans_tag = f" 🌐 [{translated_text}]" if translated_text else ""
        logger.info(f"{status}{censor_tag} {display_text or '[DROPPED]'}{trans_tag}")

        # 7. Dispatch to Web Overlay (Browser Source) and Dashboard Preview
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

        # 8. Dispatch to OBS WebSocket (Text Source & CEA-608)
        if self.obs_client and self.obs_client.is_connected:
            obs_out_text = display_text
            if translated_text and self.config.translation.display_mode == "dual":
                obs_out_text = f"{display_text}\n{translated_text}"

            if self.config.obs.update_text_source and self.config.obs.text_source_name:
                if not getattr(self.config.overlay, "final_only", False) or event.is_final:
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
        """Clear OBS Text Source and web overlay after silence timeout, respecting min_display_seconds."""
        try:
            auto_hide = getattr(self.config.overlay, "auto_hide_seconds", 4.0) or 0.0
            min_disp = getattr(self.config.overlay, "min_display_seconds", 2.0) or 0.0
            sleep_duration = max(auto_hide, min_disp)
            if sleep_duration <= 0:
                return
            await asyncio.sleep(sleep_duration)
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
