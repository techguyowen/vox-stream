"""End-to-end and edge-case stress tests for Gemini Live incremental sentence finalization and fast reconnection."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import aiohttp
from aiohttp import web
import pytest

from obs_captioner.config import AppConfig
from obs_captioner.engines.base import TranscriptEvent
from obs_captioner.engines.gemini_live import (
    FAST_RECONNECT_DELAY,
    STREAM_RECONNECT_INITIAL_DELAY,
    GeminiLiveEngine,
    _norm_word,
    _split_completed_sentences,
    _strip_finalized_prefix,
)
from obs_captioner.history import TranscriptHistory
from obs_captioner.obs.caption_sink import CaptionSink
from obs_captioner.web.server import WebOverlayServer


class _MockDisplayWebSocket:
    """In-memory mock of an aiohttp WebSocketResponse connected to /ws (display client)."""

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.messages.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True


def _build_full_pipeline(max_sentence_words: int = 24):
    """Build a connected GeminiLiveEngine -> CaptionSink -> WebOverlayServer -> /display client pipeline."""
    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    cfg.gemini_live.enable_hybrid_vad = True
    cfg.audio.max_sentence_words = max_sentence_words
    cfg.audio.sentence_break_ms = 600
    cfg.audio.suppress_music = False
    cfg.overlay.auto_hide_seconds = 0
    cfg.general.church_mode = False  # Test raw fidelity without liturgical word substitutions altering token counts
    if hasattr(cfg, "bible") and cfg.bible:
        cfg.bible.enabled = False

    history = TranscriptHistory(max_entries=500)
    server = WebOverlayServer(cfg, history=history)
    sink = CaptionSink(cfg, web_server=server, history=history)
    engine = GeminiLiveEngine(cfg)

    display_ws = _MockDisplayWebSocket()
    server.caption_sockets[display_ws] = "en"
    server.socket_roles[display_ws] = "display"

    return cfg, engine, sink, server, display_ws


@pytest.mark.asyncio
async def test_real_sermon_paragraphs_word_by_word_streaming():
    """Replay actual 70-95 word sermon paragraphs 2-3 words at a time through the full pipeline."""
    paragraphs = [
        (
            "For our global partnerships. And so, as I like to do, we're going to make this a little bit interactive. "
            "And so, we've got many global partnerships. We've got nine people that are all over the world, "
            "but I'm going to put it in four categories, so we're going to pray four times. "
            "And so, I'm going to say what I want you to pray for, and then you're going to pray for 20 seconds "
            "or so to yourself or with a partner, and then I'm going to say, \"These are the prayers of your people,\" "
            "and you're going to say, \"Lord, hear our prayer.\" So, let's practice that. "
            "These are the prayers of your people."
        ),
        (
            "Suppose one of you has a hundred sheep and loses one of them. "
            "Doesn't he leave the ninety-nine in the open country and go after the lost sheep until he finds it? "
            "And when he finds it, he joyfully puts it on his shoulders and goes home. "
            "Then he calls his friends and neighbors together and says, "
            "\"Rejoice with me; I have found my lost sheep.\""
        ),
    ]

    max_sentence_words = 24
    cfg, engine, sink, server, display_ws = _build_full_pipeline(max_sentence_words=max_sentence_words)

    try:
        for p_idx, paragraph in enumerate(paragraphs):
            words = paragraph.split()
            display_ws.messages.clear()
            sink.history.entries.clear()
            sink._last_final_text = None
            sink._last_final_time = 0.0

            emitted_finals_during_interim: List[str] = []
            emitted_finals_on_turn_end: List[str] = []

            # Stream interimInputTranscription 2-3 words at a time
            idx = 0
            step = 0
            while idx < len(words):
                step_size = 2 if (step % 2 == 0) else 3
                idx = min(len(words), idx + step_size)
                step += 1
                cumulative_interim = " ".join(words[:idx])

                server_msg = {
                    "serverContent": {
                        "interimInputTranscription": {"text": cumulative_interim}
                    }
                }
                parsed = engine.parse_server_message(server_msg)
                for text, is_final, trans in parsed:
                    seg_events = engine._segment_streaming_event(
                        text=text,
                        is_final=is_final,
                        translated_text=trans,
                        mono_now=1000.0 + p_idx * 100.0 + step * 0.2,
                    )
                    for ev in seg_events:
                        if ev.is_final:
                            emitted_finals_during_interim.append(ev.text)
                        await sink.handle_transcript(ev)

            # Assert sentences were finalized incrementally BEFORE inputTranscription arrived
            assert len(emitted_finals_during_interim) >= 3, (
                f"Paragraph {p_idx + 1} should finalize at least 3 sentences incrementally during interim streaming, "
                f"got {len(emitted_finals_during_interim)}: {emitted_finals_during_interim}"
            )

            # Now send the authoritative inputTranscription for the entire paragraph
            final_msg = {
                "serverContent": {
                    "inputTranscription": {"text": paragraph}
                }
            }
            for text, is_final, trans in engine.parse_server_message(final_msg):
                seg_events = engine._segment_streaming_event(
                    text=text,
                    is_final=is_final,
                    translated_text=trans,
                    mono_now=1000.0 + p_idx * 100.0 + 50.0,
                )
                for ev in seg_events:
                    if ev.is_final:
                        emitted_finals_on_turn_end.append(ev.text)
                    await sink.handle_transcript(ev)

            all_engine_finals = emitted_finals_during_interim + emitted_finals_on_turn_end

            # 1. Verify no finalized sentence exceeds max_sentence_words + 5
            for sent in all_engine_finals:
                word_cnt = len(sent.split())
                assert word_cnt <= max_sentence_words + 5, (
                    f"Finalized sentence exceeded {max_sentence_words + 5} words ({word_cnt}): {sent}"
                )

            # 2. Verify zero duplicate sentences emitted when inputTranscription arrives
            norm_finals = [" ".join(s.split()).casefold() for s in all_engine_finals]
            for i in range(1, len(norm_finals)):
                assert norm_finals[i] != norm_finals[i - 1], (
                    f"Adjacent duplicate sentence emitted: {all_engine_finals[i]}"
                )
            # Also verify no sentence from interim was re-emitted on turn end
            for end_sent in emitted_finals_on_turn_end:
                norm_end = " ".join(end_sent.split()).casefold()
                # Note: in paragraph 1, "These are the prayers of your people." legitimately appears twice in the sermon!
                # Check total count matches the source paragraph.
                assert norm_finals.count(norm_end) <= paragraph.casefold().count(norm_end.rstrip(".?!")), (
                    f"Duplicate sentence emitted on turn end: {end_sent}"
                )

            # 3. Verify rejoining all finalized sentences reconstructs 100% of spoken words in order
            rejoined_engine = " ".join(all_engine_finals)
            expected_tokens = [_norm_word(w) for w in paragraph.split() if _norm_word(w)]
            actual_tokens = [_norm_word(w) for w in rejoined_engine.split() if _norm_word(w)]
            assert actual_tokens == expected_tokens, (
                f"Paragraph {p_idx + 1} word mismatch!\nExpected ({len(expected_tokens)}): {expected_tokens}\n"
                f"Actual   ({len(actual_tokens)}): {actual_tokens}"
            )

            # 4. Verify WebOverlayServer and connected /display WebSocket client received every final
            await asyncio.sleep(0.05)
            ws_final_msgs = [m["text"] for m in display_ws.messages if m.get("is_final") and m.get("text")]
            assert len(ws_final_msgs) == len(all_engine_finals), (
                f"Display WebSocket final count ({len(ws_final_msgs)}) != engine finals ({len(all_engine_finals)})"
            )
            assert len(sink.history.entries) == len(all_engine_finals)
    finally:
        await sink.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_asr_mid_turn_word_revisions_no_duplicates_or_drops():
    """Verify _strip_finalized_prefix handles mid-turn ASR word revisions after Sentence 1 was finalized."""
    cfg, engine, sink, server, display_ws = _build_full_pipeline(max_sentence_words=24)

    try:
        # Case A: "90:09" revised to "ninety-nine" after Sentence 1 was finalized
        s1_initial = "Doesn't he leave the 90:09 in the open country and go after the lost sheep?"
        s2_cont = "And when he finds it, he joyfully puts it on his shoulders."

        # Emit interim with Sentence 1 + 4 words of Sentence 2 -> finalizes Sentence 1
        evs_a1 = engine._segment_streaming_event(
            f"{s1_initial} And when he finds", is_final=False, mono_now=10.0
        )
        finals_a1 = [e.text for e in evs_a1 if e.is_final]
        assert finals_a1 == [s1_initial]

        # Now Google revises "90:09" -> "ninety-nine" in the cumulative transcript
        s1_revised = "Doesn't he leave the ninety-nine in the open country and go after the lost sheep?"
        evs_a2 = engine._segment_streaming_event(
            f"{s1_revised} {s2_cont}", is_final=True, mono_now=12.0
        )
        finals_a2 = [e.text for e in evs_a2 if e.is_final]
        assert finals_a2 == [s2_cont], f"Expected only Sentence 2, got {finals_a2}"

        # Case B: "allowchance" revised to "allow a chance" (token insertion) and "1st" revised to "first"
        s1_b_initial = "This is our 1st gathering to allowchance for everyone to pray together."
        s2_b_cont = "Let us open our hearts to the word today."

        evs_b1 = engine._segment_streaming_event(
            f"{s1_b_initial} Let us open our", is_final=False, mono_now=20.0
        )
        assert [e.text for e in evs_b1 if e.is_final] == [s1_b_initial]

        s1_b_revised = "This is our first gathering to allow a chance for everyone to pray together."
        evs_b2 = engine._segment_streaming_event(
            f"{s1_b_revised} {s2_b_cont}", is_final=True, mono_now=22.0
        )
        finals_b2 = [e.text for e in evs_b2 if e.is_final]
        assert finals_b2 == [s2_b_cont], f"Expected clean Sentence 2 after multi-token revision, got {finals_b2}"
    finally:
        await sink.stop()
        await server.stop()


def test_abbreviations_bible_verses_times_not_split_prematurely():
    """Verify Biblical citations, abbreviations, initials, and times do not trigger premature splits."""
    sample = (
        "Let's open our Bibles to Luke 15:1-3 and John 3:16 at 4:00 p.m. "
        "with Dr. R. C. Sproul and Rev. Tim Keller in the U.S.A. "
        "Today we will study grace together in our sanctuary."
    )
    # Increase max_words so only punctuation rule (Rule A) is tested here
    completed, remaining = _split_completed_sentences(sample, max_words=40, require_trailing_words=3)

    # Notice: "U.S.A." is in _ABBREV_WORDS so "in the U.S.A." does not split prematurely if treated as abbreviation,
    # OR if we inspect what was split: it must NEVER split after 15:1-3, 3:16, 4:00, p.m., Dr., R., C., or Rev.!
    for forbidden_end in ("15:1-3", "3:16", "4:00", "p.m.", "Dr.", "R.", "C.", "Rev."):
        for sent in completed:
            assert not sent.endswith(forbidden_end), f"Prematurely split at '{forbidden_end}': {sent}"

    # Now test when the first sentence ends with a regular word after those abbreviations:
    sample_with_normal_end = (
        "Let's open our Bibles to Luke 15:1-3 and John 3:16 at 4:00 p.m. "
        "with Dr. R. C. Sproul and Rev. Tim Keller from the U.S.A. right now. "
        "Today we will study grace together."
    )
    comp2, rem2 = _split_completed_sentences(sample_with_normal_end, max_words=40, require_trailing_words=3)
    assert len(comp2) == 1
    assert comp2[0] == (
        "Let's open our Bibles to Luke 15:1-3 and John 3:16 at 4:00 p.m. "
        "with Dr. R. C. Sproul and Rev. Tim Keller from the U.S.A. right now."
    )
    assert rem2 == "Today we will study grace together."


@pytest.mark.asyncio
async def test_unpunctuated_80_word_run_on_speech():
    """Stream an 80-word unpunctuated run-on monologue 3 words at a time and verify clause-boundary splitting."""
    run_on_words = (
        "when we gather together in this place we remember all the blessings that have been poured out "
        "upon our community and we know that even when the week has been heavy and full of unexpected "
        "challenges we can still find rest and encouragement because we are not walking through this "
        "season alone but we are standing shoulder to shoulder with neighbors who care deeply and who "
        "will continue lifting one another up in prayer and kindness every single day of the year"
    ).split()
    assert len(run_on_words) >= 80
    full_run_on = " ".join(run_on_words)

    max_sentence_words = 24
    cfg, engine, sink, server, display_ws = _build_full_pipeline(max_sentence_words=max_sentence_words)

    try:
        emitted_finals: List[str] = []
        idx = 0
        step = 0
        while idx < len(run_on_words):
            idx = min(len(run_on_words), idx + 3)
            step += 1
            partial = " ".join(run_on_words[:idx])
            evs = engine._segment_streaming_event(partial, is_final=False, mono_now=500.0 + step * 0.2)
            for ev in evs:
                if ev.is_final:
                    emitted_finals.append(ev.text)
                await sink.handle_transcript(ev)

        # Must have already split multiple chunks before inputTranscription arrives
        assert len(emitted_finals) >= 3, f"Expected >= 3 incremental chunks during run-on, got {len(emitted_finals)}"

        # Now send final inputTranscription of the entire 80+ word run-on
        final_evs = engine._segment_streaming_event(full_run_on, is_final=True, mono_now=600.0)
        for ev in final_evs:
            if ev.is_final:
                emitted_finals.append(ev.text)
            await sink.handle_transcript(ev)

        # Verify every chunk respects max_sentence_words
        for chunk in emitted_finals:
            w_cnt = len(chunk.split())
            assert w_cnt <= max_sentence_words, f"Chunk exceeded {max_sentence_words} words ({w_cnt}): {chunk}"

        # Verify 100% of words were emitted in exact order with zero drops or duplicates
        reconstructed = " ".join(emitted_finals).split()
        assert reconstructed == run_on_words, (
            f"Run-on words mismatch!\nExpected ({len(run_on_words)}): {run_on_words}\n"
            f"Actual   ({len(reconstructed)}): {reconstructed}"
        )
    finally:
        await sink.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_mid_utterance_ws_drop_fast_reconnect_and_dedup():
    """Verify fast reconnect (<0.4s) after healthy session drop, backoff on immediate 2nd drop, and replay dedup."""
    cfg, engine, sink, server, display_ws = _build_full_pipeline(max_sentence_words=24)

    sentence_1 = "Welcome to our Sunday morning service everyone."
    sentence_2_interim = "We are going to open our Bibles to Luke chapter 15"
    sentence_2_replayed_with_s3 = (
        f"{sentence_1} {sentence_2_interim} and read the parable of the lost sheep together."
    )

    sleep_delays: List[float] = []
    connect_SharedState = {"attempt": 0}

    class _FakeWSMessage:
        def __init__(self, msg_type, data: str = ""):
            self.type = msg_type
            self.data = data
            self.extra = ""

    class _FakeWS:
        def __init__(self, attempt: int, engine_ref: GeminiLiveEngine):
            self.attempt = attempt
            self.engine_ref = engine_ref
            self.closed = False
            self.close_code = 1006
            self.sent_Payloads: List[Dict[str, Any]] = []
            self._iter_step = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.closed = True

        async def send_str(self, data: str):
            self.sent_Payloads.append(json.loads(data))

        async def close(self):
            self.closed = True

        async def receive(self, timeout=None):
            if self.attempt in (1, 3):
                return _FakeWSMessage(aiohttp.WSMsgType.TEXT, json.dumps({"setupComplete": {}}))
            else:
                # Attempt 2 fails immediately during setupComplete (< 3.0s uptime)
                self.closed = True
                return _FakeWSMessage(aiohttp.WSMsgType.CLOSED, "1011")

        def __aiter__(self):
            return self

        async def __anext__(self):
            self._iter_step += 1
            if self.attempt == 1:
                if self._iter_step == 1:
                    # Simulate that this session has been healthy for > 5 seconds
                    # by backdating session_start_time via monotonic clock offset or waiting
                    return _FakeWSMessage(
                        aiohttp.WSMsgType.TEXT,
                        json.dumps({
                            "sessionResumptionUpdate": {"resumable": True, "newHandle": "resume-token-xyz"},
                            "serverContent": {
                                "interimInputTranscription": {
                                    "text": f"{sentence_1} {sentence_2_interim}"
                                }
                            },
                        }),
                    )
                elif self._iter_step == 2:
                    # Unexpected WebSocket drop mid-utterance!
                    self.closed = True
                    self.close_code = 1006
                    return _FakeWSMessage(aiohttp.WSMsgType.ERROR, "Connection reset by peer")
                raise StopAsyncIteration

            elif self.attempt == 3:
                if self._iter_step == 1:
                    # Reconnected session replays Sentence 1 + Sentence 2 + continues into Sentence 3!
                    return _FakeWSMessage(
                        aiohttp.WSMsgType.TEXT,
                        json.dumps({
                            "serverContent": {
                                "inputTranscription": {
                                    "text": sentence_2_replayed_with_s3
                                }
                            }
                        }),
                    )
                elif self._iter_step == 2:
                    self.engine_ref.is_running = False
                    raise StopAsyncIteration
            raise StopAsyncIteration

    class _FakeClientSession:
        def __init__(self, engine_ref: GeminiLiveEngine):
            self.engine_ref = engine_ref
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.closed = True

        def ws_connect(self, url, heartbeat=20.0, ssl=None):
            connect_SharedState["attempt"] += 1
            return _FakeWS(connect_SharedState["attempt"], self.engine_ref)

    real_monotonic = time.monotonic
    mono_offset = {"val": 0.0}

    def fake_monotonic():
        return real_monotonic() + mono_offset["val"]

    orig_sleep = asyncio.sleep

    async def fake_sleep(delay: float):
        # Record non-probe reconnect sleeps (ignore 0.5/4.0 ping_probe_loop sleeps)
        if delay in (FAST_RECONNECT_DELAY, STREAM_RECONNECT_INITIAL_DELAY) or (0.05 <= delay <= 3.0 and delay != 0.5):
            sleep_delays.append(delay)
        await orig_sleep(0.01)

    try:
        audio_q: asyncio.Queue = asyncio.Queue()
        # Feed a few silent PCM chunks so send_audio stays alive while receive_transcripts runs
        for _ in range(5):
            audio_q.put_nowait(b"\x00\x00" * 320)

        # Advance fake monotonic clock by 5.0s right after attempt 1's setupComplete
        orig_parse = engine.parse_server_message

        def wrapped_parse(data):
            if connect_SharedState["attempt"] == 1:
                mono_offset["val"] += 5.0  # Session 1 has been up for 5.0s (>= 3.0s healthy threshold)
            return orig_parse(data)

        with patch.object(engine, "parse_server_message", side_effect=wrapped_parse), \
             patch("obs_captioner.engines.gemini_live.time.monotonic", side_effect=fake_monotonic), \
             patch("obs_captioner.engines.gemini_live.asyncio.sleep", side_effect=fake_sleep), \
             patch("obs_captioner.engines.gemini_live.aiohttp.ClientSession", side_effect=lambda **kw: _FakeClientSession(engine)):
            await engine._stream_from_queue(audio_q, sink.handle_transcript)

        # 1. Verify Fast Reconnect engaged after Attempt 1 (healthy session > 3s uptime)
        assert len(sleep_delays) >= 2, f"Expected at least 2 reconnect sleep calls, got {sleep_delays}"
        assert sleep_delays[0] == pytest.approx(FAST_RECONNECT_DELAY, abs=0.01), (
            f"First drop after healthy session must use FAST_RECONNECT_DELAY ({FAST_RECONNECT_DELAY}s), got {sleep_delays[0]}s"
        )

        # 2. Verify Attempt 2 (which failed immediately during setup, < 3s uptime) used exponential backoff (2.0s)
        assert sleep_delays[1] == pytest.approx(STREAM_RECONNECT_INITIAL_DELAY, abs=0.01), (
            f"Second consecutive drop (<3s uptime) must use STREAM_RECONNECT_INITIAL_DELAY ({STREAM_RECONNECT_INITIAL_DELAY}s), got {sleep_delays[1]}s"
        )

        # 3. Verify all 3 segments were finalized cleanly without duplicating Sentence 1 or Sentence 2!
        history_texts = [e.text for e in sink.history.entries]
        assert len(history_texts) == 3, f"Expected 3 finalized history entries, got {len(history_texts)}: {history_texts}"

        rejoined_history = " ".join(history_texts)
        expected_words = [_norm_word(w) for w in sentence_2_replayed_with_s3.split() if _norm_word(w)]
        actual_words = [_norm_word(w) for w in rejoined_history.split() if _norm_word(w)]
        assert actual_words == expected_words, (
            f"Words after mid-utterance reconnect + replay did not match!\n"
            f"Expected: {expected_words}\nActual:   {actual_words}\nHistory entries: {history_texts}"
        )
    finally:
        await sink.stop()
        await server.stop()


def test_mid_sentence_breath_pause_vs_terminal_punct_vs_extended_silence():
    """Verify 600ms breath mid-sentence does not finalize, punctuated pause does, and 2.5s silence does."""
    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    engine = GeminiLiveEngine(cfg)
    pause_threshold = 0.60  # 600ms

    # 1. 600ms breath mid-sentence without terminal punctuation -> must NOT finalize!
    engine._latest_interim_text = "Because God so loved the world"
    engine._last_interim_change_mono = 100.0
    breath_res = engine._maybe_finalize_on_vad_pause(
        speech_active=False,
        silence_duration=0.65,
        pause_threshold=pause_threshold,
        mono_now=100.65,
    )
    assert breath_res is None, "Mid-sentence 600ms breath without punctuation must NOT finalize!"
    assert engine._latest_interim_text == "Because God so loved the world"

    # 2. 600ms pause with terminal punctuation -> MUST finalize!
    engine._latest_interim_text = "Because God so loved the world."
    engine._last_interim_change_mono = 100.0
    punct_res = engine._maybe_finalize_on_vad_pause(
        speech_active=False,
        silence_duration=0.65,
        pause_threshold=pause_threshold,
        mono_now=100.65,
    )
    assert punct_res is not None, "600ms pause with terminal punctuation MUST finalize!"
    assert punct_res.is_final is True
    assert punct_res.text == "Because God so loved the world."
    assert engine._latest_interim_text is None

    # 3. 2.5s extended dead silence without punctuation -> MUST finalize!
    engine._latest_interim_text = "Because God so loved the world"
    engine._last_interim_change_mono = 100.0
    extended_res = engine._maybe_finalize_on_vad_pause(
        speech_active=False,
        silence_duration=2.55,
        pause_threshold=pause_threshold,
        mono_now=102.55,
    )
    assert extended_res is not None, "2.5s extended dead silence without punctuation MUST finalize!"
    assert extended_res.is_final is True
    assert extended_res.text == "Because God so loved the world"
    assert engine._latest_interim_text is None

    # 4. Interim ending in abbreviation or digit+period does NOT finalize on 600ms pause
    for text in (
        "Let us open our Bibles to Luke ch.",
        "We are reading from Matt. chap.",
        "Please turn in your copies of Scripture to verse 15.",
    ):
        engine._latest_interim_text = text
        engine._last_interim_change_mono = 100.0
        res = engine._maybe_finalize_on_vad_pause(
            speech_active=False,
            silence_duration=0.65,
            pause_threshold=pause_threshold,
            mono_now=100.65,
        )
        assert res is None, f"Interim ending in '{text}' should not finalize on 600ms pause!"


def test_biblical_abbreviations_and_lowercase_continuations_no_premature_split():
    """Verify biblical abbreviations, numbers with periods, and lowercase continuations do not split."""
    # 1. Biblical abbreviations followed by words
    sample_abbr = "We are reading from Gen. ch. 12 and Matt. chap. 5 and Rom. v. 8 with Dr. Sproul today."
    comp, rem = _split_completed_sentences(sample_abbr, max_words=24, require_trailing_words=3)
    assert comp == [], f"Should not split inside abbreviations: comp={comp}, rem={rem}"

    # 2. Lowercase continuation after a period (e.g. Luke 15. we read ...)
    sample_lower = "Let's turn to Luke 15. we read about the parable of the lost sheep."
    comp_lower, rem_lower = _split_completed_sentences(sample_lower, max_words=24, require_trailing_words=3)
    assert comp_lower == [], f"Sentence followed by lowercase continuation must not split: comp={comp_lower}"

    # 3. Number/decimal followed by continuation
    sample_decimal = "We had an increase of 3. 5 percent in our attendance this morning."
    comp_dec, rem_dec = _split_completed_sentences(sample_decimal, max_words=24, require_trailing_words=3)
    assert comp_dec == [], f"Decimal number must not split: comp={comp_dec}"

    # 4. Real sentence with legitimate terminal punctuation followed by capital letter
    sample_real = (
        "Let's turn to Luke chapter 15 right now. "
        "We read about the parable of the lost sheep together."
    )
    comp_real, rem_real = _split_completed_sentences(sample_real, max_words=24, require_trailing_words=3)
    assert comp_real == ["Let's turn to Luke chapter 15 right now."]
    assert rem_real == "We read about the parable of the lost sheep together."


def test_subordinate_clauses_not_chopped_mid_sentence():
    """Verify 25-35 word sentences with subordinate clauses ('who', 'that', 'which') are not chopped."""
    # 27 words with subordinate clauses "that", "whoever", "for"
    sent_1 = (
        "Because God so loved the world that He gave His only begotten Son "
        "that whoever believes in Him should not perish but have everlasting life for all generations."
    )
    comp1, rem1 = _split_completed_sentences(sent_1, max_words=24, require_trailing_words=3)
    assert comp1 == [], f"Sentence 1 should remain intact without premature chop: comp={comp1}"
    assert rem1 == sent_1

    # 27 words with subordinate clauses "that", "who"
    sent_2 = (
        "And we remember all the blessings that have been poured out upon our families "
        "who love Him and who walk according to His wonderful purpose in our community."
    )
    comp2, rem2 = _split_completed_sentences(sent_2, max_words=24, require_trailing_words=3)
    assert comp2 == [], f"Sentence 2 should remain intact without premature chop: comp={comp2}"
    assert rem2 == sent_2

    # 29 words with "which", "where", "because"
    sent_3 = (
        "This is the sacred covenant which was established in ancient times "
        "where faithful people gathered because they knew that the grace of the Lord would sustain them."
    )
    comp3, rem3 = _split_completed_sentences(sent_3, max_words=24, require_trailing_words=3)
    assert comp3 == [], f"Sentence 3 should remain intact without premature chop: comp={comp3}"
    assert rem3 == sent_3


def test_context_window_compression_in_setup_payload():
    """Verify contextWindowCompression with slidingWindow is present in setup payload for both modes."""
    # 1. Standard transcribe mode
    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    engine = GeminiLiveEngine(cfg)
    payload = engine.build_setup_payload()
    assert "setup" in payload
    assert "contextWindowCompression" in payload["setup"]
    assert payload["setup"]["contextWindowCompression"] == {"slidingWindow": {}}

    # 2. Live translate / interpreter mode
    cfg_interp = AppConfig()
    cfg_interp.gemini_live.api_key = "test-live-key"
    cfg_interp.translation.interpreter_mode = True
    cfg_interp.translation.target_language = "es"
    engine_interp = GeminiLiveEngine(cfg_interp)
    payload_interp = engine_interp.build_setup_payload()
    assert "setup" in payload_interp
    assert "contextWindowCompression" in payload_interp["setup"]
    assert payload_interp["setup"]["contextWindowCompression"] == {"slidingWindow": {}}


@pytest.mark.asyncio
async def test_seamless_hot_handoff_rotation_zero_downtime():
    """Verify seamless hot-handoff rotation: Socket 2 pre-warms, audio switches with 0ms gap, and Socket 1 drains trailing transcript."""
    from obs_captioner.engines import gemini_live as gl

    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    cfg.gemini_live.enable_hybrid_vad = True
    cfg.audio.suppress_music = False
    engine = gl.GeminiLiveEngine(cfg)
    engine.vad.is_music = lambda *a, **k: False
    engine.vad.is_speech = lambda *a, **k: False

    connections: List[Any] = []
    received_transcripts: List[TranscriptEvent] = []

    class _MockLiveMsg:
        def __init__(self, msg_type, data=None):
            self.type = msg_type
            self.data = data
            self.extra = ""

    class _MockLiveWs:
        def __init__(self, idx: int):
            self.idx = idx
            self.closed = False
            self.close_code = None
            self.sent_payloads: List[Dict[str, Any]] = []
            self._incoming: asyncio.Queue = asyncio.Queue()

        async def send_str(self, s: str):
            if self.closed:
                raise ConnectionResetError("Socket closed")
            payload = json.loads(s)
            self.sent_payloads.append(payload)

        async def receive(self, timeout=None):
            if not self._incoming.empty():
                return self._incoming.get_nowait()
            if self.closed:
                return _MockLiveMsg(aiohttp.WSMsgType.CLOSED)
            try:
                if timeout:
                    return await asyncio.wait_for(self._incoming.get(), timeout=timeout)
                return await self._incoming.get()
            except (asyncio.TimeoutError, asyncio.CancelledError):
                raise

        def __aiter__(self):
            return self

        async def __anext__(self):
            while not self.closed:
                try:
                    return await asyncio.wait_for(self._incoming.get(), timeout=0.05)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    if self.closed:
                        raise StopAsyncIteration
            raise StopAsyncIteration

        async def close(self, code=1000):
            self.closed = True
            self.close_code = code

    class _MockSession:
        def __init__(self, *a, **k):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            self.closed = True
            return False

        async def close(self):
            self.closed = True

        def ws_connect(self, *a, **k):
            ws = _MockLiveWs(len(connections))
            ws._incoming.put_nowait(
                _MockLiveMsg(aiohttp.WSMsgType.TEXT, json.dumps({"setupComplete": {}}))
            )
            connections.append(ws)
            return ws

    audio_q: asyncio.Queue = asyncio.Queue()
    audio_q.put_nowait(b"\x01\x00" * 160)
    audio_q.put_nowait(b"\x02\x00" * 160)

    def on_transcript(event: TranscriptEvent):
        received_transcripts.append(event)

    with patch.object(gl.aiohttp, "ClientSession", _MockSession), \
         patch.object(gl, "SESSION_PREWARM_THRESHOLD_SEC", 0.04), \
         patch.object(gl, "SESSION_ROTATION_THRESHOLD_SEC", 0.08), \
         patch.object(gl, "SESSION_HARD_ROTATION_SEC", 0.25):

        stream_task = asyncio.create_task(engine._stream_from_queue(audio_q, on_transcript))

        # Wait for Chunk 1 and Chunk 2 to be processed on Socket 1 and rotation threshold reached
        await asyncio.sleep(0.12)

        # Verify Socket 2 connected and completed setupComplete
        assert len(connections) >= 2, "Socket 2 must prewarm and connect"
        sock1 = connections[0]
        sock2 = connections[1]

        # Queue a trailing final transcript on Socket 1 before/during handover
        sock1._incoming.put_nowait(
            _MockLiveMsg(
                aiohttp.WSMsgType.TEXT,
                json.dumps({
                    "serverContent": {
                        "inputTranscription": {"text": "Trailing final on socket 1."}
                    }
                })
            )
        )

        # Feed Chunk 3 to audio_q - this should flow to Socket 2 with 0ms downtime!
        audio_q.put_nowait(b"\x03\x00" * 160)
        await asyncio.sleep(0.08)

        # End of stream sentinel
        audio_q.put_nowait(None)
        await asyncio.wait_for(stream_task, timeout=2.0)

    # 1. Verify Socket 2 connected and received setupComplete
    assert len(connections) >= 2
    assert any("setup" in p for p in sock2.sent_payloads)

    # 2. Verify Socket 1 received audioStreamEnd
    assert any(p.get("realtimeInput", {}).get("audioStreamEnd") for p in sock1.sent_payloads)

    # 3. Verify audio routing: Chunks 1 & 2 on Socket 1, Chunk 3 on Socket 2
    sock1_audio = [p for p in sock1.sent_payloads if "audio" in p.get("realtimeInput", {})]
    sock2_audio = [p for p in sock2.sent_payloads if "audio" in p.get("realtimeInput", {})]
    assert len(sock1_audio) == 2, f"Expected 2 audio chunks on Socket 1, got {len(sock1_audio)}"
    assert len(sock2_audio) == 1, f"Expected 1 audio chunk on Socket 2, got {len(sock2_audio)}"

    # 4. Verify trailing final transcript on Socket 1 was received by the drain task
    trailing_events = [ev for ev in received_transcripts if "Trailing final on socket 1." in ev.text]
    assert len(trailing_events) >= 1, f"Drain task failed to deliver trailing transcript: {received_transcripts}"
    assert trailing_events[0].is_final is True

    # 5. Verify Socket 1 is closed cleanly by drain task
    assert sock1.closed is True
    assert engine.rotation_count >= 1


@pytest.mark.asyncio
async def test_goaway_triggers_graceful_prewarm_handover():
    """Verify that receiving a goAway message triggers pre-warming and completes seamless handover."""
    from obs_captioner.engines import gemini_live as gl

    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    cfg.gemini_live.enable_hybrid_vad = True
    cfg.audio.suppress_music = False
    engine = gl.GeminiLiveEngine(cfg)
    engine.vad.is_music = lambda *a, **k: False
    engine.vad.is_speech = lambda *a, **k: False

    connections: List[Any] = []
    received_transcripts: List[TranscriptEvent] = []

    class _MockLiveMsg:
        def __init__(self, msg_type, data=None):
            self.type = msg_type
            self.data = data
            self.extra = ""

    class _MockLiveWs:
        def __init__(self, idx: int):
            self.idx = idx
            self.closed = False
            self.close_code = None
            self.sent_payloads: List[Dict[str, Any]] = []
            self._incoming: asyncio.Queue = asyncio.Queue()

        async def send_str(self, s: str):
            if self.closed:
                raise ConnectionResetError("Socket closed")
            payload = json.loads(s)
            self.sent_payloads.append(payload)

        async def receive(self, timeout=None):
            if not self._incoming.empty():
                return self._incoming.get_nowait()
            if self.closed:
                return _MockLiveMsg(aiohttp.WSMsgType.CLOSED)
            try:
                if timeout:
                    return await asyncio.wait_for(self._incoming.get(), timeout=timeout)
                return await self._incoming.get()
            except (asyncio.TimeoutError, asyncio.CancelledError):
                raise

        def __aiter__(self):
            return self

        async def __anext__(self):
            while not self.closed:
                try:
                    return await asyncio.wait_for(self._incoming.get(), timeout=0.05)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    if self.closed:
                        raise StopAsyncIteration
            raise StopAsyncIteration

        async def close(self, code=1000):
            self.closed = True
            self.close_code = code

    class _MockSession:
        def __init__(self, *a, **k):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            self.closed = True
            return False

        async def close(self):
            self.closed = True

        def ws_connect(self, *a, **k):
            ws = _MockLiveWs(len(connections))
            ws._incoming.put_nowait(
                _MockLiveMsg(aiohttp.WSMsgType.TEXT, json.dumps({"setupComplete": {}}))
            )
            connections.append(ws)
            return ws

    audio_q: asyncio.Queue = asyncio.Queue()
    audio_q.put_nowait(b"\x01\x00" * 160)

    # Standard rotation thresholds remain high (not hit by session age)
    with patch.object(gl.aiohttp, "ClientSession", _MockSession), \
         patch.object(gl, "SESSION_PREWARM_THRESHOLD_SEC", 300.0), \
         patch.object(gl, "SESSION_ROTATION_THRESHOLD_SEC", 400.0), \
         patch.object(gl, "SESSION_HARD_ROTATION_SEC", 500.0):

        stream_task = asyncio.create_task(engine._stream_from_queue(audio_q, lambda ev: received_transcripts.append(ev)))

        await asyncio.sleep(0.05)
        assert len(connections) == 1
        sock1 = connections[0]

        # Send goAway notification to Socket 1
        sock1._incoming.put_nowait(
            _MockLiveMsg(
                aiohttp.WSMsgType.TEXT,
                json.dumps({"goAway": {"timeLeft": "10s"}})
            )
        )

        # Allow goAway message to be processed and trigger prewarm
        await asyncio.sleep(0.08)

        # Feed Chunk 2 - triggers rotation completion and routes to Socket 2
        audio_q.put_nowait(b"\x02\x00" * 160)
        await asyncio.sleep(0.08)

        # End stream
        audio_q.put_nowait(None)
        await asyncio.wait_for(stream_task, timeout=2.0)

    # Verify Socket 2 pre-warmed and connected
    assert len(connections) >= 2, "goAway must trigger pre-warm of replacement socket"
    sock2 = connections[1]

    # Verify Socket 1 received audioStreamEnd
    assert any(p.get("realtimeInput", {}).get("audioStreamEnd") for p in sock1.sent_payloads)

    # Verify chunk 2 flowed to Socket 2
    sock2_audio = [p for p in sock2.sent_payloads if "audio" in p.get("realtimeInput", {})]
    assert len(sock2_audio) >= 1, "Chunk 2 must flow to Socket 2 after goAway handover"

    # Verify clean rotation and reset of _go_away_received
    assert engine.rotation_count >= 1
    assert engine._go_away_received is False
    assert sock1.closed is True


@pytest.mark.asyncio
async def test_drain_and_close_ignores_interim_and_updates_disconnect_prefix():
    """Verify _drain_and_close ignores interim packets and updates _last_disconnect_finalized_text."""
    from obs_captioner.engines import gemini_live as gl

    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    engine = gl.GeminiLiveEngine(cfg)

    emitted_events: List[TranscriptEvent] = []

    class _MockMsg:
        def __init__(self, data: str):
            self.type = aiohttp.WSMsgType.TEXT
            self.data = data

    class _MockWs:
        def __init__(self, msgs: List[str]):
            self._q: asyncio.Queue = asyncio.Queue()
            for m in msgs:
                self._q.put_nowait(_MockMsg(m))
            self.closed = False

        async def receive(self, timeout=None):
            try:
                return await asyncio.wait_for(self._q.get(), timeout=timeout or 0.1)
            except (asyncio.TimeoutError, asyncio.QueueEmpty):
                self.closed = True
                raise asyncio.TimeoutError()

        async def close(self):
            self.closed = True

    class _MockSession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    mock_ws = _MockWs([
        # 1. Interim packet - must be IGNORED
        json.dumps({
            "serverContent": {
                "interimInputTranscription": {"text": "Ignore this interim text"}
            }
        }),
        # 2. Trailing final packet - must strip prefix, emit final, update disconnect tracking
        json.dumps({
            "serverContent": {
                "inputTranscription": {"text": "Already finalized prefix. This is the trailing finalized sentence."}
            }
        }),
    ])
    mock_session = _MockSession()
    final_ev = asyncio.Event()

    t_before = time.monotonic()
    await engine._drain_and_close(
        old_session=mock_session,
        old_ws=mock_ws,
        final_received_event=final_ev,
        on_transcript=lambda ev: emitted_events.append(ev),
        drain_turn_prefix="Already finalized prefix.",
    )

    assert final_ev.is_set(), "final_received_event should be set when final transcript is emitted"
    assert len(emitted_events) == 1, f"Expected exactly 1 final event, got {len(emitted_events)}: {emitted_events}"
    assert emitted_events[0].is_final is True
    assert emitted_events[0].text == "This is the trailing finalized sentence."
    # Per Fix 7, _drain_and_close prevents state pollution on _last_disconnect_finalized_text
    assert engine._last_disconnect_finalized_text == ""
    assert mock_ws.closed is True
    assert mock_session.closed is True


@pytest.mark.asyncio
async def test_receive_loop_disconnect_strips_already_finalized_prefix():
    """Verify receive_loop unexpected disconnect does not re-emit already finalized words."""
    from obs_captioner.engines import gemini_live as gl

    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    engine = gl.GeminiLiveEngine(cfg)

    # 1. Candidate is already in finalized turn prefix
    engine._latest_interim_text = "The Lord is my shepherd."
    engine._finalized_turn_prefix = "The Lord is my shepherd."

    candidate = engine._latest_interim_text.strip()
    candidate = _strip_finalized_prefix(candidate, engine._finalized_turn_prefix)
    candidate = engine._strip_disconnect_replay_prefix(candidate, time.monotonic())
    words = candidate.split()
    assert len(words) < 2, "Candidate should be fully stripped by _finalized_turn_prefix"

    # 2. Candidate is covered by disconnect replay prefix
    engine._latest_interim_text = "I shall not want."
    engine._finalized_turn_prefix = ""
    engine._last_disconnect_finalized_text = "I shall not want."
    engine._last_disconnect_mono = time.monotonic()

    candidate = engine._latest_interim_text.strip()
    candidate = _strip_finalized_prefix(candidate, engine._finalized_turn_prefix)
    candidate = engine._strip_disconnect_replay_prefix(candidate, time.monotonic())
    words = candidate.split()
    assert len(words) < 2, "Candidate should be fully stripped by _strip_disconnect_replay_prefix"



# ---------------------------------------------------------------------------
# Hot-handoff rotation hardening: strict concurrent-receive mock sockets
# ---------------------------------------------------------------------------

class _StrictMsg:
    def __init__(self, msg_type, data=None):
        self.type = msg_type
        self.data = data
        self.extra = ""


class _StrictWs:
    """Mock ws that raises RuntimeError on concurrent receive(), like aiohttp."""

    def __init__(self, sink: List["_StrictWs"], closed: bool = False):
        self.idx = len(sink)
        self.closed = closed
        self.close_code = None
        self.sent: List[Dict[str, Any]] = []
        self.audio_times: List[float] = []
        self.send_hook = None
        self.readers = 0
        self.concurrent_error = False
        self._incoming: asyncio.Queue = asyncio.Queue()

    async def send_str(self, s: str):
        if self.closed:
            raise ConnectionResetError("closed")
        payload = json.loads(s)
        if self.send_hook is not None:
            await self.send_hook(payload)
        self.sent.append(payload)
        if "audio" in payload.get("realtimeInput", {}):
            self.audio_times.append(time.monotonic())

    async def _recv(self, timeout):
        if self.readers:
            self.concurrent_error = True
            raise RuntimeError("Concurrent call to receive() is not allowed")
        self.readers += 1
        try:
            if not self._incoming.empty():
                return self._incoming.get_nowait()
            if self.closed:
                return _StrictMsg(aiohttp.WSMsgType.CLOSED)
            return await asyncio.wait_for(self._incoming.get(), timeout=timeout)
        finally:
            self.readers -= 1

    async def receive(self, timeout=None):
        return await self._recv(timeout)

    def __aiter__(self):
        return self

    async def __anext__(self):
        while not self.closed:
            try:
                return await self._recv(0.05)
            except asyncio.TimeoutError:
                continue
        raise StopAsyncIteration

    async def close(self, code=1000):
        self.closed = True
        self.close_code = code


def _strict_engine(first_new_closed: bool = False):
    from obs_captioner.engines import gemini_live as gl

    cfg = AppConfig()
    cfg.gemini_live.api_key = "test-live-key"
    cfg.audio.suppress_music = False
    engine = gl.GeminiLiveEngine(cfg)
    engine.vad.is_music = lambda *a, **k: False
    engine.vad.is_speech = lambda *a, **k: False
    conns: List[_StrictWs] = []

    class _Sess:
        def __init__(self, *a, **k):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            self.closed = True
            return False

        async def close(self):
            self.closed = True

        def ws_connect(self, *a, **k):
            ws = _StrictWs(conns, closed=False)
            ws._incoming.put_nowait(_StrictMsg(aiohttp.WSMsgType.TEXT, json.dumps({"setupComplete": {}})))
            conns.append(ws)
            return ws

    return gl, engine, conns, _Sess


def _rot_patches(gl, sess):
    return (
        patch.object(gl.aiohttp, "ClientSession", sess),
        patch.object(gl, "SESSION_PREWARM_THRESHOLD_SEC", 0.04),
        patch.object(gl, "SESSION_ROTATION_THRESHOLD_SEC", 0.08),
        patch.object(gl, "SESSION_HARD_ROTATION_SEC", 0.25),
    )


@pytest.mark.asyncio
async def test_rotation_old_socket_trailing_final_delivered_without_concurrent_receive():
    gl, engine, conns, Sess = _strict_engine()
    got: List[TranscriptEvent] = []
    audio_q: asyncio.Queue = asyncio.Queue()
    audio_q.put_nowait(b"\x01\x00" * 160)
    p = _rot_patches(gl, Sess)
    with p[0], p[1], p[2], p[3]:
        task = asyncio.create_task(engine._stream_from_queue(audio_q, got.append))
        await asyncio.sleep(0.12)
        assert len(conns) >= 2
        conns[0]._incoming.put_nowait(_StrictMsg(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"serverContent": {"inputTranscription": {"text": "Old socket trailing final."}}}),
        ))
        audio_q.put_nowait(b"\x02\x00" * 160)
        await asyncio.sleep(0.15)
        audio_q.put_nowait(None)
        await asyncio.wait_for(task, timeout=2.0)
    assert not any(c.concurrent_error for c in conns), "concurrent receive() on one socket"
    assert any("Old socket trailing final." in e.text and e.is_final for e in got), got
    await asyncio.wait_for(asyncio.gather(*list(engine._drain_tasks), return_exceptions=True), timeout=2.0)
    assert conns[0].closed


@pytest.mark.asyncio
async def test_rotation_closed_prewarm_keeps_old_session_and_chunk():
    gl, engine, conns, Sess = _strict_engine()
    got: List[TranscriptEvent] = []
    audio_q: asyncio.Queue = asyncio.Queue()
    audio_q.put_nowait(b"\x01\x00" * 160)
    p = _rot_patches(gl, Sess)
    orig_ws_connect = Sess.ws_connect

    def ws_connect(self, *a, **k):
        ws = orig_ws_connect(self, *a, **k)
        if len(conns) >= 2:
            # Pre-warmed socket dies right after setup completes.
            async def _die():
                await asyncio.sleep(0.02)
                ws.closed = True
            asyncio.get_running_loop().create_task(_die())
        return ws

    Sess.ws_connect = ws_connect
    with p[0], p[1], p[2], p[3]:
        task = asyncio.create_task(engine._stream_from_queue(audio_q, got.append))
        await asyncio.sleep(0.2)
        audio_q.put_nowait(b"\x07\x00" * 160)
        await asyncio.sleep(0.1)
        audio_q.put_nowait(None)
        await asyncio.wait_for(task, timeout=2.0)
    first = conns[0]
    audio = [x for x in first.sent if "audio" in x.get("realtimeInput", {})]
    assert len(audio) == 2, f"chunk dropped on old socket: {len(audio)}"
    assert engine.rotation_count == 0 or len(conns) > 2


@pytest.mark.asyncio
async def test_rotation_never_blocks_audio_loop():
    gl, engine, conns, Sess = _strict_engine()
    got: List[TranscriptEvent] = []
    audio_q: asyncio.Queue = asyncio.Queue()
    p = _rot_patches(gl, Sess)
    with p[0], p[1], p[2], p[3]:
        task = asyncio.create_task(engine._stream_from_queue(audio_q, got.append))
        end = time.monotonic() + 0.4
        sent_trailing = False
        while time.monotonic() < end:
            audio_q.put_nowait(b"\x01\x00" * 160)
            if not sent_trailing and len(conns) >= 2:
                sent_trailing = True
                engine._latest_interim_text = "pending interim words here"
                conns[0]._incoming.put_nowait(_StrictMsg(
                    aiohttp.WSMsgType.TEXT,
                    json.dumps({"serverContent": {"inputTranscription": {"text": "Late final."}}}),
                ))
            await asyncio.sleep(0.01)
        audio_q.put_nowait(None)
        await asyncio.wait_for(task, timeout=2.0)
    times = sorted(t for c in conns for t in c.audio_times)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert engine.rotation_count >= 1
    assert gaps and max(gaps) < 0.05, f"audio loop stalled {max(gaps)*1000:.0f}ms"


@pytest.mark.asyncio
async def test_cancelled_error_during_rotation_resets_flag():
    gl, engine, conns, Sess = _strict_engine()
    got: List[TranscriptEvent] = []
    audio_q: asyncio.Queue = asyncio.Queue()
    audio_q.put_nowait(b"\x01\x00" * 160)
    p = _rot_patches(gl, Sess)

    async def hook(payload):
        if payload.get("realtimeInput", {}).get("audioStreamEnd"):
            raise asyncio.CancelledError()

    with p[0], p[1], p[2], p[3]:
        task = asyncio.create_task(engine._stream_from_queue(audio_q, got.append))
        await asyncio.sleep(0.02)
        conns[0].send_hook = hook
        await asyncio.sleep(0.2)
        engine.is_running = False
        audio_q.put_nowait(None)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            task.cancel()
    assert engine._rotation_in_progress is False


@pytest.mark.asyncio
async def test_smart_mode_mid_turn_sentence_drop_and_rewrite_no_duplicates():
    """Verify suffix-to-prefix stripping when Gemini SMART mode drops early filler sentences mid-turn."""
    cfg, engine, sink, server, display_ws = _build_full_pipeline(max_sentence_words=24)

    try:
        # Scenario 1: Speaker says "Okay. All right. Well, moving on." (split finalized)
        # followed by "This son was very spoiled." (VAD pause finalized).
        # Then Gemini SMART mode rewrites the turn to:
        # "Moving on, this son was very spoiled. He did not treat his father very well."
        evs_1 = engine._segment_streaming_event(
            "Okay. All right. Well, moving on. This son was very",
            is_final=False,
            mono_now=100.0,
        )
        finals_1 = [e.text for e in evs_1 if e.is_final]
        assert finals_1 == ["Okay. All right. Well, moving on."]

        evs_2 = engine._segment_streaming_event(
            "Okay. All right. Well, moving on. This son was very spoiled.",
            is_final=False,
            mono_now=100.5,
        )
        assert [e.text for e in evs_2 if e.is_final] == []

        # Simulate VAD pause finalizing "This son was very spoiled."
        vad_ev = engine._maybe_finalize_on_vad_pause(
            speech_active=False, silence_duration=0.8, pause_threshold=0.6, mono_now=101.3
        )
        assert vad_ev is not None and vad_ev.is_final
        assert vad_ev.text == "This son was very spoiled."

        # Now authoritative inputTranscription arrives with SMART mode dropping "Okay. All right. Well,"
        evs_3 = engine._segment_streaming_event(
            "Moving on, this son was very spoiled. He did not treat his father very well.",
            is_final=True,
            mono_now=103.0,
        )
        finals_3 = [e.text for e in evs_3 if e.is_final]
        assert finals_3 == ["He did not treat his father very well."], (
            f"Expected only new sentence after suffix-to-prefix strip, got {finals_3}"
        )

        # Scenario 2: Exact full-turn repeat when both sentences were already finalized via split + VAD pause
        evs_b1 = engine._segment_streaming_event(
            "Okay. All right. Well, moving on. This son was very",
            is_final=False,
            mono_now=200.0,
        )
        assert [e.text for e in evs_b1 if e.is_final] == ["Okay. All right. Well, moving on."]

        engine._segment_streaming_event(
            "Okay. All right. Well, moving on. This son was very spoiled.",
            is_final=False,
            mono_now=200.5,
        )
        vad_b = engine._maybe_finalize_on_vad_pause(
            speech_active=False, silence_duration=0.8, pause_threshold=0.6, mono_now=201.3
        )
        assert vad_b is not None and vad_b.text == "This son was very spoiled."

        evs_b_final = engine._segment_streaming_event(
            "Okay. All right. Well, moving on. This son was very spoiled.",
            is_final=True,
            mono_now=202.0,
        )
        assert [e.text for e in evs_b_final if e.is_final] == []
    finally:
        await sink.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_leading_stutter_stripped_by_smart_mode_no_duplicates():
    """Verify leading hyphenated stutter ('Wai- Wait a 2nd.') finalized on VAD pause does not duplicate when SMART mode drops 'Wai-'."""
    cfg, engine, sink, server, display_ws = _build_full_pipeline(max_sentence_words=24)

    try:
        # 1. Interim arrives with leading stutter "Wai- Wait a 2nd." and speaker pauses
        engine._segment_streaming_event("Wai- Wait a 2nd.", is_final=False, mono_now=300.0)
        vad_1 = engine._maybe_finalize_on_vad_pause(
            speech_active=False, silence_duration=0.8, pause_threshold=0.6, mono_now=300.8
        )
        assert vad_1 is not None and vad_1.is_final
        # Leading stutter should be cleaned on emission
        assert vad_1.text == "Wait a 2nd."

        # 2. Speaker continues in same turn, SMART mode strips "Wai-" from the cumulative transcript
        evs_2 = engine._segment_streaming_event(
            "Wait a 2nd. Is this who I'm hanging out with now? The best I got is",
            is_final=False,
            mono_now=302.0,
        )
        finals_2 = [e.text for e in evs_2 if e.is_final]
        assert finals_2 == ["Is this who I'm hanging out with now?"]

        # 3. Final inputTranscription arrives for the full turn
        evs_3 = engine._segment_streaming_event(
            "Wait a 2nd. Is this who I'm hanging out with now? The best I got is pigs?",
            is_final=True,
            mono_now=304.0,
        )
        finals_3 = [e.text for e in evs_3 if e.is_final]
        assert finals_3 == ["The best I got is pigs?"], (
            f"Expected only the final sentence without duplicating earlier sentences, got {finals_3}"
        )
    finally:
        await sink.stop()
        await server.stop()


@pytest.mark.asyncio
async def test_gemini_live_stop_unblocks_start_streaming_and_allows_clean_restart():
    """Verify GeminiLiveEngine.stop() unblocks start_streaming(), cancels _pump_task, and allows immediate restart."""
    gl, engine, conns, Sess = _strict_engine()
    got: List[TranscriptEvent] = []

    closed_gen = False

    async def mock_audio_stream():
        nonlocal closed_gen
        try:
            while True:
                yield b"\x01\x00" * 160
                await asyncio.sleep(0.02)
        finally:
            closed_gen = True

    with patch.object(gl.aiohttp, "ClientSession", Sess):
        stream_task = asyncio.create_task(engine.start_streaming(mock_audio_stream(), got.append))
        await asyncio.sleep(0.08)
        assert engine.is_running is True
        assert len(conns) == 1

        # Stop engine and verify start_streaming terminates promptly and closes audio generator
        await asyncio.wait_for(engine.stop(), timeout=1.5)
        await asyncio.wait_for(stream_task, timeout=1.5)
        assert engine.is_running is False
        assert closed_gen is True

        # Now start streaming again without recreating the process — must set is_running=True and connect cleanly
        closed_gen_2 = False

        async def mock_audio_stream_2():
            nonlocal closed_gen_2
            try:
                yield b"\x02\x00" * 160
                await asyncio.sleep(0.05)
            finally:
                closed_gen_2 = True

        stream_task_2 = asyncio.create_task(engine.start_streaming(mock_audio_stream_2(), got.append))
        await asyncio.sleep(0.08)
        assert engine.is_running is True
        assert len(conns) >= 2
        await asyncio.wait_for(engine.stop(), timeout=1.5)
        await asyncio.wait_for(stream_task_2, timeout=1.5)
        assert closed_gen_2 is True


def test_v1_2_14_live_capture_glued_words_compounds_and_stutters():
    """Verify v1.2.14 live sermon capture fixes for glued words, split compounds, stutters, and quote echoes."""
    from obs_captioner.formatter import TextFormatter

    fmt = TextFormatter()

    # 1. Glued words & 3-word chains
    assert "something spectacular" in fmt.format_text("somethingspectacular", is_final=False).lower()
    assert "simplistic statements" in fmt.format_text("simplisticstatements", is_final=False).lower()
    assert "without necessarily experiencing" in fmt.format_text("withoutnecessarilyexperiencing", is_final=False).lower()
    assert "were proverbial" in fmt.format_text("wereproverbial", is_final=False).lower()
    assert "of smallness" in fmt.format_text("ofsmallness", is_final=False).lower()

    # 2. Apostrophe-glued contractions
    assert fmt.format_text("We'reregardless of the cost.", is_final=True) == "We're regardless of the cost."
    assert fmt.format_text("It doesn'tstop there.", is_final=True) == "It doesn't stop there."
    assert fmt.format_text("In God'skingdom today.", is_final=True) == "In God's kingdom today."
    assert fmt.format_text("Transformation doesn'thappen overnight.", is_final=True) == "Transformation doesn't happen overnight."

    # 3. Split compound words & east -> yeast
    assert "relationships" in fmt.format_text("Our relation ships matter.", is_final=True).lower()
    assert "partnerships" in fmt.format_text("Ministry partner ships grow.", is_final=True).lower()
    assert "bedtime" in fmt.format_text("Right before bed time.", is_final=True).lower()
    assert "you're already" in fmt.format_text("When you'real ready there.", is_final=True).lower()
    assert "like yeast" in fmt.format_text("The kingdom of heaven is like east.", is_final=True).lower()
    assert "yeast penetrates" in fmt.format_text("East penetrates the dough.", is_final=True).lower()

    # 4. Single-word stutters and quote-boundary word echoes
    assert fmt.format_text("We want to to to see this.", is_final=True) == "We want to see this."
    assert fmt.format_text(
        'The birds of the air come and perch in its branches.\'" Branches. To understand this.',
        is_final=True,
    ) == 'The birds of the air come and perch in its branches.\'" To understand this.'


def test_v1_2_14_interim_space_restoration_and_prefix_anchoring():
    """Verify interim word spaces are preserved when Gemini glues words on subsequent chunks and prefix stripping anchors on tail."""
    from obs_captioner.config import AppConfig
    from obs_captioner.engines.gemini_live import (
        GeminiLiveEngine,
        _split_completed_sentences,
        _split_final_paragraph,
        _strip_finalized_prefix,
    )

    cfg = AppConfig()
    engine = GeminiLiveEngine(cfg)

    # 1. Interim has "starts making", final glues "startsmaking" -> restored to "starts making"
    evs_interim = engine._segment_streaming_event(
        "So Jina starts making the bread.",
        is_final=False,
        mono_now=10.0,
    )
    assert len(evs_interim) == 1 and not evs_interim[0].is_final
    assert "starts making" in evs_interim[0].text

    evs_final = engine._segment_streaming_event(
        "So Jina startsmaking the bread.",
        is_final=True,
        mono_now=11.0,
    )
    assert len(evs_final) == 1 and evs_final[0].is_final
    assert evs_final[0].text == "So Jina starts making the bread."

    # 2. Spurious periods after dangling prepositions ("loved by. God.", "worked all through. The dough.\"")
    sents_1, rem_1 = _split_completed_sentences("We are deeply loved by. God.")
    assert sents_1 == []
    assert rem_1 == "We are deeply loved by. God."

    parts_1 = _split_final_paragraph("We are deeply loved by. God.")
    assert parts_1 == ["We are deeply loved by God."]

    parts_2 = _split_final_paragraph(
        'She mixed into about 60 pounds of flour until it worked all through. The dough."'
    )
    assert parts_2 == ['She mixed into about 60 pounds of flour until it worked all through the dough."']

    # 3. _strip_finalized_prefix when SMART mode drops >4 filler words from a long multi-sentence prefix (utt=447)
    finalized_pref = (
        "Until one day, you know, I think, like, in high school, I looked at him, "
        "and he was, you know, like, taller than me."
    )
    smart_rewritten_full = (
        "Until one day in high school, I looked at him and he was taller than me. "
        "I'm like, what just happened?"
    )
    stripped = _strip_finalized_prefix(smart_rewritten_full, finalized_pref)
    assert stripped == "I'm like, what just happened?", f"Got over-stripped remainder: {stripped!r}"

    # 4. turnComplete: true without inputTranscription finalizes _latest_interim_text
    engine._reset_turn_state()
    engine._segment_streaming_event("In the vineyard, you have to wait.", is_final=False, mono_now=20.0)
    assert engine._latest_interim_text == "In the vineyard, you have to wait."
    parsed = engine.parse_server_message({"serverContent": {"turnComplete": True}})
    assert len(parsed) == 1
    assert parsed[0].is_final is True
    assert parsed[0].text == "In the vineyard, you have to wait."


