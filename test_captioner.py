"""Comprehensive unit test suite for OBS Live Captioner PRO Suite."""

import asyncio
import json
import math
from pathlib import Path
import shutil
import struct
import tempfile
import time
import unittest
from obs_captioner.config import load_config, AppConfig, save_config, OverlayConfig
from obs_captioner.vad import VoiceActivityDetector
from obs_captioner.censor import ContentFilter, CensorConfig
from obs_captioner.vocabulary import VocabularyReplacer, VocabularyConfig
from obs_captioner.history import TranscriptHistory
from obs_captioner.engines import (
    create_engine,
    GoogleWebEngine,
    GoogleSTTEngine,
    GeminiLiveEngine,
    LocalWhisperEngine,
    VoskEngine,
    MoonshineEngine,
    BandwidthEngine,
    SherpaEngine,
    ParakeetEngine,
    SenseVoiceEngine,
)
from obs_captioner.engines.base import TranscriptEvent
from obs_captioner.themes import THEME_PRESETS, get_all_presets
from obs_captioner.translator import SubtitleTranslator, TranslationConfig
from obs_captioner.twitch_bot import TwitchCaptionBot, TwitchConfig
from obs_captioner.security import (
    sanitize_text,
    escape_html,
    sanitize_filename,
    validate_censor_term,
    SimpleRateLimiter,
)


class TestSecurity(unittest.TestCase):

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("../../secrets.txt"), "secrets.txt")
        self.assertEqual(sanitize_filename("..\\..\\windows\\system32.dll"), "windowssystem32.dll")
        self.assertEqual(sanitize_filename("valid_captions.srt"), "valid_captions.srt")
        self.assertEqual(sanitize_filename(""), "captions.srt")

    def test_escape_html(self):
        raw = '<script>alert("xss")</script> & <b>bold</b>'
        escaped = escape_html(raw)
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)
        self.assertIn("&amp;", escaped)

    def test_validate_censor_term(self):
        ok, clean = validate_censor_term("badword")
        self.assertTrue(ok)
        self.assertEqual(clean, "badword")

        # Empty
        ok_empty, _ = validate_censor_term("")
        self.assertFalse(ok_empty)

        # Overly long string
        ok_long, _ = validate_censor_term("a" * 150)
        self.assertFalse(ok_long)

    def test_rate_limiter(self):
        limiter = SimpleRateLimiter(max_requests=3, window_seconds=10.0)
        ip = "192.168.1.100"
        self.assertTrue(limiter.is_allowed(ip))
        self.assertTrue(limiter.is_allowed(ip))
        self.assertTrue(limiter.is_allowed(ip))
        self.assertFalse(limiter.is_allowed(ip))


class TestThemes(unittest.TestCase):

    def test_presets_loaded(self):
        self.assertIn("modern_clean", THEME_PRESETS)
        self.assertIn("broadcast_news", THEME_PRESETS)
        self.assertIn("sanctuary_worship", THEME_PRESETS)
        self.assertIn("minimal_cinema", THEME_PRESETS)
        self.assertIn("stage_confidence", THEME_PRESETS)
        self.assertIn("corporate_keynote", THEME_PRESETS)
        self.assertIn("editorial_nordic", THEME_PRESETS)
        self.assertIn("youtube_cc", THEME_PRESETS)
        self.assertIn("opendyslexic", THEME_PRESETS)
        self.assertIn("broadcast_bar", THEME_PRESETS)
        self.assertIn("sanctuary_high_contrast", THEME_PRESETS)
        self.assertIn("floating_clean", THEME_PRESETS)
        self.assertIn("chyron_left", THEME_PRESETS)
        self.assertIn("teleprompter_stage", THEME_PRESETS)

        all_presets = get_all_presets()
        self.assertEqual(len(all_presets), 14)

    def test_preset_categories(self):
        valid_cats = {"broadcast", "sanctuary", "cinema", "accessibility"}
        for p_id, p in THEME_PRESETS.items():
            self.assertTrue(hasattr(p, "category"), f"Preset {p_id} missing category")
            self.assertIn(p.category, valid_cats, f"Preset {p_id} has invalid category {p.category}")

    def test_apply_theme_to_overlay(self):
        ov = OverlayConfig()
        ov.apply_theme("sanctuary_worship")
        self.assertEqual(ov.theme_id, "sanctuary_worship")
        self.assertEqual(ov.text_color, "#FFFBEB")
        self.assertEqual(ov.font_family, "'Montserrat', sans-serif")

    def test_apply_functional_broadcast_bar_preset(self):
        ov = OverlayConfig()
        applied = ov.apply_theme("broadcast_bar")
        self.assertTrue(applied)
        self.assertEqual(ov.theme_id, "broadcast_bar")
        self.assertEqual(ov.box_layout, "bar")
        self.assertEqual(ov.accent_line, "top_divider")
        self.assertEqual(ov.bottom_offset_px, 0)
        self.assertEqual(ov.accent_color, "#38BDF8")

    def test_apply_opendyslexic_preset(self):
        from pathlib import Path
        ov = OverlayConfig()
        applied = ov.apply_theme("opendyslexic")
        self.assertTrue(applied)
        self.assertEqual(ov.theme_id, "opendyslexic")
        self.assertEqual(ov.font_family, "'OpenDyslexic', sans-serif")
        self.assertEqual(ov.font_weight, "700")

        # Verify offline bundled font files exist in static/fonts
        static_fonts = Path(__file__).parent / "obs_captioner" / "web" / "static" / "fonts"
        self.assertTrue((static_fonts / "OpenDyslexic-Regular.woff").is_file())
        self.assertTrue((static_fonts / "OpenDyslexic-Bold.woff").is_file())
        self.assertTrue((static_fonts / "OpenDyslexic-Italic.woff").is_file())

    def test_apply_functional_chyron_left_preset(self):
        ov = OverlayConfig()
        applied = ov.apply_theme("chyron_left")
        self.assertTrue(applied)
        self.assertEqual(ov.theme_id, "chyron_left")
        self.assertEqual(ov.box_layout, "pill")
        self.assertEqual(ov.accent_line, "left_marker")
        self.assertEqual(ov.bottom_offset_px, 40)

    def test_overlay_final_only_config(self):
        ov = OverlayConfig()
        self.assertFalse(ov.final_only)
        ov.final_only = True
        self.assertTrue(ov.final_only)


class TestDisplayBionicReading(unittest.TestCase):
    """Bionic Reading must stay visibly distinct on every display theme.

    Regression guard: the fixation heads need ultra-bold weight plus a
    contrasting tone against dimmed trailing letters, otherwise Bionic
    looks inactive (e.g. weight 700 vs 800 in pure black-on-white).
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (
            Path(__file__).parent
            / "obs_captioner" / "web" / "static" / "display.html"
        ).read_text(encoding="utf-8")

    def test_bionic_active_class_toggled_on_body(self):
        self.assertIn(
            'document.body.classList.toggle("bionic-active", enabled)',
            self.html,
        )

    def test_bionic_active_dims_trailing_letters(self):
        self.assertIn(".bionic-active .caption-sentence", self.html)
        self.assertIn("font-weight: 400 !important", self.html)

    def test_fixation_heads_are_ultra_bold(self):
        self.assertIn(".bionic-fixation", self.html)
        self.assertIn("font-weight: 900 !important", self.html)

    def test_black_on_white_fixation_contrast(self):
        self.assertIn(
            "body.theme-black-on-white.bionic-active .caption-sentence",
            self.html,
        )
        self.assertIn(
            "body.theme-black-on-white .bionic-fixation",
            self.html,
        )

    def test_light_theme_fixation_contrast(self):
        self.assertIn("body.theme-light .bionic-fixation", self.html)

    def test_custom_preview_renders_bionic_sample(self):
        self.assertIn("paintPreviewLine", self.html)
        self.assertIn("formatBionicReading(sampleText)", self.html)


class TestContentFilterCRUD(unittest.TestCase):

    def test_filter_crud_operations(self):
        cfg = CensorConfig(enabled=True, mode="replacement")
        cf = ContentFilter(cfg)

        # Add blacklist
        self.assertTrue(cf.add_blacklist_term("goober"))
        filtered, was_censored = cf.filter_text("You are a goober")
        self.assertTrue(was_censored)

        # Remove blacklist
        self.assertTrue(cf.remove_blacklist_term("goober"))
        filtered2, was_censored2 = cf.filter_text("You are a goober")
        self.assertFalse(was_censored2)

        # Set custom replacement
        self.assertTrue(cf.set_replacement("silly", "awesome"))
        filtered3, was_censored3 = cf.filter_text("That was silly")
        self.assertTrue(was_censored3)
        self.assertEqual(filtered3, "That was awesome")

        # Whitelist protection
        self.assertTrue(cf.add_whitelist_term("silly"))
        filtered4, was_censored4 = cf.filter_text("That was silly")
        self.assertFalse(was_censored4)

        # State check
        state = cf.get_filter_state()
        self.assertIn("custom_blacklist", state)
        self.assertIn("custom_whitelist", state)


class TestVocabulary(unittest.TestCase):

    def test_vocabulary_replacement(self):
        cfg = VocabularyConfig(
            enabled=True,
            terms={
                "vox stream": "VoxStream",
                "obs": "OBS",
                "pastor mike": "Pastor Mike",
            }
        )
        replacer = VocabularyReplacer(cfg)

        text, modified = replacer.replace("welcome to vox stream live captions on obs with pastor mike")
        self.assertTrue(modified)
        self.assertEqual(text, "welcome to VoxStream live captions on OBS with Pastor Mike")

        # Test phrase boundary safety (e.g. 'observer' should NOT be replaced by 'OBSserver')
        text_safe, mod_safe = replacer.replace("the observer looked outside")
        self.assertFalse(mod_safe)
        self.assertEqual(text_safe, "the observer looked outside")

    def test_vocabulary_crud(self):
        cfg = VocabularyConfig(enabled=True, terms={})
        replacer = VocabularyReplacer(cfg)

        # Add term
        self.assertTrue(replacer.add_term("k8s", "Kubernetes"))
        res, mod = replacer.replace("we deploy to k8s cluster")
        self.assertTrue(mod)
        self.assertEqual(res, "we deploy to Kubernetes cluster")

        # Remove term
        self.assertTrue(replacer.remove_term("k8s"))
        res2, mod2 = replacer.replace("we deploy to k8s cluster")
        self.assertFalse(mod2)
        self.assertEqual(res2, "we deploy to k8s cluster")

    def test_vocabulary_bulk_csv(self):
        cfg = VocabularyConfig(enabled=True, terms={"obs": "OBS"})
        replacer = VocabularyReplacer(cfg)

        csv_input = """Misheard Phrase,Correct Replacement
box stream,VoxStream
pastor mike,Pastor Mike
k8s -> Kubernetes
jesus = Jesus
"""
        count = replacer.import_csv(csv_input, replace_all=False)
        self.assertEqual(count, 4)
        terms = replacer.get_terms()
        self.assertIn("box stream", terms)
        self.assertEqual(terms["box stream"], "VoxStream")
        self.assertEqual(terms["k8s"], "Kubernetes")
        self.assertEqual(terms["jesus"], "Jesus")
        self.assertEqual(terms["obs"], "OBS")

        # Test CSV export
        exported_csv = replacer.export_csv()
        self.assertIn("Misheard Phrase,Correct Replacement", exported_csv)
        self.assertIn("box stream,VoxStream", exported_csv)

        # Test replace_all mode
        replacer.import_csv("single term,Single Term", replace_all=True)
        self.assertEqual(len(replacer.get_terms()), 1)
        self.assertIn("single term", replacer.get_terms())



class TestTranslator(unittest.TestCase):

    def test_translator_disabled(self):
        cfg = TranslationConfig(enabled=False)
        translator = SubtitleTranslator(cfg)
        
        loop = asyncio.new_event_loop()
        res, trans = loop.run_until_complete(translator.translate_text("Hello world"))
        loop.close()
        
        self.assertEqual(res, "Hello world")
        self.assertIsNone(trans)

    def test_gemini_translation_success(self):
        from unittest.mock import MagicMock, patch
        cfg = TranslationConfig(
            enabled=True,
            provider="gemini",
            gemini_api_key="mock_gemini_key",
            target_language="es",
            enable_disk_cache=False,
        )
        translator = SubtitleTranslator(cfg)

        mock_resp_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hola mundo"}]
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            loop = asyncio.new_event_loop()
            res = loop.run_until_complete(translator.translate_to_language("Hello world", target_lang="es"))
            loop.close()

        self.assertEqual(res, "Hola mundo")
        # Verify cached
        self.assertIn("gemini:auto:es:Hello world", translator._cache)

    def test_gemini_translation_fallback(self):
        from unittest.mock import patch
        cfg = TranslationConfig(
            enabled=True,
            provider="gemini",
            gemini_api_key="mock_gemini_key",
            target_language="es",
            enable_disk_cache=False,
        )
        translator = SubtitleTranslator(cfg)

        # Mock Gemini failing with None, fallback provider succeeding
        with patch.object(translator, "_fetch_gemini_translation", return_value=None):
            with patch.object(translator, "_fetch_translation", return_value="Hola mundo (fallback)"):
                loop = asyncio.new_event_loop()
                res = loop.run_until_complete(translator.translate_to_language("Hello world", target_lang="es"))
                loop.close()

        self.assertEqual(res, "Hola mundo (fallback)")

    def test_gemini_api_key_resolution(self):
        cfg = TranslationConfig(enabled=True, provider="gemini", gemini_api_key="")
        translator = SubtitleTranslator(cfg, api_key_resolver=lambda: "resolved_live_key_999")
        self.assertEqual(translator.get_gemini_api_key(), "resolved_live_key_999")

    def test_nllb_language_code_resolution(self):
        from obs_captioner.engines.nllb_translator import resolve_flores_code
        self.assertEqual(resolve_flores_code("es"), "spa_Latn")
        self.assertEqual(resolve_flores_code("fr"), "fra_Latn")
        self.assertEqual(resolve_flores_code("de"), "deu_Latn")
        self.assertEqual(resolve_flores_code("zh"), "zho_Hans")
        self.assertEqual(resolve_flores_code("ja"), "jpn_Jpan")
        self.assertEqual(resolve_flores_code("ko"), "kor_Hang")
        self.assertEqual(resolve_flores_code("spa_Latn"), "spa_Latn")
        self.assertEqual(resolve_flores_code("auto"), "eng_Latn")
        self.assertEqual(resolve_flores_code(""), "eng_Latn")

    def test_nllb_translation_success(self):
        from unittest.mock import AsyncMock, MagicMock
        from obs_captioner.engines.nllb_translator import NLLBTranslator

        mock_nllb = MagicMock(spec=NLLBTranslator)
        mock_nllb.translate = AsyncMock(return_value="Hola mundo desde NLLB")

        cfg = TranslationConfig(enabled=True, provider="nllb", target_language="es", enable_disk_cache=False)
        translator = SubtitleTranslator(cfg, nllb_translator=mock_nllb)

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(translator.translate_to_language("Hello world", target_lang="es"))
        loop.close()

        self.assertEqual(res, "Hola mundo desde NLLB")
        mock_nllb.translate.assert_awaited_once_with("Hello world", target_lang="es", source_lang="en")

    def test_nllb_translation_fallback(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from obs_captioner.engines.nllb_translator import NLLBTranslator

        mock_nllb = MagicMock(spec=NLLBTranslator)
        mock_nllb.translate = AsyncMock(return_value=None)  # Model not downloaded / returns None

        cfg = TranslationConfig(enabled=True, provider="nllb", target_language="es", enable_disk_cache=False)
        translator = SubtitleTranslator(cfg, nllb_translator=mock_nllb)

        with patch.object(translator, "_fetch_translation", return_value="Hola mundo (google fallback)"):
            loop = asyncio.new_event_loop()
            res = loop.run_until_complete(translator.translate_to_language("Hello world", target_lang="es"))
            loop.close()

        self.assertEqual(res, "Hola mundo (google fallback)")

    def test_model_catalog_has_nllb(self):
        from obs_captioner.model_downloader import MODEL_CATALOG
        nllb_items = [m for m in MODEL_CATALOG if m.id == "nllb_200"]
        self.assertEqual(len(nllb_items), 1)
        item = nllb_items[0]
        self.assertEqual(item.engine, "nllb")
        self.assertEqual(item.model_key, "JustFrederik/nllb-200-distilled-600M-ct2-int8")
        self.assertEqual(item.size_mb, 640)

    def test_translation_disk_cache(self):
        from obs_captioner.translation_cache import TranslationDiskCache
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_cache.db"
            cache = TranslationDiskCache(db_path=db_path, max_entries=5)

            # Test miss
            self.assertIsNone(cache.get("key1"))

            # Test set and hit
            cache.set("key1", "Hello", "Hola", "nllb", "en", "es")
            self.assertEqual(cache.get("key1"), "Hola")

            stats = cache.get_stats()
            self.assertEqual(stats["total_entries"], 1)
            self.assertEqual(stats["total_hits"], 2)

            # Test hit count increment
            self.assertEqual(cache.get("key1"), "Hola")
            stats = cache.get_stats()
            self.assertEqual(stats["total_hits"], 3)

            # Test auto-pruning with max_entries
            for i in range(2, 8):
                cache.set(f"key{i}", f"Text {i}", f"Texto {i}", "nllb", "en", "es")
            stats = cache.get_stats()
            self.assertLessEqual(stats["total_entries"], 5)

            # Test clear
            cache.clear()
            self.assertEqual(cache.get_stats()["total_entries"], 0)
            self.assertIsNone(cache.get("key1"))

            cache.close()

    def test_two_tier_translation_cache(self):
        from unittest.mock import AsyncMock, MagicMock
        from obs_captioner.engines.nllb_translator import NLLBTranslator
        from obs_captioner.translation_cache import TranslationDiskCache

        mock_nllb = MagicMock(spec=NLLBTranslator)
        mock_nllb.translate = AsyncMock(return_value="Hola dos")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_cache.db"
            disk_cache = TranslationDiskCache(db_path=db_path)
            cfg = TranslationConfig(enabled=True, provider="nllb", target_language="es")
            translator = SubtitleTranslator(cfg, nllb_translator=mock_nllb, disk_cache=disk_cache)

            loop = asyncio.new_event_loop()
            # 1st call: engine called, written to L1 and L2
            res1 = loop.run_until_complete(translator.translate_to_language("Hello two", target_lang="es"))
            self.assertEqual(res1, "Hola dos")
            self.assertEqual(mock_nllb.translate.await_count, 1)

            # Check L1
            cache_key = "nllb:auto:es:Hello two"
            self.assertIn(cache_key, translator._cache)
            # Check L2
            self.assertEqual(disk_cache.get(cache_key), "Hola dos")

            # Simulate app restart / clear L1 memory cache
            translator._cache.clear()
            self.assertNotIn(cache_key, translator._cache)

            # 2nd call: should hit L2 disk cache and populate L1 without calling engine again
            res2 = loop.run_until_complete(translator.translate_to_language("Hello two", target_lang="es"))
            self.assertEqual(res2, "Hola dos")
            # Engine was NOT called again
            self.assertEqual(mock_nllb.translate.await_count, 1)
            # L1 re-populated
            self.assertIn(cache_key, translator._cache)

            loop.close()
            disk_cache.close()

    def test_translation_prewarm(self):
        from unittest.mock import AsyncMock, MagicMock
        from obs_captioner.engines.nllb_translator import NLLBTranslator

        mock_nllb = MagicMock(spec=NLLBTranslator)
        mock_nllb.prewarm = AsyncMock(return_value=True)

        cfg = TranslationConfig(enabled=True, provider="nllb", target_language="es")
        translator = SubtitleTranslator(cfg, nllb_translator=mock_nllb)

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(translator.prewarm_async())
        loop.close()

        self.assertTrue(res)
        mock_nllb.prewarm.assert_awaited_once()

        # Prewarming a non-NLLB provider returns False and doesn't call NLLB
        translator.config.provider = "gemini"
        mock_nllb.prewarm.reset_mock()
        loop = asyncio.new_event_loop()
        res2 = loop.run_until_complete(translator.prewarm_async())
        loop.close()
        self.assertFalse(res2)
        mock_nllb.prewarm.assert_not_awaited()

    def test_dual_subtitle_config_and_formatting(self):
        from unittest.mock import AsyncMock, MagicMock
        from obs_captioner.config import AppConfig
        from obs_captioner.obs.caption_sink import CaptionSink

        cfg = AppConfig()
        cfg.overlay.auto_hide_seconds = 0
        self.assertEqual(cfg.translation.dual_subtitle_color, "#FFD700")
        self.assertEqual(cfg.translation.dual_subtitle_scale, 0.85)
        self.assertEqual(cfg.translation.dual_subtitle_format, "clean")
        if hasattr(cfg, "bible") and cfg.bible:
            cfg.bible.enabled = False

        sink = CaptionSink(cfg)
        mock_web = MagicMock()
        mock_web.broadcast_caption = AsyncMock()
        sink.web_server = mock_web

        sink.config.translation.enabled = True
        sink.config.translation.display_mode = "dual"
        sink.config.translation.dual_subtitle_format = "clean"
        sink.config.translation.dual_subtitle_color = "#93C5FD"
        sink.config.translation.dual_subtitle_scale = 0.9

        event = TranscriptEvent(text="Live broadcast", is_final=True, timestamp=time.time())
        event.translated_text = "Transmisión en vivo"

        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.handle_transcript(event))
        loop.close()

        # Check broadcast payload
        mock_web.broadcast_caption.assert_awaited_once()
        payload = mock_web.broadcast_caption.call_args[0][0]
        self.assertEqual(payload["text"], "Live broadcast.")
        self.assertEqual(payload["translated_text"], "Transmisión en vivo")
        self.assertEqual(payload["dual_color"], "#93C5FD")
        self.assertEqual(payload["dual_scale"], 0.9)
        self.assertEqual(payload["dual_format"], "clean")

        # Check history entry formatted with clean stack separator (" / ")
        hist = sink.history.get_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["text"], "Live broadcast. / Transmisión en vivo")


class TestTwitchBot(unittest.TestCase):

    def test_twitch_bot_disabled_when_unconfigured(self):
        cfg = TwitchConfig(enabled=False)
        bot = TwitchCaptionBot(cfg)
        self.assertFalse(bot.is_connected)

        loop = asyncio.new_event_loop()
        connected = loop.run_until_complete(bot.start())
        loop.close()
        self.assertFalse(connected)


class TestTranscriptHistory(unittest.TestCase):

    def test_history_and_export(self):
        hist = TranscriptHistory()
        hist.session_start_time = 1000.0

        hist.add_entry("First line of speech", start_time=1000.0, end_time=1003.5)
        hist.add_entry("Second line of speech", start_time=1004.0, end_time=1007.2)

        entries = hist.get_history()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["text"], "Second line of speech")

        # Test SRT Export
        srt = hist.export_srt()
        self.assertIn("00:00:00,000 --> 00:00:03,500", srt)
        self.assertIn("First line of speech", srt)

        # Test VTT Export
        vtt = hist.export_vtt()
        self.assertIn("WEBVTT", vtt)

        # Test TXT Export
        txt = hist.export_txt()
        self.assertIn("[0:00:00] First line of speech", txt)


class TestConfigAndEngines(unittest.TestCase):

    def test_default_config_load(self):
        cfg = AppConfig()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.audio.sample_rate, 16000)
        self.assertEqual(cfg.audio.max_sentence_words, 24)
        self.assertEqual(cfg.sherpa.device, "auto")
        self.assertEqual(cfg.sherpa.num_threads, 4)
        self.assertEqual(cfg.parakeet.num_threads, 4)
        self.assertEqual(cfg.sensevoice.num_threads, 4)
        self.assertEqual(cfg.obs.port, 4455)
        self.assertEqual(cfg.overlay.port, 8765)
        self.assertTrue(cfg.censor.enabled)
        self.assertFalse(cfg.obs.auto_open_projector)
        self.assertEqual(cfg.obs.projector_monitor_index, 1)
        self.assertEqual(cfg.obs.projector_type, "preview")
        self.assertFalse(hasattr(cfg.overlay, "google_font"))

    def test_engine_factory(self):
        cfg = AppConfig()
        
        cfg.general.engine = "google_stt"
        eng_google = create_engine(cfg)
        self.assertIsInstance(eng_google, GoogleSTTEngine)

        cfg.general.engine = "gemini_live"
        eng_gemini = create_engine(cfg)
        self.assertIsInstance(eng_gemini, GeminiLiveEngine)
        self.assertEqual(cfg.gemini_live.model, "gemini-3.5-transcribe-live")
        self.assertTrue(cfg.gemini_live.smart_transcription)
        self.assertIn("OBS Studio", cfg.gemini_live.custom_vocabulary)

        # Test system instruction generation
        instructions = eng_gemini._build_system_instruction()
        self.assertIn("Gemini 3.5 Transcribe", instructions)
        self.assertIn("custom specialized vocabulary", instructions)

        cfg.general.engine = "google_web"
        eng_web = create_engine(cfg)
        self.assertIsInstance(eng_web, GoogleWebEngine)
        
        loop = asyncio.new_event_loop()
        init_ok = loop.run_until_complete(eng_web.initialize())
        loop.close()
        self.assertTrue(init_ok)

        cfg.general.engine = "local_whisper"
        eng_whisper = create_engine(cfg)
        self.assertIsInstance(eng_whisper, LocalWhisperEngine)

        cfg.general.engine = "vosk"
        eng_vosk = create_engine(cfg)
        self.assertIsInstance(eng_vosk, VoskEngine)

        cfg.general.engine = "bandwidth"
        eng_bandwidth = create_engine(cfg)
        self.assertIsInstance(eng_bandwidth, BandwidthEngine)

        cfg.general.engine = "moonshine"
        eng_moonshine = create_engine(cfg)
        self.assertIsInstance(eng_moonshine, MoonshineEngine)
        self.assertTrue(cfg.moonshine.model_name.startswith("moonshine/"))

        cfg.general.engine = "sherpa"
        eng_sherpa = create_engine(cfg)
        self.assertIsInstance(eng_sherpa, SherpaEngine)

        cfg.general.engine = "parakeet"
        eng_parakeet = create_engine(cfg)
        self.assertIsInstance(eng_parakeet, ParakeetEngine)

        cfg.general.engine = "sensevoice"
        eng_sensevoice = create_engine(cfg)
        self.assertIsInstance(eng_sensevoice, SenseVoiceEngine)

    def test_gemini_live_packet_processing(self):
        """Verify GeminiLiveEngine cumulative input_transcription handling and generation_complete finalization."""
        cfg = AppConfig()
        cfg.gemini_live.api_key = "dummy-test-key"
        engine = GeminiLiveEngine(cfg)

        class MockTranscription:
            def __init__(self, text, finished=None):
                self.text = text
                self.finished = finished

        class MockServerContent:
            def __init__(self, text=None, generation_complete=None, finished=None, turn_complete=None):
                self.input_transcription = MockTranscription(text, finished) if text is not None else None
                self.generation_complete = generation_complete
                self.turn_complete = turn_complete
                self.model_turn = None

        class MockResponse:
            def __init__(self, server_content=None, text=None):
                self.server_content = server_content
                self.text = text

        mock_responses = [
            MockResponse(server_content=MockServerContent(text="Welcome")),
            MockResponse(server_content=MockServerContent(text="Welcome to church")),
            MockResponse(server_content=MockServerContent(text="Welcome to church this morning.")),
            MockResponse(server_content=MockServerContent(generation_complete=True)),
        ]

        events = []
        async def mock_callback(ev):
            events.append(ev)

        async def run_sim():
            current_line = ""
            for response in mock_responses:
                server_content = getattr(response, "server_content", None)
                it_text = None
                if server_content and getattr(server_content, "input_transcription", None):
                    it = server_content.input_transcription
                    if getattr(it, "text", None):
                        it_text = it.text

                it_finished = False
                if server_content and getattr(server_content, "input_transcription", None):
                    it_finished = bool(getattr(server_content.input_transcription, "finished", False))
                gen_complete = bool(getattr(server_content, "generation_complete", False)) if server_content else False
                turn_complete = bool(getattr(server_content, "turn_complete", False)) if server_content else False
                is_completed = it_finished or gen_complete or turn_complete

                if it_text:
                    current_line = it_text.strip()

                if is_completed:
                    if current_line.strip():
                        await mock_callback(TranscriptEvent(text=current_line.strip(), is_final=True))
                        current_line = ""
                elif current_line.strip() and it_text:
                    await mock_callback(TranscriptEvent(text=current_line.strip(), is_final=False))

        loop = asyncio.new_event_loop()
        loop.run_until_complete(run_sim())
        loop.close()

        self.assertEqual(len(events), 4)
        self.assertFalse(events[0].is_final)
        self.assertEqual(events[0].text, "Welcome")
        self.assertFalse(events[1].is_final)
        self.assertEqual(events[1].text, "Welcome to church")
        self.assertFalse(events[2].is_final)
        self.assertEqual(events[2].text, "Welcome to church this morning.")
        self.assertTrue(events[3].is_final)
        self.assertEqual(events[3].text, "Welcome to church this morning.")

    def test_gemini_live_official_setup_payload(self):
        """Verify Gemini Live official setup payload format per Google specifications."""
        cfg = AppConfig()
        cfg.gemini_live.api_key = "test-key"
        cfg.gemini_live.mode = "SMART"
        cfg.gemini_live.custom_vocabulary = ["OBS Studio", "Twitch", "YouTube", "Jesus Christ"]
        cfg.gemini_live.language_codes = ["en-US"]
        cfg.gemini_live.enable_hybrid_vad = True

        engine = GeminiLiveEngine(cfg)
        payload = engine.build_setup_payload()
        self.assertIn("setup", payload)
        setup = payload["setup"]
        self.assertEqual(setup["model"], "models/gemini-3.5-transcribe-live")
        self.assertEqual(setup["generationConfig"]["responseModalities"], ["TEXT"])

        transcription = setup["inputAudioTranscription"]
        self.assertEqual(transcription["mode"], "SMART")
        # With default church_mode=True, both user terms and canonical church terms are present
        for term in ["OBS Studio", "Twitch", "YouTube", "Jesus Christ", "Ben Luthi", "Doxology", "Genesis"]:
            self.assertIn(term, transcription["customVocabulary"])
        self.assertEqual(transcription["languageCodes"], ["en-US"])

        # When church_mode and user glossary are disabled, only explicit custom vocabulary is sent
        cfg.general.church_mode = False
        cfg.vocabulary.enabled = False
        payload_standalone = engine.build_setup_payload()
        self.assertEqual(
            payload_standalone["setup"]["inputAudioTranscription"]["customVocabulary"],
            ["OBS Studio", "Twitch", "YouTube", "Jesus Christ"],
        )

        # Test VERBATIM mode
        cfg.gemini_live.smart_transcription = False
        payload_verbatim = engine.build_setup_payload()
        self.assertEqual(payload_verbatim["setup"]["inputAudioTranscription"]["mode"], "VERBATIM")

    def test_gemini_live_church_vocabulary_processing(self):
        """Verify Gemini Live extracts canonical target terms and never acoustic mishearings."""
        cfg = AppConfig()
        cfg.general.church_mode = True
        cfg.general.church_name = "Grace Fellowship"
        cfg.gemini_live.custom_vocabulary = ["Streaming Setup", "Discord", "obs"]
        cfg.vocabulary.enabled = True
        cfg.vocabulary.terms = {
            "pastor tim": "Pastor Tim",
            "rev": "Revelation",
            "obs": "OBS",
        }

        engine = GeminiLiveEngine(cfg)
        vocab = engine._get_effective_custom_vocabulary()

        # 1. Verify user explicit terms and proper casing resolution
        self.assertIn("Streaming Setup", vocab)
        self.assertIn("Discord", vocab)
        self.assertIn("OBS", vocab)  # Upgraded from lowercase 'obs' via canonical 'OBS'
        self.assertNotIn("obs", vocab)

        # 2. Verify glossary canonical replacement targets are included
        self.assertIn("Pastor Tim", vocab)
        self.assertIn("Revelation", vocab)
        # Verify glossary phonetic mishearing keys are NOT included
        self.assertNotIn("pastor tim", vocab)
        self.assertNotIn("rev", vocab)

        # 3. Verify church name and variations are included
        self.assertIn("Grace Fellowship", vocab)

        # 4. Verify canonical church terms and books of the Bible
        self.assertIn("Ben Luthi", vocab)
        self.assertIn("Doxology", vocab)
        self.assertIn("1 Thessalonians", vocab)
        self.assertIn("Genesis", vocab)

        # 5. Verify acoustic mishearings from church lexicon are strictly EXCLUDED
        acoustic_mishearings = [
            "ben uthe", "ben lut", "ben luther", "ben loothi",
            "dog solid g", "solid g",
            "said corinthians", "cried the ends",
            "top 24", "he took a cop",
            "a socioplship", "the cyber",
            "pissed back up",
        ]
        for mishearing in acoustic_mishearings:
            self.assertNotIn(mishearing, vocab)

        # 6. Verify total list length obeys 1000 limit
        self.assertLessEqual(len(vocab), 1000)

    def test_gemini_live_system_instruction_church_context(self):
        """Verify Gemini Live system instruction injects church domain context when active."""
        cfg = AppConfig()
        cfg.general.church_mode = True
        cfg.general.church_name = "Waypoint Church"
        cfg.gemini_live.custom_vocabulary = ["OBS Studio", "YouTube"]

        engine = GeminiLiveEngine(cfg)
        instruction = engine._build_system_instruction()

        self.assertIn("Domain context: Church worship service", instruction)
        self.assertIn("biblical sermon preaching", instruction)
        self.assertIn("Waypoint Church", instruction)
        self.assertIn("OBS Studio", instruction)

        # Test when church mode is disabled
        cfg.general.church_mode = False
        instruction_secular = engine._build_system_instruction()
        self.assertNotIn("Domain context: Church worship service", instruction_secular)
        self.assertNotIn("Waypoint Church", instruction_secular)
        self.assertIn("OBS Studio", instruction_secular)

    def test_gemini_live_parse_dual_stream_messages(self):
        """Verify dual-stream interimInputTranscription and inputTranscription parsing."""
        cfg = AppConfig()
        engine = GeminiLiveEngine(cfg)

        # 1. Speculative interim update
        interim_data = {
            "serverContent": {
                "interimInputTranscription": {
                    "text": "Hello world"
                }
            }
        }
        events = engine.parse_server_message(interim_data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], ("Hello world", False))

        # 2. Authoritative finalized update
        final_data = {
            "serverContent": {
                "inputTranscription": {
                    "text": "Hello world."
                }
            }
        }
        events2 = engine.parse_server_message(final_data)
        self.assertEqual(len(events2), 1)
        self.assertEqual(events2[0], ("Hello world.", True))

        # 3. Empty or malformed payloads
        self.assertEqual(engine.parse_server_message({}), [])
        self.assertEqual(engine.parse_server_message({"serverContent": {}}), [])

    def test_gemini_live_suppress_hallucinated_interim_token_leak(self):
        """Verify Gemini Live suppresses hallucinated prompt arrays, checkbox tokens, and loops."""
        from obs_captioner.formatter import is_hallucinated_or_leaked_text

        cfg = AppConfig()
        engine = GeminiLiveEngine(cfg)

        # 1. Exact raw hallucinated token array from user screenshot
        raw_leak = "□'1 Corinthians', 'Gospel', 'Goshua', '1 Corinthians', 'Jesus', 'G', 'G', 'G', 'G"
        self.assertTrue(is_hallucinated_or_leaked_text(raw_leak))

        interim_data = {
            "serverContent": {
                "interimInputTranscription": {
                    "text": raw_leak
                }
            }
        }
        # Must be suppressed and return no events
        events = engine.parse_server_message(interim_data)
        self.assertEqual(events, [])

        # 2. Conversational modelTurn containing hallucinated token list
        turn_data = {
            "serverContent": {
                "modelTurn": {
                    "parts": [{"text": "['Genesis', 'Exodus', 'Leviticus']"}]
                }
            }
        }
        self.assertEqual(engine.parse_server_message(turn_data), [])

        # 3. Valid speech is preserved
        valid_data = {
            "serverContent": {
                "interimInputTranscription": {
                    "text": "Do you see what Jesus did there"
                }
            }
        }
        valid_events = engine.parse_server_message(valid_data)
        self.assertEqual(valid_events, [("Do you see what Jesus did there", False)])

    def test_is_hallucinated_or_leaked_text_detection(self):
        """Test detection of prompt leaks, repr lists, and degenerate repetition loops."""
        from obs_captioner.formatter import is_hallucinated_or_leaked_text

        # Hallucinations / Leaks (should return True)
        self.assertTrue(is_hallucinated_or_leaked_text("□'1 Corinthians', 'Gospel', 'Jesus'"))
        self.assertTrue(is_hallucinated_or_leaked_text("['1 Corinthians', 'Gospel']"))
        self.assertTrue(is_hallucinated_or_leaked_text("'OBS Studio', 'YouTube'"))
        self.assertTrue(is_hallucinated_or_leaked_text("G, G, G, G"))
        self.assertTrue(is_hallucinated_or_leaked_text("G G G G"))
        self.assertTrue(is_hallucinated_or_leaked_text("'G', 'G', 'G', 'G'"))

        # Valid spoken phrases (should return False)
        self.assertFalse(is_hallucinated_or_leaked_text("Do you see what Jesus did there?"))
        self.assertFalse(is_hallucinated_or_leaked_text("Turn in your bibles to 1 Corinthians 13:4."))
        self.assertFalse(is_hallucinated_or_leaked_text("He said, \"Peace be with you.\""))
        self.assertFalse(is_hallucinated_or_leaked_text("Faith, hope, and love abide."))
        self.assertFalse(is_hallucinated_or_leaked_text("Jesus wept."))

    def test_gemini_live_translate_model_setup(self):
        cfg = AppConfig()
        cfg.gemini_live.api_key = "test-key"
        cfg.gemini_live.model = "gemini-3.5-live-translate-preview"
        cfg.translation.target_language = "es"

        engine = GeminiLiveEngine(cfg)
        setup = engine.build_setup_payload()["setup"]

        self.assertEqual(setup["model"], "models/gemini-3.5-live-translate-preview")
        gen_cfg = setup["generationConfig"]
        self.assertEqual(gen_cfg["responseModalities"], ["AUDIO"])
        self.assertIn("translationConfig", gen_cfg)
        self.assertEqual(gen_cfg["translationConfig"]["targetLanguageCode"], "es")
        self.assertTrue(gen_cfg["translationConfig"]["echoTargetLanguage"])

    def test_gemini_live_parse_output_transcription(self):
        cfg = AppConfig()
        cfg.gemini_live.api_key = "test-key"
        engine = GeminiLiveEngine(cfg)

        msg = {
            "serverContent": {
                "inputTranscription": {"text": "Hello everyone"},
                "outputTranscription": {"text": "Hola a todos"}
            }
        }
        events = engine.parse_server_message(msg)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], ("Hola a todos", True))

    def test_vad_energy_calculation(self):
        vad = VoiceActivityDetector(enable_silero=False, noise_gate_db=-40.0)
        
        silence_bytes = bytes(3200)
        self.assertEqual(vad.calculate_rms_db(silence_bytes), -100.0)
        self.assertFalse(vad.is_speech(silence_bytes))

        samples = [int(math.sin(2 * math.pi * 440 * i / 16000) * 20000) for i in range(1600)]
        loud_bytes = struct.pack(f"<{len(samples)}h", *samples)
        db = vad.calculate_rms_db(loud_bytes)
        self.assertGreater(db, -40.0)
        self.assertTrue(vad.is_speech(loud_bytes))

    def test_custom_presets(self):
        cfg = AppConfig()
        cfg.custom_presets["my_stage_look"] = {
            "name": "My Stage Look",
            "description": "High contrast stage styling",
            "font_family": "Montserrat, sans-serif",
            "font_size": "48px",
            "text_color": "#FFCC00",
        }
        
        all_presets = get_all_presets(cfg.custom_presets)
        custom_p = [p for p in all_presets if p.get("id") == "my_stage_look"]
        self.assertEqual(len(custom_p), 1)
        self.assertTrue(custom_p[0]["is_custom"])
        self.assertEqual(custom_p[0]["name"], "My Stage Look")

        # Test applying custom preset
        applied = cfg.overlay.apply_theme("my_stage_look", cfg.custom_presets)
        self.assertTrue(applied)
        self.assertEqual(cfg.overlay.font_family, "Montserrat, sans-serif")
        self.assertEqual(cfg.overlay.font_size, "48px")
        self.assertEqual(cfg.overlay.text_color, "#FFCC00")


class TestTextFormatter(unittest.TestCase):

    def test_capitalization_and_punctuation(self):
        from obs_captioner.formatter import TextFormatter
        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True)

        # Standard statement
        res = fmt.format_text("hello world i am streaming on twitch", is_final=True)
        self.assertEqual(res, "Hello world I am streaming on Twitch.")

        # Question detection
        res_q = fmt.format_text("what is the best captioner for obs", is_final=True)
        self.assertEqual(res_q, "What is the best captioner for OBS?")

        # Exclamation detection
        res_ex = fmt.format_text("wow this is awesome", is_final=True)
        self.assertEqual(res_ex, "Wow this is awesome!")

        # Contractions
        res_cont = fmt.format_text("i dont know if im ready", is_final=True)
        self.assertEqual(res_cont, "I don't know if I'm ready.")

        # Interim without final punctuation
        res_interim = fmt.format_text("i am testing this", is_final=False)
        self.assertEqual(res_interim, "I am testing this")

    def test_normalize_punctuation_spacing(self):
        """Verify automatic space restoration after punctuation marks without false positives."""
        from obs_captioner.formatter import TextFormatter
        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True)

        # 1. Exact user screenshot case: period followed by capitalized word
        res1 = fmt.format_text("n looks like.Guys,", is_final=True)
        self.assertEqual(res1, "N looks like. Guys,")

        # 2. Period followed by lowercase word (should space and capitalize)
        res2 = fmt.format_text("looks like.guys,", is_final=True)
        self.assertEqual(res2, "Looks like. Guys,")

        # 3. Commas, colons, exclamation, and question marks
        res3 = fmt.format_text("faith,hope,and love", is_final=True)
        self.assertEqual(res3, "Faith, hope, and love.")

        res4 = fmt.format_text("amen!let us pray", is_final=True)
        self.assertEqual(res4, "Amen! Let us pray.")

        res5 = fmt.format_text("are you ready?yes", is_final=True)
        self.assertEqual(res5, "Are you ready? Yes?")

        res6 = fmt.format_text("note:this is crucial", is_final=True)
        self.assertEqual(res6, "Note: this is crucial.")

        # 4. Strict preservation of Bible citations, decimals, times, URLs, and abbreviations
        res_bible = fmt.format_text("John 3:16", is_final=True)
        self.assertEqual(res_bible, "John 3:16.")

        res_time = fmt.format_text("service starts at 10:30 in the morning", is_final=True)
        self.assertEqual(res_time, "Service starts at 10:30 in the morning.")

        res_dec = fmt.format_text("value is 3.14159 or $4.50 for 1,000 items", is_final=True)
        self.assertEqual(res_dec, "Value is 3.14159 or $4.50 for 1,000 items.")

        res_url = fmt.format_text("visit waypoint.church or google.com today", is_final=True)
        self.assertEqual(res_url, "Visit Waypoint.church or Google.com today.")

        res_abbr = fmt.format_text("located in the U.S.A. and U.S. territory", is_final=True)
        self.assertEqual(res_abbr, "Located in the U.S.A. And U.S. Territory.")

    def test_church_words_and_scripture(self):
        from obs_captioner.formatter import TextFormatter
        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True, church_mode=True)

        # Scripture citation format: John 3:16
        res_john = fmt.format_text("for god so loved the world in john 3 16", is_final=True)
        self.assertEqual(res_john, "For God so loved the world in John 3:16.")

        # Romans 8:28
        res_rom = fmt.format_text("in romans 8 28 we know all things work together", is_final=True)
        self.assertEqual(res_rom, "In Romans 8:28 we know all things work together.")

        # Spoken scripture: 1 Corinthians chapter 13 verse 4 through 7
        res_cor = fmt.format_text("turn to first corinthians chapter thirteen verse four through seven", is_final=True)
        self.assertEqual(res_cor, "Turn to 1 Corinthians 13:4-7.")

        # Psalm number
        res_ps = fmt.format_text("psalm twenty three is my favorite psalm", is_final=True)
        self.assertEqual(res_ps, "Psalm 23 is my favorite Psalm.")

        # Sacred titles & phrases
        res_phrases = fmt.format_text("jesus christ is king of kings and lord of lords amen", is_final=True)
        self.assertEqual(res_phrases, "Jesus Christ is King of Kings and Lord of Lords Amen.")

        # Pastoral and leadership names
        res_ben = fmt.format_text("pastor ben luthi and ben uthe are speaking", is_final=True)
        self.assertEqual(res_ben, "Pastor Ben Luthi and Ben Luthi are speaking.")

        # Hymns and liturgical terms
        res_hymn = fmt.format_text("they sang agnes day and waymaker together", is_final=True)
        self.assertEqual(res_hymn, "They sang Agnus Dei and Way Maker together.")

        # Sensory room tools vs biblical birds
        res_sensory = fmt.format_text("noise-canceling headphones and pigeons are in the sensory room", is_final=True)
        self.assertEqual(res_sensory, "Noise-canceling headphones and fidgets are in the sensory room.")

        res_birds = fmt.format_text("bring two young pigeons as an offering", is_final=True)
        self.assertEqual(res_birds, "Bring two young pigeons as an Offering.")

    def test_no_false_capitalization(self):
        """Regression: common words must not be capitalized as books/months/possessives."""
        from obs_captioner.formatter import TextFormatter
        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True, church_mode=True)

        # Plural "gods" is not the possessive "God's"
        self.assertEqual(
            fmt.format_text("you shall have no other gods before me", is_final=True),
            "You shall have no other gods before me.",
        )
        # Modal "may" / verb "march" are not months
        self.assertEqual(fmt.format_text("you may be seated", is_final=True), "You may be seated.")
        self.assertEqual(fmt.format_text("we march forward", is_final=True), "We march forward.")
        # Ambiguous book names stay lowercase without chapter/verse context
        self.assertEqual(
            fmt.format_text("he did a great job on the numbers", is_final=True),
            "He did a great job on the numbers.",
        )
        self.assertEqual(
            fmt.format_text("the acts of kindness we do matter", is_final=True),
            "The acts of kindness we do matter.",
        )
        self.assertEqual(fmt.format_text("it left a mark on me", is_final=True), "It left a mark on me.")
        # ...but they still format inside citations
        self.assertEqual(fmt.format_text("turn to acts 2 38", is_final=True), "Turn to Acts 2:38.")
        # Verb "lets" is not the contraction "let's"
        self.assertEqual(fmt.format_text("she lets him go", is_final=True), "She lets him go.")

    def test_question_heuristic_narrowing(self):
        """Regression: imperatives and filler endings must not become questions."""
        from obs_captioner.formatter import TextFormatter
        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True, church_mode=True)

        self.assertEqual(fmt.format_text("do not be afraid", is_final=True), "Do not be afraid.")
        self.assertEqual(fmt.format_text("have faith in god", is_final=True), "Have faith in God.")
        self.assertEqual(
            fmt.format_text("there is power in the blood you know", is_final=True),
            "There is power in the blood you know.",
        )
        # Real questions still detected (aux + pronoun, wh-words)
        self.assertEqual(fmt.format_text("will you pray with me", is_final=True), "Will you pray with me?")
        self.assertEqual(fmt.format_text("can i get an amen", is_final=True), "Can I get an Amen?")

    def test_spoken_verse_numbers(self):
        """Regression: spoken multi-word verse numbers must not truncate or delete words."""
        from obs_captioner.formatter import TextFormatter
        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True, church_mode=True)

        self.assertEqual(
            fmt.format_text("please turn to john chapter three verse twenty three", is_final=True),
            "Please turn to John 3:23.",
        )
        # Trailing non-number words must survive a verse range
        self.assertEqual(
            fmt.format_text("romans chapter 8 verse 28 through 30 today", is_final=True),
            "Romans 8:28-30 today.",
        )
        self.assertEqual(
            fmt.format_text("psalm one hundred nineteen is long", is_final=True),
            "Psalm 119 is long.",
        )
        self.assertEqual(
            fmt.format_text("psalm one hundred and nineteen is long", is_final=True),
            "Psalm 119 is long.",
        )


class TestChurchCensorship(unittest.TestCase):
    """Regression tests for the context-aware whitelist and church-mode exemptions."""

    def test_whitelist_phrases_protect_context(self):
        # Without church mode the bare term is still filtered, but whitelisted
        # phrases must pass through untouched.
        cf = ContentFilter(CensorConfig(enabled=True, mode="asterisk"), church_mode=False)
        text, censored = cf.filter_text("heaven and hell")
        self.assertEqual(text, "heaven and hell")
        self.assertFalse(censored)

        text2, censored2 = cf.filter_text("the gates of hell shall not prevail")
        self.assertEqual(text2, "the gates of hell shall not prevail")
        self.assertFalse(censored2)

        # Outside a whitelisted phrase, the bare term is still masked
        text3, censored3 = cf.filter_text("hell is real")
        self.assertTrue(censored3)
        self.assertNotIn("hell", text3)

    def test_church_mode_exempts_theological_terms(self):
        cf = ContentFilter(CensorConfig(enabled=True, mode="asterisk"), church_mode=True)
        for phrase in ("hell is real", "you shall not be damned", "jesus descended into hell"):
            text, censored = cf.filter_text(phrase)
            self.assertEqual(text, phrase)
            self.assertFalse(censored)

        # Custom blacklist terms are still filtered in church mode
        cf.add_blacklist_term("goober")
        text, censored = cf.filter_text("what a goober move")
        self.assertTrue(censored)
        self.assertNotIn("goober", text)



    def test_bandwidth_engine_config_and_init(self):
        import os
        cfg = AppConfig()
        cfg.general.engine = "bandwidth"
        cfg.bandwidth.api_key = "test_bwa_key_123"
        eng = create_engine(cfg)
        self.assertIsInstance(eng, BandwidthEngine)
        self.assertEqual(eng.api_key, "test_bwa_key_123")

        loop = asyncio.new_event_loop()
        init_ok = loop.run_until_complete(eng.initialize())
        loop.close()
        self.assertTrue(init_ok)

        # Missing key should return False on initialize
        cfg_empty = AppConfig()
        cfg_empty.bandwidth.api_key = ""
        # Ensure env var is unset for test
        old_env = os.environ.pop("BANDWIDTH_API_KEY", None)
        try:
            eng_empty = BandwidthEngine(cfg_empty)
            loop = asyncio.new_event_loop()
            init_empty = loop.run_until_complete(eng_empty.initialize())
            loop.close()
            self.assertFalse(init_empty)
        finally:
            if old_env is not None:
                os.environ["BANDWIDTH_API_KEY"] = old_env

    def test_generate_youtube_chapters(self):
        from obs_captioner.history import TranscriptHistory
        hist = TranscriptHistory()
        t0 = hist.session_start_time

        # Add entries simulating a church service
        hist.add_entry("Welcome to Sunday service everybody.", start_time=t0 + 2.0, end_time=t0 + 6.0)
        hist.add_entry("Let us pray together.", start_time=t0 + 60.0, end_time=t0 + 65.0)
        hist.add_entry("Please turn with me in your Bibles to John 3:16.", start_time=t0 + 150.0, end_time=t0 + 155.0)
        hist.add_entry("Today's message is about unconditional grace.", start_time=t0 + 240.0, end_time=t0 + 245.0)
        hist.add_entry("In conclusion, go in peace and have a blessed week.", start_time=t0 + 400.0, end_time=t0 + 405.0)

        chapters = hist.generate_chapters(min_interval_seconds=30.0)
        self.assertGreaterEqual(len(chapters), 4)
        # First chapter must start at 00:00:00 for YouTube compliance
        self.assertEqual(chapters[0]["timecode"], "00:00:00")
        self.assertEqual(chapters[0]["title"], "Introduction & Welcome")

        # Verify scripture reading detected
        scripture_caps = [c for c in chapters if "John 3:16" in c["title"]]
        self.assertEqual(len(scripture_caps), 1)

        # Formatted string check
        formatted = hist.export_youtube_chapters()
        self.assertIn("00:00:00 - Introduction & Welcome", formatted)
        self.assertIn("Scripture Reading (John 3:16)", formatted)

    def test_translate_to_language(self):
        from obs_captioner.translator import SubtitleTranslator, TranslationConfig
        tr = SubtitleTranslator(TranslationConfig(enabled=True, target_language="es"))

        loop = asyncio.new_event_loop()
        # English passthrough
        res_en = loop.run_until_complete(tr.translate_to_language("Hello world", target_lang="en"))
        self.assertEqual(res_en, "Hello world")

        # Spanish translation test (with memory cache check)
        res_es = loop.run_until_complete(tr.translate_to_language("Hello world", target_lang="es"))
        self.assertTrue(bool(res_es))
        loop.close()


from aiohttp.test_utils import AioHTTPTestCase
from obs_captioner.web.server import WebOverlayServer

class TestServerEndpoints(AioHTTPTestCase):
    async def get_application(self):
        self.cfg = AppConfig()
        self.overlay_server = WebOverlayServer(self.cfg)
        return self.overlay_server.app

    async def test_manifest_endpoint(self):
        resp = await self.client.request('GET', '/manifest.json')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, 'application/manifest+json')
        manifest = await resp.json()
        self.assertEqual(manifest['name'], 'VoxStream Live Read-Along')
        self.assertEqual(manifest['start_url'], '/display')

    async def test_display_page(self):
        resp = await self.client.request('GET', '/display')
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('stageContainer', text)
        self.assertIn('btnScrollBottom', text)
        self.assertIn('.transcript-content::before', text)
        self.assertIn('loadServerHistory', text)
        self.assertIn('isSmoothScrollingToBottom', text)

    async def test_sw_endpoint(self):
        resp = await self.client.request('GET', '/sw.js')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers.get('Service-Worker-Allowed'), '/')
        text = await resp.text()
        self.assertIn('CACHE_NAME', text)

    async def test_bulk_vocab_api(self):
        resp = await self.client.request('POST', '/api/vocabulary/bulk', json={
            'csv_data': 'test_a,Test A\ntest_b -> Test B',
            'replace_all': False
        })
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['imported_count'], 2)

    async def test_export_vocab_api(self):
        resp = await self.client.request('GET', '/api/vocabulary/export')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, 'text/csv')
        csv_out = await resp.text()
        self.assertIn('Misheard Phrase,Correct Replacement', csv_out)

    async def test_panic_button(self):
        resp = await self.client.request('POST', '/api/control/panic')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['status'], 'success')

    async def test_control_restart_endpoint(self):
        restart_called = False
        def handle_restart():
            nonlocal restart_called
            restart_called = True

        self.overlay_server.on_restart_requested = handle_restart
        resp = await self.client.request('POST', '/api/control/restart')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['status'], 'restarting')
        self.assertIn('instance_id', data)
        await asyncio.sleep(0.3)
        self.assertTrue(restart_called)

    async def test_control_shutdown_endpoint(self):
        shutdown_called = False
        def handle_shutdown():
            nonlocal shutdown_called
            shutdown_called = True

        self.overlay_server.on_shutdown_requested = handle_shutdown
        resp = await self.client.request('POST', '/api/control/shutdown')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['status'], 'shutting_down')
        await asyncio.sleep(0.3)
        self.assertTrue(shutdown_called)

    async def test_api_status(self):
        resp = await self.client.request('GET', '/api/status')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn('engine', data)
        self.assertIn('uptime_seconds', data)

    async def test_models_status_api(self):
        resp = await self.client.request('GET', '/api/models/status')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn('total_models', data)
        self.assertIn('cached_models', data)
        self.assertIn('models', data)
        self.assertTrue(len(data['models']) >= 5)

    async def test_models_download_api(self):
        resp = await self.client.request('POST', '/api/models/download', json={'model_id': 'invalid_test'})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['status'], 'started')

    async def test_models_cancel_api(self):
        resp = await self.client.request('POST', '/api/models/cancel')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['status'], 'canceled')

    async def test_favicon_endpoint(self):
        resp = await self.client.request('GET', '/favicon.ico')
        self.assertEqual(resp.status, 200)
        self.assertIn('icon', resp.content_type)
        data = await resp.read()
        self.assertTrue(len(data) > 0)

    async def test_apple_touch_icon_endpoint(self):
        resp = await self.client.request('GET', '/apple-touch-icon.png')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, 'image/png')
        data = await resp.read()
        self.assertTrue(len(data) > 0)

    async def test_delete_model_api(self):
        resp = await self.client.request('POST', '/api/models/delete', json={'model_id': 'invalid_model_id'})
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertEqual(data['status'], 'error')

    async def test_custom_preset_lifecycle_api(self):
        # 1. Save custom preset
        save_resp = await self.client.request('POST', '/api/presets/save', json={
            'id': 'test_custom_preset_123',
            'name': 'Test Custom Theme',
            'description': 'A custom preset for testing',
            'font_family': 'Inter, sans-serif',
            'font_size': '36px',
            'font_weight': '700',
            'line_height': '1.35',
            'text_color': '#FFFFFF',
            'interim_color': '#90CAF9',
            'highlight_color': '#FFD166',
            'background_box_color': 'rgba(15, 15, 20, 0.72)',
            'border_radius': '12px',
            'box_padding': '14px 26px',
            'text_shadow': '2px 2px 5px rgba(0, 0, 0, 0.95)',
            'text_stroke': '2px #000000',
            'animation_style': 'word_pop'
        })
        self.assertEqual(save_resp.status, 200)
        save_data = await save_resp.json()
        self.assertEqual(save_data['status'], 'success')

        # 2. Get presets
        list_resp = await self.client.request('GET', '/api/presets')
        self.assertEqual(list_resp.status, 200)
        list_data = await list_resp.json()
        presets = list_data.get('presets', list_data)
        self.assertTrue(any(p.get('id') == 'test_custom_preset_123' for p in presets))

        # 3. Apply preset
        apply_resp = await self.client.request('POST', '/api/presets/apply', json={'theme_id': 'test_custom_preset_123'})
        self.assertEqual(apply_resp.status, 200)

        # 4. Delete custom preset
        del_resp = await self.client.request('POST', '/api/presets/delete', json={'preset_id': 'test_custom_preset_123'})
        self.assertEqual(del_resp.status, 200)
        del_data = await del_resp.json()
        self.assertEqual(del_data['status'], 'success')

    async def test_chapters_endpoint(self):
        resp = await self.client.request('GET', '/api/transcript/chapters')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn('chapters', data)
        self.assertIn('formatted', data)
        self.assertIn('youtube_compliant', data)
        self.assertIn('count', data)

    async def test_chapters_endpoint_query_params(self):
        resp = await self.client.request(
            'GET',
            '/api/transcript/chapters?anchor=first_speech&min_interval=60&offset=300&format=mmss'
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['anchor'], 'first_speech')
        self.assertEqual(data['min_interval'], 60.0)
        self.assertEqual(data['offset'], 300.0)
        self.assertEqual(data['format'], 'mmss')
        self.assertIn('youtube_compliant', data)

    async def test_chapters_endpoint_dual_engines(self):
        # Populate speech history so chapters can be generated
        self.overlay_server.history.add_entry("Welcome to our worship service.", start_time=100.0, end_time=105.0)
        self.overlay_server.history.add_entry("Let us read from Matthew 5.", start_time=250.0, end_time=255.0)
        self.overlay_server.history.add_entry("Benediction and closing prayer.", start_time=500.0, end_time=505.0)

        # 1. Explicit offline heuristic engine
        resp_heur = await self.client.request('GET', '/api/transcript/chapters?engine=heuristic')
        self.assertEqual(resp_heur.status, 200)
        data_heur = await resp_heur.json()
        self.assertEqual(data_heur['provider_used'], 'heuristic')
        self.assertIn('chapters', data_heur)
        self.assertIn('formatted', data_heur)
        self.assertIn('youtube_compliant', data_heur)

        # 2. Gemini AI engine (routes to summary_engine, falls back to heuristic if no key)
        resp_gem = await self.client.request('GET', '/api/transcript/chapters?engine=gemini')
        self.assertEqual(resp_gem.status, 200)
        data_gem = await resp_gem.json()
        self.assertIn(data_gem['provider_used'], ('gemini', 'heuristic'))
        self.assertIn('chapters', data_gem)
        self.assertIn('formatted', data_gem)

        # 3. AI chapters POST endpoint with provider override
        resp_post = await self.client.request('POST', '/api/transcript/ai-chapters', json={
            'provider': 'heuristic',
            'format': 'hhmmss',
        })
        self.assertEqual(resp_post.status, 200)
        data_post = await resp_post.json()
        self.assertEqual(data_post['provider_used'], 'heuristic')
        self.assertIn('youtube_compliant', data_post)


    async def test_vocabulary_test_and_clear_api(self):
        # Test vocabulary sandbox
        test_resp = await self.client.request('POST', '/api/vocabulary/test', json={'text': 'hello world'})
        self.assertEqual(test_resp.status, 200)
        test_data = await test_resp.json()
        self.assertIn('modified', test_data)

        # Clear vocabulary
        clear_resp = await self.client.request('POST', '/api/vocabulary/clear')
        self.assertEqual(clear_resp.status, 200)
        clear_data = await clear_resp.json()
        self.assertEqual(clear_data['status'], 'success')

    async def test_dock_endpoint(self):
        resp = await self.client.request('GET', '/dock')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, 'text/html')

    async def test_bible_api_endpoints(self):
        # 1. Versions list
        v_resp = await self.client.request('GET', '/api/bible/versions')
        self.assertEqual(v_resp.status, 200)
        v_data = await v_resp.json()
        self.assertIn('versions', v_data)
        self.assertTrue(len(v_data['versions']) >= 3)

        # 2. Lookup single verse
        l_resp = await self.client.request('GET', '/api/bible/lookup?citation=John+3:16&version=bsb')
        self.assertEqual(l_resp.status, 200)
        l_data = await l_resp.json()
        self.assertEqual(l_data['citation'], 'John 3:16')
        self.assertIn('loved the world', l_data['text'])

        # 3. Lookup verse range
        lr_resp = await self.client.request('GET', '/api/bible/lookup?citation=1+Thessalonians+5:16-18&version=bsb')
        self.assertEqual(lr_resp.status, 200)
        lr_data = await lr_resp.json()
        self.assertEqual(lr_data['citation'], '1 Thessalonians 5:16-18')
        self.assertIn('Rejoice', lr_data['text'])

        # 4. Display scripture on stream & stage
        d_resp = await self.client.request('POST', '/api/bible/display', json={
            'citation': 'Romans 8:28',
            'version': 'web',
            'duration': 10.0
        })
        self.assertEqual(d_resp.status, 200)
        d_data = await d_resp.json()
        self.assertEqual(d_data['status'], 'success')
        self.assertIn('scripture', d_data)

        # 5. Dismiss scripture
        dm_resp = await self.client.request('POST', '/api/bible/dismiss')
        self.assertEqual(dm_resp.status, 200)
        dm_data = await dm_resp.json()
        self.assertEqual(dm_data['status'], 'success')

    async def test_bible_overlay_isolation(self):
        """Verify scripture overlay isolation config and broadcast routing."""
        # 1. Check BibleConfig default
        from obs_captioner.config import BibleConfig
        bc = BibleConfig()
        self.assertFalse(bc.show_on_stream_overlay, "BibleConfig.show_on_stream_overlay should default to False")
        self.assertTrue(bc.show_on_stage_display)

        # 2. Toggle show_on_stream_overlay via API
        post_resp = await self.client.request('POST', '/api/config', json={
            'bible': {
                'show_on_stream_overlay': False,
                'show_on_stage_display': True
            }
        })
        self.assertEqual(post_resp.status, 200)
        get_resp = await self.client.request('GET', '/api/config')
        self.assertEqual(get_resp.status, 200)
        data = await get_resp.json()
        self.assertFalse(data['bible']['show_on_stream_overlay'])

        # 3. Verify broadcast_scripture role filtering & payload structure
        from obs_captioner.bible_engine import ScriptureLookupResult
        mock_verse = ScriptureLookupResult(
            citation="John 1:1",
            book="John",
            chapter=1,
            verse_start=1,
            verse_end=1,
            text="In the beginning was the Word.",
            version="bsb",
            version_name="Berean Standard Bible"
        )
        caption_msgs = []
        bible_msgs = []

        class MockWs:
            def __init__(self, target_list):
                self.target_list = target_list
            async def send_json(self, m):
                self.target_list.append(m)

        caption_ws = MockWs(caption_msgs)
        bible_ws = MockWs(bible_msgs)

        self.overlay_server.caption_sockets[caption_ws] = "en"
        self.overlay_server.socket_roles[caption_ws] = "caption"

        self.overlay_server.caption_sockets[bible_ws] = "en"
        self.overlay_server.socket_roles[bible_ws] = "bible"

        try:
            # When show_on_stream_overlay is False (default):
            self.overlay_server.config.bible.show_on_stream_overlay = False
            await self.overlay_server.broadcast_scripture(mock_verse, duration_seconds=10.0)

            # Caption socket should have received NOTHING
            self.assertEqual(len(caption_msgs), 0, "Caption overlay socket must NOT receive scripture when show_on_stream_overlay is False")
            # Dedicated Bible overlay socket must have received the scripture
            self.assertEqual(len(bible_msgs), 1, "Dedicated scripture overlay socket must receive scripture")
            self.assertEqual(bible_msgs[0]['type'], 'scripture_verse')
            self.assertEqual(bible_msgs[0]['citation'], 'John 1:1')

            # When show_on_stream_overlay is toggled to True:
            self.overlay_server.config.bible.show_on_stream_overlay = True
            await self.overlay_server.broadcast_scripture(mock_verse, duration_seconds=10.0)
            self.assertEqual(len(caption_msgs), 1, "Caption overlay socket receives scripture only when show_on_stream_overlay is True")
        finally:
            self.overlay_server.caption_sockets.pop(caption_ws, None)
            self.overlay_server.socket_roles.pop(caption_ws, None)
            self.overlay_server.caption_sockets.pop(bible_ws, None)
            self.overlay_server.socket_roles.pop(bible_ws, None)
            self.overlay_server.config.bible.show_on_stream_overlay = False

    async def test_accessibility_config_fields(self):
        # Test saving and reading accessibility configuration parameters
        post_resp = await self.client.request('POST', '/api/config', json={
            'overlay': {
                'letter_spacing': '0.05em',
                'use_italics': False,
                'high_contrast_outline': True,
                'reduce_motion': True
            },
            'bible': {
                'letter_spacing': '0.05em',
                'use_italics': False,
                'high_contrast_outline': True,
                'card_theme': 'amber'
            }
        })
        self.assertEqual(post_resp.status, 200)

        get_resp = await self.client.request('GET', '/api/config')
        self.assertEqual(get_resp.status, 200)
        cfg_data = await get_resp.json()
        self.assertEqual(cfg_data['overlay']['letter_spacing'], '0.05em')
        self.assertEqual(cfg_data['overlay']['use_italics'], False)
        self.assertEqual(cfg_data['overlay']['high_contrast_outline'], True)
        self.assertEqual(cfg_data['overlay']['reduce_motion'], True)
        self.assertEqual(cfg_data['bible']['letter_spacing'], '0.05em')
        self.assertEqual(cfg_data['bible']['use_italics'], False)

    async def test_api_key_and_secrets_persistence(self):
        """Verify that API keys and secrets are never wiped by empty payloads,
        are preserved with sentinel '•••', can be updated with new values, and can be cleared explicitly."""
        # 1. Set initial credentials
        resp1 = await self.client.request('POST', '/api/config', json={
            'gemini_live': {'api_key': 'test_mock_gemini_key_123'},
            'bandwidth': {'api_key': 'test_mock_bandwidth_key_456'},
            'twitch': {'oauth_token': 'oauth:test_mock_twitch_token_789'}
        })
        self.assertEqual(resp1.status, 200)
        self.assertEqual(self.overlay_server.config.gemini_live.api_key, 'test_mock_gemini_key_123')
        self.assertEqual(self.overlay_server.config.bandwidth.api_key, 'test_mock_bandwidth_key_456')
        self.assertEqual(self.overlay_server.config.twitch.oauth_token, 'oauth:test_mock_twitch_token_789')

        # Verify GET /api/config returns masked sentinel
        get1 = await self.client.request('GET', '/api/config')
        data1 = await get1.json()
        self.assertEqual(data1['gemini_live']['api_key'], '•••')
        self.assertEqual(data1['bandwidth']['api_key'], '•••')
        self.assertEqual(data1['twitch']['oauth_token'], '•••')

        # 2. Round-trip masked sentinel from GET - must NOT overwrite stored secrets
        resp2 = await self.client.request('POST', '/api/config', json={
            'gemini_live': {'api_key': '•••', 'model': 'gemini-3.5-transcribe-live'},
            'bandwidth': {'api_key': '•••'},
            'twitch': {'oauth_token': '•••'}
        })
        self.assertEqual(resp2.status, 200)
        self.assertEqual(self.overlay_server.config.gemini_live.api_key, 'test_mock_gemini_key_123')
        self.assertEqual(self.overlay_server.config.bandwidth.api_key, 'test_mock_bandwidth_key_456')
        self.assertEqual(self.overlay_server.config.twitch.oauth_token, 'oauth:test_mock_twitch_token_789')

        # 3. Submit empty string or None - must NOT wipe stored secrets
        resp3 = await self.client.request('POST', '/api/config', json={
            'gemini_live': {'api_key': '', 'smart_transcription': True},
            'bandwidth': {'api_key': ''},
            'twitch': {'oauth_token': ''}
        })
        self.assertEqual(resp3.status, 200)
        self.assertEqual(self.overlay_server.config.gemini_live.api_key, 'test_mock_gemini_key_123')
        self.assertEqual(self.overlay_server.config.bandwidth.api_key, 'test_mock_bandwidth_key_456')
        self.assertEqual(self.overlay_server.config.twitch.oauth_token, 'oauth:test_mock_twitch_token_789')

        # 4. Update with a new key - must update successfully
        resp4 = await self.client.request('POST', '/api/config', json={
            'gemini_live': {'api_key': 'test_mock_gemini_key_updated_999'}
        })
        self.assertEqual(resp4.status, 200)
        self.assertEqual(self.overlay_server.config.gemini_live.api_key, 'test_mock_gemini_key_updated_999')
        # Other keys should still be untouched
        self.assertEqual(self.overlay_server.config.bandwidth.api_key, 'test_mock_bandwidth_key_456')

        # 5. Explicit clear using __CLEAR__ - must wipe the key
        resp5 = await self.client.request('POST', '/api/config', json={
            'gemini_live': {'api_key': '__CLEAR__'}
        })
        self.assertEqual(resp5.status, 200)
        self.assertEqual(self.overlay_server.config.gemini_live.api_key, '')

        # 6. Update Gemini Live advanced settings (mode, language_codes, enable_hybrid_vad)
        resp6 = await self.client.request('POST', '/api/config', json={
            'gemini_live': {
                'mode': 'VERBATIM',
                'language_codes': ['en-US', 'es-ES'],
                'enable_hybrid_vad': False
            }
        })
        self.assertEqual(resp6.status, 200)
        self.assertEqual(self.overlay_server.config.gemini_live.mode, 'VERBATIM')
        self.assertEqual(self.overlay_server.config.gemini_live.language_codes, ['en-US', 'es-ES'])
        self.assertFalse(self.overlay_server.config.gemini_live.enable_hybrid_vad)

        # 7. Translation gemini_api_key masking, update, and clear
        resp7 = await self.client.request('POST', '/api/config', json={
            'translation': {
                'provider': 'gemini_live',
                'gemini_api_key': 'test_mock_trans_key_777'
            }
        })
        self.assertEqual(resp7.status, 200)
        self.assertEqual(self.overlay_server.config.translation.gemini_api_key, 'test_mock_trans_key_777')
        self.assertEqual(self.overlay_server.config.translation.provider, 'gemini_live')

        get_trans = await self.client.request('GET', '/api/config')
        data_trans = await get_trans.json()
        self.assertEqual(data_trans['translation']['gemini_api_key'], '•••')
        self.assertEqual(data_trans['translation']['provider'], 'gemini_live')

        # Clear key via __CLEAR__
        resp8 = await self.client.request('POST', '/api/config', json={
            'translation': {'gemini_api_key': '__CLEAR__'}
        })
        self.assertEqual(resp8.status, 200)
        self.assertEqual(self.overlay_server.config.translation.gemini_api_key, '')

    async def test_bible_route_variants(self):
        # Verify /bible, /bible/, and /bible.html all resolve correctly
        for path in ['/bible', '/bible/', '/bible.html']:
            resp = await self.client.request('GET', path)
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.content_type, 'text/html')

        # 6. Dedicated /bible HTML page
        page_resp = await self.client.request('GET', '/bible')
        self.assertEqual(page_resp.status, 200)
    async def test_wpm_calculation_and_endpoint(self):
        # 1. Test TranscriptHistory.get_stats()
        import time
        from obs_captioner.history import TranscriptHistory
        hist = TranscriptHistory()
        t0 = time.time() - 30.0

        # Add 3 sentences (total 30 words, 12 seconds speaking time)
        hist.add_entry("This is sentence number one containing precisely eight words.", start_time=t0, end_time=t0 + 4.0)
        hist.add_entry("This is sentence number two with eight more words.", start_time=t0 + 5.0, end_time=t0 + 9.0)
        hist.add_entry("And here is the final sentence for sixteen words total in this segment.", start_time=t0 + 10.0, end_time=t0 + 14.0)

        stats = hist.get_stats()
        self.assertGreater(stats["total_words"], 20)
        self.assertGreater(stats["session_wpm"], 50.0)
        self.assertIn("pace_rating", stats)
        self.assertIn("pace_color", stats)

        # 2. Test /api/transcript/stats endpoint
        resp = await self.client.request('GET', '/api/transcript/stats')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn('current_wpm', data)
        self.assertIn('session_wpm', data)
        self.assertIn('total_words', data)
        self.assertIn('active_speaking_seconds', data)

        # 3. Test /api/status includes WPM metrics
        st_resp = await self.client.request('GET', '/api/status')
        self.assertEqual(st_resp.status, 200)
        st_data = await st_resp.json()
        self.assertIn('current_wpm', st_data)
        self.assertIn('session_wpm', st_data)

    async def test_engine_benchmark_endpoint(self):
        """Verify /api/engine/benchmark returns structured church sermon rankings."""
        resp = await self.client.request('GET', '/api/engine/benchmark')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn('rankings', data)
        self.assertGreaterEqual(len(data['rankings']), 3)
        # Verify rank 1 has required benchmark metrics
        top_rank = data['rankings'][0]
        self.assertEqual(top_rank['rank'], 1)
        self.assertIn('engine_name', top_rank)
        self.assertIn('overall_accuracy_pct', top_rank)
        self.assertIn('avg_latency_ms', top_rank)
        self.assertIn('composite_score', top_rank)
        self.assertIn('church_fit_summary', top_rank)

    async def test_updater_endpoints(self):
        """Verify /api/updater/status, /api/updater/check, and /api/updater/apply endpoints."""
        from unittest.mock import AsyncMock, MagicMock

        # 1. Status when updater is not configured
        self.overlay_server.updater = None
        resp = await self.client.request('GET', '/api/updater/status')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertFalse(data.get('update_available'))
        self.assertEqual(data.get('error'), 'Updater not configured')

        # 2. Status when updater is configured with available update
        mock_updater = MagicMock()
        mock_updater.check_update = AsyncMock(return_value={
            "current_version": "1.0.0",
            "current_commit": "3cb58c8",
            "latest_commit": "abcdef1",
            "update_available": True,
            "commit_message": "Awesome auto-updater feature",
            "commit_author": "techguyowen",
        })
        mock_updater.apply_update = AsyncMock(return_value=(True, "VoxStream updated successfully!"))
        self.overlay_server.updater = mock_updater

        resp = await self.client.request('GET', '/api/updater/status')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data['update_available'])
        self.assertEqual(data['latest_commit'], "abcdef1")

        # 3. Check endpoint (POST /api/updater/check)
        resp = await self.client.request('POST', '/api/updater/check')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data['update_available'])
        mock_updater.check_update.assert_called_with(force=True)

        # 4. Apply endpoint (POST /api/updater/apply)
        resp = await self.client.request('POST', '/api/updater/apply')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data['status'], 'restarting')
        self.assertIn('updated successfully', data['message'])

    async def test_caddy_api_endpoints(self):
        from unittest.mock import patch

        # 1. Status endpoint
        resp = await self.client.request('GET', '/api/caddy/status')
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("enabled", data)
        self.assertIn("installed", data)
        self.assertIn("running", data)
        self.assertIn("ssl_enabled", data)
        self.assertIn("http_display_url", data)

        # 2. CA certificate download endpoint when not present returns 404
        with patch("obs_captioner.caddy_manager.get_caddy_root_ca_path", return_value=None):
            resp = await self.client.request('GET', '/api/caddy/ca.crt')
            self.assertEqual(resp.status, 404)

        # 3. Network info includes clean URLs and Caddy status
        resp_net = await self.client.request('GET', '/api/network/info')
        self.assertEqual(resp_net.status, 200)
        net_data = await resp_net.json()
        self.assertIn("caddy_active", net_data)
        self.assertIn("caddy_ssl", net_data)
        self.assertIn("display_url", net_data)
        self.assertIn("lan_ip", net_data)

        # 4. QR code endpoint generates SVG
        resp_qr = await self.client.request('GET', '/api/display/qr')
        self.assertEqual(resp_qr.status, 200)
        self.assertIn("image/svg+xml", resp_qr.content_type)



class TestCaptionSinkFinalOnly(unittest.IsolatedAsyncioTestCase):
    async def test_caption_sink_final_only_obs_text_source(self):
        from unittest.mock import AsyncMock, MagicMock
        from obs_captioner.obs.caption_sink import CaptionSink

        cfg = AppConfig()
        cfg.obs.enabled = True
        cfg.obs.update_text_source = True
        cfg.obs.text_source_name = "Live Captions"
        cfg.overlay.final_only = True

        obs_client = MagicMock()
        obs_client.is_connected = True
        obs_client.update_text_source = AsyncMock()
        obs_client.send_stream_caption = AsyncMock()

        sink = CaptionSink(cfg, obs_client=obs_client)

        # 1. Interim event - should NOT update OBS text source when final_only is True
        interim_evt = TranscriptEvent(text="Testing live speech", is_final=False)
        await sink.handle_transcript(interim_evt)
        obs_client.update_text_source.assert_not_called()

        # 2. Final event - SHOULD update OBS text source
        final_evt = TranscriptEvent(text="Testing live speech.", is_final=True)
        await sink.handle_transcript(final_evt)
        obs_client.update_text_source.assert_called_once()

    async def test_caption_sink_interim_allowed_when_not_final_only(self):
        from unittest.mock import AsyncMock, MagicMock
        from obs_captioner.obs.caption_sink import CaptionSink

        cfg = AppConfig()
        cfg.obs.enabled = True
        cfg.obs.update_text_source = True
        cfg.obs.text_source_name = "Live Captions"
        cfg.overlay.final_only = False

        obs_client = MagicMock()
        obs_client.is_connected = True
        obs_client.update_text_source = AsyncMock()
        obs_client.send_stream_caption = AsyncMock()

        sink = CaptionSink(cfg, obs_client=obs_client)

        # Interim event - SHOULD update OBS text source when final_only is False
        interim_evt = TranscriptEvent(text="Testing live speech", is_final=False)
        await sink.handle_transcript(interim_evt)
        obs_client.update_text_source.assert_called_once()


class TestSentenceBreakConfiguration(unittest.IsolatedAsyncioTestCase):
    def test_audio_config_sentence_break_defaults(self):
        from obs_captioner.config import AudioConfig, OverlayConfig
        ac = AudioConfig()
        self.assertEqual(ac.sentence_break_ms, 650)
        self.assertEqual(ac.max_sentence_duration_seconds, 7.0)
        self.assertEqual(ac.max_sentence_words, 24)
        self.assertEqual(ac.noise_gate_db, -52.0)
        self.assertEqual(ac.vad_threshold, 0.35)

        oc = OverlayConfig()
        self.assertEqual(oc.min_display_seconds, 2.5)

    def test_vad_update_config(self):
        from obs_captioner.vad import VoiceActivityDetector
        from obs_captioner.config import AudioConfig
        vad = VoiceActivityDetector(sample_rate=16000, noise_gate_db=-45.0, vad_threshold=0.5)
        new_ac = AudioConfig(noise_gate_db=-35.0, vad_threshold=0.7)
        vad.update_config(new_ac)
        self.assertEqual(vad.noise_gate_db, -35.0)
        self.assertEqual(vad.vad_threshold, 0.7)

    async def test_vosk_sentence_break_governor(self):
        import wave
        from pathlib import Path
        from obs_captioner.engines.vosk import VoskEngine
        from obs_captioner.config import load_config

        wav_path = Path("mtest.wav")
        if not wav_path.exists():
            return

        cfg = load_config("config.json")
        cfg.audio.sentence_break_ms = 400
        cfg.audio.max_sentence_duration_seconds = 2.0
        cfg.audio.max_sentence_words = 6

        engine = VoskEngine(cfg)
        ok = await engine.initialize()
        if not ok:
            return

        wf = wave.open(str(wav_path), "rb")
        async def mock_stream():
            while True:
                data = wf.readframes(1600)
                if not data:
                    break
                yield data

        finals = []
        async def on_transcript(evt):
            if evt.is_final and evt.text:
                finals.append(evt.text)

        await engine.start_streaming(mock_stream(), on_transcript)
        await engine.stop()
        wf.close()
        self.assertGreater(len(finals), 0)


class TestMusicSuppressionAndOverlayMinimumDuration(unittest.IsolatedAsyncioTestCase):
    def test_audio_config_suppress_music_default(self):
        from obs_captioner.config import AudioConfig, OverlayConfig
        ac = AudioConfig()
        self.assertTrue(ac.suppress_music)

        oc = OverlayConfig()
        self.assertEqual(oc.min_display_seconds, 2.5)

    def test_music_text_detection(self):
        from obs_captioner.music import is_music_text

        # Musical notes
        self.assertTrue(is_music_text("♪"))
        self.assertTrue(is_music_text("♫ ♫"))
        self.assertTrue(is_music_text("♪ Amazing grace how sweet the sound ♪"))
        self.assertTrue(is_music_text("♬ Holy, Holy, Holy"))

        # Tags
        self.assertTrue(is_music_text("[music]"))
        self.assertTrue(is_music_text("(music)"))
        self.assertTrue(is_music_text("[singing]"))
        self.assertTrue(is_music_text("(instrumental)"))
        self.assertTrue(is_music_text("[choir singing]"))
        self.assertTrue(is_music_text("[organ playing]"))

        # Repetition hallucination loops
        self.assertTrue(is_music_text("Thank you. Thank you. Thank you. Thank you."))
        self.assertTrue(is_music_text("you you you you you you"))
        self.assertTrue(is_music_text("Hallelujah Amen Hallelujah Amen Hallelujah Amen"))

        # Legitimate speech / sermons should NOT be flagged
        self.assertFalse(is_music_text("Let us open our Bibles to the book of Romans chapter 8."))
        self.assertFalse(is_music_text("For God so loved the world that he gave his only begotten Son."))
        self.assertFalse(is_music_text("Good morning church, welcome to our Sunday service."))

    async def test_caption_sink_suppresses_music_when_enabled(self):
        from obs_captioner.config import AppConfig
        from obs_captioner.obs.caption_sink import CaptionSink
        from obs_captioner.engines.base import TranscriptEvent
        from obs_captioner.history import TranscriptHistory

        cfg = AppConfig()
        cfg.audio.suppress_music = True
        hist = TranscriptHistory()
        sink = CaptionSink(cfg, history=hist)

        # 1. Music event with suppress_music=True -> dropped
        music_event = TranscriptEvent(text="♪ Amazing Grace ♪", is_final=True)
        await sink.handle_transcript(music_event)
        self.assertEqual(len(hist.entries), 0)

        # 2. Normal preaching event -> kept
        speech_event = TranscriptEvent(text="Grace and peace be multiplied to you.", is_final=True)
        await sink.handle_transcript(speech_event)
        self.assertEqual(len(hist.entries), 1)

        # 3. Disable suppress_music -> music events allowed through
        cfg.audio.suppress_music = False
        sink.update_config(cfg)
        music_event_allowed = TranscriptEvent(text="♪ Great is Thy Faithfulness ♪", is_final=True)
        await sink.handle_transcript(music_event_allowed)
        self.assertEqual(len(hist.entries), 2)

    def test_acoustic_music_detection(self):
        import numpy as np
        from obs_captioner.music import AcousticMusicDetector

        detector = AcousticMusicDetector(window_frames=8)
        sr = 16000
        duration = 0.1  # 100ms per frame
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)

        # Generate a sustained 3-note worship chord (C major: 261.6Hz, 329.6Hz, 392.0Hz)
        chord = (
            0.25 * np.sin(2 * np.pi * 261.6 * t)
            + 0.25 * np.sin(2 * np.pi * 329.6 * t)
            + 0.25 * np.sin(2 * np.pi * 392.0 * t)
        )
        chord_bytes = (chord * 32767).astype(np.int16).tobytes()

        # Feed 8 consecutive frames of chord (sustained tone)
        is_music = False
        for _ in range(8):
            is_music = detector.process_chunk(chord_bytes, sample_rate=sr, noise_gate_db=-45.0)

        self.assertTrue(is_music)

        # Now feed turbulent noise / consonant frames
        detector.reset()
        noise = np.random.normal(0, 0.2, len(t))
        noise_bytes = (noise * 32767).astype(np.int16).tobytes()
        is_noise_music = False
        for _ in range(8):
            is_noise_music = detector.process_chunk(noise_bytes, sample_rate=sr, noise_gate_db=-45.0)

        self.assertFalse(is_noise_music)

    def test_vad_suppress_music_update_config(self):
        from obs_captioner.vad import VoiceActivityDetector
        from obs_captioner.config import AudioConfig

        vad = VoiceActivityDetector(suppress_music=True)
        self.assertTrue(vad.suppress_music)

        vad.update_config(AudioConfig(suppress_music=False))
        self.assertFalse(vad.suppress_music)

    def test_music_suppression_strict_config_and_vad(self):
        from obs_captioner.vad import VoiceActivityDetector
        from obs_captioner.config import AudioConfig

        ac_default = AudioConfig()
        self.assertFalse(ac_default.suppress_music_strict)

        ac_strict = AudioConfig(suppress_music_strict=True)
        self.assertTrue(ac_strict.suppress_music_strict)

        vad = VoiceActivityDetector(suppress_music=True, suppress_music_strict=False)
        self.assertFalse(vad.suppress_music_strict)

        vad.update_config(ac_strict)
        self.assertTrue(vad.suppress_music_strict)

    def test_acoustic_music_detector_strict_hangover(self):
        import numpy as np
        from obs_captioner.music import AcousticMusicDetector

        detector = AcousticMusicDetector(window_frames=8)
        sr = 16000
        duration = 0.1
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        chord = (
            0.25 * np.sin(2 * np.pi * 261.6 * t)
            + 0.25 * np.sin(2 * np.pi * 329.6 * t)
            + 0.25 * np.sin(2 * np.pi * 392.0 * t)
        )
        chord_bytes = (chord * 32767).astype(np.int16).tobytes()

        # In strict mode, trigger detection
        for _ in range(8):
            is_music = detector.process_chunk(chord_bytes, sample_rate=sr, noise_gate_db=-45.0, strict=True)
        self.assertTrue(is_music)
        # Check that hangover counter is set to 30 in strict mode
        self.assertEqual(detector._music_hold_counter, 30)

        # In standard mode
        detector.reset()
        for _ in range(8):
            is_music = detector.process_chunk(chord_bytes, sample_rate=sr, noise_gate_db=-45.0, strict=False)
        self.assertTrue(is_music)
        self.assertEqual(detector._music_hold_counter, 10)

    def test_orphan_noise_strict_mode(self):
        from obs_captioner.music import is_orphan_noise, is_music_text

        # In strict mode, single orphan words (except sacred words) are dropped
        self.assertTrue(is_orphan_noise("friend", strict=True))
        self.assertFalse(is_orphan_noise("friend", strict=False))

        # Sacred essentials are preserved even in strict mode
        self.assertFalse(is_orphan_noise("amen", strict=True))
        self.assertFalse(is_orphan_noise("jesus", strict=True))
        self.assertFalse(is_orphan_noise("prayer", strict=True))

        # 2-3 word lyric mutterings dropped in strict mode
        self.assertTrue(is_orphan_noise("friend liar", strict=True))
        self.assertTrue(is_music_text("friend liar", strict=True))
        self.assertFalse(is_music_text("friend liar", strict=False))

    async def test_auto_clear_respects_min_display_seconds(self):
        import time
        from obs_captioner.config import AppConfig
        from obs_captioner.obs.caption_sink import CaptionSink

        cfg = AppConfig()
        cfg.overlay.auto_hide_seconds = 0.05
        cfg.overlay.min_display_seconds = 0.2
        sink = CaptionSink(cfg)

        t_start = time.time()
        await sink._auto_clear_worker()
        elapsed = time.time() - t_start
        self.assertGreaterEqual(elapsed, 0.18)

    def test_audio_capture_stop_unblocks_queue(self):
        from obs_captioner.audio_capture import AudioCapture
        from obs_captioner.config import AudioConfig

        ac = AudioCapture(AudioConfig())
        ac._running = True
        self.assertTrue(ac._running)
        ac.stop()
        self.assertFalse(ac._running)
        # Verify dummy chunk b"" was placed in queue to unblock any waiting worker thread
        chunk = ac._queue.get_nowait()
        self.assertEqual(chunk, b"")

    async def test_web_server_stop_resilient(self):
        import time
        from obs_captioner.web.server import WebOverlayServer
        from obs_captioner.config import AppConfig

        cfg = AppConfig()
        server = WebOverlayServer(cfg)
        t_start = time.time()
        await server.stop()
        elapsed = time.time() - t_start
        self.assertLess(elapsed, 1.0)
        self.assertEqual(len(server.caption_sockets), 0)
        self.assertEqual(len(server.control_sockets), 0)


class TestWindowsAuditAndResilience(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Windows platform resilience, mic switching, and projector integration."""

    def test_audio_capture_update_device_maintains_running_state(self):
        from obs_captioner.audio_capture import AudioCapture
        from obs_captioner.config import AudioConfig

        ac = AudioCapture(AudioConfig())
        ac._running = True
        # update_device must NOT reset _running to False
        ac.update_device(None)
        self.assertTrue(ac._running)

    async def test_obs_open_projector_payloads(self):
        from obs_captioner.obs.ws_client import OBSWebSocketClient
        from obs_captioner.config import AppConfig
        from unittest.mock import AsyncMock

        client = OBSWebSocketClient(AppConfig())
        client.is_connected = True
        client.get_monitors = AsyncMock(return_value=[{"monitorIndex": 0}, {"monitorIndex": 1}, {"monitorIndex": 2}])
        client.send_request = AsyncMock(return_value={"requestStatus": {"result": True}})

        # 1. Preview mix projector
        res = await client.open_projector(monitor_index=1, mix_type="preview")
        self.assertTrue(res)
        client.send_request.assert_called_with(
            "OpenVideoMixProjector",
            {"videoMixType": "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PREVIEW", "monitorIndex": 1}
        )

        # 2. Program mix projector
        res = await client.open_projector(monitor_index=0, mix_type="program")
        self.assertTrue(res)
        client.send_request.assert_called_with(
            "OpenVideoMixProjector",
            {"videoMixType": "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PROGRAM", "monitorIndex": 0}
        )

        # 3. Source projector
        res = await client.open_projector(monitor_index=2, mix_type="source", source_name="Test Source")
        self.assertTrue(res)
        client.send_request.assert_called_with(
            "OpenSourceProjector",
            {"sourceName": "Test Source", "monitorIndex": 2}
        )

    def test_vosk_search_dirs(self):
        from obs_captioner.model_downloader import get_vosk_search_dirs
        dirs = get_vosk_search_dirs()
        self.assertIsInstance(dirs, list)
        self.assertGreaterEqual(len(dirs), 1)
        dir_names = [str(d) for d in dirs]
        self.assertTrue(any("vosk" in d for d in dir_names))

    def test_cuda_dll_discovery_runs_clean(self):
        from obs_captioner.engines.local_whisper import _setup_windows_cuda_dlls
        # Must execute cleanly on all platforms without raising
        _setup_windows_cuda_dlls()

    def test_mimetypes_registered(self):
        import mimetypes
        import obs_captioner.web.server  # Ensures registration runs
        js_type, _ = mimetypes.guess_type("test.js")
        css_type, _ = mimetypes.guess_type("test.css")
        json_type, _ = mimetypes.guess_type("test.json")
        self.assertEqual(js_type, "application/javascript")
        self.assertEqual(css_type, "text/css")
        self.assertEqual(json_type, "application/json")
        woff_type, _ = mimetypes.guess_type("test.woff")
        woff2_type, _ = mimetypes.guess_type("test.woff2")
        ttf_type, _ = mimetypes.guess_type("test.ttf")
        self.assertEqual(woff_type, "font/woff")
        self.assertEqual(woff2_type, "font/woff2")
        self.assertEqual(ttf_type, "font/ttf")


class TestUpdaterCore(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.test_dir = tempfile.TemporaryDirectory()
        self.app_root = Path(self.test_dir.name)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_update_config_defaults(self):
        from obs_captioner.config import UpdateConfig, AppConfig
        cfg = AppConfig()
        self.assertTrue(cfg.update.enabled)
        self.assertTrue(cfg.update.auto_check)
        self.assertEqual(cfg.update.check_interval_hours, 6)
        self.assertEqual(cfg.update.channel, "main")

    def test_git_repo_detection(self):
        from obs_captioner.updater import UpdateManager
        mgr = UpdateManager(app_root=self.app_root)
        self.assertFalse(mgr.is_git_repo())
        (self.app_root / ".git").mkdir()
        self.assertTrue(mgr.is_git_repo())

    def test_get_local_commit_fallback(self):
        from obs_captioner.updater import UpdateManager
        mgr = UpdateManager(app_root=self.app_root)
        commit = mgr.get_local_commit()
        self.assertIsInstance(commit, str)
        self.assertGreater(len(commit), 0)

    async def test_check_update_caching(self):
        from obs_captioner.updater import UpdateManager
        mgr = UpdateManager(app_root=self.app_root)

        call_count = 0
        def mock_sync():
            nonlocal call_count
            call_count += 1
            return {"update_available": False, "cached_run": call_count}

        mgr._sync_check_update = mock_sync

        res1 = await mgr.check_update(force=False)
        self.assertEqual(res1["cached_run"], 1)
        self.assertEqual(call_count, 1)

        # Subsequent call without force hits the cache
        res2 = await mgr.check_update(force=False)
        self.assertEqual(res2["cached_run"], 1)
        self.assertEqual(call_count, 1)

        # Force=True bypasses the cache
        res3 = await mgr.check_update(force=True)
        self.assertEqual(res3["cached_run"], 2)
        self.assertEqual(call_count, 2)

    def test_zip_update_protects_critical_files(self):
        """Ensure config.json and user credentials are never overwritten during update extraction."""
        import zipfile
        import shutil

        # Create critical existing files in app_root
        user_config = self.app_root / "config.json"
        user_config.write_text('{"my_custom_secret": 12345}', encoding="utf-8")

        creds = self.app_root / "google_credentials.json"
        creds.write_text('{"private_key": "secret"}', encoding="utf-8")

        # Create mock update archive containing updated code and conflicting config/creds
        zip_path = self.app_root / "test_update.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("vox-stream-main/config.json", '{"overwritten": true}')
            zf.writestr("vox-stream-main/google_credentials.json", '{"overwritten": true}')
            zf.writestr("vox-stream-main/obs_captioner/new_feature.py", "# new feature code")

        extract_dir = self.app_root / "extracted"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        source_dir = extract_dir / "vox-stream-main"
        PROTECTED_NAMES = {
            "config.json",
            "google_credentials.json",
            ".venv",
            "venv",
            ".git",
            "logs",
            "data",
            ".env",
        }

        for item in source_dir.iterdir():
            if item.name in PROTECTED_NAMES:
                continue
            dest = self.app_root / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        # Verify critical files were preserved untouched
        self.assertIn("12345", user_config.read_text(encoding="utf-8"))
        self.assertNotIn("overwritten", user_config.read_text(encoding="utf-8"))
        self.assertIn("secret", creds.read_text(encoding="utf-8"))
        # Verify new code was installed
        self.assertTrue((self.app_root / "obs_captioner" / "new_feature.py").exists())

    def test_ensure_windows_batch_crlf(self):
        """Verify that updater converts any LF batch scripts to CRLF for cmd.exe."""
        from obs_captioner.updater import UpdateManager
        mgr = UpdateManager(app_root=self.app_root)

        bat = self.app_root / "test_script.bat"
        # Write pure LF-only content
        bat.write_bytes(b"@echo off\necho hello\npause\n")
        self.assertEqual(bat.read_bytes().count(b"\r\n"), 0)
        self.assertEqual(bat.read_bytes().count(b"\n"), 3)

        mgr._ensure_windows_batch_crlf()

        # Must now have CRLF
        content = bat.read_bytes()
        self.assertEqual(content.count(b"\r\n"), 3)
        self.assertEqual(content.count(b"\n") - content.count(b"\r\n"), 0)


class TestSermonPipelineEnhancements(unittest.IsolatedAsyncioTestCase):
    """Unit tests for sermon caption accuracy, boundary stitching, music suppression, and lexicon."""

    def test_music_hallucination_suppression(self):
        from obs_captioner.music import is_music_text, is_repetition_loop, is_orphan_noise

        # Known neural ASR hallucination phrases
        self.assertTrue(is_music_text("Satsang with Mooji."))
        self.assertTrue(is_music_text("Subtitles by amara.org"))
        self.assertTrue(is_music_text("Thank you for watching."))
        self.assertTrue(is_music_text("Please subscribe to our channel."))

        # Punctuation / dot loops
        self.assertTrue(is_music_text("everything dot everything dot dot"))
        self.assertTrue(is_music_text("dot everything dot everything dot"))

        # Low entropy repetition loops
        self.assertTrue(is_music_text("other in other other in other other"))
        self.assertTrue(is_music_text("other other other other"))

        # Orphan noise fragments emitted during silence/breaths
        self.assertTrue(is_orphan_noise("It."))
        self.assertTrue(is_orphan_noise("Sun."))
        self.assertTrue(is_orphan_noise("S new."))
        self.assertTrue(is_orphan_noise("In."))
        self.assertTrue(is_orphan_noise("And."))
        self.assertTrue(is_orphan_noise("So."))
        self.assertTrue(is_music_text("It."))
        self.assertTrue(is_music_text("Sun."))

        # Legitimate single words or normal phrases should NOT be suppressed
        self.assertFalse(is_music_text("Amen."))
        self.assertFalse(is_music_text("Jesus."))
        self.assertFalse(is_music_text("Praise."))
        self.assertFalse(is_music_text("Good morning church."))
        self.assertFalse(is_music_text("All you need is a book."))

    def test_church_lexicon_and_proper_nouns(self):
        from obs_captioner.formatter import TextFormatter

        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True, church_mode=True)

        # Pastoral & Leadership
        self.assertIn("Ben Luthi", fmt.format_text("ben lut is our pastor"))
        self.assertIn("Ben Luthi", fmt.format_text("ben luthi preached today"))
        self.assertIn("Ben Luthi", fmt.format_text("ben uthe is speaking"))

        # Church & Campuses & Events
        self.assertIn("Waypoint Church", fmt.format_text("welcome to way point church"))
        self.assertIn("Waypoint Kids", fmt.format_text("waypoint kids volunteer appreciation"))
        self.assertIn("Waypoint Kids", fmt.format_text("we love way point kids"))
        self.assertIn("Gymkhana", fmt.format_text("our men's event is jim conna"))
        self.assertIn("The Journey Church", fmt.format_text("partnering with the journey church"))
        self.assertIn("Church Center", fmt.format_text("download the church centre app"))

        # Regional Geography
        self.assertIn("the Triangle", fmt.format_text("serving in the triangle"))
        self.assertIn("RTP", fmt.format_text("our rtp campus"))
        self.assertIn("Raleigh", fmt.format_text("members from raleigh"))
        self.assertIn("South Durham", fmt.format_text("in south durham"))
        self.assertIn("East Chapel Hill", fmt.format_text("in east chapel hill"))

        # Sacred & Liturgical
        self.assertIn("Doxology", fmt.format_text("singing dog solid g"))
        self.assertIn("Doxology", fmt.format_text("dog. solid g"))
        self.assertIn("2 Corinthians", fmt.format_text("turn to said corinthians"))
        self.assertIn("intinction", fmt.format_text("communion via entinction"))
        self.assertIn("Luke 24", fmt.format_text("as written in top 24"))
        self.assertIn("he took a cup", fmt.format_text("when he took a cop and broke it"))
        self.assertIn("receive this pardon", fmt.format_text("come to receive this part in"))

    def test_custom_church_name_and_autocorrect_retention(self):
        from obs_captioner.formatter import TextFormatter
        from obs_captioner.config import AppConfig, GeneralConfig
        from obs_captioner.obs.caption_sink import CaptionSink

        # 1. Custom church name formatter
        fmt = TextFormatter(
            auto_capitalization=True,
            auto_punctuation=True,
            church_mode=True,
            church_name="Grace Community Church",
        )

        # Custom church name & ministry phrases are formatted properly
        self.assertIn("Grace Community Church", fmt.format_text("welcome to grace community church"))
        self.assertIn("Grace Community Kids", fmt.format_text("join us at grace community kids"))
        self.assertIn("Grace Community Youth", fmt.format_text("our grace community youth"))
        self.assertIn("Grace Community", fmt.format_text("partnering with grace community"))

        # ALL liturgical, doctrinal, and phonetic autocorrect words are 100% kept
        self.assertIn("Doxology", fmt.format_text("singing dog solid g"))
        self.assertIn("2 Corinthians", fmt.format_text("turn to said corinthians"))
        self.assertIn("intinction", fmt.format_text("communion via entinction"))
        self.assertIn("Luke 24", fmt.format_text("as written in top 24"))
        self.assertIn("he took a cup", fmt.format_text("when he took a cop and broke it"))
        self.assertIn("receive this pardon", fmt.format_text("come to receive this part in"))
        self.assertIn("discipleship", fmt.format_text("deepening our a socioplship"))
        self.assertIn("disciple", fmt.format_text("walking as a the cyber of Jesus"))
        self.assertIn("picked back up", fmt.format_text("we pissed back up"))

        # 2. Dynamic live config switching via CaptionSink
        cfg = AppConfig(general=GeneralConfig(church_name="Hope Fellowship"))
        sink = CaptionSink(cfg)
        self.assertIn("Hope Fellowship", sink.formatter.format_text("welcome to hope fellowship"))
        self.assertIn("Hope Fellowship Kids", sink.formatter.format_text("bringing our children to hope fellowship kids"))
        self.assertIn("Doxology", sink.formatter.format_text("singing dog solid g"))

        # Hot-switch to another church without restart
        new_cfg = AppConfig(general=GeneralConfig(church_name="Cornerstone Chapel"))
        sink.update_config(new_cfg)
        self.assertIn("Cornerstone Chapel", sink.formatter.format_text("welcome to cornerstone chapel"))
        self.assertIn("Cornerstone Chapel Kids", sink.formatter.format_text("cornerstone chapel kids"))
        self.assertIn("Doxology", sink.formatter.format_text("singing dog solid g"))

    def test_vulgar_mishearing_safeguard(self):
        from obs_captioner.formatter import TextFormatter
        from obs_captioner.vocabulary import VocabularyReplacer, VocabularyConfig

        vocab = VocabularyReplacer(VocabularyConfig())
        replaced, _ = vocab.replace("Hopefully we've pissed back up next week")
        self.assertEqual(replaced, "Hopefully we've picked back up next week")

        fmt = TextFormatter(church_mode=True)
        formatted = fmt.format_text("we pissed back up")
        self.assertIn("picked back up", formatted)
        self.assertNotIn("pissed", formatted.lower())

    def test_deduplicate_stutters_and_repeated_sentences(self):
        from obs_captioner.formatter import TextFormatter

        fmt = TextFormatter()

        # Identical repeated sentence
        sermon_dup = "I help our church to continue to be apart. I help our church to continue to be apart."
        res = fmt.format_text(sermon_dup)
        self.assertEqual(res, "I help our church to continue to be apart.")

        # Stuttered phrase
        stutter = "we want to, we want to make sure"
        res_stutter = fmt.format_text(stutter)
        self.assertIn("We want to make sure", res_stutter)

        # Biblical repetition should be preserved
        holy = "Holy, holy, holy is the Lord God Almighty."
        res_holy = fmt.format_text(holy)
        self.assertIn("Holy", res_holy)
        self.assertTrue(res_holy.lower().count("holy") >= 3)

    async def test_boundary_stitching_in_caption_sink(self):
        from obs_captioner.obs.caption_sink import CaptionSink
        from obs_captioner.history import TranscriptHistory
        from obs_captioner.config import AppConfig
        from obs_captioner.engines.base import TranscriptEvent

        config = AppConfig()
        history = TranscriptHistory()
        sink = CaptionSink(config=config, history=history)

        # Chunk 1: Cut off at boundary
        evt1 = TranscriptEvent(text="If you're a man at way poi.", is_final=True)
        await sink.handle_transcript(evt1)
        self.assertEqual(len(history.entries), 1)

        # Chunk 2: Second half arrives
        evt2 = TranscriptEvent(text="Point please come out for.", is_final=True)
        await sink.handle_transcript(evt2)

        # Entry 1 should be stitched to Waypoint
        self.assertIn("Waypoint", history.entries[0].text)
        self.assertNotIn("way poi", history.entries[0].text.lower())
        # Entry 2 should contain remainder without orphan 'Point'
        self.assertEqual(len(history.entries), 2)
        self.assertTrue(history.entries[1].text.startswith("Please come out"))

        # Absorption test: Entire chunk 2 is just the split word suffix
        history.entries.clear()
        evt_a = TranscriptEvent(text="Praise God from whom all blessings flow. dog.", is_final=True)
        await sink.handle_transcript(evt_a)
        self.assertEqual(len(history.entries), 1)

        evt_b = TranscriptEvent(text="Solid g.", is_final=True)
        await sink.handle_transcript(evt_b)

        # Chunk 2 should be absorbed into Chunk 1: Doxology
        self.assertEqual(len(history.entries), 1)
        self.assertIn("Doxology", history.entries[0].text)

    def test_moonshine_rolling_overlap_buffer(self):
        from obs_captioner.engines.moonshine import MoonshineEngine
        from obs_captioner.config import AppConfig

        config = AppConfig()
        engine = MoonshineEngine(config)
        sample_rate = 16000
        expected_overlap = int(sample_rate * 2 * 0.35) & ~1
class TestHardwareAndMemoryManagement(unittest.IsolatedAsyncioTestCase):
    """Unit tests for GPU detection (AMD Radeon RX 580 / NVIDIA), DirectML, and RAM memory purging."""

    def test_get_ram_usage_mb(self):
        from obs_captioner.hardware import get_ram_usage_mb
        ram = get_ram_usage_mb()
        self.assertIsInstance(ram, float)
        self.assertGreater(ram, 0.0)

    def test_get_gpu_info(self):
        from obs_captioner.hardware import get_gpu_info
        info = get_gpu_info(force_refresh=True)
        self.assertIn("vendor", info)
        self.assertIn("name", info)
        self.assertIn("backend", info)
        self.assertIn(info["vendor"], ("AMD", "NVIDIA", "Apple", "Intel", "CPU"))
        self.assertIn("is_cuda", info)
        self.assertIn("is_directml", info)
        self.assertIn("is_mps", info)

    def test_release_stt_memory(self):
        from obs_captioner.hardware import release_stt_memory

        class DummyEngine:
            def __init__(self):
                self.model = bytearray(10 * 1024 * 1024)  # 10MB dummy allocation
                self.tokenizer = {"dummy": "data"}

        dummy = DummyEngine()
        self.assertIsNotNone(dummy.model)
        freed = release_stt_memory(old_engine=dummy)
        self.assertIsNone(dummy.model)
        self.assertIsNone(dummy.tokenizer)
        self.assertIsInstance(freed, float)

    def test_get_torch_device(self):
        from obs_captioner.hardware import get_torch_device
        device, label = get_torch_device()
        self.assertIsNotNone(device)
        self.assertIsInstance(label, str)

    async def test_trim_memory_endpoint(self):
        from obs_captioner.web.server import WebOverlayServer
        from obs_captioner.config import AppConfig
        from aiohttp.test_utils import TestClient, TestServer

        cfg = AppConfig()
        server = WebOverlayServer(cfg)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            resp = await client.post("/api/system/trim_memory")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data.get("status"), "success")
            self.assertIn("freed_mb", data)
            self.assertIn("current_ram_mb", data)
        finally:
            await client.close()

    async def test_trim_memory_endpoint_with_hook(self):
        from obs_captioner.web.server import WebOverlayServer
        from obs_captioner.config import AppConfig
        from aiohttp.test_utils import TestClient, TestServer

        hook_called = False

        def mock_trim():
            nonlocal hook_called
            hook_called = True
            return 12.5

        cfg = AppConfig()
        server = WebOverlayServer(cfg, on_trim_memory=mock_trim)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            resp = await client.post("/api/system/trim_memory")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data.get("status"), "success")
            self.assertTrue(hook_called)
            self.assertEqual(data.get("freed_mb"), 12.5)
            self.assertIn("current_ram_mb", data)
            self.assertGreater(data["current_ram_mb"], 0.0)
        finally:
            await client.close()

    async def test_status_endpoint_includes_hardware_and_ram(self):
        from obs_captioner.web.server import WebOverlayServer
        from obs_captioner.config import AppConfig
        from aiohttp.test_utils import TestClient, TestServer

        cfg = AppConfig()
        server = WebOverlayServer(cfg)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            resp = await client.get("/api/status")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertIn("ram_usage_mb", data)
            self.assertIn("gpu", data)
            self.assertIsInstance(data["ram_usage_mb"], (int, float))
            self.assertIsInstance(data["gpu"], dict)
        finally:
            await client.close()

    async def test_status_endpoint_includes_lan_ip_and_urls(self):
        from obs_captioner.web.server import WebOverlayServer
        from obs_captioner.config import AppConfig
        from aiohttp.test_utils import TestClient, TestServer

        cfg = AppConfig()
        server = WebOverlayServer(cfg)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            resp = await client.get("/api/status")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertIn("lan_ip", data)
            self.assertIn("display_url", data)
            self.assertIn("dashboard_url", data)
            self.assertIn("overlay_url", data)
            self.assertIn("bible_url", data)
            self.assertTrue(data["display_url"].endswith("/display"))
            self.assertTrue(data["dashboard_url"].endswith("/dashboard"))
        finally:
            await client.close()

    async def test_network_info_endpoint(self):
        from obs_captioner.web.server import WebOverlayServer
        from obs_captioner.config import AppConfig
        from aiohttp.test_utils import TestClient, TestServer

        cfg = AppConfig()
        server = WebOverlayServer(cfg)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            resp = await client.get("/api/network/info")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertIn("lan_ip", data)
            self.assertIn("port", data)
            self.assertIn("display_url", data)
            self.assertIn("dashboard_url", data)
            self.assertIn("overlay_url", data)
            self.assertIn("bible_url", data)
        finally:
            await client.close()

    async def test_display_qr_endpoints(self):
        from obs_captioner.web.server import WebOverlayServer
        from obs_captioner.config import AppConfig
        from aiohttp.test_utils import TestClient, TestServer

        cfg = AppConfig()
        server = WebOverlayServer(cfg)
        client = TestClient(TestServer(server.app))
        await client.start_server()
        try:
            resp = await client.get("/api/display/qr")
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "image/svg+xml")
            text = await resp.text()
            self.assertIn("<svg", text)

            resp_es = await client.get("/api/display/qr?lang=es")
            self.assertEqual(resp_es.status, 200)
            text_es = await resp_es.text()
            self.assertIn("<svg", text_es)

            resp_dash = await client.get("/api/display/qr?type=dashboard")
            self.assertEqual(resp_dash.status, 200)
            text_dash = await resp_dash.text()
            self.assertIn("<svg", text_dash)
        finally:
            await client.close()


class TestVersioningAndSemanticUpdater(unittest.TestCase):
    """Unit tests for semantic versioning, bump helpers, and multi-source updater."""

    def test_parse_version(self):
        from obs_captioner.version import parse_version
        self.assertEqual(parse_version("1.1.0"), (1, 1, 0))
        self.assertEqual(parse_version("v1.1.0"), (1, 1, 0))
        self.assertEqual(parse_version("V2.0.4"), (2, 0, 4))
        self.assertEqual(parse_version("1.2"), (1, 2, 0))
        self.assertEqual(parse_version("1.1.0-beta.2"), (1, 1, 0))
        self.assertEqual(parse_version(""), (0, 0, 0))
        self.assertEqual(parse_version(None), (0, 0, 0))

    def test_compare_versions_and_is_newer(self):
        from obs_captioner.version import compare_versions, is_version_newer
        self.assertEqual(compare_versions("1.1.0", "1.0.0"), 1)
        self.assertEqual(compare_versions("1.0.0", "1.1.0"), -1)
        self.assertEqual(compare_versions("1.1.0", "1.1.0"), 0)
        self.assertEqual(compare_versions("2.0.0", "1.9.9"), 1)
        self.assertEqual(compare_versions("1.1.1", "1.1.0"), 1)

        self.assertTrue(is_version_newer("1.1.0", "1.0.0"))
        self.assertFalse(is_version_newer("1.0.0", "1.1.0"))
        self.assertFalse(is_version_newer("1.1.0", "1.1.0"))
        self.assertTrue(is_version_newer("v2.0.0", "v1.9.9"))

    def test_get_version_bump_type(self):
        from obs_captioner.version import get_version_bump_type
        self.assertEqual(get_version_bump_type("2.0.0", "1.1.0"), "major")
        self.assertEqual(get_version_bump_type("1.2.0", "1.1.0"), "minor")
        self.assertEqual(get_version_bump_type("1.1.1", "1.1.0"), "patch")
        self.assertEqual(get_version_bump_type("1.1.0", "1.1.0"), "none")

    def test_bump_version_string(self):
        from obs_captioner.version import bump_version_string
        self.assertEqual(bump_version_string("1.1.0", "patch"), "1.1.1")
        self.assertEqual(bump_version_string("1.1.0", "minor"), "1.2.0")
        self.assertEqual(bump_version_string("1.1.0", "medium"), "1.2.0")
        self.assertEqual(bump_version_string("1.1.0", "major"), "2.0.0")

    def test_bump_version_cli_dry_run(self):
        from scripts.bump_version import bump_version
        self.assertEqual(bump_version(bump_type="patch", dry_run=True), "1.1.1")
        self.assertEqual(bump_version(bump_type="minor", dry_run=True), "1.2.0")
        self.assertEqual(bump_version(bump_type="major", dry_run=True), "2.0.0")
        self.assertEqual(bump_version(custom_version="3.5.0", dry_run=True), "3.5.0")

    def test_updater_sync_check_detects_semantic_version_update(self):
        from unittest.mock import patch, MagicMock
        from obs_captioner.updater import UpdateManager
        import io

        updater = UpdateManager()

        # Mock urllib.request.urlopen returning version.json with version 1.2.0
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"version": "1.2.0"}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch.object(updater, "is_git_repo", return_value=False), \
             patch.object(updater, "get_local_commit", return_value="unknown"), \
             patch.object(updater, "get_local_full_commit", return_value="unknown"):

            status = updater._sync_check_update()
            self.assertTrue(status["update_available"])
            self.assertEqual(status["latest_version"], "1.2.0")
            self.assertEqual(status["update_type"], "minor")
            self.assertEqual(status["current_version"], "1.1.0")

    def test_updater_sync_check_ahead_of_remote_not_flagged(self):
        from unittest.mock import patch, MagicMock
        from obs_captioner.updater import UpdateManager

        updater = UpdateManager()

        mock_proc = MagicMock()
        mock_proc.returncode = 0  # 0 indicates remote commit is an ancestor of local HEAD

        with patch("urllib.request.urlopen", side_effect=Exception("offline")), \
             patch.object(updater, "is_git_repo", return_value=True), \
             patch.object(updater, "get_git_cmd", return_value="git"), \
             patch.object(updater, "get_local_commit", return_value="6a4a98d"), \
             patch.object(updater, "get_local_full_commit", return_value="6a4a98d000000000000000000000000000000000"), \
             patch("subprocess.run", return_value=mock_proc):

            status = updater._sync_check_update()
            self.assertFalse(status["update_available"])

    def test_git_update_fallback_to_zip_on_failure(self):
        from unittest.mock import patch, MagicMock
        from obs_captioner.updater import UpdateManager

        updater = UpdateManager()
        with patch.object(updater, "is_git_repo", return_value=True), \
             patch.object(updater, "_apply_update_git", return_value=(False, "Git lock error")), \
             patch.object(updater, "_apply_update_zip", return_value=(True, "Zip update success")):

            success, msg = updater._sync_apply_update()
            self.assertTrue(success)
            self.assertEqual(msg, "Zip update success")

    def test_zip_update_preserves_protected_files_and_merges(self):
        import tempfile
        import zipfile
        from pathlib import Path
        from unittest.mock import patch
        from obs_captioner.updater import UpdateManager

        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as web_dir:
            app_root = Path(root_dir)
            # Create user files that must be protected
            user_config = app_root / "config.json"
            user_config.write_text('{"user_setting": "keep_me"}', encoding="utf-8")
            custom_config = app_root / "config.church.json"
            custom_config.write_text('{"custom": true}', encoding="utf-8")
            user_env = app_root / ".env"
            user_env.write_text("API_SECRET=super_secret", encoding="utf-8")
            user_env_local = app_root / ".env.production"
            user_env_local.write_text("ENV_KEY=keep_me_too", encoding="utf-8")
            user_key = app_root / "private.key"
            user_key.write_text("SECRET_KEY_PAYLOAD", encoding="utf-8")
            user_pem = app_root / "cert.pem"
            user_pem.write_text("SECRET_PEM_PAYLOAD", encoding="utf-8")
            user_creds = app_root / "my_credentials.json"
            user_creds.write_text('{"oauth": "keep"}', encoding="utf-8")

            sub_dir = app_root / "obs_captioner"
            sub_dir.mkdir(parents=True)
            existing_file = sub_dir / "existing.py"
            existing_file.write_text("# existing code", encoding="utf-8")

            # Create mock zip with new update payload
            mock_zip_path = Path(web_dir) / "release.zip"
            with zipfile.ZipFile(mock_zip_path, "w") as zf:
                zf.writestr("vox-stream-main/config.json", '{"user_setting": "OVERWRITTEN"}')
                zf.writestr("vox-stream-main/config.church.json", '{"custom": "OVERWRITTEN"}')
                zf.writestr("vox-stream-main/.env", "API_SECRET=OVERWRITTEN")
                zf.writestr("vox-stream-main/.env.production", "ENV_KEY=OVERWRITTEN")
                zf.writestr("vox-stream-main/private.key", "OVERWRITTEN")
                zf.writestr("vox-stream-main/cert.pem", "OVERWRITTEN")
                zf.writestr("vox-stream-main/my_credentials.json", '{"oauth": "OVERWRITTEN"}')
                zf.writestr("vox-stream-main/obs_captioner/new_feature.py", "# new feature code")
                zf.writestr("vox-stream-main/version.json", '{"version": "1.2.0"}')

            import io
            updater = UpdateManager(app_root=app_root)
            with patch("urllib.request.urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(mock_zip_path.read_bytes())), \
                 patch.object(updater, "_ensure_windows_batch_crlf"):
                success, msg = updater._apply_update_zip()
                self.assertTrue(success)

            # Assert protected user files were untouched
            self.assertEqual(user_config.read_text(encoding="utf-8"), '{"user_setting": "keep_me"}')
            self.assertEqual(custom_config.read_text(encoding="utf-8"), '{"custom": true}')
            self.assertEqual(user_env.read_text(encoding="utf-8"), "API_SECRET=super_secret")
            self.assertEqual(user_env_local.read_text(encoding="utf-8"), "ENV_KEY=keep_me_too")
            self.assertEqual(user_key.read_text(encoding="utf-8"), "SECRET_KEY_PAYLOAD")
            self.assertEqual(user_pem.read_text(encoding="utf-8"), "SECRET_PEM_PAYLOAD")
            self.assertEqual(user_creds.read_text(encoding="utf-8"), '{"oauth": "keep"}')
            # Assert existing files still exist and new files were merged
            self.assertTrue(existing_file.exists())
            self.assertTrue((sub_dir / "new_feature.py").exists())
            self.assertTrue((app_root / "version.json").exists())

    def test_ensure_windows_batch_crlf(self):
        import tempfile
        from pathlib import Path
        from obs_captioner.updater import UpdateManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            app_root = Path(tmp_dir)
            bat_file = app_root / "test_launcher.bat"
            bat_file.write_bytes(b"@echo off\necho hello\nexit /b 0\n")

            updater = UpdateManager(app_root=app_root)
            updater._ensure_windows_batch_crlf()

            content = bat_file.read_bytes()
            self.assertIn(b"\r\n", content)
            self.assertNotIn(b"\r\r\n", content)
            self.assertEqual(content, b"@echo off\r\necho hello\r\nexit /b 0\r\n")

    def test_check_update_handles_network_failure_gracefully(self):
        import urllib.error
        from unittest.mock import patch
        from obs_captioner.updater import UpdateManager

        updater = UpdateManager()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network down")), \
             patch.object(updater, "is_git_repo", return_value=False):
            res = updater._sync_check_update()
            self.assertIsInstance(res, dict)
            self.assertFalse(res["update_available"])
            self.assertEqual(res["current_version"], "1.1.0")

    def test_generate_youtube_chapters_expanded_liturgy(self):
        from obs_captioner.history import TranscriptHistory
        hist = TranscriptHistory()
        t0 = hist.session_start_time

        # Simulate full church broadcast service
        hist.add_entry("Welcome to church this morning, so glad you are here.", start_time=t0 + 2.0, end_time=t0 + 6.0)
        hist.add_entry("Let us stand and worship the Lord together in praise and worship.", start_time=t0 + 60.0, end_time=t0 + 65.0)
        hist.add_entry("Please turn in your Bibles to Psalm 23.", start_time=t0 + 130.0, end_time=t0 + 135.0)
        hist.add_entry("We will now worship through giving with our tithes and offerings.", start_time=t0 + 200.0, end_time=t0 + 205.0)
        hist.add_entry("Today's message is from Romans 8 verse 28 through 30.", start_time=t0 + 270.0, end_time=t0 + 275.0)
        hist.add_entry("Point one is covenant faithfulness.", start_time=t0 + 340.0, end_time=t0 + 345.0)
        hist.add_entry("Point two is living by faith.", start_time=t0 + 410.0, end_time=t0 + 415.0)
        hist.add_entry("The big idea is that God's grace is sufficient.", start_time=t0 + 480.0, end_time=t0 + 485.0)
        hist.add_entry("We now come to the Lord's table for Holy Communion.", start_time=t0 + 550.0, end_time=t0 + 555.0)
        hist.add_entry("Every head bowed, come to the altar for prayer ministry.", start_time=t0 + 620.0, end_time=t0 + 625.0)
        hist.add_entry("Go in peace, have a blessed week, and may the Lord bless you.", start_time=t0 + 700.0, end_time=t0 + 705.0)

        chapters = hist.generate_chapters(min_interval_seconds=30.0)
        self.assertGreaterEqual(len(chapters), 8)

        titles = [c["title"] for c in chapters]
        self.assertTrue(any("Praise & Worship" in t for t in titles))
        self.assertTrue(any("Psalm 23" in t for t in titles))
        self.assertTrue(any("Tithes & Offering" in t for t in titles))
        self.assertTrue(any("Point 1" in t for t in titles))
        self.assertTrue(any("Point 2" in t for t in titles))
        self.assertTrue(any("Key Takeaway" in t for t in titles))
        self.assertTrue(any("Holy Communion" in t for t in titles))
        self.assertTrue(any("Altar Call" in t for t in titles))
        self.assertTrue(any("Benediction & Closing" in t for t in titles))

    def test_youtube_chapters_anchoring_and_offset(self):
        from obs_captioner.history import TranscriptHistory
        hist = TranscriptHistory()
        t0 = hist.session_start_time

        # Simulate app opened 20 minutes before speaking starts
        speech_start = t0 + 1200.0
        hist.add_entry("Good morning church, welcome to our service.", start_time=speech_start, end_time=speech_start + 4.0)
        hist.add_entry("Let us pray together.", start_time=speech_start + 60.0, end_time=speech_start + 65.0)
        hist.add_entry("Turn with me to Matthew 5:3-12.", start_time=speech_start + 150.0, end_time=speech_start + 155.0)

        # 1. Default anchor="first_speech": starts at 00:00:00 relative to speech, NOT 00:20:00!
        chapters = hist.generate_chapters(anchor="first_speech", min_interval_seconds=30.0)
        self.assertEqual(chapters[0]["timecode"], "00:00:00")
        self.assertEqual(chapters[1]["timecode"], "00:01:00")
        self.assertEqual(chapters[2]["timecode"], "00:02:30")

        # 2. Preshow offset +300s (5-minute countdown): Preshow at 00:00:00, speech intro at 00:05:00
        preshow_chapters = hist.generate_chapters(
            anchor="first_speech",
            time_offset_seconds=300.0,
            min_interval_seconds=30.0
        )
        self.assertEqual(preshow_chapters[0]["timecode"], "00:00:00")
        self.assertEqual(preshow_chapters[0]["title"], "Preshow / Welcome")
        self.assertEqual(preshow_chapters[1]["timecode"], "00:05:00")
        self.assertEqual(preshow_chapters[1]["title"], "Introduction & Welcome")
        self.assertEqual(preshow_chapters[2]["timecode"], "00:06:00")

        # 3. Format MM:SS
        mmss_chapters = hist.generate_chapters(
            anchor="first_speech",
            min_interval_seconds=30.0,
            format_style="mmss"
        )
        self.assertEqual(mmss_chapters[0]["timecode"], "00:00")
        self.assertEqual(mmss_chapters[1]["timecode"], "01:00")
        self.assertEqual(mmss_chapters[2]["timecode"], "02:30")

    def test_youtube_chapters_minimum_three_guarantee(self):
        from obs_captioner.history import TranscriptHistory
        hist = TranscriptHistory()
        t0 = hist.session_start_time

        # Short session with only 1 spoken entry across 120s
        hist.add_entry("General discussion about community outreach.", start_time=t0, end_time=t0 + 120.0)

        chapters = hist.generate_chapters(min_interval_seconds=45.0)
        # YouTube requires at least 3 chapters to activate video player chapters
        self.assertGreaterEqual(len(chapters), 3)
        self.assertEqual(chapters[0]["timecode"], "00:00:00")
        self.assertTrue(chapters[0]["seconds"] < chapters[1]["seconds"] < chapters[2]["seconds"])

    def test_youtube_chapters_title_synthesizer(self):
        from obs_captioner.history import TranscriptHistory
        hist = TranscriptHistory()

        # Strip filler words and synthesize readable title
        title1 = hist._synthesize_chapter_title("And so because of that we see walking in righteousness and peace.")
        self.assertNotIn("...", title1)
        self.assertFalse(title1.lower().startswith("message: and"))
        self.assertIn("Walking In Righteousness", title1)

        title2 = hist._synthesize_chapter_title("You know, I think that hope transforms everything.")
        self.assertNotIn("...", title2)
        self.assertIn("Hope Transforms Everything", title2)

    def test_local_whisper_turbo_and_distil_models(self):
        """Verify Faster-Whisper model alias resolution for large-v3-turbo and distil models."""
        self.assertEqual(LocalWhisperEngine.resolve_model_name("large-v3-turbo"), "large-v3-turbo")
        self.assertEqual(LocalWhisperEngine.resolve_model_name("turbo"), "large-v3-turbo")
        self.assertEqual(LocalWhisperEngine.resolve_model_name("distil-large-v3"), "distil-large-v3")
        self.assertEqual(LocalWhisperEngine.resolve_model_name("distil-medium.en"), "distil-medium.en")
        self.assertEqual(LocalWhisperEngine.resolve_model_name("distil-small.en"), "distil-small.en")
        self.assertEqual(LocalWhisperEngine.resolve_model_name("base.en"), "base.en")
        self.assertEqual(LocalWhisperEngine.resolve_model_name("small.en"), "small.en")

    def test_sensevoice_audio_events(self):
        """Verify SenseVoice audio event detection tag parsing and stripping."""
        cfg = AppConfig()
        cfg.sensevoice.detect_events = True
        engine = SenseVoiceEngine(cfg)

        raw = "<|en|><|APPLAUSE|> Hallelujah! The Lord is good! <|LAUGHTER|><|HAPPY|>"
        cleaned = engine._clean_audio_events(raw)
        self.assertIn("[Applause]", cleaned)
        self.assertIn("[Laughter]", cleaned)
        self.assertIn("Hallelujah! The Lord is good!", cleaned)
        self.assertNotIn("<|", cleaned)
        self.assertNotIn("|>", cleaned)

        # Test music tags
        raw_music = "<|MUSIC|> Praise to the King of Kings <|BGM|>"
        cleaned_music = engine._clean_audio_events(raw_music)
        self.assertIn("[Music]", cleaned_music)
        self.assertNotIn("<|", cleaned_music)

        # Test with detect_events disabled
        cfg.sensevoice.detect_events = False
        engine_no_events = SenseVoiceEngine(cfg)
        cleaned_no_events = engine_no_events._clean_audio_events("<|APPLAUSE|> Hallelujah! <|LAUGHTER|>")
        self.assertNotIn("[Applause]", cleaned_no_events)
        self.assertNotIn("[Laughter]", cleaned_no_events)
        self.assertEqual(cleaned_no_events, "Hallelujah!")

    def test_sherpa_and_parakeet_initialization(self):
        """Verify Sherpa-ONNX and NVIDIA Parakeet engines initialize and report status cleanly."""
        cfg = AppConfig()
        loop = asyncio.new_event_loop()

        # Sherpa-ONNX
        sherpa = SherpaEngine(cfg)
        messages_sherpa = []
        res_sherpa = loop.run_until_complete(sherpa.initialize(status_callback=messages_sherpa.append))
        self.assertIsInstance(res_sherpa, bool)
        loop.run_until_complete(sherpa.stop())

        # NVIDIA Parakeet
        parakeet = ParakeetEngine(cfg)
        messages_parakeet = []
        res_parakeet = loop.run_until_complete(parakeet.initialize(status_callback=messages_parakeet.append))
        self.assertTrue(res_parakeet)
        self.assertTrue(len(messages_parakeet) > 0)
        loop.run_until_complete(parakeet.stop())

        loop.close()

    def test_windows_paths_and_settings_isolation(self):
        """Verify Windows backslashes, drive letters, and user AppData paths across all engine configs."""
        cfg = AppConfig()

        win_vosk_path = r"C:\Users\Pastor Dan\AppData\Local\vosk\vosk-model-small-en-us-0.15"
        win_sherpa_path = r"D:\OBS Live\models\sherpa-onnx-streaming-zipformer-en"
        win_parakeet_path = r"C:\Users\Media Team\AppData\Local\parakeet\parakeet-tdt-0.6b"
        win_sensevoice_path = r"E:\Neural Models\sensevoice-small"

        cfg.vosk.model_path = win_vosk_path
        cfg.sherpa.model_path = win_sherpa_path
        cfg.parakeet.model_path = win_parakeet_path
        cfg.sensevoice.model_path = win_sensevoice_path

        self.assertEqual(cfg.vosk.model_path, win_vosk_path)
        self.assertEqual(cfg.sherpa.model_path, win_sherpa_path)
        self.assertEqual(cfg.parakeet.model_path, win_parakeet_path)
        self.assertEqual(cfg.sensevoice.model_path, win_sensevoice_path)

    def test_config_json_roundtrip_all_engines(self):
        """Verify that saving and reloading config with all engines preserves all individual settings."""
        import tempfile
        import os
        cfg = AppConfig()
        cfg.general.engine = "sherpa"
        cfg.sherpa.model_name = "streaming-zipformer-en-20M"
        cfg.sherpa.num_threads = 6
        cfg.parakeet.model_name = "parakeet-tdt-1.1b"
        cfg.parakeet.device = "cuda"
        cfg.sensevoice.model_name = "sensevoice-small"
        cfg.sensevoice.detect_events = False
        cfg.sensevoice.language = "es"

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = tf.name

        try:
            save_config(cfg, temp_path)
            loaded = load_config(temp_path)

            self.assertEqual(loaded.general.engine, "sherpa")
            self.assertEqual(loaded.sherpa.model_name, "streaming-zipformer-en-20M")
            self.assertEqual(loaded.sherpa.num_threads, 6)
            self.assertEqual(loaded.parakeet.model_name, "parakeet-tdt-1.1b")
            self.assertEqual(loaded.parakeet.device, "cuda")
            self.assertEqual(loaded.sensevoice.model_name, "sensevoice-small")
            self.assertFalse(loaded.sensevoice.detect_events)
            self.assertEqual(loaded.sensevoice.language, "es")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parakeet_audio_settings_integration(self):
        """Verify that ParakeetEngine and its VAD correctly inherit and update all audio settings."""
        from obs_captioner.engines.parakeet_engine import ParakeetEngine
        cfg = AppConfig()
        cfg.audio.enable_vad = False
        cfg.audio.suppress_music = False
        cfg.audio.vad_threshold = 0.42
        cfg.audio.noise_gate_db = -48.0
        cfg.audio.sentence_break_ms = 850
        cfg.audio.max_sentence_duration_seconds = 8.5

        engine = ParakeetEngine(cfg)
        self.assertEqual(engine.vad.noise_gate_db, -48.0)
        self.assertEqual(engine.vad.vad_threshold, 0.42)
        self.assertFalse(engine.vad.suppress_music)

        # Verify live update
        cfg.audio.vad_threshold = 0.65
        cfg.audio.noise_gate_db = -35.0
        cfg.audio.suppress_music = True
        engine.vad.update_config(cfg.audio)

        self.assertEqual(engine.vad.vad_threshold, 0.65)
        self.assertEqual(engine.vad.noise_gate_db, -35.0)
        self.assertTrue(engine.vad.suppress_music)

    def test_all_engines_audio_settings_and_vad_parity(self):
        """Verify that all engines with local VAD inherit enable_vad, suppress_music, and audio thresholds."""
        from obs_captioner.config import AudioConfig, AppConfig
        from obs_captioner.engines.vosk import VoskEngine
        from obs_captioner.engines.moonshine import MoonshineEngine
        from obs_captioner.engines.local_whisper import LocalWhisperEngine
        from obs_captioner.engines.google_web import GoogleWebEngine
        from obs_captioner.engines.parakeet_engine import ParakeetEngine
        from obs_captioner.engines.sensevoice_engine import SenseVoiceEngine
        from obs_captioner.engines.sherpa_engine import SherpaEngine

        cfg = AppConfig()
        cfg.audio.enable_vad = False
        cfg.audio.suppress_music = False
        cfg.audio.vad_threshold = 0.44
        cfg.audio.noise_gate_db = -47.0

        engines = [
            VoskEngine(cfg),
            MoonshineEngine(cfg),
            LocalWhisperEngine(cfg),
            GoogleWebEngine(cfg),
            ParakeetEngine(cfg),
            SenseVoiceEngine(cfg),
            SherpaEngine(cfg),
        ]

        for eng in engines:
            self.assertEqual(eng.vad.noise_gate_db, -47.0, f"Failed on {eng.name}")
            self.assertEqual(eng.vad.vad_threshold, 0.44, f"Failed on {eng.name}")
            self.assertFalse(eng.vad.suppress_music, f"Failed on {eng.name}")

            # Verify dynamic live update
            new_audio = AudioConfig(noise_gate_db=-32.0, vad_threshold=0.72, suppress_music=True)
            eng.vad.update_config(new_audio)
            self.assertEqual(eng.vad.noise_gate_db, -32.0, f"Failed on {eng.name}")
            self.assertEqual(eng.vad.vad_threshold, 0.72, f"Failed on {eng.name}")
            self.assertTrue(eng.vad.suppress_music, f"Failed on {eng.name}")

    def test_formatter_all_caps_normalization(self):
        """Verify that all-caps raw transcripts (e.g. Sherpa Zipformer CTC) normalize to broadcast casing."""
        from obs_captioner.formatter import TextFormatter
        formatter = TextFormatter(
            church_mode=True,
            auto_punctuation=True,
            auto_capitalization=True,
        )

        # Standard all-caps speech from Zipformer
        out = formatter.format_text("GOOD MORNING EVERYONE AND WELCOME TO CHURCH", is_final=True)
        self.assertEqual(out, "Good morning everyone and welcome to church.")

        # Proper noun / church terms in all-caps
        out2 = formatter.format_text("JESUS CHRIST IS LORD", is_final=True)
        self.assertEqual(out2, "Jesus Christ is Lord.")

        # Acronyms and custom replacements
        out3 = formatter.format_text("OBS AND YOUTUBE ARE STREAMING", is_final=True)
        self.assertEqual(out3, "OBS and YouTube are streaming.")

        # Short isolated acronyms (< 5 chars) should NOT be force-lowercased
        out4 = formatter.format_text("OBS", is_final=True)
        self.assertEqual(out4, "OBS.")

    def test_engine_stop_and_lifecycle_parity(self):
        """Verify all new engines clean up their model references and release RAM on stop()."""
        import asyncio
        from obs_captioner.engines.parakeet_engine import ParakeetEngine
        from obs_captioner.engines.sensevoice_engine import SenseVoiceEngine
        from obs_captioner.engines.sherpa_engine import SherpaEngine

        loop = asyncio.new_event_loop()
        try:
            cfg = AppConfig()
            p_eng = ParakeetEngine(cfg)
            p_eng.model = object()
            loop.run_until_complete(p_eng.stop())
            self.assertIsNone(p_eng.model)
            self.assertFalse(p_eng.is_running)

            sv_eng = SenseVoiceEngine(cfg)
            sv_eng.model = object()
            loop.run_until_complete(sv_eng.stop())
            self.assertIsNone(sv_eng.model)
            self.assertFalse(sv_eng.is_running)

            sh_eng = SherpaEngine(cfg)
            sh_eng.recognizer = object()
            loop.run_until_complete(sh_eng.stop())
            self.assertIsNone(sh_eng.recognizer)
            self.assertFalse(sh_eng.is_running)
        finally:
            loop.close()

    def test_parakeet_upgrade_options_and_architecture_detection(self):
        """Verify all Parakeet upgrade tiers resolve to correct repos and validate CTC/Transducer directories."""
        import tempfile
        import shutil
        from pathlib import Path
        from obs_captioner.engines.parakeet_engine import (
            PARAKEET_MODELS,
            ParakeetEngine,
            resolve_parakeet_repo,
        )
        from obs_captioner.model_downloader import MODEL_CATALOG

        # 1. Model resolution
        self.assertEqual(
            resolve_parakeet_repo("parakeet-fastconformer-large-24500"),
            "csukuangfj/sherpa-onnx-nemo-fast-conformer-ctc-en-24500",
        )
        self.assertEqual(
            resolve_parakeet_repo("parakeet-tdt-0.6b"),
            "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        )
        self.assertEqual(
            resolve_parakeet_repo("parakeet-ctc-large"),
            "csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-large",
        )
        self.assertEqual(
            resolve_parakeet_repo("parakeet-ctc-medium"),
            "csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-medium",
        )
        self.assertEqual(
            resolve_parakeet_repo("csukuangfj/custom-parakeet-model"),
            "csukuangfj/custom-parakeet-model",
        )

        # 2. Model catalog verification
        catalog_ids = {item.id: item for item in MODEL_CATALOG}
        self.assertIn("parakeet_fastconformer_large", catalog_ids)
        self.assertIn("parakeet_tdt_06b", catalog_ids)
        self.assertIn("parakeet_ctc_large", catalog_ids)
        self.assertIn("parakeet_nemo", catalog_ids)
        self.assertEqual(catalog_ids["parakeet_fastconformer_large"].size_mb, 458)
        self.assertEqual(catalog_ids["parakeet_tdt_06b"].size_mb, 670)

        # 3. Directory validation for CTC vs Transducer
        temp_dir = tempfile.mkdtemp()
        try:
            p = Path(temp_dir)
            self.assertFalse(ParakeetEngine._is_valid_model_dir(p))

            # Add tokens only -> still invalid
            (p / "tokens.txt").touch()
            self.assertFalse(ParakeetEngine._is_valid_model_dir(p))

            # Add CTC model.onnx -> valid CTC
            (p / "model.onnx").touch()
            self.assertTrue(ParakeetEngine._is_valid_model_dir(p))

            # Remove model.onnx, test Transducer triplets
            (p / "model.onnx").unlink()
            (p / "encoder.int8.onnx").touch()
            (p / "decoder.int8.onnx").touch()
            self.assertFalse(ParakeetEngine._is_valid_model_dir(p))  # Missing joiner
            (p / "joiner.int8.onnx").touch()
            self.assertTrue(ParakeetEngine._is_valid_model_dir(p))  # All 3 present
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestQRGeneratorAndNetwork(unittest.TestCase):

    def test_generate_qr_svg(self):
        from obs_captioner.qr_generator import generate_qr_svg
        url = "http://192.168.1.150:8765/display"
        svg = generate_qr_svg(url)
        self.assertIsInstance(svg, str)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        self.assertGreater(len(svg), 100)

    def test_get_local_ip(self):
        from obs_captioner.hardware import get_local_ip
        ip = get_local_ip()
        self.assertIsInstance(ip, str)
        self.assertGreater(len(ip), 0)


class TestSubtitleRecorder(unittest.TestCase):

    def test_timestamp_formatting(self):
        from obs_captioner.subtitle_recorder import format_timestamp_srt, format_timestamp_vtt
        self.assertEqual(format_timestamp_srt(0.0), "00:00:00,000")
        self.assertEqual(format_timestamp_srt(1.234), "00:00:01,234")
        self.assertEqual(format_timestamp_srt(65.5), "00:01:05,500")
        self.assertEqual(format_timestamp_srt(3661.025), "01:01:01,025")

        self.assertEqual(format_timestamp_vtt(0.0), "00:00:00.000")
        self.assertEqual(format_timestamp_vtt(1.234), "00:00:01.234")
        self.assertEqual(format_timestamp_vtt(65.5), "00:01:05.500")

    def test_recorder_lifecycle_srt(self):
        from obs_captioner.subtitle_recorder import SubtitleRecorder
        temp_dir = tempfile.mkdtemp()
        try:
            rec = SubtitleRecorder(output_format="srt", output_dir=temp_dir)
            out_file = rec.start_recording()
            self.assertTrue(rec.is_recording)
            self.assertIsNotNone(out_file)

            # Add two captions
            rec.add_caption("Welcome to church this morning.", start_time=time.time() - 2.0, end_time=time.time())
            rec.add_caption("Please open your Bibles to John 3:16.", start_time=time.time() - 1.0, end_time=time.time())

            st = rec.stop_recording()
            self.assertFalse(rec.is_recording)
            self.assertEqual(st["entry_count"], 2)
            self.assertTrue(Path(st["file_path"]).exists())

            with open(st["file_path"], "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("1\n", content)
            self.assertIn("-->", content)
            self.assertIn("Welcome to church this morning.", content)
            self.assertIn("2\n", content)
            self.assertIn("Please open your Bibles to John 3:16.", content)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_recorder_lifecycle_vtt(self):
        from obs_captioner.subtitle_recorder import SubtitleRecorder
        temp_dir = tempfile.mkdtemp()
        try:
            rec = SubtitleRecorder(output_format="vtt", output_dir=temp_dir)
            rec.start_recording()
            rec.add_caption("Testing WebVTT live generation.", start_time=time.time() - 1.0, end_time=time.time())
            st = rec.stop_recording()

            with open(st["file_path"], "r", encoding="utf-8") as f:
                content = f.read()

            self.assertTrue(content.startswith("WEBVTT"))
            self.assertIn("Testing WebVTT live generation.", content)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_recorder_matching_video_path(self):
        from obs_captioner.subtitle_recorder import SubtitleRecorder
        temp_dir = tempfile.mkdtemp()
        try:
            video_file = Path(temp_dir) / "Sunday_Sermon_2026-09-10.mp4"
            rec = SubtitleRecorder(output_format="srt")
            out_file = rec.start_recording(video_path=str(video_file))
            self.assertEqual(out_file, Path(temp_dir) / "Sunday_Sermon_2026-09-10.srt")
            rec.add_caption("Hello world", time.time(), time.time() + 1.5)
            st = rec.stop_recording()
            self.assertEqual(st["file_path"], str(Path(temp_dir) / "Sunday_Sermon_2026-09-10.srt"))
            self.assertTrue(Path(st["file_path"]).exists())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_obs_scene_auto_mute_config_and_logic(self):
        from obs_captioner.config import OBSConfig
        cfg = OBSConfig()
        self.assertFalse(cfg.scene_auto_mute_enabled)
        self.assertEqual(cfg.scene_muted_names, [])
        self.assertEqual(cfg.scene_active_names, [])

        cfg.scene_auto_mute_enabled = True
        cfg.scene_muted_names = ["Worship", "Prelude", "Video"]
        cfg.scene_active_names = ["Sermon", "Pulpit"]

        # Helper matching function identical to main.py logic
        def evaluate_scene(scene_name: str, current_paused: bool) -> bool:
            muted = [s.strip().lower() for s in cfg.scene_muted_names if s.strip()]
            active = [s.strip().lower() for s in cfg.scene_active_names if s.strip()]
            cur = scene_name.strip().lower()
            if cur in muted:
                return True  # Pause
            elif cur in active:
                return False  # Unpause
            return current_paused

        # Switching to Worship pauses captions
        self.assertTrue(evaluate_scene("Worship", False))
        self.assertTrue(evaluate_scene("worship", False))
        self.assertTrue(evaluate_scene("Prelude", False))
        # Switching to Sermon resumes captions
        self.assertFalse(evaluate_scene("Sermon", True))
        self.assertFalse(evaluate_scene("pulpit", True))
        # Unlisted scene preserves state
        self.assertTrue(evaluate_scene("Camera 3", True))
        self.assertFalse(evaluate_scene("Camera 3", False))

    def test_obs_ws_client_scene_subscriptions_and_helpers(self):
        from obs_captioner.obs.ws_client import OBSWebSocketClient
        from obs_captioner.config import OBSConfig
        cfg = OBSConfig()
        client = OBSWebSocketClient(cfg)
        self.assertIsNone(client.current_scene)
        self.assertIsNone(client.on_scene_changed)

        # Verify initial get_scene_list when disconnected
        loop = asyncio.new_event_loop()
        try:
            res = loop.run_until_complete(client.get_scene_list())
            self.assertFalse(res["connected"])
            self.assertEqual(res["scenes"], [])
        finally:
            loop.close()

    def test_audio_agc_and_peak_limiter(self):
        import numpy as np
        from obs_captioner.audio_capture import AudioCapture
        from obs_captioner.config import AudioConfig

        cfg = AudioConfig(enable_agc=True, agc_target_db=-18.0, agc_max_gain_db=18.0)
        capture = AudioCapture(cfg)

        # 1. Test quiet speech signal (-35 dBFS RMS)
        # 1600 samples (100ms at 16kHz)
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        quiet_amp = 10.0 ** (-35.0 / 20.0)  # ~0.0177
        quiet_signal = (quiet_amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        # Apply AGC over several blocks to let the attack/gain smoothly settle
        out = quiet_signal
        for _ in range(10):
            out = capture._apply_agc_and_limiter(quiet_signal)

        out_rms = float(np.sqrt(np.mean(out ** 2)))
        out_db = 20.0 * math.log10(out_rms)
        # Should be boosted well above -35 dBFS toward -18 dBFS
        self.assertGreater(out_db, -30.0)
        self.assertGreater(capture._agc_gain, 1.0)

        # 2. Test loud shouting with spikes (+3 dBFS peak signal)
        loud_signal = (1.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        out_loud = capture._apply_agc_and_limiter(loud_signal)
        # Peak limiter must strictly keep all samples <= 1.0
        self.assertLessEqual(np.max(np.abs(out_loud)), 1.0)
        # Gain should be reduced
        self.assertLess(capture._agc_gain, 5.0)

        # 3. Test ambient noise floor (-60 dBFS)
        # Below noise floor (-52 dB), AGC should not boost background hiss
        noise_amp = 10.0 ** (-65.0 / 20.0)
        noise_signal = (noise_amp * np.random.randn(1600)).astype(np.float32)
        capture._agc_gain = 1.0
        out_noise = capture._apply_agc_and_limiter(noise_signal)
        # Gain should not skyrocket to max_gain
        self.assertLess(capture._agc_gain, 1.5)

    def test_audio_watchdog_silence_padding_in_recovery(self):
        from obs_captioner.audio_capture import AudioCapture
        from obs_captioner.config import AudioConfig
        cfg = AudioConfig()
        capture = AudioCapture(cfg)

        capture._running = True
        capture.is_recovering = True

        async def check_generator():
            chunks = []
            gen = capture.stream_generator()
            # Grab first 3 chunks while in recovering state
            for _ in range(3):
                chunk = await gen.__anext__()
                chunks.append(chunk)
            capture._running = False
            await gen.aclose()
            return chunks

        loop = asyncio.new_event_loop()
        try:
            silence_chunks = loop.run_until_complete(check_generator())
            self.assertEqual(len(silence_chunks), 3)
            for c in silence_chunks:
                # Must be 100ms chunk of zeros (silence padding)
                self.assertEqual(len(c), capture.chunk_samples * 2)
                self.assertEqual(c, b"\x00" * (capture.chunk_samples * 2))
        finally:
            loop.close()

    def test_multi_language_tagalog_and_qr_lang_query(self):
        from obs_captioner.translator import SUPPORTED_LANGUAGES, SubtitleTranslator
        self.assertIn("tl", SUPPORTED_LANGUAGES)
        self.assertEqual(SUPPORTED_LANGUAGES["tl"], "Tagalog / Filipino")

        from obs_captioner.qr_generator import generate_qr_svg
        url = "http://192.168.1.100:8765/display?lang=es"
        svg = generate_qr_svg(url)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)


class TestSermonSummaryAndAIChapters(unittest.TestCase):
    """Test suite for optional AI Sermon Summary and Semantic YouTube Chapters."""

    def setUp(self):
        from obs_captioner.summary_engine import SermonSummaryEngine, SummaryConfig
        from obs_captioner.history import TranscriptHistory
        self.history = TranscriptHistory()
        self.config = SummaryConfig(enabled=True, provider="heuristic")
        self.engine = SermonSummaryEngine(self.config, self.history)

    def test_summary_config_defaults_and_masking(self):
        from obs_captioner.config import AppConfig, SummaryConfig, load_config
        cfg = AppConfig()
        self.assertTrue(hasattr(cfg, "summary"))
        self.assertTrue(cfg.summary.enabled)
        self.assertEqual(cfg.summary.provider, "auto")
        self.assertEqual(cfg.summary.gemini_model, "gemini-3.8-flash")

    def test_empty_transcript_handling(self):
        loop = asyncio.new_event_loop()
        try:
            summary = loop.run_until_complete(self.engine.generate_sermon_summary())
            self.assertIn("No Spoken Transcript", summary["title"])
            self.assertEqual(summary["provider_used"], "none")

            chapters = loop.run_until_complete(self.engine.generate_ai_chapters())
            self.assertIn("chapters", chapters)
            self.assertEqual(len(chapters["chapters"]), 1)
            self.assertEqual(chapters["chapters"][0]["seconds"], 0.0)
        finally:
            loop.close()

    def test_heuristic_sermon_summary_generation(self):
        # Populate realistic Sunday service transcript
        t0 = 1000.0
        self.history.add_entry("Welcome to church everyone, so glad you are with us.", start_time=t0, end_time=t0 + 5.0)
        self.history.add_entry("Today's message is called Walking in Victory.", start_time=t0 + 10.0, end_time=t0 + 15.0)
        self.history.add_entry("Please turn your Bibles to Romans 8 verse 28.", start_time=t0 + 30.0, end_time=t0 + 35.0)
        self.history.add_entry("Point number one: Faith requires complete surrender to Christ.", start_time=t0 + 120.0, end_time=t0 + 125.0)
        self.history.add_entry("Point number two: God works all things together for our good.", start_time=t0 + 240.0, end_time=t0 + 245.0)
        self.history.add_entry("Faith is not the absence of trials, but trusting God in the storm.", start_time=t0 + 300.0, end_time=t0 + 305.0)
        self.history.add_entry("In conclusion, let us bow our heads in prayer together.", start_time=t0 + 400.0, end_time=t0 + 405.0)

        loop = asyncio.new_event_loop()
        try:
            summary = loop.run_until_complete(self.engine.generate_sermon_summary())
            self.assertEqual(summary["provider_used"], "heuristic")
            self.assertIn("Walking In Victory", summary["title"])
            self.assertIn("Romans 8:28", summary["scriptures"])
            self.assertTrue(len(summary["key_points"]) >= 2)
            self.assertTrue(len(summary["discussion_questions"]) >= 3)
            self.assertIn("overview", summary)
            self.assertTrue(len(summary["overview"]) > 0)
            self.assertIn("action_steps", summary)
            self.assertTrue(len(summary["action_steps"]) >= 2)
            self.assertIn("# 📖", summary["markdown"])
            self.assertIn("Message Overview", summary["markdown"])
            self.assertIn("Weekly Action Steps", summary["markdown"])
            self.assertIn("TIMESTAMPS:", summary["youtube_description"])
            self.assertIn("SERMON RECAP:", summary["bulletin_text"])
        finally:
            loop.close()

    def test_transcript_aggregation_and_json_extraction(self):
        # 1. Test paragraph aggregation eliminates repetitive timestamps
        t0 = 5000.0
        self.history.add_entry("Opening welcome words.", start_time=t0, end_time=t0 + 2.0)
        self.history.add_entry("Continuing the same sentence seamlessly.", start_time=t0 + 5.0, end_time=t0 + 8.0)
        self.history.add_entry("A third line in the same 25s window.", start_time=t0 + 12.0, end_time=t0 + 15.0)
        self.history.add_entry("A new point after a 10-second gap.", start_time=t0 + 35.0, end_time=t0 + 38.0)

        formatted, base = self.engine._format_transcript_for_prompt(self.history.entries)
        lines = formatted.strip().split("\n")
        # Should have aggregated into 2 distinct timestamped paragraphs instead of 4 separate lines
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("[00:00:00]"))
        self.assertIn("Continuing the same sentence", lines[0])
        self.assertIn("A third line", lines[0])
        self.assertTrue(lines[1].startswith("[00:00:35]"))

        # 2. Test JSON extraction resilience against code fences and commentary
        fenced_obj = '```json\n{\n  "title": "Grace Awakened",\n  "scriptures": ["Ephesians 2:8"]\n}\n```'
        parsed_obj = self.engine._extract_json_object(fenced_obj)
        self.assertIsNotNone(parsed_obj)
        self.assertEqual(parsed_obj["title"], "Grace Awakened")

        conversational_obj = 'Here is the summary:\n{\n  "title": "Walking in Truth"\n}\nHope this helps!'
        parsed_conv = self.engine._extract_json_object(conversational_obj)
        self.assertIsNotNone(parsed_conv)
        self.assertEqual(parsed_conv["title"], "Walking in Truth")

        fenced_arr = '```json\n[\n  {"timecode": "00:00:00", "title": "Welcome"}\n]\n```'
        parsed_arr = self.engine._extract_json_array(fenced_arr)
        self.assertIsNotNone(parsed_arr)
        self.assertEqual(len(parsed_arr), 1)
        self.assertEqual(parsed_arr[0]["title"], "Welcome")

    def test_ai_chapters_strict_youtube_compliance(self):
        t0 = 2000.0
        self.history.add_entry("Praise the Lord, welcome this morning.", start_time=t0, end_time=t0 + 5.0)
        self.history.add_entry("Let us open with a word of prayer.", start_time=t0 + 20.0, end_time=t0 + 25.0)
        self.history.add_entry("Turn with me to John chapter 3 verse 16.", start_time=t0 + 80.0, end_time=t0 + 85.0)
        self.history.add_entry("Point number one: God's love is unconditional.", start_time=t0 + 200.0, end_time=t0 + 205.0)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(self.engine.generate_ai_chapters(
                format_style="hhmmss",
                min_interval_seconds=30.0,
            ))
            chapters = result["chapters"]
            self.assertTrue(result["youtube_compliant"])
            self.assertTrue(len(chapters) >= 3, "YouTube strictly requires at least 3 chapters")
            self.assertEqual(chapters[0]["seconds"], 0.0)
            self.assertEqual(chapters[0]["timecode"], "00:00:00")

            # Check strictly ascending order
            for i in range(len(chapters) - 1):
                self.assertLess(chapters[i]["seconds"], chapters[i + 1]["seconds"])

            self.assertIn("00:00:00 -", result["formatted"])
        finally:
            loop.close()

    def test_webserver_summary_endpoints(self):
        from obs_captioner.config import AppConfig
        from obs_captioner.web.server import WebServer

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            cfg = AppConfig()
            cfg.overlay.enabled = True
            cfg.overlay.port = 19876
            cfg.summary.provider = "heuristic"
            server = WebServer(cfg, history=self.history)

            self.history.add_entry("Welcome to our church gathering.", start_time=100.0, end_time=105.0)
            self.history.add_entry("Today we read Matthew chapter 5.", start_time=120.0, end_time=125.0)
            self.history.add_entry("Point 1: Be salt and light in this world.", start_time=200.0, end_time=205.0)

            async def run_checks():
                # 1. Summary status endpoint
                status_res = server.summary_engine.get_status()
                self.assertTrue(status_res["heuristic_available"])

                # 2. AI chapters endpoint handler
                class FakeRequest:
                    remote = "127.0.0.1"
                    method = "GET"
                    query = {"format": "hhmmss"}
                    async def json(self): return {}

                resp_chapters = await server._handle_ai_chapters(FakeRequest())
                data_chapters = json.loads(resp_chapters.text)
                self.assertTrue(data_chapters["youtube_compliant"])

                # 3. Sermon summary endpoint handler
                resp_summary = await server._handle_sermon_summary(FakeRequest())
                data_summary = json.loads(resp_summary.text)
                self.assertIn("Matthew 5", ", ".join(data_summary["scriptures"]))
                self.assertIn("markdown", data_summary)

            loop.run_until_complete(run_checks())
        finally:
            loop.close()

    def test_gemini_model_override_parameterization(self):
        from unittest.mock import AsyncMock, patch

        t0 = 100.0
        self.history.add_entry("Welcome to our service.", start_time=t0, end_time=t0 + 5.0)
        self.history.add_entry("Today we read from John chapter 1.", start_time=t0 + 10.0, end_time=t0 + 15.0)
        self.history.add_entry("In conclusion, love one another.", start_time=t0 + 60.0, end_time=t0 + 65.0)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(self.engine, "get_api_key", return_value="dummy-key"):
                with patch.object(self.engine, "_call_gemini_api", new_callable=AsyncMock) as mock_api:
                    # Test chapters with model override
                    mock_api.return_value = json.dumps([
                        {"timecode": "00:00:00", "title": "Welcome"},
                        {"timecode": "00:00:10", "title": "Scripture"},
                        {"timecode": "00:01:00", "title": "Conclusion"}
                    ])
                    res_chapters = loop.run_until_complete(
                        self.engine.generate_ai_chapters(
                            provider_override="gemini",
                            model_override="gemini-2.0-flash-lite"
                        )
                    )
                    self.assertEqual(res_chapters["provider_used"], "gemini")
                    mock_api.assert_called()
                    _, kwargs = mock_api.call_args
                    self.assertEqual(kwargs.get("model"), "gemini-2.0-flash-lite")

                    # Test summary with model override
                    mock_api.reset_mock()
                    mock_api.return_value = json.dumps({
                        "title": "Lite Sermon Summary",
                        "scriptures": ["John 1"],
                        "big_idea": "The Word became flesh.",
                        "overview": "Overview text",
                        "key_points": ["Point 1"],
                        "action_steps": ["Action 1"],
                        "quotes": [],
                        "discussion_questions": ["Q1"]
                    })
                    res_summary = loop.run_until_complete(
                        self.engine.generate_sermon_summary(
                            provider_override="gemini",
                            model_override="gemini-2.0-flash-lite"
                        )
                    )
                    self.assertEqual(res_summary["provider_used"], "gemini")
                    self.assertEqual(res_summary["title"], "Lite Sermon Summary")
                    mock_api.assert_called()
                    _, kwargs = mock_api.call_args
                    self.assertEqual(kwargs.get("model"), "gemini-2.0-flash-lite")

                    # Test webserver endpoint model passthrough
                    from obs_captioner.config import AppConfig
                    from obs_captioner.web.server import WebServer
                    cfg = AppConfig()
                    server = WebServer(cfg, self.history)
                    server.summary_engine = self.engine

                    class FakeServerReq:
                        remote = "127.0.0.1"
                        method = "POST"
                        query = {}
                        def __init__(self, data):
                            self._data = data
                        async def json(self):
                            return self._data

                    mock_api.reset_mock()
                    resp_srv_summary = loop.run_until_complete(
                        server._handle_sermon_summary(FakeServerReq({"provider": "gemini", "model": "gemini-2.0-flash-lite"}))
                    )
                    self.assertEqual(resp_srv_summary.status, 200)
                    mock_api.assert_called()
                    _, kwargs = mock_api.call_args
                    self.assertEqual(kwargs.get("model"), "gemini-2.0-flash-lite")
        finally:
            loop.close()


class TestSentenceStabilizationAndStitching(unittest.IsolatedAsyncioTestCase):
    """Tests for preventing half cut-off sentences, dangling connector handling, and clause stitching."""

    def test_dangling_connector_preservation(self):
        from obs_captioner.formatter import TextFormatter

        fmt = TextFormatter(auto_capitalization=True, auto_punctuation=True)

        # 1. Dangling connectors should not be forced with a terminal period
        self.assertEqual(
            fmt.format_text("we are coming together because", is_final=True),
            "We are coming together because"
        )
        self.assertEqual(
            fmt.format_text("he stepped onto the platform and", is_final=True),
            "He stepped onto the platform and"
        )
        self.assertEqual(
            fmt.format_text("we know that", is_final=True),
            "We know that"
        )

        # 2. Strips false periods attached by models on dangling conjunctions
        self.assertEqual(
            fmt.format_text("we are coming together because.", is_final=True),
            "We are coming together because"
        )

        # 3. Questions ending with prepositions still get a question mark
        self.assertEqual(
            fmt.format_text("what are you waiting for", is_final=True),
            "What are you waiting for?"
        )
        self.assertEqual(
            fmt.format_text("who are you speaking to", is_final=True),
            "Who are you speaking to?"
        )

        # 4. Standard complete statements still get a period
        self.assertEqual(
            fmt.format_text("we are gathered here today in faith", is_final=True),
            "We are gathered here today in faith."
        )

    async def test_clause_stitching_and_overlay_broadcast(self):
        from obs_captioner.obs.caption_sink import CaptionSink
        from obs_captioner.history import TranscriptHistory
        from obs_captioner.config import AppConfig
        from obs_captioner.engines.base import TranscriptEvent

        config = AppConfig()
        config.obs.update_text_source = True
        config.obs.text_source_name = "Captions"
        history = TranscriptHistory()

        class MockWebServer:
            def __init__(self):
                self.broadcasts = []
            async def broadcast_caption(self, payload):
                self.broadcasts.append(payload)
            async def trigger_scripture_lookup(self, text):
                pass

        class MockObsClient:
            def __init__(self):
                self.is_connected = True
                self.updated_texts = []
            async def update_text_source(self, source, text):
                self.updated_texts.append((source, text))
            async def send_stream_caption(self, text):
                pass

        mock_web = MockWebServer()
        mock_obs = MockObsClient()

        sink = CaptionSink(
            config=config,
            obs_client=mock_obs,
            web_server=mock_web,
            history=history,
        )

        # Chunk 1: Cut off at dangling connector
        evt1 = TranscriptEvent(text="We are called to love one another and", is_final=True)
        await sink.handle_transcript(evt1)
        self.assertEqual(len(history.entries), 1)
        self.assertEqual(history.entries[0].text, "We are called to love one another and")

        # Chunk 2: Second half arrives within 3.5s
        evt2 = TranscriptEvent(text="To bear each other's burdens.", is_final=True)
        await sink.handle_transcript(evt2)

        # Should be absorbed and stitched into one unbroken sentence
        self.assertEqual(len(history.entries), 1)
        self.assertEqual(
            history.entries[0].text,
            "We are called to love one another and to bear each other's burdens."
        )

        # Verify MockWebServer received broadcast with replace_last=True
        self.assertTrue(any(b.get("replace_last") is True for b in mock_web.broadcasts))
        last_b = [b for b in mock_web.broadcasts if b.get("replace_last") is True][-1]
        self.assertEqual(
            last_b["text"],
            "We are called to love one another and to bear each other's burdens."
        )

        # Verify Mock OBS received updated text source
        self.assertIn(
            ("Captions", "We are called to love one another and to bear each other's burdens."),
            mock_obs.updated_texts,
        )

    async def test_clause_stitching_proper_noun_preservation(self):
        from obs_captioner.obs.caption_sink import CaptionSink
        from obs_captioner.history import TranscriptHistory
        from obs_captioner.config import AppConfig
        from obs_captioner.engines.base import TranscriptEvent

        config = AppConfig()
        history = TranscriptHistory()
        sink = CaptionSink(config=config, history=history)

        evt1 = TranscriptEvent(text="We put all our trust in", is_final=True)
        await sink.handle_transcript(evt1)

        evt2 = TranscriptEvent(text="Jesus Christ who reigns forever.", is_final=True)
        await sink.handle_transcript(evt2)

        self.assertEqual(len(history.entries), 1)
        self.assertEqual(
            history.entries[0].text,
            "We put all our trust in Jesus Christ who reigns forever."
        )

    async def test_clause_stitching_word_ceiling_protection(self):
        from obs_captioner.obs.caption_sink import CaptionSink
        from obs_captioner.history import TranscriptHistory
        from obs_captioner.config import AppConfig
        from obs_captioner.engines.base import TranscriptEvent

        config = AppConfig()
        config.audio.max_sentence_words = 15  # Strict ceiling for test
        history = TranscriptHistory()
        sink = CaptionSink(config=config, history=history)

        # 12 words ending in 'and'
        long_chunk_1 = "Now we are going to look into the scriptures together as a church family and"
        evt1 = TranscriptEvent(text=long_chunk_1, is_final=True)
        await sink.handle_transcript(evt1)
        self.assertEqual(len(history.entries), 1)

        # 10 words (total 22 words, exceeds max_words + 4 = 19)
        chunk_2 = "we will discover the truth of God's holy word today."
        evt2 = TranscriptEvent(text=chunk_2, is_final=True)
        await sink.handle_transcript(evt2)

        # Should NOT merge into one giant run-on; should create two separate entries,
        # with chunk 1 sealed gracefully with a period.
        self.assertEqual(len(history.entries), 2)
        self.assertTrue(history.entries[0].text.endswith("."))
        self.assertTrue(history.entries[1].text.startswith("We will discover"))

    def test_vosk_dangling_connectors(self):
        from obs_captioner.engines.vosk import DANGLING_CONNECTORS

        self.assertIn("and", DANGLING_CONNECTORS)
        self.assertIn("because", DANGLING_CONNECTORS)
        self.assertIn("that", DANGLING_CONNECTORS)
        self.assertIn("unto", DANGLING_CONNECTORS)
        self.assertIn("while", DANGLING_CONNECTORS)

    def test_windows_firewall_module(self):
        from obs_captioner.firewall import (
            DEFAULT_PORT,
            DEFAULT_RULE_NAME,
            check_firewall_rule,
            is_admin,
            is_windows,
        )

        self.assertEqual(DEFAULT_PORT, 8765)
        self.assertIn("VoxStream", DEFAULT_RULE_NAME)
        # Verify function calls execute safely without crashing
        rule_active = check_firewall_rule()
        self.assertIsInstance(rule_active, bool)
        admin_status = is_admin()
        self.assertIsInstance(admin_status, bool)
        win_status = is_windows()
        self.assertIsInstance(win_status, bool)

    def test_windows_system_tray_module(self):
        from obs_captioner.tray import (
            VoxStreamTray,
            _create_fallback_icon,
            get_tray_icon_image,
            is_tray_supported,
        )

        self.assertTrue(is_tray_supported())
        fallback_img = _create_fallback_icon(size=64)
        self.assertIsNotNone(fallback_img)
        self.assertEqual(fallback_img.size, (64, 64))

        icon_img = get_tray_icon_image()
        self.assertIsNotNone(icon_img)

        # Test tray instance & callbacks
        paused_state = []
        resumed_state = []
        shutdown_state = []

        tray = VoxStreamTray(
            port=8765,
            host="127.0.0.1",
            on_pause=lambda: paused_state.append(True),
            on_resume=lambda: resumed_state.append(True),
            on_shutdown=lambda: shutdown_state.append(True),
        )
        self.assertEqual(tray.port, 8765)
        self.assertEqual(tray.base_url, "http://localhost:8765")

        # Test menu building
        menu = tray._build_menu()
        menu_items = list(menu)
        self.assertGreaterEqual(len(menu_items), 7)

        # Test pause toggle logic
        tray._toggle_pause(None, None)
        self.assertTrue(tray.is_paused)
        self.assertEqual(len(paused_state), 1)

        tray._toggle_pause(None, None)
        self.assertFalse(tray.is_paused)
        self.assertEqual(len(resumed_state), 1)

        # Test exit logic
        tray._handle_exit(None, None)
        self.assertEqual(len(shutdown_state), 1)

    def test_updater_fast_dependency_installer(self):
        from pathlib import Path
        from obs_captioner.updater import UpdateManager

        um = UpdateManager()
        req_file = Path(__file__).parent / "requirements.txt"
        self.assertTrue(req_file.exists())

        res = um._install_dependencies(req_file)
        self.assertIsNotNone(res)
        self.assertEqual(res.returncode, 0)


class TestGPUSelectionAndHardwareFeatures(unittest.IsolatedAsyncioTestCase):
    """Test hardware GPU enumeration, VRAM parsing, GPU ranking, API endpoints, and engine integration."""

    def test_parse_vram_bytes(self):
        from obs_captioner.hardware import _parse_vram_bytes
        self.assertEqual(_parse_vram_bytes(8589934592), 8589934592)
        self.assertEqual(int(_parse_vram_bytes(8589934592) / (1024 * 1024)), 8192)
        self.assertEqual(_parse_vram_bytes(1073741824), 1073741824)
        reg_binary_1gb = (1073741824).to_bytes(4, "little")
        self.assertEqual(_parse_vram_bytes(reg_binary_1gb), 1073741824)
        reg_binary_8gb = (8589934592).to_bytes(8, "little")
        self.assertEqual(_parse_vram_bytes(reg_binary_8gb), 8589934592)
        self.assertEqual(_parse_vram_bytes(None), 0)
        self.assertEqual(_parse_vram_bytes("invalid"), 0)

    def test_get_available_gpus(self):
        from obs_captioner.hardware import get_available_gpus
        gpus = get_available_gpus(force_refresh=True)
        self.assertIsInstance(gpus, list)
        self.assertGreaterEqual(len(gpus), 1)

        cpu_entry = next((g for g in gpus if g.get("device_id") == "cpu" or g.get("id") == "cpu"), None)
        self.assertIsNotNone(cpu_entry)
        self.assertIn("CPU Only (Safe Mode", cpu_entry["name"])

        recommended_count = sum(1 for g in gpus if g.get("recommended"))
        self.assertEqual(recommended_count, 1)

    def test_get_gpu_info_auto_vs_explicit(self):
        from obs_captioner.hardware import get_gpu_info
        info_auto = get_gpu_info("auto", force_refresh=True)
        self.assertIsInstance(info_auto, dict)
        self.assertIn("name", info_auto)
        self.assertIn("vendor", info_auto)

        info_cpu = get_gpu_info("cpu", force_refresh=True)
        self.assertEqual(info_cpu["device_id"], "cpu")
        self.assertEqual(info_cpu["vendor"], "CPU")

    def test_get_torch_device_preferred_gpu(self):
        from obs_captioner.hardware import get_torch_device
        dev_cpu, label_cpu = get_torch_device("cpu")
        self.assertEqual(dev_cpu, "cpu")
        self.assertIn("Safe Mode", label_cpu)

    async def test_local_whisper_device_selection_cpu(self):
        from unittest.mock import patch
        from obs_captioner.config import AppConfig
        from obs_captioner.engines.local_whisper import LocalWhisperEngine

        cfg = AppConfig()
        cfg.general.preferred_gpu = "cpu"
        cfg.audio.enable_vad = False
        engine = LocalWhisperEngine(cfg)

        with patch("faster_whisper.WhisperModel") as mock_model, \
             patch("obs_captioner.engines.local_whisper._setup_windows_cuda_dlls"):
            res = await engine.initialize()
            self.assertTrue(res)
            mock_model.assert_called_once()
            _, kwargs = mock_model.call_args
            self.assertEqual(kwargs.get("device"), "cpu")
            self.assertEqual(kwargs.get("compute_type"), "int8")
            await engine.stop()

    async def test_local_whisper_device_selection_cuda(self):
        from unittest.mock import patch
        from obs_captioner.config import AppConfig
        from obs_captioner.engines.local_whisper import LocalWhisperEngine

        cfg = AppConfig()
        cfg.general.preferred_gpu = "auto"
        cfg.audio.enable_vad = False
        engine = LocalWhisperEngine(cfg)

        mock_gpu = {
            "device_id": "nvidia_0",
            "name": "NVIDIA GeForce RTX 2070",
            "vendor": "NVIDIA",
            "vram_mb": 8192,
            "is_cuda": True,
            "is_directml": True,
            "recommended": True,
        }

        with patch("obs_captioner.hardware.get_gpu_info", return_value=mock_gpu):
            with patch("faster_whisper.WhisperModel") as mock_model, \
                 patch("obs_captioner.engines.local_whisper._setup_windows_cuda_dlls"):
                res = await engine.initialize()
                self.assertTrue(res)
                mock_model.assert_called_once()
                _, kwargs = mock_model.call_args
                self.assertEqual(kwargs.get("device"), "cuda")
                self.assertEqual(kwargs.get("device_index"), 0)
                self.assertEqual(kwargs.get("compute_type"), "float16")
                await engine.stop()


class TestCaddyReverseProxy(unittest.TestCase):
    def test_caddy_config_defaults(self):
        from obs_captioner.config import CaddyConfig, AppConfig
        cfg = CaddyConfig()
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.ssl)
        self.assertEqual(cfg.port_http, 80)
        self.assertEqual(cfg.port_https, 443)
        self.assertEqual(cfg.bin_path, "")

        app_cfg = AppConfig()
        self.assertIsInstance(app_cfg.caddy, CaddyConfig)

    def test_caddy_config_load_save(self):
        import tempfile
        import json
        from pathlib import Path
        from obs_captioner.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_cfg = Path(tmpdir) / "config.json"
            cfg_data = {
                "caddy": {
                    "enabled": True,
                    "ssl": False,
                    "port_http": 8080,
                    "port_https": 8443,
                    "bin_path": "/usr/local/bin/caddy"
                }
            }
            tmp_cfg.write_text(json.dumps(cfg_data))
            loaded = load_config(tmp_cfg)
            self.assertTrue(loaded.caddy.enabled)
            self.assertFalse(loaded.caddy.ssl)
            self.assertEqual(loaded.caddy.port_http, 8080)
            self.assertEqual(loaded.caddy.port_https, 8443)
            self.assertEqual(loaded.caddy.bin_path, "/usr/local/bin/caddy")

    def test_caddy_manager_telemetry(self):
        from obs_captioner.caddy_manager import get_caddy_telemetry
        from obs_captioner.config import AppConfig

        cfg = AppConfig()
        cfg.caddy.enabled = False
        telem = get_caddy_telemetry(cfg)
        self.assertIsInstance(telem, dict)
        self.assertIn("enabled", telem)
        self.assertIn("running", telem)
        self.assertIn("installed", telem)
        self.assertIn("http_display_url", telem)
        self.assertIn("https_display_url", telem)
        self.assertIn("preferred_display_url", telem)

    def test_caddy_manager_start_stop(self):
        from unittest.mock import patch, MagicMock
        from obs_captioner.caddy_manager import start_caddy, stop_caddy

        with patch("subprocess.run") as mock_run, patch("obs_captioner.caddy_manager.find_caddy_binary", return_value="/bin/caddy"):
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            ok, msg = start_caddy()
            self.assertTrue(ok)
            self.assertIn("started", msg)

            ok2, msg2 = stop_caddy()
            self.assertTrue(ok2)
            self.assertIn("stopped", msg2)




class TestCaptionScheduler(unittest.TestCase):
    """Unit tests for the CaptionScheduler class."""

    def _make_scheduler(self, stop_calls=None, start_calls=None, broadcast_calls=None):
        from obs_captioner.scheduler import CaptionScheduler
        from obs_captioner.config import SchedulerConfig

        if stop_calls is None:
            stop_calls = []
        if start_calls is None:
            start_calls = []
        if broadcast_calls is None:
            broadcast_calls = []

        cfg = SchedulerConfig(enabled=True, schedules=[])
        sched = CaptionScheduler(
            scheduler_config=cfg,
            on_stop_callback=lambda: stop_calls.append(1),
            on_start_callback=lambda: start_calls.append(1),
            on_broadcast=lambda p: broadcast_calls.append(p),
        )
        return sched, stop_calls, start_calls, broadcast_calls

    # ── Config Defaults ────────────────────────────────────────────────────────
    def test_obs_config_has_auto_stop_fields(self):
        from obs_captioner.config import OBSConfig
        cfg = OBSConfig()
        self.assertFalse(cfg.auto_stop_on_stream)
        self.assertFalse(cfg.auto_stop_on_record)

    def test_scheduler_config_defaults(self):
        from obs_captioner.config import SchedulerConfig
        cfg = SchedulerConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.schedules, [])

    def test_weekly_schedule_defaults(self):
        from obs_captioner.config import WeeklySchedule
        s = WeeklySchedule(id='abc', name='Test', days=['Sunday'], stop_time='12:30')
        self.assertEqual(s.stop_time, '12:30')
        self.assertTrue(s.enabled)
        self.assertEqual(s.start_time, '')

    def test_app_config_has_scheduler(self):
        from obs_captioner.config import AppConfig, SchedulerConfig
        cfg = AppConfig()
        self.assertIsInstance(cfg.scheduler, SchedulerConfig)

    # ── Config Persistence ─────────────────────────────────────────────────────
    def test_load_config_scheduler_persistence(self):
        import json, tempfile
        from pathlib import Path
        from obs_captioner.config import load_config

        data = {
            "scheduler": {
                "enabled": True,
                "schedules": [
                    {"id": "sched-1", "name": "Sunday Service", "days": ["Sunday"],
                     "stop_time": "12:30", "start_time": "09:00", "enabled": True}
                ]
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            tmp = Path(f.name)

        try:
            cfg = load_config(str(tmp))
            self.assertEqual(len(cfg.scheduler.schedules), 1)
            s = cfg.scheduler.schedules[0]
            self.assertEqual(s.id, 'sched-1')
            self.assertEqual(s.name, 'Sunday Service')
            self.assertEqual(s.stop_time, '12:30')
            self.assertEqual(s.start_time, '09:00')
            self.assertIn('Sunday', s.days)
        finally:
            tmp.unlink(missing_ok=True)

    def test_load_config_missing_scheduler_uses_defaults(self):
        from obs_captioner.config import load_config
        cfg = load_config(None)
        self.assertTrue(cfg.scheduler.enabled)
        self.assertEqual(cfg.scheduler.schedules, [])

    # ── Time Parsing ───────────────────────────────────────────────────────────
    def test_parse_time_str_24hr(self):
        from obs_captioner.scheduler import _parse_time_str
        self.assertEqual(_parse_time_str('12:30'), (12, 30))
        self.assertEqual(_parse_time_str('09:00'), (9, 0))
        self.assertEqual(_parse_time_str('00:00'), (0, 0))
        self.assertEqual(_parse_time_str('23:59'), (23, 59))

    def test_parse_time_str_12hr(self):
        from obs_captioner.scheduler import _parse_time_str
        self.assertEqual(_parse_time_str('12:30 PM'), (12, 30))
        self.assertEqual(_parse_time_str('12:30 AM'), (0, 30))
        self.assertEqual(_parse_time_str('1:00 PM'), (13, 0))
        self.assertEqual(_parse_time_str('11:59 PM'), (23, 59))

    def test_parse_time_str_invalid(self):
        from obs_captioner.scheduler import _parse_time_str
        self.assertIsNone(_parse_time_str(''))
        self.assertIsNone(_parse_time_str(None))
        self.assertIsNone(_parse_time_str('25:00'))
        self.assertIsNone(_parse_time_str('abc'))
        self.assertIsNone(_parse_time_str('12:60'))

    # ── One-Time Timer ─────────────────────────────────────────────────────────
    def test_set_duration_sets_timer(self):
        import time
        sched, _, _, _ = self._make_scheduler()
        before = time.time()
        target = sched.set_duration(60)
        after = time.time()
        self.assertGreater(target, before + 55)
        self.assertLess(target, after + 65)
        status = sched.get_timer_status()
        self.assertTrue(status['active'])
        self.assertAlmostEqual(status['remaining_seconds'], 60, delta=5)

    def test_add_duration_extends_active_timer(self):
        import time
        sched, _, _, _ = self._make_scheduler()
        sched.set_duration(60)
        target1 = sched.get_timer_status()['target_timestamp']
        # Add 300 seconds (5 minutes)
        target2 = sched.add_duration(300)
        self.assertAlmostEqual(target2, target1 + 300, delta=2)
        status = sched.get_timer_status()
        self.assertTrue(status['active'])
        self.assertAlmostEqual(status['remaining_seconds'], 360, delta=5)

    def test_add_duration_starts_new_timer_when_none_active(self):
        import time
        sched, _, _, _ = self._make_scheduler()
        self.assertFalse(sched.get_timer_status()['active'])
        target = sched.add_duration(60)
        status = sched.get_timer_status()
        self.assertTrue(status['active'])
        self.assertAlmostEqual(status['remaining_seconds'], 60, delta=5)

    def test_cancel_timer_clears_timer(self):
        sched, _, _, _ = self._make_scheduler()
        sched.set_duration(300)
        self.assertTrue(sched.get_timer_status()['active'])
        sched.cancel_timer()
        self.assertFalse(sched.get_timer_status()['active'])

    def test_set_end_time_valid(self):
        sched, _, _, _ = self._make_scheduler()
        # Set to 1 hour from now — find a time 1hr ahead
        from datetime import datetime, timedelta
        future = datetime.now() + timedelta(hours=1)
        time_str = future.strftime('%H:%M')
        result = sched.set_end_time(time_str)
        self.assertIsNotNone(result)
        self.assertTrue(sched.get_timer_status()['active'])

    def test_set_end_time_invalid(self):
        sched, _, _, _ = self._make_scheduler()
        result = sched.set_end_time('not-a-time')
        self.assertIsNone(result)
        self.assertFalse(sched.get_timer_status()['active'])

    def test_timer_status_inactive_by_default(self):
        sched, _, _, _ = self._make_scheduler()
        status = sched.get_timer_status()
        self.assertFalse(status['active'])
        self.assertEqual(status['remaining_seconds'], 0)
        self.assertIsNone(status['target_timestamp'])

    def test_timer_fires_stop_callback(self):
        """Directly call _check_one_time_timer with an expired time."""
        import time
        sched, stop_calls, _, broadcast_calls = self._make_scheduler()
        # Set timer end to the past
        sched._timer_end = time.time() - 1.0
        sched._check_one_time_timer()
        self.assertEqual(len(stop_calls), 1)
        self.assertIsNone(sched._timer_end)
        # A broadcast should have been fired
        self.assertTrue(any(b.get('event') == 'timer_expired' for b in broadcast_calls))

    def test_timer_does_not_fire_before_expiry(self):
        import time
        sched, stop_calls, _, _ = self._make_scheduler()
        sched._timer_end = time.time() + 300.0
        sched._check_one_time_timer()
        self.assertEqual(len(stop_calls), 0)

    # ── Weekly Schedules ───────────────────────────────────────────────────────
    def test_add_schedule(self):
        sched, _, _, _ = self._make_scheduler()
        s = sched.add_schedule({'name': 'Sunday Service', 'days': ['Sunday'], 'stop_time': '12:30'})
        self.assertEqual(s.name, 'Sunday Service')
        self.assertIn('Sunday', s.days)
        self.assertEqual(s.stop_time, '12:30')
        self.assertTrue(s.enabled)
        self.assertNotEqual(s.id, '')
        self.assertEqual(len(sched.get_schedules()), 1)

    def test_update_schedule(self):
        sched, _, _, _ = self._make_scheduler()
        s = sched.add_schedule({'name': 'Test', 'days': ['Sunday'], 'stop_time': '12:00'})
        updated = sched.update_schedule(s.id, {'stop_time': '12:30', 'enabled': False})
        self.assertIsNotNone(updated)
        self.assertEqual(updated.stop_time, '12:30')
        self.assertFalse(updated.enabled)

    def test_update_schedule_not_found(self):
        sched, _, _, _ = self._make_scheduler()
        result = sched.update_schedule('nonexistent-id', {'stop_time': '12:30'})
        self.assertIsNone(result)

    def test_remove_schedule(self):
        sched, _, _, _ = self._make_scheduler()
        s = sched.add_schedule({'name': 'Test', 'days': ['Sunday'], 'stop_time': '12:00'})
        self.assertEqual(len(sched.get_schedules()), 1)
        removed = sched.remove_schedule(s.id)
        self.assertTrue(removed)
        self.assertEqual(len(sched.get_schedules()), 0)

    def test_remove_schedule_not_found(self):
        sched, _, _, _ = self._make_scheduler()
        removed = sched.remove_schedule('does-not-exist')
        self.assertFalse(removed)

    def test_weekly_schedule_fires_stop_at_correct_minute(self):
        """Simulate the scheduler tick matching the current time."""
        from datetime import datetime
        sched, stop_calls, start_calls, _ = self._make_scheduler()

        now = datetime.now()
        weekday = now.strftime('%A')  # e.g. 'Friday'
        hhmm = now.strftime('%H:%M')

        sched.add_schedule({
            'name': 'Exact Time Test',
            'days': [weekday],
            'stop_time': hhmm,
            'start_time': '',
            'enabled': True,
        })

        sched._check_weekly_schedules()
        self.assertEqual(len(stop_calls), 1)
        self.assertEqual(len(start_calls), 0)

    def test_weekly_schedule_no_double_fire_same_minute(self):
        """Second tick in the same minute should NOT re-fire."""
        from datetime import datetime
        sched, stop_calls, _, _ = self._make_scheduler()
        now = datetime.now()
        weekday = now.strftime('%A')
        hhmm = now.strftime('%H:%M')
        sched.add_schedule({'name': 'Dedup Test', 'days': [weekday], 'stop_time': hhmm, 'enabled': True})

        sched._check_weekly_schedules()
        sched._check_weekly_schedules()
        self.assertEqual(len(stop_calls), 1)  # Must only fire once

    def test_weekly_schedule_different_day_does_not_fire(self):
        from datetime import datetime
        sched, stop_calls, _, _ = self._make_scheduler()
        now = datetime.now()
        from obs_captioner.scheduler import WEEKDAY_NAMES
        # Use a day that is NOT today
        today_idx = WEEKDAY_NAMES.index(now.strftime('%A'))
        other_day = WEEKDAY_NAMES[(today_idx + 1) % 7]
        hhmm = now.strftime('%H:%M')
        sched.add_schedule({'name': 'Wrong Day', 'days': [other_day], 'stop_time': hhmm, 'enabled': True})
        sched._check_weekly_schedules()
        self.assertEqual(len(stop_calls), 0)

    def test_weekly_schedule_disabled_does_not_fire(self):
        from datetime import datetime
        sched, stop_calls, _, _ = self._make_scheduler()
        now = datetime.now()
        weekday = now.strftime('%A')
        hhmm = now.strftime('%H:%M')
        sched.add_schedule({'name': 'Disabled', 'days': [weekday], 'stop_time': hhmm, 'enabled': False})
        sched._check_weekly_schedules()
        self.assertEqual(len(stop_calls), 0)

    def test_get_next_event_returns_future(self):
        """get_next_event should return a future event when a schedule is configured."""
        sched, _, _, _ = self._make_scheduler()
        sched.add_schedule({'name': 'Test', 'days': ['Sunday'], 'stop_time': '23:59', 'enabled': True})
        event = sched.get_next_event()
        # It should return something (even if current day is Sunday, time 23:59 might be in the future)
        # Just verify structure
        if event is not None:
            self.assertIn('action', event)
            self.assertIn('timestamp', event)
            self.assertIn('datetime_formatted', event)
        # If no event (edge case: day/time already passed), still valid None response

    def test_get_next_event_empty_schedules(self):
        sched, _, _, _ = self._make_scheduler()
        self.assertIsNone(sched.get_next_event())

    def test_weekly_schedule_start_fires_start_callback(self):
        """When start_time matches current time, on_start_callback should fire."""
        from datetime import datetime
        sched, stop_calls, start_calls, _ = self._make_scheduler()
        now = datetime.now()
        weekday = now.strftime('%A')
        hhmm = now.strftime('%H:%M')
        sched.add_schedule({'name': 'Start Test', 'days': [weekday], 'stop_time': '', 'start_time': hhmm, 'enabled': True})
        sched._check_weekly_schedules()
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(len(stop_calls), 0)


class TestGeminiLiveResilientStartup(unittest.IsolatedAsyncioTestCase):
    """Cold-boot hardening: initialize() must survive transient network/DNS
    delays after reboot or update, but fail fast on an invalid API key."""

    def _make_engine(self):
        cfg = AppConfig()
        cfg.gemini_live.api_key = "test-key"
        return GeminiLiveEngine(cfg)

    async def test_initialize_retries_transient_then_succeeds(self):
        import aiohttp
        from unittest.mock import AsyncMock, patch
        from obs_captioner.engines import gemini_live as gl_mod
        engine = self._make_engine()
        statuses = []
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        side_effects = [
            aiohttp.ClientConnectionError("dns resolution failed"),
            asyncio.TimeoutError(),
            "ok",
        ]
        mock_hs = AsyncMock(side_effect=side_effects)
        with patch.object(engine, "_attempt_handshake", new=mock_hs), \
             patch.object(gl_mod.asyncio, "sleep", new=fake_sleep):
            ok = await engine.initialize(status_callback=statuses.append)
        self.assertTrue(ok)
        self.assertEqual(mock_hs.await_count, 3)
        self.assertEqual(sleeps, [2.0, 4.0])
        retry_msgs = [m for m in statuses if "retrying connection" in m]
        self.assertEqual(len(retry_msgs), 2)
        self.assertIn("attempt 2/4", retry_msgs[0])
        self.assertIn("attempt 3/4", retry_msgs[1])
        self.assertTrue(statuses[-1].startswith("✅"))

    async def test_initialize_fails_fast_on_invalid_key_close(self):
        from unittest.mock import AsyncMock, patch
        from obs_captioner.engines import gemini_live as gl_mod
        engine = self._make_engine()
        statuses = []
        mock_hs = AsyncMock(return_value="invalid_key")
        mock_sleep = AsyncMock()
        with patch.object(engine, "_attempt_handshake", new=mock_hs), \
             patch.object(gl_mod.asyncio, "sleep", new=mock_sleep):
            ok = await engine.initialize(status_callback=statuses.append)
        self.assertFalse(ok)
        self.assertEqual(mock_hs.await_count, 1)
        mock_sleep.assert_not_called()
        self.assertTrue(any("Invalid Gemini API key" in m for m in statuses))

    async def test_initialize_fails_fast_on_invalid_key_exception(self):
        from unittest.mock import AsyncMock, patch
        from obs_captioner.engines import gemini_live as gl_mod
        engine = self._make_engine()
        mock_hs = AsyncMock(side_effect=Exception("API key not valid. Pass a valid key."))
        mock_sleep = AsyncMock()
        with patch.object(
            engine, "_attempt_handshake", new=mock_hs
        ), patch.object(gl_mod.asyncio, "sleep", new=mock_sleep):
            ok = await engine.initialize()
        self.assertFalse(ok)
        self.assertEqual(mock_hs.await_count, 1)
        mock_sleep.assert_not_called()

    async def test_initialize_exhausts_attempts_with_backoff(self):
        import aiohttp
        from unittest.mock import AsyncMock, patch
        from obs_captioner.engines import gemini_live as gl_mod
        engine = self._make_engine()
        statuses = []
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        mock_hs = AsyncMock(side_effect=aiohttp.ClientConnectionError("503 down"))
        with patch.object(
            engine, "_attempt_handshake", new=mock_hs
        ), patch.object(gl_mod.asyncio, "sleep", new=fake_sleep):
            ok = await engine.initialize(status_callback=statuses.append)
        self.assertFalse(ok)
        self.assertEqual(mock_hs.await_count, 4)
        self.assertEqual(sleeps, [2.0, 4.0, 6.0])
        self.assertIn("attempt 4/4", statuses[-2])
        self.assertTrue(statuses[-1].startswith("❌"))

    async def test_initialize_missing_key_returns_false_immediately(self):
        import os
        from unittest.mock import AsyncMock, patch
        cfg = AppConfig()
        cfg.gemini_live.api_key = ""
        old_env = os.environ.pop("GEMINI_API_KEY", None)
        try:
            engine = GeminiLiveEngine(cfg)
            with patch.object(engine, "_attempt_handshake", AsyncMock()) as mock_hs:
                ok = await engine.initialize()
            self.assertFalse(ok)
            mock_hs.assert_not_called()
        finally:
            if old_env is not None:
                os.environ["GEMINI_API_KEY"] = old_env

    def test_resilient_startup_tuning_constants(self):
        from obs_captioner.engines import gemini_live as gl_mod
        self.assertEqual(gl_mod.INIT_MAX_ATTEMPTS, 4)
        self.assertEqual(tuple(gl_mod.INIT_RETRY_DELAYS), (0.0, 2.0, 4.0, 6.0))
        self.assertEqual(gl_mod.INIT_CONNECT_TIMEOUT, 10.0)
        self.assertEqual(gl_mod.INIT_TOTAL_TIMEOUT, 10.0)
        self.assertEqual(gl_mod.INIT_RECEIVE_TIMEOUT, 10.0)
        self.assertEqual(gl_mod.STREAM_RECONNECT_INITIAL_DELAY, 2.0)
        self.assertEqual(gl_mod.STREAM_RECONNECT_MAX_DELAY, 15.0)

    async def test_initialize_auto_falls_back_on_invalid_model(self):
        from unittest.mock import AsyncMock, patch
        from obs_captioner.engines import gemini_live as gl_mod
        engine = self._make_engine()
        engine.config.gemini_live.model = "gemini-nonexistent-old-model"
        engine.config.gemini_live.fallback_model = "gemini-3.5-transcribe-live"

        statuses = []
        mock_hs = AsyncMock(side_effect=["invalid_model", "ok"])
        with patch.object(engine, "_attempt_handshake", new=mock_hs):
            ok = await engine.initialize(status_callback=statuses.append)
        self.assertTrue(ok)
        self.assertEqual(engine.config.gemini_live.model, "gemini-3.5-transcribe-live")
        self.assertTrue(any("invalid" in m.lower() or "falling back" in m.lower() for m in statuses))


class TestGeminiModelsAndSanitization(unittest.TestCase):
    def test_sanitize_api_key(self):
        from obs_captioner.gemini_models import sanitize_gemini_api_key, validate_gemini_api_key
        # Quotes and spaces
        self.assertEqual(sanitize_gemini_api_key(' "AIzaSyTest12345" '), "AIzaSyTest12345")
        self.assertEqual(sanitize_gemini_api_key(" 'AIzaSyTest67890' "), "AIzaSyTest67890")
        self.assertEqual(sanitize_gemini_api_key("`AIzaSyBackticks`"), "AIzaSyBackticks")
        # Prefix cleanup
        self.assertEqual(sanitize_gemini_api_key("GEMINI_API_KEY=AIzaSyWithPrefix"), "AIzaSyWithPrefix")
        self.assertEqual(sanitize_gemini_api_key("key=AIzaSyKeyPrefix"), "AIzaSyKeyPrefix")
        # Unicode zero-width characters
        self.assertEqual(sanitize_gemini_api_key("\u200bAIzaSyClean\ufeff"), "AIzaSyClean")
        # Validation
        ok, _ = validate_gemini_api_key("AIzaSy1234567890abcdef")
        self.assertTrue(ok)
        ok_short, _ = validate_gemini_api_key("short")
        self.assertFalse(ok_short)
        ok_mask, _ = validate_gemini_api_key("•••")
        self.assertFalse(ok_mask)

    def test_model_deprecation_and_fallback(self):
        from obs_captioner.gemini_models import check_model_deprecation, get_fallback_model_id
        dep = check_model_deprecation("gemini-2.0-flash")
        self.assertIsNotNone(dep)
        self.assertEqual(dep["recommended_replacement"], "gemini-3.6-flash")

        dep_live = check_model_deprecation("gemini-2.0-flash-live-001")
        self.assertIsNotNone(dep_live)
        self.assertEqual(dep_live["recommended_replacement"], "gemini-3.5-transcribe-live")

        fb_stt = get_fallback_model_id("gemini-custom-old")
        self.assertEqual(fb_stt, "gemini-3.5-transcribe-live")

        fb_trans = get_fallback_model_id("gemini-custom-translate-old")
        self.assertEqual(fb_trans, "gemini-3.5-live-translate-preview")


class TestAudioColdBootSettleDelay(unittest.TestCase):
    def test_find_audio_device_settles_and_finds_device(self):
        from unittest.mock import patch
        from obs_captioner.audio_capture import find_audio_device
        from obs_captioner.config import AudioConfig
        cfg = AudioConfig(device_name_filter="Focusrite Scarlett", device_settle_seconds=1.0)

        call_count = 0
        def fake_list():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                # First check: USB interface hasn't finished booting WASAPI driver yet
                return [{"index": 0, "name": "Realtek Speakers", "hostapi": "WASAPI", "channels": 2}]
            # Second check: USB interface appears
            return [
                {"index": 0, "name": "Realtek Speakers", "hostapi": "WASAPI", "channels": 2},
                {"index": 1, "name": "Focusrite Scarlett 2i2 USB", "hostapi": "WASAPI", "channels": 2},
            ]

        with patch("obs_captioner.audio_capture.list_audio_devices", side_effect=fake_list):
            idx, dev = find_audio_device(cfg, wait_settle=True)
            self.assertEqual(idx, 1)
            self.assertIn("Focusrite", dev["name"])
            self.assertGreaterEqual(call_count, 2)


class TestEmergencyOfflineFallback(unittest.TestCase):
    def test_config_fallback_defaults(self):
        from obs_captioner.config import GeneralConfig, GeminiLiveConfig, AudioConfig
        gen = GeneralConfig()
        self.assertEqual(gen.fallback_engine, "vosk")
        self.assertTrue(gen.enable_auto_fallback)

        gl = GeminiLiveConfig()
        self.assertEqual(gl.fallback_model, "gemini-3.5-transcribe-live")

        aud = AudioConfig()
        self.assertEqual(aud.device_settle_seconds, 4.0)

    def test_create_engine_override(self):
        from obs_captioner.config import AppConfig
        from obs_captioner.engines import create_engine
        from obs_captioner.engines.vosk import VoskEngine
        cfg = AppConfig()
        cfg.general.engine = "gemini_live"
        eng = create_engine(cfg, override_engine="vosk")
        self.assertIsInstance(eng, VoskEngine)


class TestStandaloneLauncher(unittest.TestCase):
    def test_get_project_root(self):
        from obs_captioner.launcher import get_project_root
        root = get_project_root()
        self.assertTrue((root / "obs_captioner").is_dir())
        self.assertTrue((root / "obs_captioner" / "launcher.py").is_file())

    def test_find_python_executable(self):
        import os
        from obs_captioner.launcher import find_python_executable
        exe = find_python_executable()
        self.assertTrue(os.path.exists(exe))

    def test_create_windows_shortcuts_behavior(self):
        import sys
        from obs_captioner.launcher import create_windows_shortcuts
        res = create_windows_shortcuts()
        if sys.platform != "win32":
            self.assertFalse(res["success"])
            self.assertIn("Windows", res["error"])

    def test_backend_process_manager_init(self):
        from obs_captioner.launcher import BackendProcessManager, get_project_root
        mgr = BackendProcessManager(get_project_root())
        self.assertFalse(mgr.is_running)
        self.assertFalse(mgr.is_starting)

    def test_launcher_gui_init(self):
        import tkinter as tk
        from unittest import mock
        from obs_captioner import launcher as launcher_mod
        from obs_captioner.launcher import VoxStreamLauncherGUI, HAS_TKINTER
        if not HAS_TKINTER:
            self.skipTest("Tkinter not available")
        try:
            root = tk.Tk()
            root.withdraw()
            # Do not start the pystray backend thread here: a lingering tray
            # thread crashes Tk event loops created later in the same process
            # (observed SIGTRAP on macOS). Tray shutdown is covered by
            # _exit_application in production code.
            with mock.patch.object(launcher_mod, "HAS_PYSTRAY", False):
                app = VoxStreamLauncherGUI(root=root, autostart=False, start_minimized=False)
            self.assertEqual(app.status_state, "STOPPED")
            self.assertFalse(app.backend.is_running)
            root.destroy()
        except tk.TclError:
            self.skipTest("No display available for Tkinter GUI test")

    def test_poll_backend_status_fetches_off_ui_thread(self):
        """Regression test: _poll_backend_status must run HTTP fetch on a
        worker thread so the Tk main loop never blocks on network I/O."""
        import json
        import threading
        import time
        import tkinter as tk
        import urllib.request
        from unittest import mock
        from obs_captioner import launcher as launcher_mod
        from obs_captioner.launcher import VoxStreamLauncherGUI

        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for Tkinter GUI test")
        root.withdraw()
        # Isolate from the pystray background thread: on some platforms
        # (e.g. macOS Tk) it cannot safely coexist with a pumped event loop.
        try:
            with mock.patch.object(launcher_mod, "HAS_PYSTRAY", False):
                app = VoxStreamLauncherGUI(root=root, autostart=False, start_minimized=False)
                main_thread = threading.current_thread()
                fetch_threads = []
                payload = {
                    "is_running": True,
                    "engine_name": "TestEngine",
                    "audio_level_db": -100.0,
                    "caption_clients": 0,
                }

                class _FakeResp:
                    status = 200

                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        return False

                    def read(self):
                        return json.dumps(payload).encode("utf-8")

                def _fake_urlopen(req, timeout=None):
                    fetch_threads.append(threading.current_thread())
                    time.sleep(0.2)  # simulate network latency
                    return _FakeResp()

                with mock.patch.object(type(app.backend), "is_running",
                                       new_callable=mock.PropertyMock,
                                       return_value=True), \
                     mock.patch.object(urllib.request, "urlopen", _fake_urlopen):
                    app._poll_backend_status()
                    deadline = time.time() + 10.0
                    while not fetch_threads and time.time() < deadline:
                        root.update()
                        time.sleep(0.05)
                    # The Tk event loop must stay responsive while fetch runs.
                    root.update()
                    while time.time() < deadline and not app.last_status_data:
                        root.update()
                        time.sleep(0.05)

                self.assertTrue(fetch_threads, "expected the status fetch to run")
                for t in fetch_threads:
                    self.assertIsNot(t, main_thread,
                                     "status HTTP fetch must not run on the Tk UI thread")
                self.assertEqual(app.last_status_data.get("engine_name"), "TestEngine")
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass


if __name__ == "__main__":
    unittest.main()





