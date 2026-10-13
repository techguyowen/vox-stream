"""Tests for Ollama long context / options and SherpaEngine VAD omission."""

import asyncio
import json
from unittest import mock
import urllib.request
import pytest

from obs_captioner.config import AppConfig, SummaryConfig
from obs_captioner.history import HistoryEntry
from obs_captioner.summary_engine import (
    OLLAMA_MAX_TRANSCRIPT_CHARS,
    SermonSummaryEngine,
)


def test_ollama_summary_long_transcript_and_num_ctx():
    """Verify Ollama summary receives transcripts > 8000 chars (capped at 40k) and sets num_ctx."""
    assert OLLAMA_MAX_TRANSCRIPT_CHARS == 40000

    cfg = SummaryConfig(provider="ollama")
    engine = SermonSummaryEngine(config=cfg)

    # Create entries with transcript text > 8000 chars (e.g. ~20,000 chars)
    chunk = "Now faith is confidence in what we hope for and assurance about what we do not see. " * 6
    entries = [
        HistoryEntry(
            id=i,
            text=f"Part {i}: {chunk}",
            start_time=float(i * 10),
            end_time=float(i * 10 + 9),
        )
        for i in range(40)
    ]

    captured_requests = []
    captured_timeouts = []

    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = json.dumps({
        "response": json.dumps({
            "title": "Faith and Assurance",
            "big_idea": "Standing strong in faith",
            "scriptures": ["Hebrews 11:1"],
            "key_points": ["Point 1", "Point 2"],
            "quotes": ["Walking by faith"],
            "discussion_questions": ["Question 1?", "Question 2?"],
        })
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    def fake_urlopen(req, timeout=None):
        captured_requests.append(req)
        captured_timeouts.append(timeout)
        return mock_resp

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = asyncio.run(engine.generate_sermon_summary(entries=entries))

    assert result is not None
    assert result.get("provider_used") == "ollama"
    assert len(captured_requests) == 1

    req = captured_requests[0]
    payload = json.loads(req.data.decode("utf-8"))

    # Assert options.num_ctx is present and set to 16384
    assert "options" in payload
    assert payload["options"].get("num_ctx") == 16384

    # Assert timeout is raised to 120s
    assert captured_timeouts[0] == 120.0

    # Assert prompt includes transcript longer than old 8000 limit
    prompt = payload["prompt"]
    transcript_in_prompt = prompt.split("\n\n", 1)[1]
    assert len(transcript_in_prompt) > 8000
    assert len(transcript_in_prompt) <= OLLAMA_MAX_TRANSCRIPT_CHARS


def test_ollama_summary_transcript_capped_at_max_chars():
    """Verify Ollama summary prompt caps extremely long transcripts (> 40000 chars) at 40000."""
    cfg = SummaryConfig(provider="ollama")
    engine = SermonSummaryEngine(config=cfg)

    # 100 entries * 500 chars = 50,000 chars > 40,000 cap
    chunk = "abcdefghij" * 50
    entries = [
        HistoryEntry(id=i, text=chunk, start_time=float(i), end_time=float(i + 1))
        for i in range(100)
    ]

    captured_requests = []
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = json.dumps({
        "response": json.dumps({"title": "Long Sermon", "big_idea": "Capped transcript"})
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    def fake_urlopen(req, timeout=None):
        captured_requests.append(req)
        return mock_resp

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        asyncio.run(engine.generate_sermon_summary(entries=entries))

    assert len(captured_requests) == 1
    payload = json.loads(captured_requests[0].data.decode("utf-8"))
    transcript_in_prompt = payload["prompt"].split("\n\n", 1)[1]
    assert len(transcript_in_prompt) == OLLAMA_MAX_TRANSCRIPT_CHARS


def test_sherpa_engine_vad_parity():
    """Verify SherpaEngine instantiates VoiceActivityDetector for uniform engine settings parity."""
    pytest.importorskip("sherpa_onnx")

    from obs_captioner.engines.sherpa_engine import SherpaEngine

    app_cfg = AppConfig()
    app_cfg.audio.noise_gate_db = -42.0
    app_cfg.audio.vad_threshold = 0.55
    engine = SherpaEngine(app_cfg)

    assert hasattr(engine, "vad")
    assert engine.vad.noise_gate_db == -42.0
    assert engine.vad.vad_threshold == 0.55
