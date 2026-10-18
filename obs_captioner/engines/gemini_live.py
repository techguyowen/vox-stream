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
import re
import ssl
import time
from typing import AsyncGenerator, Callable, Dict, List, Optional, Tuple, Any, Sequence

import aiohttp

from .base import BaseSTTEngine, CaptionCallback, TranscriptEvent
from ..config import AppConfig
from ..formatter import is_hallucinated_or_leaked_text
from ..music import is_music_text
from ..vad import VoiceActivityDetector
from ..gemini_models import (
    sanitize_gemini_api_key,
    validate_gemini_api_key,
    check_gemini_dns_liveness,
    get_fallback_model_id,
    check_model_deprecation,
)

logger = logging.getLogger("obs_captioner.engine.gemini")

GEMINI_LIVE_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

# Resilient startup tuning. Right after a reboot, network restart, cold start,
# or software update, DNS resolution, TLS handshake, or routing frequently takes
# 4-8s to settle, so a single fast handshake attempt is not enough.
INIT_MAX_ATTEMPTS = 4
INIT_RETRY_DELAYS = (0.0, 2.0, 4.0, 6.0)
INIT_CONNECT_TIMEOUT = 10.0
INIT_TOTAL_TIMEOUT = 10.0
INIT_RECEIVE_TIMEOUT = 10.0

# Streaming reconnection uses exponential backoff capped at this maximum,
# with fast reconnect (150ms) when an already-healthy session drops mid-stream.
FAST_RECONNECT_DELAY = 0.15
HEALTHY_SESSION_MIN_UPTIME_SEC = 3.0
STREAM_RECONNECT_INITIAL_DELAY = 2.0
STREAM_RECONNECT_MAX_DELAY = 15.0
DISCONNECT_DEDUP_WINDOW_SEC = 5.0

# Session duration thresholds for proactive graceful rotation (Google 600s ceiling)
SESSION_ROTATION_THRESHOLD_SEC = 510.0  # (~8.5 minutes) natural pause rotation threshold
SESSION_HARD_ROTATION_SEC = 550.0       # (~9.16 minutes) forced rotation threshold



class ParsedTranscriptTuple(tuple):
    """3-element tuple (text, is_final, translated_text) with backwards-compatible 2-tuple equality."""

    def __new__(cls, text: str, is_final: bool, translated_text: Optional[str] = None):
        return super().__new__(cls, (text, is_final, translated_text))

    @property
    def text(self) -> str:
        return self[0]

    @property
    def is_final(self) -> bool:
        return self[1]

    @property
    def translated_text(self) -> Optional[str]:
        return self[2]

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, tuple):
            if len(other) == 2:
                return (self[0], self[1]) == other
            elif len(other) == 3:
                return (self[0], self[1], self[2]) == other
        return super().__eq__(other)

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)


def _is_duplicate_of_events(text: str, events: Sequence[Tuple[Any, ...]]) -> bool:
    """Return True when an interim hypothesis merely restates an event's text.

    Same-message interim/final pairs overlap when the interim is a stale
    prefix of the just-finalized utterance; genuinely newer speech extends
    beyond the finals and must still be emitted.
    """
    candidate = " ".join(str(text).split()).casefold()
    if not candidate:
        return True
    for ev in events:
        event_text = ev[0]
        existing = " ".join(str(event_text).split()).casefold()
        if candidate and existing and candidate in existing:
            return True
    return False


_WORD_NORM_RE = re.compile(r"[^\w']+", re.UNICODE)


def _norm_word(w: str) -> str:
    return _WORD_NORM_RE.sub("", w).casefold()


def _strip_finalized_prefix(text: str, finalized_prefix: str) -> str:
    """Strip already-finalized prefix text from cumulative turn transcriptions.

    Matches normalized word tokens from the start of `text` against the words
    of `finalized_prefix`, tolerating minor ASR word revisions (>= 70% word
    overlap or exact 2-word tail anchor at the prefix boundary) as well as
    case/punctuation differences.
    """
    if not text or not finalized_prefix:
        return (text or "").strip()

    raw_text = text.strip()
    raw_prefix = finalized_prefix.strip()
    if not raw_text or not raw_prefix:
        return raw_text

    # Fast exact case-insensitive prefix check
    if raw_text.casefold().startswith(raw_prefix.casefold()):
        remainder = raw_text[len(raw_prefix):]
        return re.sub(r"^[\s.,;!?:\"')\]—\-]+", "", remainder).strip()

    prefix_tokens = [_norm_word(t) for t in raw_prefix.split()]
    prefix_tokens = [t for t in prefix_tokens if t]
    if not prefix_tokens:
        return raw_text

    text_matches = list(re.finditer(r"\S+", raw_text))
    text_tokens = [_norm_word(m.group(0)) for m in text_matches]

    n_pref = len(prefix_tokens)
    n_text = len(text_tokens)

    # If text is shorter than or equal to prefix and shares >= 70% of words, it is completely covered
    if n_text <= n_pref:
        overlap = sum(1 for a, b in zip(text_tokens, prefix_tokens[:n_text]) if a and a == b)
        if n_text > 0 and (overlap / n_text) >= 0.70:
            return ""

    # Search candidate cut positions around n_pref (+/- 4 words for ASR insertions/deletions)
    best_cut_idx = 0
    best_score = 0.0
    min_k = max(1, n_pref - 4)
    max_k = min(n_text, n_pref + 4)

    # First check exact token sequence match
    if n_text >= n_pref and text_tokens[:n_pref] == prefix_tokens:
        best_cut_idx = n_pref
        best_score = 1.0
    else:
        from collections import Counter
        pref_counts = Counter(prefix_tokens)
        for k in range(min_k, max_k + 1):
            cand_tokens = [t for t in text_tokens[:k] if t]
            if not cand_tokens:
                continue
            cand_counts = Counter(cand_tokens)
            common = sum(min(pref_counts[w], cand_counts[w]) for w in pref_counts)
            # Check boundary alignment (last words of prefix vs candidate end, or punctuation boundary)
            raw_cand_last = text_matches[k - 1].group(0)
            raw_pref_last = raw_prefix.split()[-1]
            punct_end_match = (
                raw_cand_last[-1] in ".?!,;:" and raw_pref_last[-1] in ".?!,;:"
            )
            exact_two_tail = (
                len(cand_tokens) >= 2
                and len(prefix_tokens) >= 2
                and cand_tokens[-2:] == prefix_tokens[-2:]
            )
            tail_match = (
                cand_tokens[-1] == prefix_tokens[-1]
                or (len(cand_tokens) >= 2 and len(prefix_tokens) >= 2 and cand_tokens[-2] == prefix_tokens[-2])
            )
            score = common / max(n_pref, len(cand_tokens))
            if exact_two_tail:
                score += 0.12
            elif tail_match:
                score += 0.05
            if punct_end_match:
                score += 0.04
            if k == n_pref:
                score += 0.02
            if score > best_score:
                best_score = score
                best_cut_idx = k

    if best_score >= 0.72 and best_cut_idx > 0:
        if best_cut_idx >= len(text_matches):
            return ""
        slice_pos = text_matches[best_cut_idx].start()
        remainder = raw_text[slice_pos:]
        return re.sub(r"^[\s.,;!?:\"')\]—\-]+", "", remainder).strip()

    return raw_text


_ABBREV_WORDS = {
    "dr.", "mr.", "mrs.", "ms.", "prof.", "rev.", "st.", "jr.", "sr.",
    "vs.", "etc.", "e.g.", "i.e.", "u.s.", "u.s.a.", "u.k.", "a.m.", "p.m.",
    "no.", "fig.", "inc.", "ltd.", "co.", "corp.", "gen.", "col.", "lt.", "sgt.",
    "ch.", "chap.", "v.", "vss.", "ver.", "ps.", "matt.", "heb.", "cor.",
    "rom.", "deut.", "lev.", "num.", "ex.", "exod.", "chron.", "sam.",
    "tim.", "thess.", "pet.", "phil.", "col.", "eph.", "gal.", "cf.",
    "approx.", "est.", "min.", "sec.",
}

_SENTENCE_BOUNDARY_RE = re.compile(r'([.?!]+["\'”’)]*)\s+')
_CLAUSE_JOINERS = {";", "—", "–", "--"}
_CONJUNCTION_WORDS = {"and", "but", "so"}


def _split_completed_sentences(
    text: str,
    max_words: int = 24,
    require_trailing_words: int = 3,
) -> Tuple[List[str], str]:
    """Split cumulative unfinalized text into (completed_sentences, remaining_interim).

    Rule A (Punctuated sentence boundary):
      Split on terminal sentence punctuation (`.`, `?`, `!`) when:
      - The completed sentence has at least 4 words (and does not end on a common
        abbreviation, verse citation, digit, or single-letter initial), AND
      - The trailing text does not start with a lowercase letter, AND
      - There are at least `require_trailing_words` (default 3) trailing words
        after the punctuation mark, proving the speaker has moved on and ASR
        punctuation has stabilized.

    Rule B (Word ceiling split):
      If remaining contains ANY terminal sentence punctuation ([.?!]), Rule B
      is skipped to allow sentence boundary stabilization or turn completion.
      For true unpunctuated run-on speech (no period anywhere):
      - Only triggers when word count >= max(32, effective_max + 8).
      - Splits on true clause breaks (`;`, `—`, or coordinating conjunctions
        `and`, `but`, `so` preceded by >= 10 words).
      - Otherwise splits cleanly at `effective_max`.
    """
    effective_max = max(12, int(max_words or 24))
    completed: List[str] = []
    remaining = (text or "").strip()
    if not remaining:
        return completed, ""

    # Rule A: Punctuated sentence boundaries
    while remaining:
        split_done = False
        for match in _SENTENCE_BOUNDARY_RE.finditer(remaining):
            end_pos = match.end(1)
            after_pos = match.end()
            candidate_sent = remaining[:end_pos].strip()
            trailing_text = remaining[after_pos:].strip()

            cand_words = candidate_sent.split()
            trail_words = trailing_text.split()

            if len(cand_words) < 4:
                continue

            last_token = cand_words[-1].lower().lstrip("\"'“‘(")
            if last_token in _ABBREV_WORDS:
                continue
            # Avoid splitting on single-letter initials like "J. R. Tolkien"
            if re.match(r"^[a-z]\.$", last_token):
                continue
            # Ignore numbers with periods ("3. 5 percent", "15.") or verse citations ("15:1-3.", "15:1.")
            if re.match(r"^\d+\.?$", last_token) or re.match(r"^\d+:\d+", last_token):
                continue

            if not trail_words:
                continue

            # In English, a new sentence never begins with a lowercase letter
            first_trail_char = trail_words[0].lstrip("\"'“‘([{\u201c\u2018")
            if first_trail_char and first_trail_char[0].islower():
                continue

            if len(trail_words) >= require_trailing_words:
                completed.append(candidate_sent)
                remaining = trailing_text
                split_done = True
                break

        if not split_done:
            break

    # Rule B: Word ceiling split for continuous unpunctuated run-on speech
    # If remaining contains any terminal sentence punctuation ([.?!]), DO NOT RUN RULE B.
    # Wait for the sentence boundary to stabilize or the turn to complete.
    if re.search(r"[.?!]", remaining):
        return completed, remaining

    threshold = max(32, effective_max + 8)
    while remaining:
        word_matches = list(re.finditer(r"\S+", remaining))
        if len(word_matches) < threshold:
            break

        min_preceding_words = 10
        split_word_idx: Optional[int] = None

        # Search backwards from effective_max down to min_preceding_words for the last clause boundary
        for i in range(effective_max, min_preceding_words - 1, -1):
            if i >= len(word_matches):
                continue
            prev_word = word_matches[i - 1].group(0)
            curr_word_clean = _norm_word(word_matches[i].group(0))

            # Punctuation clause break at end of word i-1 (semicolon, dash)
            if prev_word[-1] in _CLAUSE_JOINERS or prev_word in _CLAUSE_JOINERS:
                split_word_idx = i
                break
            # Coordinating conjunction at start of word i preceded by >= 10 words
            if curr_word_clean in _CONJUNCTION_WORDS:
                split_word_idx = i
                break

        if split_word_idx is None:
            split_word_idx = effective_max

        char_cut = word_matches[split_word_idx - 1].end()
        chunk_sent = remaining[:char_cut].strip()
        # Strip trailing standalone dash/semicolon if split right after it
        chunk_sent = re.sub(r"\s+[-–—;]$", "", chunk_sent).strip()
        if chunk_sent:
            completed.append(chunk_sent)
        remaining = remaining[word_matches[split_word_idx].start():].strip()

    return completed, remaining


def _split_final_paragraph(text: str, max_words: int = 24) -> List[str]:
    """Split a long finalized paragraph into readable caption sentences."""
    raw = (text or "").strip()
    if not raw:
        return []
    effective_max = max(12, int(max_words or 24))
    if len(raw.split()) <= effective_max:
        return [raw]

    sentences: List[str] = []
    remaining = raw
    # 1. Split on all legitimate terminal punctuation boundaries
    while remaining:
        split_done = False
        for match in _SENTENCE_BOUNDARY_RE.finditer(remaining):
            end_pos = match.end(1)
            after_pos = match.end()
            cand = remaining[:end_pos].strip()
            trail = remaining[after_pos:].strip()
            cand_words = cand.split()
            if len(cand_words) < 4:
                continue
            last_token = cand_words[-1].lower().lstrip("\"'“‘(")
            if last_token in _ABBREV_WORDS or re.match(r"^[a-z]\.$", last_token):
                continue
            if re.match(r"^\d+\.?$", last_token) or re.match(r"^\d+:\d+", last_token):
                continue
            trail_words = trail.split()
            if trail_words:
                first_trail_char = trail_words[0].lstrip("\"'“‘([{\u201c\u2018")
                if first_trail_char and first_trail_char[0].islower():
                    continue
            if trail:
                sentences.append(cand)
                remaining = trail
                split_done = True
                break
        if not split_done:
            if remaining:
                rem_words = remaining.split()
                if sentences and len(rem_words) < 3:
                    prev = sentences[-1]
                    has_unclosed_quote = (
                        (prev.count('"') % 2 == 1)
                        or (prev.count("'") % 2 == 1)
                        or (prev.count("“") > prev.count("”"))
                        or (prev.count("‘") > prev.count("’"))
                    )
                    if has_unclosed_quote:
                        sentences[-1] = f"{prev} {remaining}".strip()
                    # Otherwise discard dangling quote/word fragment echo
                else:
                    sentences.append(remaining)
            break

    # 2. Enforce word ceiling on any remaining long segments
    final_parts: List[str] = []
    for seg in sentences:
        if len(seg.split()) <= effective_max:
            final_parts.append(seg)
        else:
            chunks, tail = _split_completed_sentences(seg, max_words=effective_max, require_trailing_words=1)
            final_parts.extend(chunks)
            if tail:
                # If tail is still over effective_max, chunk it into <= effective_max pieces
                curr_tail = tail
                while curr_tail:
                    t_matches = list(re.finditer(r"\S+", curr_tail))
                    if len(t_matches) <= effective_max:
                        final_parts.append(curr_tail)
                        break
                    cut_idx = effective_max
                    for i in range(effective_max, max(9, effective_max // 2), -1):
                        if i >= len(t_matches):
                            continue
                        prev = t_matches[i - 1].group(0)
                        curr_c = _norm_word(t_matches[i].group(0))
                        if prev[-1] in _CLAUSE_JOINERS or prev in _CLAUSE_JOINERS or curr_c in _CONJUNCTION_WORDS:
                            cut_idx = i
                            break
                    c_sent = curr_tail[:t_matches[cut_idx - 1].end()].strip()
                    c_sent = re.sub(r"\s+[-–—;]$", "", c_sent).strip()
                    if c_sent:
                        final_parts.append(c_sent)
                    curr_tail = curr_tail[t_matches[cut_idx].start():].strip()

    return [p for p in final_parts if p]


class GeminiLiveEngine(BaseSTTEngine):
    """Real-time streaming speech transcription using Gemini 3.5 Transcribe Live."""

    def __init__(self, config: AppConfig):
        super().__init__("Gemini 3.5 Transcribe Live")
        self.config = config
        self.api_key = config.gemini_live.api_key or os.environ.get("GEMINI_API_KEY", "")
        self.is_running = False
        self._active_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._active_session: Optional[aiohttp.ClientSession] = None
        self.current_ping_ms: Optional[float] = None
        self.on_ping: Optional[Callable[[float], None]] = None
        self._ping_task: Optional[asyncio.Task] = None
        self.rotation_count: int = 0
        self.session_start_monotonic: float = 0.0
        self._resumption_handle: Optional[str] = None
        self._go_away_received: bool = False
        self.last_rotation_time: float = 0.0
        self.on_audio_data: Optional[Callable[[bytes, str], Any]] = None

        # Token usage & cost tracking (usageMetadata maxima + streamed audio duration)
        self._prompt_tokens: int = 0
        self._response_tokens: int = 0
        self._audio_seconds_streamed: float = 0.0

        # Per-turn incremental sentence segmentation & prefix tracking
        self._finalized_turn_prefix: str = ""
        self._finalized_turn_words: List[str] = []
        self._latest_interim_text: Optional[str] = None
        self._last_interim_change_mono: float = 0.0
        self._last_disconnect_finalized_text: str = ""
        self._last_disconnect_mono: float = 0.0

        # Client-side VAD for zero-latency Hybrid VAD turn finalization
        self.vad = VoiceActivityDetector(
            sample_rate=config.audio.sample_rate,
            noise_gate_db=config.audio.noise_gate_db,
            vad_threshold=config.audio.vad_threshold,
            enable_silero=getattr(config.audio, "enable_vad", True),
            suppress_music=getattr(config.audio, "suppress_music", True),
            suppress_music_strict=getattr(config.audio, "suppress_music_strict", False),
        )

    async def measure_ping(self) -> Optional[float]:
        """Probe network round-trip ping time to Google Gemini API servers.

        Uses an HTTP HEAD request to generativelanguage.googleapis.com to gauge
        network round-trip latency in milliseconds.
        """
        try:
            t0 = time.perf_counter()
            ssl_ctx = self._build_ssl_context()
            if self._active_session and not self._active_session.closed:
                async with self._active_session.head("https://generativelanguage.googleapis.com", ssl=ssl_ctx):
                    pass
            else:
                timeout = aiohttp.ClientTimeout(total=4.0, connect=3.0)
                async with aiohttp.ClientSession(timeout=timeout) as probe_session:
                    async with probe_session.head("https://generativelanguage.googleapis.com", ssl=ssl_ctx):
                        pass
            rtt_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            self.current_ping_ms = rtt_ms
            if self.on_ping:
                try:
                    self.on_ping(rtt_ms)
                except Exception:
                    pass
            return rtt_ms
        except Exception as e:
            logger.debug(f"Gemini API ping probe failed: {e}")
            return None

    @staticmethod
    def _clean_vocab_term(term: str) -> str:
        """Sanitize an individual custom vocabulary term for Gemini speech biasing."""
        if not isinstance(term, str):
            return ""
        cleaned = term.strip()
        # Strip extraneous outer quotes, commas, semicolons, and periods while preserving internal apostrophes
        cleaned = re.sub(r'^[",`\s]+|[",`\s.]+$', '', cleaned)
        # Drop empty strings or single characters (preserving 2+ letter acronyms)
        if len(cleaned) < 2:
            return ""
        return cleaned

    def _get_effective_custom_vocabulary(self) -> List[str]:
        """Construct an optimized, sanitized custom vocabulary list for Gemini Live speech biasing.

        Gathers in priority order:
          1. Explicit Gemini Live custom vocabulary (config.gemini_live.custom_vocabulary)
          2. Canonical replacement values from user glossary (config.vocabulary.terms.values())
          3. Active organization profile terms, leaders, programs, and venues (profiles/*.json)
          4. When church_mode is enabled:
             - Configured church name and campus terms (ChurchLexiconFormatter.generate_church_name_terms)
             - Canonical sacred titles, biblical figures, holy places, doctrines (ChurchLexiconFormatter.CHURCH_TERMS.values())
             - Canonical books of the Bible (ChurchLexiconFormatter.BOOKS_OF_BIBLE.values())
             - Custom censor whitelist words (config.censor.custom_whitelist)
          5. When church_mode is disabled:
             - ZERO church terms, liturgy, or biblical books are added.

        Gemini Neural Biasing Principles:
          - Canonical target phrases ONLY (never phonetic mishearings or regex keys).
          - Case-insensitive deduplication that retains the properly capitalized canonical representation.
          - Enforces Gemini Live API limit of 1,000 terms.
        """
        candidates: List[str] = []
        church_name = (getattr(self.config.general, "church_name", "") or "").strip()
        church_mode = getattr(self.config.general, "church_mode", True)

        # 1. User-specified Gemini custom vocabulary
        raw_vocab = getattr(self.config.gemini_live, "custom_vocabulary", []) or []
        for v in raw_vocab:
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())

        # 2. User glossary replacement values (canonical targets only, never misheard keys)
        vocab_cfg = getattr(self.config, "vocabulary", None)
        if vocab_cfg and getattr(vocab_cfg, "enabled", True):
            terms_dict = getattr(vocab_cfg, "terms", {}) or {}
            for target_val in terms_dict.values():
                if isinstance(target_val, str) and target_val.strip():
                    candidates.append(target_val.strip())

        # 3. Active organization profile terms, leaders, programs, venues (scoped by church_mode)
        try:
            from ..profiles import get_profile_manager
            pm = get_profile_manager()
            prof = pm.get_profile(church_name)
            if prof:
                prof_is_church = prof.get("church_mode", True)
                if (church_mode and prof_is_church) or (not church_mode and not prof_is_church):
                    for word in prof.get("speech_biasing_words", []):
                        if isinstance(word, str) and word.strip():
                            candidates.append(word.strip())
                    for target_val in prof.get("terms", {}).values():
                        if isinstance(target_val, str) and target_val.strip():
                            candidates.append(target_val.strip())
                    for leader in prof.get("leaders", []):
                        if isinstance(leader, str) and leader.strip():
                            candidates.append(leader.strip())
                    for program in prof.get("programs", []):
                        if isinstance(program, str) and program.strip():
                            candidates.append(program.strip())
                    for venue in prof.get("venues_and_geography", []):
                        if isinstance(venue, str) and venue.strip():
                            candidates.append(venue.strip())
        except Exception as e:
            logger.warning(f"Failed to load active profile for Gemini Live vocabulary: {e}")

        # 4. Church-specific vocabulary (ONLY when Church Mode is enabled)
        if church_mode:
            try:
                from ..church_lexicon import ChurchLexiconFormatter

                # 4a. Church name variations and campus terms
                if church_name:
                    name_terms = ChurchLexiconFormatter.generate_church_name_terms(church_name)
                    for val in name_terms.values():
                        if isinstance(val, str) and val.strip():
                            candidates.append(val.strip())

                # 4b. Sacred titles, biblical figures, holy places, doctrines
                for val in ChurchLexiconFormatter.CHURCH_TERMS.values():
                    if isinstance(val, str) and val.strip():
                        candidates.append(val.strip())

                # 4c. Books of the Bible (canonical 66 books)
                for val in ChurchLexiconFormatter.BOOKS_OF_BIBLE.values():
                    if isinstance(val, str) and val.strip():
                        candidates.append(val.strip())

            except Exception as e:
                logger.warning(f"Failed to load ChurchLexiconFormatter for Gemini Live vocabulary: {e}")

            # 4d. Censor custom whitelist words
            censor_cfg = getattr(self.config, "censor", None)
            if censor_cfg:
                whitelist = getattr(censor_cfg, "custom_whitelist", []) or []
                for w in whitelist:
                    if isinstance(w, str) and w.strip():
                        candidates.append(w.strip())

        # Clean and case-insensitively deduplicate, preserving best casing and insertion order
        seen_lower: Dict[str, str] = {}
        for item in candidates:
            cleaned = self._clean_vocab_term(item)
            if not cleaned:
                continue
            key = cleaned.lower()
            if key not in seen_lower:
                seen_lower[key] = cleaned
            else:
                # If existing stored version is all-lowercase and the new version has proper capitalization, upgrade it
                existing = seen_lower[key]
                if existing.islower() and not cleaned.islower():
                    seen_lower[key] = cleaned

        return list(seen_lower.values())[:1000]

    def _build_system_instruction(self) -> str:
        """Construct system instruction with domain conditioning and transcription guidelines."""
        base = getattr(self.config.gemini_live, "system_instruction", None) or (
            "You are Gemini 3.5 Transcribe, a real-time speech transcriber. "
            "Transcribe the incoming audio stream into text accurately and verbatim. "
            "Output only the transcribed words without commentary, conversation, or filler."
        )
        extras = []
        if getattr(self.config.gemini_live, "smart_transcription", True):
            extras.append(
                "Clean up speech disfluencies (such as 'ums' and 'ahs'), handle natural self-corrections, "
                "and format proper capitalization and punctuation."
            )

        church_name = (getattr(self.config.general, "church_name", "") or "").strip()
        church_mode = getattr(self.config.general, "church_mode", True)

        # Domain conditioning: church vs general organization
        if church_mode:
            church_context = (
                "Domain context: Church worship service, scripture readings, theology, and biblical sermon preaching. "
                "Accurately transcribe sacred titles, biblical person names, scriptural book citations, and theological terms. "
                "Format scripture citations using standard biblical notation with numbers and colons (e.g., 'John 3:16', 'Romans 8:28', '1 Corinthians 13:4–7'). "
                "Preserve archaic and traditional biblical phrasing (e.g., 'unto', 'thee', 'thou', 'hath', 'saith') when quoted without modernizing. "
                "Never answer rhetorical questions, provide commentary, or engage in conversation with the speaker. "
                "Ignore background ambient church music, organ pads, or keyboard accompaniment under prayer."
            )
            if church_name:
                church_context += f" Local ministry organization: {church_name}."
            extras.append(church_context)
        else:
            secular_context = "Domain context: Live presentation, speech, and discussion."
            if church_name:
                religious_words = ["church", "chapel", "ministry", "fellowship", "parish", "cathedral", "synagogue", "temple", "mosque"]
                if not any(w in church_name.lower() for w in religious_words):
                    secular_context += f" Organization: {church_name}."
            extras.append(secular_context)
        # Custom vocabulary hints sampled from top collected effective custom vocabulary terms
        effective_vocab = self._get_effective_custom_vocabulary()
        if effective_vocab:
            sample_terms = ", ".join(effective_vocab[:20])
            extras.append(f"Adapt accurately to custom specialized vocabulary: {sample_terms}.")

        extras.append(
            "Output strictly the spoken transcription as continuous natural text. "
            "Never output raw code, array structures, brackets, bullet lists, or repetitive token loops."
        )

        if extras:
            return f"{base} {' '.join(extras)}"
        return base

    def _get_api_key(self) -> str:
        """Return the sanitized active API key from config or environment."""
        raw = (
            self.config.gemini_live.api_key
            or getattr(getattr(self.config, "translation", None), "gemini_api_key", "")
            or os.environ.get("GEMINI_API_KEY", "")
        )
        return sanitize_gemini_api_key(raw)

    def _get_ws_url(self) -> str:
        """Return WebSocket endpoint URL formatted with API key parameter."""
        key = self._get_api_key()
        return f"{GEMINI_LIVE_WS_URL}?key={key}"

    def build_setup_payload(self, force_transcribe: bool = False) -> Dict[str, Any]:
        """Construct the official Gemini Live Transcribe setup payload.

        Matches: https://ai.google.dev/gemini-api/docs/live-api/live-transcribe#websockets

        When force_transcribe is True, skip the live-translate branch and always
        build the standard transcribe-live payload, even in interpreter mode.
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
        vocab = self._get_effective_custom_vocabulary()

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

        if force_transcribe:
            is_live_translate = False
        else:
            is_live_translate = (
                "translate" in model.lower()
                or bool(
                    getattr(self.config, "translation", None)
                    and getattr(self.config.translation, "interpreter_mode", False)
                    and getattr(self.config.translation, "target_language", "en") not in ("en", "none", "")
                )
            )
        if is_live_translate:
            model = "models/gemini-3.5-live-translate-preview"
            target_lang = getattr(getattr(self.config, "translation", None), "target_language", "es") or "es"
            gen_config: Dict[str, Any] = {
                "responseModalities": ["AUDIO"],
                "translationConfig": {
                    "targetLanguageCode": target_lang,
                    "echoTargetLanguage": True,
                },
            }
            return {
                "setup": {
                    "model": model,
                    "generationConfig": gen_config,
                    "inputAudioTranscription": {},
                    "outputAudioTranscription": {},
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
        if self._resumption_handle:
            setup_dict["setup"]["sessionResumption"] = {"handle": self._resumption_handle}
        else:
            setup_dict["setup"]["sessionResumption"] = {}

        # Optional system instructions (supported on standard transcribe / agent models, not live-translate)
        instruction = self._build_system_instruction()
        if instruction:
            setup_dict["setup"]["systemInstruction"] = {
                "parts": [{"text": instruction}]
            }

        return setup_dict

    def _track_usage_metadata(self, data: Dict[str, Any]) -> None:
        """Accumulate session token maxima from a Google usageMetadata payload."""
        usage = data.get("usageMetadata")
        if isinstance(usage, dict):
            p_cnt = usage.get("promptTokenCount")
            if isinstance(p_cnt, int):
                self._prompt_tokens = max(self._prompt_tokens, p_cnt)
            r_cnt = usage.get("responseTokenCount")
            if isinstance(r_cnt, int):
                self._response_tokens = max(self._response_tokens, r_cnt)

    def parse_server_message(self, data: Dict[str, Any]) -> List[ParsedTranscriptTuple]:
        """Parse Gemini Live server JSON response and extract text events.

        Returns a list of (text, is_final, translated_text) tuples.
        """
        self._track_usage_metadata(data)
        events: List[ParsedTranscriptTuple] = []
        server_content = data.get("serverContent")
        if not isinstance(server_content, dict):
            return events

        # Extract finalized transcriptions: inputTranscription (original) and outputTranscription (translated)
        final_obj = server_content.get("inputTranscription")
        output_obj = server_content.get("outputTranscription")

        input_text = ""
        output_text = ""

        if isinstance(final_obj, dict) and final_obj.get("text"):
            raw_in = str(final_obj["text"]).strip()
            if raw_in and not is_hallucinated_or_leaked_text(raw_in):
                input_text = raw_in
            elif raw_in:
                logger.warning(f"Suppressed hallucinated final transcript from Gemini Live: {raw_in}")

        if isinstance(output_obj, dict) and output_obj.get("text"):
            raw_out = str(output_obj["text"]).strip()
            if raw_out and not is_hallucinated_or_leaked_text(raw_out):
                output_text = raw_out
            elif raw_out:
                logger.warning(f"Suppressed hallucinated translated text from Gemini Live: {raw_out}")

        if input_text and output_text:
            events.append(ParsedTranscriptTuple(input_text, True, output_text))
        elif output_text:
            events.append(ParsedTranscriptTuple(output_text, True, output_text))
        elif input_text:
            events.append(ParsedTranscriptTuple(input_text, True, None))

        # Speculative interim hypothesis (updates rapidly while user speaks).
        interim_obj = server_content.get("interimInputTranscription")
        if isinstance(interim_obj, dict) and interim_obj.get("text"):
            text = str(interim_obj["text"]).strip()
            if text:
                if not is_hallucinated_or_leaked_text(text):
                    if events and _is_duplicate_of_events(text, events):
                        logger.debug("Suppressed stale interim restating a final in the same message.")
                    else:
                        events.append(ParsedTranscriptTuple(text, False, None))
                else:
                    logger.debug(f"Suppressed hallucinated interim transcript from Gemini Live: {text}")

        # Conversational fallback & synthesized audio (modelTurn parts)
        model_turn = server_content.get("modelTurn")
        if isinstance(model_turn, dict):
            parts = model_turn.get("parts", [])
            for part in parts:
                if isinstance(part, dict):
                    inline_data = part.get("inlineData")
                    if isinstance(inline_data, dict) and inline_data.get("data"):
                        audio_b64 = inline_data.get("data")
                        mime_type = inline_data.get("mimeType", "audio/pcm;rate=24000")
                        if self.on_audio_data and audio_b64:
                            try:
                                raw_bytes = base64.b64decode(audio_b64)
                                self.on_audio_data(raw_bytes, mime_type)
                            except Exception as e:
                                logger.debug(f"Error invoking on_audio_data: {e}")

            part_texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
            full_part_text = "".join(part_texts).strip()
            if full_part_text:
                is_done = bool(server_content.get("turnComplete") or server_content.get("generationComplete"))
                if events and not is_done and _is_duplicate_of_events(full_part_text, events):
                    logger.debug("Suppressed modelTurn interim restating a final in the same message.")
                elif not is_hallucinated_or_leaked_text(full_part_text):
                    events.append(ParsedTranscriptTuple(full_part_text, is_done, None))
                else:
                    logger.debug(f"Suppressed hallucinated modelTurn from Gemini Live: {full_part_text}")

        return events

    def _reset_turn_state(self) -> None:
        """Reset per-turn prefix tracking and interim state."""
        self._finalized_turn_prefix = ""
        self._finalized_turn_words = []
        self._latest_interim_text = None
        self._last_interim_change_mono = 0.0

    def _append_finalized_prefix(self, segment: str) -> None:
        """Append a newly finalized sentence segment to the current turn's finalized prefix."""
        seg = (segment or "").strip()
        if not seg:
            return
        if self._finalized_turn_prefix:
            self._finalized_turn_prefix = f"{self._finalized_turn_prefix} {seg}"
        else:
            self._finalized_turn_prefix = seg
        self._finalized_turn_words = self._finalized_turn_prefix.split()

    def _strip_disconnect_replay_prefix(self, text: str, now_m: float) -> str:
        """Strip any turn prefix finalized right before an unexpected disconnect if replayed within 5s."""
        if not self._last_disconnect_finalized_text:
            return text
        if self._last_disconnect_mono <= 0.0 or (now_m - self._last_disconnect_mono) > DISCONNECT_DEDUP_WINDOW_SEC:
            self._last_disconnect_finalized_text = ""
            self._last_disconnect_mono = 0.0
            return text

        disc_text = self._last_disconnect_finalized_text.strip()
        if not disc_text:
            return text

        stripped = _strip_finalized_prefix(text, disc_text)
        if stripped != text.strip():
            return stripped

        # Also check if only the last finalized sentence on disconnect was replayed
        completed, tail = _split_completed_sentences(disc_text, max_words=24, require_trailing_words=1)
        last_sent = tail if tail else (completed[-1] if completed else "")
        if last_sent and last_sent != disc_text:
            stripped_tail = _strip_finalized_prefix(text, last_sent)
            if stripped_tail != text.strip():
                return stripped_tail

        return text

    def _segment_streaming_event(
        self,
        text: str,
        is_final: bool,
        translated_text: Optional[str] = None,
        mono_now: Optional[float] = None,
    ) -> List[TranscriptEvent]:
        """Process a parsed server event through incremental sentence splitting and prefix stripping.

        Maintains per-turn `_finalized_turn_prefix` and `_latest_interim_text` so
        multi-sentence `interimInputTranscription` bursts are finalized sentence-by-sentence
        in real time without duplicating text when `inputTranscription` arrives.
        """
        now_m = mono_now if mono_now is not None else time.monotonic()
        max_words = max(12, int(getattr(self.config.audio, "max_sentence_words", 24) or 24))
        out: List[TranscriptEvent] = []

        if not is_final:
            deduped_text = self._strip_disconnect_replay_prefix(text, now_m)
            if not deduped_text:
                return out
            remaining_text = _strip_finalized_prefix(deduped_text, self._finalized_turn_prefix)
            if not remaining_text:
                return out

            completed_sents, remaining_interim = _split_completed_sentences(
                remaining_text, max_words=max_words
            )
            for sent in completed_sents:
                self._append_finalized_prefix(sent)
                out.append(TranscriptEvent(text=sent, is_final=True, translated_text=None))

            if remaining_interim:
                if remaining_interim != self._latest_interim_text:
                    self._last_interim_change_mono = now_m
                self._latest_interim_text = remaining_interim
                out.append(
                    TranscriptEvent(
                        text=remaining_interim,
                        is_final=False,
                        translated_text=translated_text,
                    )
                )
            else:
                self._latest_interim_text = None
                self._last_interim_change_mono = now_m
        else:
            deduped_text = self._strip_disconnect_replay_prefix(text, now_m)
            remaining_text = _strip_finalized_prefix(deduped_text, self._finalized_turn_prefix) if deduped_text else ""
            self._reset_turn_state()
            self._last_disconnect_finalized_text = ""
            self._last_disconnect_mono = 0.0
            if not remaining_text:
                return out

            parts = _split_final_paragraph(remaining_text, max_words=max_words)
            for idx, part in enumerate(parts):
                out.append(
                    TranscriptEvent(
                        text=part,
                        is_final=True,
                        translated_text=translated_text if idx == len(parts) - 1 else None,
                    )
                )

        return out

    def _maybe_finalize_on_vad_pause(
        self,
        speech_active: bool,
        silence_duration: float,
        pause_threshold: float,
        mono_now: Optional[float] = None,
    ) -> Optional[TranscriptEvent]:
        """Finalize pending interim text when Hybrid VAD detects a pause.

        Requires terminal sentence punctuation (. ? !) for standard pauses (>= pause_threshold).
        Unpunctuated text is NEVER finalized on a short breath/pause (0.6s); it requires an
        extended dead silence (>= 2.5s) to ensure the speaker has truly finished speaking.
        """
        if speech_active:
            return None
        if not self._latest_interim_text:
            return None

        raw = self._latest_interim_text.strip()
        words = raw.split()
        if len(words) < 3:
            return None

        now_m = mono_now if mono_now is not None else time.monotonic()

        # Check if latest interim ends with terminal sentence punctuation
        has_terminal_punct = bool(re.search(r'[.?!]["\'”’)]*$', raw))
        if has_terminal_punct:
            last_token = words[-1].lower().lstrip("\"'“‘(")
            if (
                last_token in _ABBREV_WORDS
                or re.match(r"^\d+\.?$", last_token)
                or re.match(r"^\d+:\d+\.?$", last_token)
                or re.match(r"^[a-z]\.$", last_token)
            ):
                has_terminal_punct = False

        if has_terminal_punct:
            if silence_duration < pause_threshold:
                return None
            if self._last_interim_change_mono > 0.0 and (now_m - self._last_interim_change_mono) < pause_threshold:
                return None
        else:
            # DO NOT finalize unpunctuated text on a short pause (0.6s).
            # ONLY finalize unpunctuated text if silence_duration >= 2.5 seconds (true extended dead silence)
            unpunct_threshold = max(2.5, pause_threshold)
            if silence_duration < unpunct_threshold:
                return None
            if self._last_interim_change_mono > 0.0 and (now_m - self._last_interim_change_mono) < unpunct_threshold:
                return None

        text_to_finalize = raw
        self._append_finalized_prefix(text_to_finalize)
        self._latest_interim_text = None
        self._last_interim_change_mono = now_m
        return TranscriptEvent(text=text_to_finalize, is_final=True, translated_text=None)

    def reset_token_usage(self) -> None:
        """Reset session token and streamed-audio counters to zero."""
        self._prompt_tokens = 0
        self._response_tokens = 0
        self._audio_seconds_streamed = 0.0

    def get_session_stats(self) -> Dict[str, Any]:
        """Return live session duration, rotation telemetry, and token usage/cost estimate."""
        uptime = (time.monotonic() - self.session_start_monotonic) if self.session_start_monotonic > 0 else 0.0
        model_name = (self.config.gemini_live.model or "gemini-3.5-transcribe-live").strip()
        is_translate = (
            "translate" in model_name.lower()
            or bool(
                getattr(self.config, "translation", None)
                and getattr(self.config.translation, "interpreter_mode", False)
                and getattr(self.config.translation, "target_language", "en") not in ("en", "none", "")
            )
        )
        # Google Gemini Live bills audio input at ~25 tokens per second. When the
        # server has not reported usageMetadata yet, estimate input from audio.
        prompt_tokens = self._prompt_tokens
        if prompt_tokens == 0 and self._audio_seconds_streamed > 0:
            prompt_tokens = int(self._audio_seconds_streamed * 25)
        response_tokens = self._response_tokens
        total_tokens = prompt_tokens + response_tokens
        # Standard rate: $3.50 per 1M prompt tokens + $21.00 per 1M response tokens.
        cost_usd = (prompt_tokens / 1_000_000.0) * 3.50 + (response_tokens / 1_000_000.0) * 21.00
        return {
            "uptime_seconds": round(uptime, 1),
            "rotations": self.rotation_count,
            "last_rotation_time": self.last_rotation_time,
            "resumption_handle_active": bool(self._resumption_handle),
            "token_usage": {
                "prompt_tokens": prompt_tokens,
                "response_tokens": response_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(cost_usd, 4),
                "cost_rate_per_hour": "$2.21 / hr" if is_translate else "$0.54 / hr",
                "audio_seconds": round(self._audio_seconds_streamed, 1),
                "model": model_name,
                "is_offline": False,
                "free_tier_eligible": True,
                "spend_cap_url": "https://ai.studio/spend",
            },
        }

    @staticmethod
    def _build_ssl_context() -> "ssl.SSLContext":
        """Standard SSL context for aiohttp (LibreSSL / proxy compatible)."""
        return ssl.create_default_context()

    @staticmethod
    def _is_invalid_key_failure(reason: str, code: Any = None) -> bool:
        """Detect credential rejections that must fail fast without retrying."""
        if code == 1007:
            return True
        text = str(reason or "")
        return "API key not valid" in text or "API_KEY_INVALID" in text

    @staticmethod
    def _is_invalid_model_failure(reason: str, code: Any = None) -> bool:
        """Detect model deprecation, sunsetting, or not-found errors."""
        text = str(reason or "").lower()
        return (
            "not found" in text
            or "404" in text
            or "invalid model" in text
            or "not supported" in text
            or "is not found" in text
            or "unknown model" in text
        )

    async def _attempt_handshake(self, ws_url: str, model: str) -> str:
        """Perform one handshake attempt. Returns 'ok', 'invalid_key', 'invalid_model', or raises transient errors."""
        timeout = aiohttp.ClientTimeout(total=INIT_TOTAL_TIMEOUT, connect=INIT_CONNECT_TIMEOUT)
        ssl_context = self._build_ssl_context()
        t_start = time.perf_counter()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(ws_url, ssl=ssl_context) as ws:
                setup_payload = self.build_setup_payload()
                await ws.send_str(json.dumps(setup_payload))
                msg = await ws.receive(timeout=INIT_RECEIVE_TIMEOUT)

                if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    raw_data = msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8")
                    res_json = json.loads(raw_data)
                    if "setupComplete" in res_json:
                        self.current_ping_ms = round((time.perf_counter() - t_start) * 1000.0, 1)
                        if self.on_ping:
                            try:
                                self.on_ping(self.current_ping_ms)
                            except Exception:
                                pass
                        logger.info(
                            f"✅ Gemini 3.5 Transcribe Live verified successfully with model '{model}' "
                            f"(handshake: {self.current_ping_ms}ms)."
                        )
                        return "ok"
                    raise aiohttp.ClientConnectionError(
                        f"Gemini handshake failed: unexpected response {raw_data[:200]}"
                    )

                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    reason = getattr(msg, "extra", "") or f"Close code {msg.data}"
                    if self._is_invalid_key_failure(reason, getattr(msg, "data", None)):
                        return "invalid_key"
                    if self._is_invalid_model_failure(reason, getattr(msg, "data", None)):
                        return "invalid_model"
                    raise aiohttp.ClientConnectionError(f"Gemini handshake failed: {reason}")

                raise aiohttp.ClientConnectionError(
                    f"Gemini handshake failed: unexpected message type {msg.type}"
                )

    async def initialize(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Validate API key and verify Gemini Live WebSockets connectivity.

        Resilient to cold-start networks: retries transient handshake failures
        with progressive backoff, but fails fast on an invalid API key.
        """
        self.api_key = self._get_api_key()
        if not self.api_key:
            msg = "Missing GEMINI_API_KEY. Please enter your Google AI Studio key in Audio & Engine settings."
            logger.warning(msg)
            if status_callback:
                status_callback(f"❌ {msg}")
            return False

        # Pre-flight DNS check (fast non-blocking probe)
        dns_ok = await check_gemini_dns_liveness()
        if not dns_ok:
            logger.warning("Gemini Live: DNS check failed for generativelanguage.googleapis.com (offline or network delay).")
            if status_callback:
                status_callback("🔄 Network/DNS initializing, checking connection...")

        model = self.config.gemini_live.model or "gemini-3.5-transcribe-live"
        if status_callback:
            status_callback(f"Connecting to Gemini Live ({model})...")

        ws_url = self._get_ws_url()
        for attempt in range(1, INIT_MAX_ATTEMPTS + 1):
            if attempt > 1:
                delay = INIT_RETRY_DELAYS[min(attempt - 1, len(INIT_RETRY_DELAYS) - 1)]
                if delay > 0:
                    await asyncio.sleep(delay)
            try:
                outcome = await self._attempt_handshake(ws_url, model)
                if outcome == "ok":
                    logger.info("Gemini 3.5 Transcribe client verified successfully.")
                    if status_callback:
                        status_callback(f"✅ Gemini Live ({model}) ready!")
                    return True
                if outcome == "invalid_model":
                    fallback = getattr(self.config.gemini_live, "fallback_model", "") or get_fallback_model_id(model)
                    if fallback and fallback != model:
                        logger.warning(
                            f"⚠️ Gemini model '{model}' was rejected by Google (deprecated or invalid). Auto-falling back to '{fallback}'..."
                        )
                        if status_callback:
                            status_callback(f"⚠️ Model '{model}' deprecated/invalid. Auto-falling back to {fallback}...")
                        self.config.gemini_live.model = fallback
                        model = fallback
                        continue
                    err = f"Configured Gemini model '{model}' is invalid or deprecated."
                    logger.error(err)
                    if status_callback:
                        status_callback(f"❌ {err}")
                    return False
                err = "Invalid Gemini API key. Please check your key at aistudio.google.com."
                logger.error(err)
                if status_callback:
                    status_callback(f"❌ {err}")
                return False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._is_invalid_key_failure(str(e)):
                    err = "Invalid Gemini API key. Please check your key at aistudio.google.com."
                    logger.error(err)
                    if status_callback:
                        status_callback(f"❌ {err}")
                    return False
                if self._is_invalid_model_failure(str(e)):
                    fallback = getattr(self.config.gemini_live, "fallback_model", "") or get_fallback_model_id(model)
                    if fallback and fallback != model:
                        logger.warning(
                            f"⚠️ Model '{model}' rejected ({e}). Auto-falling back to '{fallback}'..."
                        )
                        if status_callback:
                            status_callback(f"⚠️ Model '{model}' invalid. Auto-falling back to {fallback}...")
                        self.config.gemini_live.model = fallback
                        model = fallback
                        continue
                if attempt >= INIT_MAX_ATTEMPTS:
                    err = f"Failed to connect to Gemini Live after {INIT_MAX_ATTEMPTS} attempts: {e}"
                    logger.error(err)
                    if status_callback:
                        status_callback(f"❌ {err}")
                    return False
                logger.warning(f"Gemini Live handshake attempt {attempt}/{INIT_MAX_ATTEMPTS} failed ({e}); retrying...")
                if status_callback:
                    status_callback(
                        f"🔄 Network initializing, retrying connection (attempt {attempt + 1}/{INIT_MAX_ATTEMPTS})..."
                    )
        return False

    async def start_streaming(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        on_transcript: CaptionCallback,
    ) -> None:
        """Stream real-time PCM audio to Gemini 3.5 Transcribe Live over raw WebSockets.

        The capture generator is drained by one long-lived pump task into a
        queue. Per-connection sender tasks read from that queue, so cancelling
        a sender on disconnect can never kill the capture generator (a
        cancelled async generator is finished forever, which would otherwise
        leave every reconnect with no audio) and no queued audio is lost.
        """
        audio_q: asyncio.Queue = asyncio.Queue(maxsize=1500)

        async def _pump() -> None:
            try:
                async for chunk in audio_stream:
                    if audio_q.full():
                        try:
                            audio_q.get_nowait()  # drop oldest rather than block capture
                        except asyncio.QueueEmpty:
                            pass
                    audio_q.put_nowait(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Gemini Live audio pump ended: {e}")
            finally:
                if audio_q.full():
                    try:
                        audio_q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    audio_q.put_nowait(None)  # end-of-stream sentinel
                except asyncio.QueueFull:
                    pass

        pump_task = asyncio.ensure_future(_pump())
        try:
            await self._stream_from_queue(audio_q, on_transcript)
        finally:
            if not pump_task.done():
                pump_task.cancel()

    async def _run_interpreter_sidecar(self, interp_q: "asyncio.Queue") -> None:
        """Run the interpreter audio sidecar on the live-translate model.

        Forwards PCM audio to models/gemini-3.5-live-translate-preview and routes
        only translated audio (modelTurn inlineData) to on_audio_data. Never
        emits transcript events so outputTranscription fragments cannot pollute
        or lag the primary STT stream.
        """
        sample_rate = self.config.audio.sample_rate or 16000
        sidecar_reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY

        def _next_sidecar_backoff() -> float:
            nonlocal sidecar_reconnect_delay
            d = sidecar_reconnect_delay
            sidecar_reconnect_delay = min(sidecar_reconnect_delay * 2.0, STREAM_RECONNECT_MAX_DELAY)
            return d

        while self.is_running:
            sidecar_start_time = 0.0
            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=INIT_CONNECT_TIMEOUT)
                ssl_context = self._build_ssl_context()
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(
                        self._get_ws_url(), heartbeat=20.0, ssl=ssl_context
                    ) as ws:
                        await ws.send_str(
                            json.dumps(self.build_setup_payload(force_transcribe=False))
                        )
                        try:
                            init_msg = await ws.receive(timeout=INIT_RECEIVE_TIMEOUT)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            if not self.is_running:
                                return
                            await asyncio.sleep(_next_sidecar_backoff())
                            continue
                        except Exception:
                            if not self.is_running:
                                return
                            await asyncio.sleep(_next_sidecar_backoff())
                            continue
                        if init_msg.type in (
                            aiohttp.WSMsgType.TEXT,
                            aiohttp.WSMsgType.BINARY,
                        ):
                            raw = (
                                init_msg.data
                                if isinstance(init_msg.data, str)
                                else init_msg.data.decode("utf-8")
                            )
                            try:
                                init_data = json.loads(raw)
                            except Exception:
                                await asyncio.sleep(_next_sidecar_backoff())
                                continue
                            if "setupComplete" not in init_data:
                                await asyncio.sleep(_next_sidecar_backoff())
                                continue
                            sidecar_start_time = time.monotonic()
                            sidecar_reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY
                            logger.info("Interpreter audio sidecar connected to gemini-3.5-live-translate-preview.")
                        else:
                            await asyncio.sleep(_next_sidecar_backoff())
                            continue

                        audio_chunk_count = 0

                        async def _sidecar_send() -> None:
                            while self.is_running and not ws.closed:
                                try:
                                    pcm_chunk = await interp_q.get()
                                except asyncio.CancelledError:
                                    break
                                if pcm_chunk is None:
                                    break
                                if ws.closed:
                                    break
                                if not pcm_chunk:
                                    continue
                                try:
                                    b64_audio = base64.b64encode(pcm_chunk).decode("utf-8")
                                    await ws.send_str(
                                        json.dumps(
                                            {
                                                "realtimeInput": {
                                                    "audio": {
                                                        "data": b64_audio,
                                                        "mimeType": f"audio/pcm;rate={sample_rate}",
                                                    }
                                                }
                                            }
                                        )
                                    )
                                except asyncio.CancelledError:
                                    break
                                except Exception as e:
                                    logger.warning(f"Interpreter audio sidecar sender error ({e}); reconnecting...")
                                    break

                        async def _sidecar_recv() -> None:
                            nonlocal audio_chunk_count
                            try:
                                async for msg in ws:
                                    if not self.is_running:
                                        break
                                    if msg.type in (
                                        aiohttp.WSMsgType.TEXT,
                                        aiohttp.WSMsgType.BINARY,
                                    ):
                                        raw_text = (
                                            msg.data
                                            if isinstance(msg.data, str)
                                            else msg.data.decode("utf-8")
                                        )
                                        try:
                                            data = json.loads(raw_text)
                                        except Exception:
                                            continue
                                        self._track_usage_metadata(data)
                                        server_content = data.get("serverContent")
                                        if not isinstance(server_content, dict):
                                            continue
                                        model_turn = server_content.get("modelTurn")
                                        if not isinstance(model_turn, dict):
                                            continue
                                        parts = model_turn.get("parts", [])
                                        for part in parts:
                                            if not isinstance(part, dict):
                                                continue
                                            inline_data = part.get("inlineData")
                                            if (
                                                isinstance(inline_data, dict)
                                                and inline_data.get("data")
                                            ):
                                                audio_b64 = inline_data.get("data")
                                                mime_type = inline_data.get(
                                                    "mimeType", "audio/pcm;rate=24000"
                                                )
                                                if self.on_audio_data and audio_b64:
                                                    try:
                                                        raw_bytes = base64.b64decode(
                                                            audio_b64
                                                        )
                                                        audio_chunk_count += 1
                                                        if audio_chunk_count == 1 or audio_chunk_count % 10 == 0:
                                                            logger.info(
                                                                f"Interpreter audio sidecar received {len(raw_bytes)}B PCM ({mime_type}) [chunk {audio_chunk_count}]"
                                                            )
                                                        res = self.on_audio_data(
                                                            raw_bytes, mime_type
                                                        )
                                                        if asyncio.iscoroutine(res):
                                                            await res
                                                    except Exception as e:
                                                        logger.debug(
                                                            f"Sidecar on_audio_data error: {e}"
                                                        )
                                    elif msg.type in (
                                        aiohttp.WSMsgType.CLOSED,
                                        aiohttp.WSMsgType.CLOSE,
                                        aiohttp.WSMsgType.ERROR,
                                    ):
                                        logger.warning(f"Interpreter audio sidecar connection closed ({msg.type}); reconnecting...")
                                        break
                            except asyncio.CancelledError:
                                raise
                            except Exception as e:
                                logger.warning(f"Interpreter audio sidecar receiver ended ({e}); reconnecting...")

                        send_task = asyncio.create_task(_sidecar_send())
                        recv_task = asyncio.create_task(_sidecar_recv())
                        try:
                            _done, pending = await asyncio.wait(
                                [send_task, recv_task],
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for t in pending:
                                if not t.done():
                                    t.cancel()
                            if pending:
                                await asyncio.gather(*pending, return_exceptions=True)
                        finally:
                            for t in (send_task, recv_task):
                                if not t.done():
                                    t.cancel()

                        if not self.is_running:
                            break
                        was_healthy_sidecar = (
                            sidecar_start_time > 0.0
                            and (time.monotonic() - sidecar_start_time) >= HEALTHY_SESSION_MIN_UPTIME_SEC
                        )
                        if was_healthy_sidecar:
                            delay = FAST_RECONNECT_DELAY
                            sidecar_reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY
                        else:
                            delay = _next_sidecar_backoff()
                        await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self.is_running:
                    break
                was_healthy_sidecar = (
                    sidecar_start_time > 0.0
                    and (time.monotonic() - sidecar_start_time) >= HEALTHY_SESSION_MIN_UPTIME_SEC
                )
                if was_healthy_sidecar:
                    delay = FAST_RECONNECT_DELAY
                    sidecar_reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY
                else:
                    delay = _next_sidecar_backoff()
                logger.warning(f"Interpreter sidecar error ({e}); reconnecting in {delay:.2f}s...")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break

    async def _stream_from_queue(
        self,
        audio_q: "asyncio.Queue",
        on_transcript: CaptionCallback,
    ) -> None:
        """Reconnecting Gemini Live session loop fed from a persistent audio queue."""
        self.is_running = True
        model = self.config.gemini_live.model or "gemini-3.5-transcribe-live"
        sample_rate = self.config.audio.sample_rate or 16000
        enable_hybrid_vad = getattr(self.config.gemini_live, "enable_hybrid_vad", True)
        pause_threshold = max(0.35, (getattr(self.config.audio, "sentence_break_ms", 600) or 600) / 1000.0)
        max_sentence_duration = float(getattr(self.config.audio, "max_sentence_duration_seconds", 7.0) or 7.0)
        reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY

        def _next_backoff() -> float:
            nonlocal reconnect_delay
            delay = reconnect_delay
            reconnect_delay = min(reconnect_delay * 2.0, STREAM_RECONNECT_MAX_DELAY)
            return delay

        stream_state = {"finished": False}

        use_interp_sidecar = bool(
            getattr(self.config, "translation", None)
            and getattr(self.config.translation, "interpreter_mode", False)
            and getattr(self.config.translation, "target_language", "en") not in ("en", "none", "")
            and "translate" not in (self.config.gemini_live.model or "").lower()
        )
        interp_q: "asyncio.Queue | None" = None
        interp_task: "asyncio.Task | None" = None
        if use_interp_sidecar:
            interp_q = asyncio.Queue(maxsize=60)
            interp_task = asyncio.create_task(self._run_interpreter_sidecar(interp_q))
        try:
            while self.is_running:
                self.api_key = self._get_api_key()
                if not self.api_key:
                    logger.error("Gemini Live cannot start: Missing API key. Please configure your key in settings.")
                    return

                logger.info(f"Opening Gemini Live WebSockets connection (model: {model}, Hybrid VAD: {enable_hybrid_vad})...")
                ws_url = self._get_ws_url()
                session_start_time = 0.0

                try:
                    timeout = aiohttp.ClientTimeout(total=None, connect=INIT_CONNECT_TIMEOUT)
                    ssl_context = self._build_ssl_context()
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        self._active_session = session
                        async with session.ws_connect(ws_url, heartbeat=20.0, ssl=ssl_context) as ws:
                            self._active_ws = ws
                            session_start_time = 0.0
                            rotation_requested = False
                            self._reset_turn_state()
                            final_received_event = asyncio.Event()

                            # 1. Send official setup payload
                            setup_payload = self.build_setup_payload(force_transcribe=use_interp_sidecar)
                            setup_t0 = time.perf_counter()
                            await ws.send_str(json.dumps(setup_payload))

                            # Await setupComplete confirmation
                            init_msg = await ws.receive(timeout=INIT_RECEIVE_TIMEOUT)
                            if init_msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                                raw = init_msg.data if isinstance(init_msg.data, str) else init_msg.data.decode("utf-8")
                                try:
                                    init_data = json.loads(raw)
                                    if "setupComplete" in init_data:
                                        session_start_time = time.monotonic()
                                        self.session_start_monotonic = session_start_time
                                        rotation_requested = False
                                        self._reset_turn_state()
                                        final_received_event.clear()
                                        handshake_ms = round((time.perf_counter() - setup_t0) * 1000.0, 1)
                                        self.current_ping_ms = handshake_ms
                                        if self.on_ping:
                                            try:
                                                self.on_ping(handshake_ms)
                                            except Exception:
                                                pass
                                        logger.info(f"Gemini 3.5 Transcribe Live session established and ready ({handshake_ms}ms).")
                                        reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY
                                except Exception:
                                    pass
                            elif init_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                                reason = getattr(init_msg, "extra", "") or f"Close code {init_msg.data}"
                                if self._is_invalid_key_failure(reason, getattr(init_msg, "data", None)):
                                    logger.error("Invalid Gemini API key. Please check your key at aistudio.google.com.")
                                    return
                                if self._is_invalid_model_failure(reason, getattr(init_msg, "data", None)):
                                    fallback = getattr(self.config.gemini_live, "fallback_model", "") or get_fallback_model_id(model)
                                    if fallback and fallback != model:
                                        logger.warning(f"⚠️ Model '{model}' rejected during setup ({reason}). Auto-falling back to '{fallback}'...")
                                        self.config.gemini_live.model = fallback
                                        model = fallback
                                        continue
                                delay = _next_backoff()
                                logger.warning(f"Gemini rejected connection during setup ({reason}); retrying in {delay:.0f}s...")
                                await asyncio.sleep(delay)
                                continue

                            stream_state["finished"] = False

                            # 2. Worker: stream audio chunks & signal Hybrid VAD end-of-speech
                            async def send_audio():
                                nonlocal rotation_requested
                                speech_active = False
                                speech_start_time = 0.0
                                last_speech_time = 0.0

                                eof = False
                                while True:
                                    chunk = await audio_q.get()
                                    if chunk is None:
                                        eof = True
                                        break
                                    if not self.is_running or ws.closed:
                                        break
                                    if not chunk:
                                        continue

                                    even_len = len(chunk) & ~1
                                    pcm_chunk = chunk[:even_len]

                                    # Music suppression gating:
                                    # When strict music suppression is active and acoustic music is detected,
                                    # halt streaming audio to Gemini so singing and worship instruments are completely silenced.
                                    suppress_music = getattr(self.config.audio, "suppress_music", True)
                                    strict_music = getattr(self.config.audio, "suppress_music_strict", False)
                                    is_music_active = False
                                    if suppress_music:
                                        is_music_active = self.vad.is_music(pcm_chunk, strict=strict_music)

                                    if suppress_music and strict_music and is_music_active:
                                        if speech_active:
                                            speech_active = False
                                            speech_start_time = 0.0
                                            try:
                                                await ws.send_str(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                                            except Exception:
                                                pass
                                        continue

                                    if interp_q is not None:
                                        try:
                                            if interp_q.full():
                                                try:
                                                    interp_q.get_nowait()
                                                except asyncio.QueueEmpty:
                                                    pass
                                            interp_q.put_nowait(pcm_chunk)
                                        except asyncio.QueueFull:
                                            pass

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
                                    # Track streamed audio duration (16-bit mono PCM).
                                    self._audio_seconds_streamed += len(pcm_chunk) / (sample_rate * 2)

                                    # Track local voice activity for Hybrid VAD pause finalization & session rotation
                                    now = time.time()
                                    mono_now = time.monotonic()
                                    current_pause_threshold = max(
                                        0.35, (getattr(self.config.audio, "sentence_break_ms", 600) or 600) / 1000.0
                                    )
                                    is_voice = self.vad.is_speech(pcm_chunk)
                                    if is_voice:
                                        if not speech_active:
                                            speech_active = True
                                            speech_start_time = now
                                        last_speech_time = now
                                    else:
                                        silence_dur = (now - last_speech_time) if last_speech_time > 0.0 else 0.0
                                        if speech_active and silence_dur >= min(0.35, current_pause_threshold):
                                            speech_active = False
                                        if getattr(self.config.gemini_live, "enable_hybrid_vad", enable_hybrid_vad):
                                            vad_event = self._maybe_finalize_on_vad_pause(
                                                speech_active=speech_active,
                                                silence_duration=silence_dur,
                                                pause_threshold=current_pause_threshold,
                                                mono_now=mono_now,
                                            )
                                            if vad_event is not None:
                                                final_received_event.set()
                                                try:
                                                    res = on_transcript(vad_event)
                                                    if asyncio.iscoroutine(res):
                                                        await res
                                                except Exception as ve:
                                                    logger.debug(f"Hybrid VAD pause finalization error: {ve}")

                                    # Proactive Session Rotation Check (prevent Google 10m ceiling drops)
                                    elapsed_session = time.monotonic() - session_start_time
                                    if session_start_time > 0.0 and (elapsed_session >= SESSION_ROTATION_THRESHOLD_SEC or self._go_away_received):
                                        current_silence = (now - last_speech_time) if last_speech_time > 0.0 else elapsed_session
                                        should_rotate = (
                                            self._go_away_received
                                            or (not speech_active)
                                            or (elapsed_session >= SESSION_HARD_ROTATION_SEC and current_silence >= 0.25)
                                        )
                                        if should_rotate:
                                            logger.info(
                                                f"Gemini Live proactive session rotation triggered at {elapsed_session:.1f}s "
                                                f"(speech_active={speech_active}, silence={current_silence:.2f}s, goAway={self._go_away_received})."
                                            )
                                            rotation_requested = True
                                            self.rotation_count += 1
                                            self.last_rotation_time = time.time()
                                            self._go_away_received = False
                                            try:
                                                await ws.send_str(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                                            except Exception:
                                                pass

                                            try:
                                                await asyncio.wait_for(final_received_event.wait(), timeout=0.50)
                                            except (asyncio.TimeoutError, asyncio.CancelledError):
                                                pass

                                            if not ws.closed:
                                                try:
                                                    await ws.close()
                                                except Exception:
                                                    pass
                                            break

                                # Generator exhausted (or stopped) without the socket failing:
                                # a genuine end of stream, not a connection drop.
                                stream_state["finished"] = (eof or not self.is_running) and not ws.closed

                                # End of stream signal upon loop exit
                                if not ws.closed:
                                    try:
                                        await ws.send_str(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                                    except Exception:
                                        pass

                            # 3. Worker: receive and dispatch interim and finalized transcripts
                            async def receive_transcripts():
                                try:
                                    async for msg in ws:
                                        if not self.is_running:
                                            break

                                        if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                                            raw_text = msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8")
                                            try:
                                                data = json.loads(raw_text)
                                            except Exception:
                                                continue

                                            if "goAway" in data:
                                                go_away = data.get("goAway") or {}
                                                time_left = go_away.get("timeLeft", "unknown")
                                                logger.warning(
                                                    f"Google Gemini Live sent goAway notice (timeLeft={time_left}); rotating preemptively."
                                                )
                                                self._go_away_received = True
                                                final_received_event.set()

                                            if "sessionResumptionUpdate" in data:
                                                update = data.get("sessionResumptionUpdate") or {}
                                                if update.get("resumable") and update.get("newHandle"):
                                                    self._resumption_handle = update["newHandle"]
                                                    logger.debug(
                                                        f"Received Gemini session resumption token: {self._resumption_handle[:16]}..."
                                                    )

                                            events = self.parse_server_message(data)
                                            suppress_music = getattr(self.config.audio, "suppress_music", True)
                                            strict_music = getattr(self.config.audio, "suppress_music_strict", False)
                                            for item in events:
                                                if len(item) >= 3:
                                                    text, is_final, translated_text = item[0], item[1], item[2]
                                                else:
                                                    text, is_final = item[0], item[1]
                                                    translated_text = None

                                                if suppress_music:
                                                    if (strict_music and getattr(self.vad.music_detector, "music_detected", False)) or is_music_text(text, strict=strict_music):
                                                        if is_final:
                                                            self._reset_turn_state()
                                                        logger.debug(f"Gemini Live suppressed singing/music text: {text}")
                                                        continue

                                                segmented_events = self._segment_streaming_event(
                                                    text=text,
                                                    is_final=is_final,
                                                    translated_text=translated_text,
                                                )
                                                for seg_ev in segmented_events:
                                                    if seg_ev.is_final:
                                                        final_received_event.set()
                                                    else:
                                                        final_received_event.clear()

                                                    res = on_transcript(seg_ev)
                                                    if asyncio.iscoroutine(res):
                                                        await res

                                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                            logger.warning("Gemini Live WebSocket closed by remote server.")
                                            break
                                finally:
                                    # In-Flight Interim Preservation on Unexpected Disconnect:
                                    if not rotation_requested:
                                        text_to_finalize = None
                                        if self._latest_interim_text:
                                            words = self._latest_interim_text.strip().split()
                                            if len(words) >= 2:
                                                text_to_finalize = self._latest_interim_text.strip()
                                                self._append_finalized_prefix(text_to_finalize)
                                        if self._finalized_turn_prefix:
                                            self._last_disconnect_finalized_text = self._finalized_turn_prefix
                                            self._last_disconnect_mono = time.monotonic()
                                        if text_to_finalize:
                                            logger.info(
                                                f"Gemini Live finalizing in-flight interim on unexpected disconnect: {text_to_finalize}"
                                            )
                                            self._reset_turn_state()
                                            try:
                                                res = on_transcript(
                                                    TranscriptEvent(
                                                        text=text_to_finalize,
                                                        is_final=True,
                                                        translated_text=None,
                                                    )
                                                )
                                                if asyncio.iscoroutine(res):
                                                    await res
                                            except Exception as ex:
                                                logger.warning(f"Error finalizing in-flight interim: {ex}")
                                    self._reset_turn_state()

                            # 4. Worker: periodic network latency probe
                            async def ping_probe_loop():
                                await asyncio.sleep(0.5)
                                while self.is_running and not ws.closed:
                                    try:
                                        await self.measure_ping()
                                    except Exception as pe:
                                        logger.debug(f"Gemini Live ping probe error: {pe}")
                                    await asyncio.sleep(4.0)

                            send_task = asyncio.create_task(send_audio())
                            recv_task = asyncio.create_task(receive_transcripts())
                            ping_task = asyncio.create_task(ping_probe_loop())
                            self._ping_task = ping_task

                            done, pending = await asyncio.wait(
                                [send_task, recv_task],
                                return_when=asyncio.FIRST_COMPLETED,
                            )

                            if not ping_task.done():
                                ping_task.cancel()
                            self._ping_task = None

                            if send_task in done and stream_state["finished"]:
                                # Audio capture stream completed (genuine end of stream)
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
                                # Remote connection closed, errored, or planned session rotation
                                if send_task in done and not send_task.cancelled():
                                    send_exc = send_task.exception()
                                    if send_exc is not None:
                                        logger.warning(f"Gemini Live audio sender failed ({send_exc}); reconnecting...")
                                for t in pending:
                                    if not t.done():
                                        t.cancel()
                                if pending:
                                    await asyncio.gather(*pending, return_exceptions=True)
                                if not self.is_running:
                                    break

                                close_code = getattr(ws, "close_code", None)
                                is_clean = rotation_requested or (close_code in (1000, 1001, aiohttp.WSCloseCode.OK, aiohttp.WSCloseCode.GOING_AWAY))
                                was_healthy_session = (
                                    session_start_time > 0.0
                                    and (time.monotonic() - session_start_time) >= HEALTHY_SESSION_MIN_UPTIME_SEC
                                )
                                if is_clean:
                                    delay = 0.0
                                    reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY
                                    logger.info(
                                        f"Gemini Live session finished cleanly (rotation={rotation_requested}, code={close_code}); reconnecting immediately (0s delay)..."
                                    )
                                elif was_healthy_session:
                                    delay = FAST_RECONNECT_DELAY
                                    reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY
                                    logger.warning(
                                        f"Gemini Live healthy session dropped (code={close_code}); fast-reconnecting in {delay:.2f}s..."
                                    )
                                else:
                                    delay = _next_backoff()
                                    logger.warning(f"Gemini Live connection dropped (code={close_code}); reconnecting in {delay:.0f}s...")

                                if delay > 0:
                                    await asyncio.sleep(delay)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if self.is_running:
                        if self._is_invalid_key_failure(str(e)):
                            logger.error("Invalid Gemini API key. Please check your key at aistudio.google.com.")
                            return
                        was_healthy_session = (
                            session_start_time > 0.0
                            and (time.monotonic() - session_start_time) >= HEALTHY_SESSION_MIN_UPTIME_SEC
                        )
                        if was_healthy_session:
                            delay = FAST_RECONNECT_DELAY
                            reconnect_delay = STREAM_RECONNECT_INITIAL_DELAY
                        else:
                            delay = _next_backoff()
                        logger.error(f"Gemini Live error: {e}. Reconnecting in {delay:.2f}s...")
                        await asyncio.sleep(delay)
        finally:
            if interp_task is not None and not interp_task.done():
                interp_task.cancel()
                try:
                    await interp_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def stop(self) -> None:
        """Stop Gemini Live streaming session cleanly."""
        self.is_running = False
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        self._ping_task = None
        self.current_ping_ms = None
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
