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
