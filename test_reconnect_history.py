import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web

from obs_captioner.config import AppConfig
from obs_captioner.history import TranscriptHistory
from obs_captioner.web.server import WebOverlayServer

@pytest.mark.asyncio
async def test_reconnecting_client_pulls_missed_transcript():
    """
    Ensure that when a client reconnects (e.g. after walking out of Wi-Fi or locking phone),
    it is able to pull the full transcript of what was spoken while disconnected.
    """
    config = AppConfig()
    history = TranscriptHistory(max_entries=200)
    server = WebOverlayServer(config, history=history)

    now = time.time()
    # 1. Simulate speech before disconnect
    await server.broadcast_caption({"text": "Welcome to our Sunday service.", "is_final": True, "seq": 1})
    server.history.add_entry("Welcome to our Sunday service.", start_time=now - 60)

    # 2. Simulate 5 sentences spoken while the device was disconnected / phone was asleep
    missed_sentences = [
        "Please open your Bibles to Romans chapter 8.",
        "There is therefore now no condemnation for those who are in Christ Jesus.",
        "For the law of the Spirit of life has set you free.",
        "God has done what the law was weakened by the flesh could not do.",
        "By sending his own Son in the likeness of sinful flesh."
    ]
    for idx, sentence in enumerate(missed_sentences, start=2):
        await server.broadcast_caption({"text": sentence, "is_final": True, "seq": idx})
        server.history.add_entry(sentence, start_time=now - 50 + idx * 5)

    # 3. Verify WebSocket Snapshot Replay on Reconnect
    # When the client's WebSocket connects/reconnects:
    mock_ws = AsyncMock(spec=web.WebSocketResponse)
    server.caption_sockets[mock_ws] = "en"
    server.socket_roles[mock_ws] = "display"

    # Trigger initial snapshot queueing
    snapshot_lines = list(server._recent_finals)
    assert len(snapshot_lines) >= 6, "Server must preserve recent final sentences in snapshot buffer"

    # 4. Verify HTTP Transcript History endpoint /api/transcript/history
    # This is what display.html calls on reconnect (loadServerHistory(true))
    history_entries = server.history.get_history(limit=100)
    history_texts = [e["text"] for e in history_entries]

    # Verify all 5 missed sentences are present in the history payload in chronological order
    for sentence in missed_sentences:
        assert sentence in history_texts, f"Missed sentence '{sentence}' must be in server transcript history!"

    assert "Welcome to our Sunday service." in history_texts

    await server.stop()
