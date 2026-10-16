"""Comprehensive Test Suite for Live Translation, Multi-Language Display, and Interpreter Audio.

Covers:
1. Interpreter Mode audio generation and fan-out to display sockets.
2. English display verification (clean, uncorrupted, zero-latency delivery).
3. Simultaneous multi-language delivery across concurrent audience sockets.
4. Verification of different translation models (google_free, gemini_flash/gemini, nllb, gemini_live).
5. Auto-switching translation model/provider when interpreter mode is turned on.
"""

import asyncio
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from obs_captioner.config import AppConfig, TranslationConfig, GeneralConfig, OverlayConfig
from obs_captioner.web.server import WebOverlayServer
from obs_captioner.translator import SubtitleTranslator
from obs_captioner.engines.gemini_live import GeminiLiveEngine


@pytest.mark.asyncio
async def test_1_interpreter_mode_audio_broadcast():
    """1. Ensure interpreter mode audio generation and fan-out delivers base64 PCM to display sockets."""
    config = AppConfig()
    config.translation.interpreter_mode = True
    config.translation.target_language = "es"

    server = WebOverlayServer(config)
    mock_ws_es = AsyncMock(spec=web.WebSocketResponse)
    mock_ws_en = AsyncMock(spec=web.WebSocketResponse)

    server.caption_sockets[mock_ws_es] = "es"
    server.socket_roles[mock_ws_es] = "display"
    server.caption_sockets[mock_ws_en] = "en"
    server.socket_roles[mock_ws_en] = "display"

    # Simulated 24kHz PCM linear audio bytes
    raw_pcm = b"\x00\x01\x00\x02" * 100
    server.broadcast_translated_audio(raw_pcm, mime_type="audio/pcm;rate=24000", target_lang="es")

    # Audio should be queued specifically for the Spanish display listener, NOT the English viewer
    q_es = server._ensure_caption_queue(mock_ws_es)
    q_en = server._ensure_caption_queue(mock_ws_en)

    assert not q_es.empty(), "Spanish display socket must receive translated audio."
    assert q_en.empty(), "English display socket must NOT receive translated audio."

    is_final, msg_str = q_es.get_nowait()
    payload = json.loads(msg_str)
    assert payload["type"] == "translated_audio"
    assert payload["mime"] == "audio/pcm;rate=24000"
    assert base64.b64decode(payload["data"]) == raw_pcm


@pytest.mark.asyncio
async def test_2_english_display_works_cleanly():
    """2. Ensure English display (OBS overlay and /display?lang=en) receives pure English captions instantly."""
    config = AppConfig()
    server = WebOverlayServer(config)

    mock_obs_overlay = AsyncMock(spec=web.WebSocketResponse)
    mock_english_display = AsyncMock(spec=web.WebSocketResponse)

    server.caption_sockets[mock_obs_overlay] = "en"
    server.socket_roles[mock_obs_overlay] = "caption"
    server.caption_sockets[mock_english_display] = "en"
    server.socket_roles[mock_english_display] = "display"

    # Emit interim and final English captions
    await server.broadcast_caption({"type": "caption", "text": "Good morning church", "is_final": False})
    await server.broadcast_caption({"type": "caption", "text": "Good morning church, welcome.", "is_final": True})

    q_obs = server._ensure_caption_queue(mock_obs_overlay)
    q_disp = server._ensure_caption_queue(mock_english_display)

    assert q_obs.qsize() == 2
    assert q_disp.qsize() == 2

    # Verify text is clean English and arrives immediately with no translation lag
    _fin, msg_interim = q_disp.get_nowait()
    assert json.loads(msg_interim)["text"] == "Good morning church"

    _fin, msg_final = q_disp.get_nowait()
    assert json.loads(msg_final)["text"] == "Good morning church, welcome."
    assert json.loads(msg_final)["is_final"] is True


@pytest.mark.asyncio
async def test_3_simultaneous_multi_language_delivery():
    """3. Ensure that multiple foreign languages work and fan out simultaneously to concurrent listeners."""
    config = AppConfig()
    config.translation.enabled = True
    config.translation.target_language = "es"

    server = WebOverlayServer(config)

    # Mock translator with multi-language lookup
    mock_translations = {
        "es": "La gracia sea con todos ustedes.",
        "fr": "La grâce soit avec vous tous.",
        "de": "Die Gnade sei mit euch allen.",
    }

    async def fake_translate_to_lang(text, target_lang="es", **kwargs):
        return mock_translations.get(target_lang, text)

    server.translator = MagicMock()
    server.translator.translate_to_language = AsyncMock(side_effect=fake_translate_to_lang)

    # Sockets for 4 concurrent viewers
    ws_en = AsyncMock(spec=web.WebSocketResponse)
    ws_es = AsyncMock(spec=web.WebSocketResponse)
    ws_fr = AsyncMock(spec=web.WebSocketResponse)
    ws_de = AsyncMock(spec=web.WebSocketResponse)

    server.caption_sockets[ws_en] = "en"
    server.caption_sockets[ws_es] = "es"
    server.caption_sockets[ws_fr] = "fr"
    server.caption_sockets[ws_de] = "de"

    english_sentence = "Grace be with you all."
    await server.broadcast_caption({
        "type": "caption",
        "text": english_sentence,
        "is_final": True,
        "seq": 101,
    })

    # Wait for the async translation fan-out chain to drain
    if server._caption_translate_chain:
        await server._caption_translate_chain

    # Wait a tiny moment for sender loops to deliver to ws mocks
    await asyncio.sleep(0.05)

    # Check English received immediately
    assert ws_en.send_str.called, "English websocket should receive caption"
    sent_en = json.loads(ws_en.send_str.call_args_list[-1].args[0])
    assert sent_en["text"] == english_sentence

    # Check Spanish, French, and German each received their correct simultaneous translation
    assert ws_es.send_str.called, "Spanish websocket should receive translation"
    sent_es = json.loads(ws_es.send_str.call_args_list[-1].args[0])
    assert sent_es["text"] == "La gracia sea con todos ustedes."

    assert ws_fr.send_str.called, "French websocket should receive translation"
    sent_fr = json.loads(ws_fr.send_str.call_args_list[-1].args[0])
    assert sent_fr["text"] == "La grâce soit avec vous tous."

    assert ws_de.send_str.called, "German websocket should receive translation"
    sent_de = json.loads(ws_de.send_str.call_args_list[-1].args[0])
    assert sent_de["text"] == "Die Gnade sei mit euch allen."


@pytest.mark.asyncio
async def test_4_different_translation_models():
    """4. Ensure different translation models/providers function properly."""
    # 4A. Google Free translation provider
    cfg_free = TranslationConfig(provider="google_free", enable_disk_cache=False)
    tr_free = SubtitleTranslator(cfg_free)

    with patch.object(tr_free, "_fetch_translation", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = "Hola Mundo"
        res = await tr_free.translate_to_language("Hello World", target_lang="es")
        assert res == "Hola Mundo"
        mock_fetch.assert_called_once()

    # 4B. Gemini Flash / Gemini API translation provider
    cfg_gemini = TranslationConfig(provider="gemini_flash", gemini_api_key="test-api-key", enable_disk_cache=False)
    tr_gemini = SubtitleTranslator(cfg_gemini)

    with patch.object(tr_gemini, "_fetch_gemini_translation", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = "Bonjour le monde"
        res = await tr_gemini.translate_to_language("Hello World", target_lang="fr", provider_override="gemini_flash")
        assert res == "Bonjour le monde"
        mock_gemini.assert_called_once()

    # 4C. Meta NLLB local translation provider
    cfg_nllb = TranslationConfig(provider="nllb", enable_disk_cache=False)
    tr_nllb = SubtitleTranslator(cfg_nllb)

    mock_nllb_engine = MagicMock()
    mock_nllb_engine.translate = AsyncMock(return_value="Hallo Welt")
    tr_nllb._nllb_translator = mock_nllb_engine

    res = await tr_nllb.translate_to_language("Hello World", target_lang="de", provider_override="nllb")
    assert res == "Hallo Welt"

    # 4D. Gemini Live translation provider (delegates to Gemini API for text)
    cfg_live = TranslationConfig(provider="gemini_live", gemini_api_key="test-api-key", enable_disk_cache=False)
    tr_live = SubtitleTranslator(cfg_live)
    with patch.object(tr_live, "_fetch_gemini_translation", new_callable=AsyncMock) as mock_live:
        mock_live.return_value = "Olá Mundo"
        res = await tr_live.translate_to_language("Hello World", target_lang="pt")
        assert res == "Olá Mundo"


@pytest.mark.asyncio
async def test_5_interpreter_mode_switches_translation_model():
    """5. Ensure that when interpreter mode is turned on, the translation model/provider auto-switches to gemini_live."""
    config = AppConfig()
    config.translation.enabled = True
    config.translation.provider = "google_free"
    config.translation.interpreter_mode = False

    server = WebOverlayServer(config)

    # Simulate saving settings via POST /api/config with interpreter_mode = True
    mock_request = MagicMock(spec=web.Request)
    mock_request.json = AsyncMock(return_value={
        "translation": {
            "interpreter_mode": True,
            "target_language": "fr",
            "provider": "google_free"  # Even if UI sends google_free, interpreter mode mandates gemini_live
        }
    })

    with patch.object(server, "_check_auth", return_value=True), \
         patch("obs_captioner.web.server.save_config") as mock_save:
        resp = await server._handle_post_config(mock_request)
        assert resp.status == 200

        # Assert that provider automatically switched to gemini_live
        assert server.config.translation.interpreter_mode is True
        assert server.config.translation.provider == "gemini_live", "Interpreter mode must auto-switch translation provider to gemini_live."
        mock_save.assert_called_once()
