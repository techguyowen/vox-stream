"""Comprehensive unit test suite for OBS Live Captioner PRO Suite."""

import asyncio
import math
import struct
import unittest
from obs_captioner.config import load_config, AppConfig, save_config, OverlayConfig
from obs_captioner.vad import VoiceActivityDetector
from obs_captioner.censor import ContentFilter, CensorConfig
from obs_captioner.history import TranscriptHistory
from obs_captioner.engines import (
    create_engine,
    GoogleWebEngine,
    GoogleSTTEngine,
    GeminiLiveEngine,
    LocalWhisperEngine,
)
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
        self.assertIn("cyberpunk_neon", THEME_PRESETS)
        self.assertIn("minimal_cinema", THEME_PRESETS)
        self.assertIn("twitch_purple", THEME_PRESETS)
        self.assertIn("comic_pop", THEME_PRESETS)

        all_presets = get_all_presets()
        self.assertGreaterEqual(len(all_presets), 5)

    def test_apply_theme_to_overlay(self):
        ov = OverlayConfig()
        ov.apply_theme("cyberpunk_neon")
        self.assertEqual(ov.theme_id, "cyberpunk_neon")
        self.assertEqual(ov.text_color, "#00F0FF")
        self.assertEqual(ov.font_family, "'Bebas Neue', sans-serif")


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


class TestTranslator(unittest.TestCase):

    def test_translator_disabled(self):
        cfg = TranslationConfig(enabled=False)
        translator = SubtitleTranslator(cfg)
        
        loop = asyncio.new_event_loop()
        res, trans = loop.run_until_complete(translator.translate_text("Hello world"))
        loop.close()
        
        self.assertEqual(res, "Hello world")
        self.assertIsNone(trans)


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
        cfg = load_config()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.audio.sample_rate, 16000)
        self.assertEqual(cfg.obs.port, 4455)
        self.assertEqual(cfg.overlay.port, 8765)
        self.assertTrue(cfg.censor.enabled)
        self.assertFalse(cfg.obs.auto_open_projector)
        self.assertEqual(cfg.obs.projector_monitor_index, 1)
        self.assertEqual(cfg.obs.projector_type, "preview")

    def test_engine_factory(self):
        cfg = load_config()
        
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

    def test_vad_energy_calculation(self):
        vad = VoiceActivityDetector(enable_silero=False, noise_gate_db=-40.0)
        
        silence_bytes = bytes(3200)
        self.assertEqual(vad.calculate_rms_db(silence_bytes), -100.0)
        self.assertFalse(vad.is_speech(silence_bytes))

        samples = [int(math.sin(2 * math.pi * 440 * i / 16000) * 20000) for i in range(1600)]
        loud_bytes = struct.pack(f"<{len(samples)}h", *samples)
        db = vad.calculate_rms_db(loud_bytes)
        self.assertGreater(db, -20.0)
        self.assertTrue(vad.is_speech(loud_bytes))


if __name__ == "__main__":
    unittest.main()
