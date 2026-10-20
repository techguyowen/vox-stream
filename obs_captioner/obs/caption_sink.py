"""Caption Sink and Dispatcher with Content Filtering, Live Translation, and Twitch Broadcast."""

import asyncio
import collections
from collections import deque
import logging
import re
import time
from typing import Optional, Tuple

from ..config import AppConfig
from ..engines.base import TranscriptEvent
from ..censor import ContentFilter
from ..formatter import TextFormatter, is_hallucinated_or_leaked_text
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
        subtitle_recorder=None,
    ):
        self.config = config
        self.obs_client = obs_client
        self.web_server = web_server
        self.subtitle_recorder = subtitle_recorder
        self.is_paused = is_paused  # optional callable; engines keep running, but events are dropped while paused
        self.vocabulary = VocabularyReplacer(config.vocabulary, church_name=getattr(config.general, "church_name", ""))
        church_mode = getattr(config.general, "church_mode", True)
        church_name = getattr(config.general, "church_name", "Waypoint Church")
        self.formatter = TextFormatter(
            auto_capitalization=getattr(config.general, "auto_capitalization", True),
            auto_punctuation=getattr(config.general, "auto_punctuation", True),
            church_mode=church_mode,
            church_name=church_name,
        )
        self.content_filter = ContentFilter(config.censor, church_mode=church_mode)
        self.translator = SubtitleTranslator(
            config.translation,
            api_key_resolver=lambda: (
                getattr(getattr(self, "config", config).gemini_live, "api_key", "")
                or getattr(getattr(self, "config", config).summary, "gemini_api_key", "")
            ),
        )
        self.history = history or TranscriptHistory()
        self.twitch_bot = twitch_bot
        self._last_caption_time = 0.0
        self._sentence_start_time = time.time()
        self._utterance_active = False
        self._last_partial_text: Optional[str] = None
        self._last_partial_time = 0.0
        self._auto_clear_task: Optional[asyncio.Task] = None
        self._last_final_time = 0.0
        self._prev_final_time = 0.0
        self._lock: Optional[asyncio.Lock] = None
        self._seq = 0
        self._utterance_id = 0
        self._last_final_utterance_id = 0
        self._last_final_text: Optional[str] = None
        self._duplicate_final_window = 0.75
        self._recent_final_window_sec: float = 8.0
        self._recent_finals: collections.deque[Tuple[str, float]] = collections.deque(maxlen=10)
        self._saw_interim_since_final = False
        self._history_dedup_window = 5.0
        self._obs_text_queue: deque = deque()
        self._obs_cea_queue: deque = deque()
        self._obs_text_worker_task: Optional[asyncio.Task] = None
        self._obs_cea_worker_task: Optional[asyncio.Task] = None
        self._init_boundary_stitches()

    def _init_boundary_stitches(self):
        """Initialize effective boundary stitches from universal pairs, active profile, and church name."""
        self._effective_boundary_stitches = dict(self.BOUNDARY_STITCH_PAIRS)
        church_name = getattr(getattr(self, "config", None), "general", None)
        c_name = getattr(church_name, "church_name", "") or ""

        # Query active profile
        try:
            from ..profiles import get_profile_manager
            pm = get_profile_manager()
            prof = pm.get_profile(c_name)
            if prof:
                base_target = prof.get("name", "").split()[0] if prof.get("name") else "Waypoint"
                for split in prof.get("boundary_splits", []):
                    if isinstance(split, (list, tuple)) and len(split) >= 2:
                        prefix, suffix = split[0], split[1]
                        target = split[2] if len(split) > 2 else base_target
                        self._effective_boundary_stitches[(prefix, suffix)] = (target, "")
        except Exception as e:
            logger.debug(f"Error loading profile boundary stitches: {e}")

        # Dynamically generate boundary splits from church_name words
        if c_name:
            clean_name = c_name.strip()
            first_word = clean_name.split()[0] if clean_name else ""
            if first_word.lower() == "waypoint":
                self._effective_boundary_stitches[("waypoint", "point")] = ("Waypoint", "")
                self._effective_boundary_stitches[("way point", "point")] = ("Waypoint", "")
                self._effective_boundary_stitches[("way poi", "point")] = ("Waypoint", "")
            elif len(first_word) >= 5:
                mid = len(first_word) // 2
                pref_part = first_word[:mid].lower()
                suff_part = first_word[mid:].lower()
                self._effective_boundary_stitches[(first_word.lower(), suff_part)] = (first_word, "")
                self._effective_boundary_stitches[(f"{pref_part} {suff_part}", suff_part)] = (first_word, "")

    def update_config(self, new_config: AppConfig):
        """Live update configuration, filter dictionary, and translation rules."""
        self.config = new_config
        self.vocabulary = VocabularyReplacer(new_config.vocabulary, church_name=getattr(new_config.general, "church_name", ""))
        church_mode = getattr(new_config.general, "church_mode", True)
        church_name = getattr(new_config.general, "church_name", "Waypoint Church")
        self.formatter = TextFormatter(
            auto_capitalization=getattr(new_config.general, "auto_capitalization", True),
            auto_punctuation=getattr(new_config.general, "auto_punctuation", True),
            church_mode=church_mode,
            church_name=church_name,
        )
        self.content_filter = ContentFilter(new_config.censor, church_mode=church_mode)
        self.translator = SubtitleTranslator(
            new_config.translation,
            api_key_resolver=lambda: (
                getattr(new_config.gemini_live, "api_key", "")
                or getattr(new_config.summary, "gemini_api_key", "")
            ),
        )
        self._init_boundary_stitches()

    def _enqueue_obs_text(self, source_name: str, text: str, is_final: bool):
        if not self.obs_client:
            return

        if is_final:
            if self._obs_text_queue and not self._obs_text_queue[-1][2]:
                self._obs_text_queue[-1] = (source_name, text, True)
            else:
                self._obs_text_queue.append((source_name, text, True))
        else:
            if self._obs_text_queue and not self._obs_text_queue[-1][2]:
                self._obs_text_queue[-1] = (source_name, text, False)
            else:
                self._obs_text_queue.append((source_name, text, False))

        if self._obs_text_worker_task is None or self._obs_text_worker_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._obs_text_worker_task = loop.create_task(self._obs_text_worker_loop())
            except RuntimeError:
                pass

    def _enqueue_obs_cea(self, caption_text: str):
        if not self.obs_client:
            return

        self._obs_cea_queue.append(caption_text)
        if self._obs_cea_worker_task is None or self._obs_cea_worker_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._obs_cea_worker_task = loop.create_task(self._obs_cea_worker_loop())
            except RuntimeError:
                pass

    async def _obs_text_worker_loop(self):
        try:
            while self._obs_text_queue:
                source_name, text, is_final = self._obs_text_queue.popleft()
                if self.obs_client and getattr(self.obs_client, "is_connected", False):
                    try:
                        await self.obs_client.update_text_source(source_name, text)
                    except Exception as e:
                        logger.debug(f"OBS update_text_source error: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            self._obs_text_worker_task = None

    async def _obs_cea_worker_loop(self):
        try:
            while self._obs_cea_queue:
                caption_text = self._obs_cea_queue.popleft()
                if self.obs_client and getattr(self.obs_client, "is_connected", False):
                    try:
                        if hasattr(self.obs_client, "send_stream_caption"):
                            await self.obs_client.send_stream_caption(caption_text)
                    except Exception as e:
                        logger.debug(f"OBS send_stream_caption error: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            self._obs_cea_worker_task = None

    async def stop(self):
        if self._auto_clear_task and not self._auto_clear_task.done():
            self._auto_clear_task.cancel()
            self._auto_clear_task = None
        for task_name in ("_obs_text_worker_task", "_obs_cea_worker_task"):
            task = getattr(self, task_name, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, task_name, None)
        self._obs_text_queue.clear()
        self._obs_cea_queue.clear()

    def close(self):
        if self._auto_clear_task and not self._auto_clear_task.done():
            self._auto_clear_task.cancel()
            self._auto_clear_task = None
        for task_name in ("_obs_text_worker_task", "_obs_cea_worker_task"):
            task = getattr(self, task_name, None)
            if task and not task.done():
                task.cancel()
                setattr(self, task_name, None)
        self._obs_text_queue.clear()
        self._obs_cea_queue.clear()

    def _attempt_boundary_stitch(self, clean_text: str, prev_final_time: Optional[float] = None) -> Tuple[str, bool]:
        """Stitch mid-word chunk boundary splits and continuing clauses between consecutive utterances."""
        now = time.time()
        p_time = prev_final_time if prev_final_time is not None else self._prev_final_time
        last_entry_end = self.history.entries[-1].end_time if self.history.entries else 0.0
        last_final_time = last_entry_end if last_entry_end > 1000.0 else p_time
        if not self.history.entries or (now - last_final_time > 3.5):
            return clean_text, False

        last_entry = self.history.entries[-1]
        last_text = last_entry.text
        clean_new = clean_text.strip()
        if not clean_new:
            return clean_text, False

        # 1. Lexical word-split stitching (e.g. 'way poi' + 'point' -> 'Waypoint')
        stitches = getattr(self, "_effective_boundary_stitches", self.BOUNDARY_STITCH_PAIRS)
        for (prefix, suffix), (stitched_word, _) in stitches.items():
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

    def _stamp_payload(self, payload: dict, utterance_id: int) -> dict:
        """Attach monotonic seq / utterance ids so clients can drop stale out-of-order messages."""
        self._seq += 1
        payload["seq"] = self._seq
        payload["utterance_id"] = utterance_id
        return payload

    async def handle_transcript(self, event: TranscriptEvent):
        """Process, filter, translate, record, and dispatch a new transcript event."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            await self._handle_transcript_locked(event)

    async def _handle_transcript_locked(self, event: TranscriptEvent):
        if self.is_paused and self.is_paused():
            return

        self._last_caption_time = time.time()
        raw_text = event.text.strip()
        if not raw_text:
            return

        # Music Suppression Check
        strict_music = getattr(self.config.audio, "suppress_music_strict", False)
        if getattr(self.config.audio, "suppress_music", True) and is_music_text(raw_text, strict=strict_music):
            if event.is_final:
                logger.info(f"✓ [FINAL]   🎵 [MUSIC SUPPRESSED] {raw_text}")
                self._utterance_active = False
                self._last_partial_text = None
                if self.web_server:
                    await self.web_server.broadcast_caption(
                        self._stamp_payload(
                            {"text": "", "is_final": False, "is_censored": False, "timestamp": event.timestamp},
                            self._utterance_id,
                        )
                    )
            return

        # Hallucination / Token Leak Suppression Check
        if is_hallucinated_or_leaked_text(raw_text):
            if event.is_final:
                logger.warning(f"✓ [FINAL]   🛡️ [HALLUCINATION SUPPRESSED] {raw_text}")
                self._utterance_active = False
                self._last_partial_text = None
                if self.web_server:
                    await self.web_server.broadcast_caption(
                        self._stamp_payload(
                            {"text": "", "is_final": False, "is_censored": False, "timestamp": event.timestamp},
                            self._utterance_id,
                        )
                    )
            else:
                logger.debug(f"Interim hallucination suppressed in caption sink: {raw_text}")
            return

        # Whether fresh interim hypotheses arrived since the previous final
        # (captured per final below; defaults permissive so an untracked
        # final is never mistaken for an engine double-emit).
        had_fresh_interim = True

        if event.is_final:
            # Capture whether this final was preceded by fresh hypotheses
            # for its utterance (genuine speech) or arrived back-to-back
            # (engine double-emit), then reset for the next utterance.
            had_fresh_interim = self._saw_interim_since_final
            self._saw_interim_since_final = False
            # Stash the live interim hypothesis: a duplicate final arriving
            # mid-utterance must not destroy the new utterance's partial.
            saved_partial_text = self._last_partial_text
            saved_partial_time = self._last_partial_time
            self._last_partial_text = None
            # A final closes the current utterance; finals-only engines (no
            # preceding interim) each get their own utterance id.
            if self._utterance_active:
                utterance_id = self._utterance_id
            else:
                self._utterance_id += 1
                utterance_id = self._utterance_id
            self._last_final_utterance_id = utterance_id
        else:
            # Continuous engines (Vosk) re-emit identical partials every audio
            # chunk; skip re-broadcasting unchanged interim text.
            if raw_text == self._last_partial_text:
                return
            self._last_partial_text = raw_text
            self._last_partial_time = time.time()
            self._saw_interim_since_final = True
            # First partial after a final marks the start of a new utterance
            if not self._utterance_active:
                self._utterance_active = True
                self._sentence_start_time = time.time()
                self._utterance_id += 1
            utterance_id = self._utterance_id

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
            self._last_partial_text = None
            if self.web_server:
                await self.web_server.broadcast_caption(
                    self._stamp_payload(
                        {"text": "", "is_final": False, "is_censored": True, "timestamp": event.timestamp},
                        utterance_id,
                    )
                )
            logger.info("✓ [FINAL]   🛡️ [DROPPED]")
            return

        # Sliding window caption deduplication:
        # Suppress rapid identical re-finalizations and alternating ping-pong duplicate loops
        # (such as intro videos repeating A -> B -> A -> B) within the recency window.
        prev_final_time = self._last_final_time
        if event.is_final and clean_text:
            normalized_final = re.sub(r"\s+", " ", clean_text).strip().casefold()
            now_ts = time.time()
            is_dup = False
            if normalized_final:
                for text, ts in self._recent_finals:
                    effective_ts = min(ts, self._last_final_time)
                    if text == normalized_final and (now_ts - effective_ts) < self._recent_final_window_sec:
                        is_dup = True
                        break

            if is_dup:
                logger.info(f"✓ [FINAL]   [DUPLICATE SUPPRESSED] {clean_text}")
                if self._utterance_active:
                    # Active new utterance in progress: keep it active and
                    # restore its interim hypothesis wiped above. Clients keep
                    # showing the live interim, so no clearing broadcast.
                    self._last_partial_text = saved_partial_text
                    self._last_partial_time = saved_partial_time
                    self._saw_interim_since_final = True
                elif self.web_server:
                    await self.web_server.broadcast_caption(
                        self._stamp_payload(
                            {"text": "", "is_final": False, "is_censored": False, "timestamp": event.timestamp},
                            utterance_id,
                        )
                    )
                return
            else:
                prev_final_time = self._last_final_time
                self._prev_final_time = prev_final_time
                self._recent_finals.append((normalized_final, now_ts))
                self._last_final_text = normalized_final
                self._last_final_time = now_ts

        # Echo suppression for single-word / punctuation fragments matching trailing words of previous final
        if event.is_final and clean_text and self.history.entries:
            words = clean_text.split()
            if len(words) <= 2:
                norm_w = lambda w: re.sub(r"[^\w]+", "", w).casefold()
                clean_norm_words = [norm_w(w) for w in words if norm_w(w)]
                prev_entry = self.history.entries[-1]
                prev_words = prev_entry.text.split()
                prev_norm_words = [norm_w(w) for w in prev_words if norm_w(w)]
                is_echo = False
                if not clean_norm_words:
                    # Pure punctuation or quote fragment
                    is_echo = True
                elif prev_norm_words and prev_norm_words[-len(clean_norm_words):] == clean_norm_words:
                    is_echo = True

                if is_echo:
                    logger.info(f"✓ [FINAL]   [ECHO SUPPRESSED] {clean_text}")
                    # If previous entry had an unclosed quote and this fragment closes it, seal it
                    prev_text = prev_entry.text
                    if (prev_text.count('"') % 2 == 1 or prev_text.count("'") % 2 == 1) and re.search(r'["\'”’]', clean_text):
                        quote_char = '"' if prev_text.count('"') % 2 == 1 else "'"
                        prev_entry.text = prev_text.rstrip(".,?!;:… ") + "." + quote_char

                    self._utterance_active = False
                    self._sentence_start_time = time.time()
                    if self.web_server:
                        await self.web_server.broadcast_caption(
                            self._stamp_payload(
                                {"text": "", "is_final": False, "is_censored": False, "timestamp": event.timestamp},
                                utterance_id,
                            )
                        )
                    return

        # In-place extension for trailing word additions (e.g. "So, pray for Nate and Diane." -> "So, pray for Nate and Diane now.")
        if event.is_final and clean_text and self.history.entries:
            now = time.time()
            prev_entry = self.history.entries[-1]
            dt = (now - prev_entry.end_time) if prev_entry.end_time > 1000.0 else (now - prev_final_time)
            if dt <= 4.0:
                prev_clean_base = re.sub(r"[.,?!;:…\"'”’\s]+$", "", prev_entry.text).casefold()
                new_clean_base = re.sub(r"[.,?!;:…\"'”’\s]+$", "", clean_text).casefold()
                is_extension = False
                if new_clean_base.startswith(prev_clean_base) and len(new_clean_base) > len(prev_clean_base):
                    is_extension = True
                else:
                    norm_w = lambda w: re.sub(r"[^\w]+", "", w).casefold()
                    prev_norm_words = [norm_w(w) for w in prev_entry.text.split() if norm_w(w)]
                    new_norm_words = [norm_w(w) for w in clean_text.split() if norm_w(w)]
                    if prev_norm_words and len(new_norm_words) > len(prev_norm_words):
                        matches = sum(1 for a, b in zip(prev_norm_words, new_norm_words[:len(prev_norm_words)]) if a == b)
                        if (matches / len(prev_norm_words)) >= 0.85:
                            is_extension = True

                if is_extension:
                    logger.info(f"✓ [FINAL EXTENSION UPDATED] '{clean_text}' replaced previous entry in place")
                    translated_text = None
                    if getattr(event, "translated_text", None):
                        translated_text = event.translated_text
                        display_text = clean_text
                    elif clean_text and self.config.translation.enabled:
                        primary_text, translated_text = await self.translator.translate_text(clean_text)
                        display_text = primary_text
                    else:
                        display_text = clean_text

                    recorded_text = clean_text
                    if translated_text and getattr(self.config.translation, "enabled", False):
                        dual_fmt = getattr(self.config.translation, "dual_subtitle_format", "clean")
                        if dual_fmt == "parentheses":
                            recorded_text = f"{clean_text} ({translated_text})"
                        else:
                            recorded_text = f"{clean_text} / {translated_text}"

                    prev_entry.text = recorded_text
                    prev_entry.end_time = now
                    prev_entry.is_censored = was_censored
                    self._last_final_time = now
                    self._last_final_text = re.sub(r"\s+", " ", clean_text).strip().casefold()
                    self._utterance_active = False
                    self._sentence_start_time = now

                    if self.web_server:
                        await self.web_server.broadcast_caption(
                            self._stamp_payload(
                                {
                                    "text": display_text,
                                    "translated_text": translated_text,
                                    "dual_color": getattr(self.config.translation, "dual_subtitle_color", "#FFD700"),
                                    "dual_scale": getattr(self.config.translation, "dual_subtitle_scale", 0.85),
                                    "dual_format": getattr(self.config.translation, "dual_subtitle_format", "clean"),
                                    "is_final": True,
                                    "is_censored": was_censored,
                                    "timestamp": event.timestamp,
                                    "replace_last": True,
                                },
                                utterance_id,
                            )
                        )

                    if self.obs_client:
                        obs_out_text = display_text
                        if translated_text and getattr(self.config.translation, "enabled", False) and self.config.translation.display_mode == "dual":
                            dual_fmt = getattr(self.config.translation, "dual_subtitle_format", "clean")
                            obs_out_text = f"{display_text}\n({translated_text})" if dual_fmt == "parentheses" else f"{display_text}\n{translated_text}"
                        if self.config.obs.update_text_source and self.config.obs.text_source_name:
                            self._enqueue_obs_text(
                                self.config.obs.text_source_name,
                                obs_out_text,
                                is_final=True,
                            )
                        if self.config.obs.send_cea608_captions and clean_text:
                            self._enqueue_obs_cea(clean_text)

                    if self.subtitle_recorder and getattr(self.subtitle_recorder, "is_recording", False):
                        self.subtitle_recorder.update_last_caption(
                            recorded_text if getattr(self.config.translation, "enabled", False) and translated_text else display_text,
                            end_time=now,
                        )

                    if self.web_server and hasattr(self.web_server, "trigger_scripture_lookup") and getattr(self.config, "bible", None) and self.config.bible.enabled:
                        if self.config.bible.display_mode == "auto":
                            asyncio.create_task(self.web_server.trigger_scripture_lookup(clean_text))

                    if self.config.overlay.auto_hide_seconds > 0:
                        if self._auto_clear_task:
                            self._auto_clear_task.cancel()
                        self._auto_clear_task = asyncio.create_task(self._auto_clear_worker())

                    await asyncio.sleep(0)
                    return

        # Boundary Stitcher for split chunks
        if event.is_final and clean_text:
            prev_entry_text = self.history.entries[-1].text if self.history.entries else None
            stitched_text, is_absorbed = self._attempt_boundary_stitch(clean_text, prev_final_time=prev_final_time)
            if is_absorbed:
                logger.info(f"✓ [STITCHED & ABSORBED] '{clean_text}' into previous entry")
                self._utterance_active = False
                self._sentence_start_time = time.time()
                self._last_final_time = time.time()

                # Dispatch updated full sentence to Web Overlay and OBS Text Source
                last_entry = self.history.entries[-1]
                updated_text = last_entry.text
                self._last_final_text = re.sub(r"\s+", " ", updated_text).strip().casefold()

                if self.web_server:
                    await self.web_server.broadcast_caption(
                        self._stamp_payload(
                            {
                                "text": updated_text,
                                "translated_text": None,
                                "is_final": True,
                                "is_censored": last_entry.is_censored,
                                "timestamp": event.timestamp,
                                "replace_last": True,
                            },
                            utterance_id,
                        )
                    )

                if self.config.obs.update_text_source and self.config.obs.text_source_name:
                    self._enqueue_obs_text(
                        self.config.obs.text_source_name,
                        updated_text,
                        is_final=True,
                    )

                # Rewrite the sidecar block so the absorbed continuation also
                # reaches the .srt/.vtt recording instead of being lost there.
                if self.subtitle_recorder and getattr(self.subtitle_recorder, "is_recording", False):
                    self.subtitle_recorder.update_last_caption(updated_text, end_time=time.time())

                if self.config.overlay.auto_hide_seconds > 0:
                    if self._auto_clear_task:
                        self._auto_clear_task.cancel()
                    self._auto_clear_task = asyncio.create_task(self._auto_clear_worker())

                await asyncio.sleep(0)
                return
            if prev_entry_text is not None and self.history.entries:
                _seal_norm = lambda t: re.sub(r"\s*\([^)]*\)$", "", t).rstrip(".,;:… ")
                prev_changed = _seal_norm(self.history.entries[-1].text) != _seal_norm(prev_entry_text)
            else:
                prev_changed = False
            if prev_changed:
                # A lexical mid-word correction rewrote the previous history
                # entry ("way poi" -> "Waypoint") while the remainder continues
                # as a new sentence: propagate the correction so web views, OBS,
                # and the subtitle sidecar don't keep the stale split word.
                # (A seal-only change that merely appends "." is skipped.)
                corrected_entry = self.history.entries[-1]
                corrected_text = corrected_entry.text
                if self.web_server:
                    await self.web_server.broadcast_caption(
                        self._stamp_payload(
                            {
                                "text": corrected_text,
                                "translated_text": None,
                                "is_final": True,
                                "is_censored": corrected_entry.is_censored,
                                "timestamp": event.timestamp,
                                "replace_last": True,
                            },
                            utterance_id,
                        )
                    )
                if self.config.obs.update_text_source and self.config.obs.text_source_name:
                    self._enqueue_obs_text(
                        self.config.obs.text_source_name,
                        corrected_text,
                        is_final=True,
                    )
                if self.subtitle_recorder and getattr(self.subtitle_recorder, "is_recording", False):
                    self.subtitle_recorder.update_last_caption(corrected_text)
            clean_text = stitched_text

        # 4. Live Translation if enabled
        translated_text = None
        if getattr(event, "translated_text", None):
            # Directly provided by live speech translation engine (e.g. gemini-3.5-live-translate-preview).
            # Interpreter mode runs independently of OBS dual subtitles: keep OBS/display
            # text in the original language unless translation.enabled is on, while
            # broadcast_caption below still forwards translated_text to /display rooms.
            translated_text = event.translated_text
            display_text = clean_text
        elif clean_text and self.config.translation.enabled and event.is_final:
            primary_text, translated_text = await self.translator.translate_text(clean_text)
            display_text = primary_text
        else:
            display_text = clean_text

        # 5. Record to history if finalized
        if event.is_final and clean_text:
            sentence_start = self._sentence_start_time
            sentence_end = time.time()

            # Back-to-back duplicate guard: a final arriving with no fresh
            # interim since the previous final is an engine re-emission, not
            # new speech. Genuine verbatim repeats always carry fresh interim
            # hypotheses for the new utterance and bypass this guard, as do
            # finals outside the recency window (finals-only engines).
            # Punctuation- and translation-insensitive.
            if (
                self.history.entries
                and not had_fresh_interim
                and (sentence_end - prev_final_time) < self._duplicate_final_window
            ):
                norm_new = re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", clean_text)).strip().casefold()
                last_base = re.sub(r"\s*\([^)]*\)$", "", self.history.entries[-1].text).strip()
                norm_last = re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", last_base)).strip().casefold()
                if norm_new and norm_new == norm_last:
                    logger.info(f"✓ [FINAL]   [HISTORY DUPLICATE SUPPRESSED] {clean_text}")
                    self._last_final_time = sentence_end
                    self._utterance_active = False
                    self._sentence_start_time = sentence_end
                    if self.web_server and not self._utterance_active:
                        await self.web_server.broadcast_caption(
                            self._stamp_payload(
                                {"text": "", "is_final": False, "is_censored": False, "timestamp": event.timestamp},
                                utterance_id,
                            )
                        )
                    return

            self._last_final_time = sentence_end
            self._last_final_text = re.sub(r"\s+", " ", clean_text).strip().casefold()
            recorded_text = clean_text
            if translated_text and getattr(self.config.translation, "enabled", False):
                dual_fmt = getattr(self.config.translation, "dual_subtitle_format", "clean")
                if dual_fmt == "parentheses":
                    recorded_text = f"{clean_text} ({translated_text})"
                else:
                    recorded_text = f"{clean_text} / {translated_text}"
            self.history.add_entry(
                text=recorded_text,
                start_time=sentence_start,
                end_time=sentence_end,
                is_censored=was_censored,
            )
            self._utterance_active = False
            self._sentence_start_time = sentence_end

            # Auto Scripture Lookup & Broadcast Trigger
            if self.web_server and hasattr(self.web_server, "trigger_scripture_lookup") and getattr(self.config, "bible", None) and self.config.bible.enabled:
                if self.config.bible.display_mode == "auto":
                    lookup_text = clean_text
                    bible_engine = getattr(self.web_server, "bible_engine", None)
                    if bible_engine:
                        res = bible_engine.parse_and_lookup_first(clean_text)
                        if not res and len(self.history.entries) >= 2:
                            prev_raw = self.history.entries[-2].text
                            prev_text_only = re.sub(r"\s*\([^)]*\)$", "", prev_raw).strip()
                            combined = f"{prev_text_only} {clean_text}"
                            if bible_engine.parse_and_lookup_first(combined):
                                lookup_text = combined
                    elif len(self.history.entries) >= 2:
                        try:
                            from ..bible_engine import BibleEngine
                            if not BibleEngine.extract_citations_from_text(clean_text):
                                prev_raw = self.history.entries[-2].text
                                prev_text_only = re.sub(r"\s*\([^)]*\)$", "", prev_raw).strip()
                                combined = f"{prev_text_only} {clean_text}"
                                if BibleEngine.extract_citations_from_text(combined):
                                    lookup_text = combined
                        except Exception:
                            pass
                    asyncio.create_task(self.web_server.trigger_scripture_lookup(lookup_text))

            # Broadcast to Twitch Chat if enabled
            if self.twitch_bot and self.twitch_bot.is_connected:
                await self.twitch_bot.send_caption(clean_text)

            # Synchronized Subtitle Sidecar Recording (.srt / .vtt)
            if self.subtitle_recorder and getattr(self.subtitle_recorder, "is_recording", False):
                self.subtitle_recorder.add_caption(
                    text=recorded_text if getattr(self.config.translation, "enabled", False) and translated_text else display_text,
                    start_time=sentence_start,
                    end_time=sentence_end,
                )

        # 6. Console display
        status = "✓ [FINAL]  " if event.is_final else "… [INTERIM]"
        censor_tag = " 🛡️ [CENSORED]" if was_censored else ""
        trans_tag = f" 🌐 [{translated_text}]" if translated_text else ""
        logger.info(f"{status}{censor_tag} {display_text or '[DROPPED]'}{trans_tag}")

        # 7 & 8. Concurrent dispatch to Web Overlay and OBS WebSocket
        async def _dispatch_web():
            if self.web_server:
                try:
                    await self.web_server.broadcast_caption(
                        self._stamp_payload(
                            {
                                "text": display_text,
                                "translated_text": translated_text,
                                "dual_color": getattr(self.config.translation, "dual_subtitle_color", "#FFD700"),
                                "dual_scale": getattr(self.config.translation, "dual_subtitle_scale", 0.85),
                                "dual_format": getattr(self.config.translation, "dual_subtitle_format", "clean"),
                                "is_final": event.is_final,
                                "is_censored": was_censored,
                                "timestamp": event.timestamp,
                            },
                            utterance_id,
                        )
                    )
                except Exception as e:
                    logger.error(f"Error broadcasting caption to web overlay: {e}", exc_info=True)

        def _dispatch_obs():
            if not self.obs_client:
                return
            obs_out_text = display_text
            if translated_text and getattr(self.config.translation, "enabled", False) and self.config.translation.display_mode == "dual":
                dual_fmt = getattr(self.config.translation, "dual_subtitle_format", "clean")
                if dual_fmt == "parentheses":
                    obs_out_text = f"{display_text}\n({translated_text})"
                else:
                    obs_out_text = f"{display_text}\n{translated_text}"

            if self.config.obs.update_text_source and self.config.obs.text_source_name:
                if not getattr(self.config.overlay, "final_only", False) or event.is_final:
                    self._enqueue_obs_text(
                        self.config.obs.text_source_name,
                        obs_out_text,
                        is_final=event.is_final,
                    )

            # Send Twitch/YouTube Closed Captions (only on finalized sentences)
            if self.config.obs.send_cea608_captions and event.is_final and clean_text:
                self._enqueue_obs_cea(clean_text)

        if self.obs_client:
            _dispatch_obs()
        if self.web_server:
            await _dispatch_web()

        await asyncio.sleep(0)

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
            self._last_partial_text = None
            # Clear OBS text source
            if self.config.obs.update_text_source and self.config.obs.text_source_name:
                self._enqueue_obs_text(
                    self.config.obs.text_source_name,
                    "",
                    is_final=True,
                )
            # Clear web overlay browser source too
            if self.web_server:
                await self.web_server.broadcast_caption(
                    self._stamp_payload(
                        {"text": "", "is_final": True, "is_censored": False, "timestamp": 0},
                        self._utterance_id,
                    )
                )
        except asyncio.CancelledError:
            pass
