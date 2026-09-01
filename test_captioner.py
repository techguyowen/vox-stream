"""Comprehensive unit test suite for OBS Live Captioner PRO Suite."""

import asyncio
import math
import struct
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
        self.assertIn("broadcast_news", THEME_PRESETS)
        self.assertIn("sanctuary_worship", THEME_PRESETS)
        self.assertIn("minimal_cinema", THEME_PRESETS)
        self.assertIn("stage_confidence", THEME_PRESETS)
        self.assertIn("corporate_keynote", THEME_PRESETS)
        self.assertIn("editorial_nordic", THEME_PRESETS)
        self.assertIn("youtube_cc", THEME_PRESETS)
        self.assertIn("opendyslexic", THEME_PRESETS)

        all_presets = get_all_presets()
        self.assertEqual(len(all_presets), 9)

    def test_apply_theme_to_overlay(self):
        ov = OverlayConfig()
        ov.apply_theme("sanctuary_worship")
        self.assertEqual(ov.theme_id, "sanctuary_worship")
        self.assertEqual(ov.text_color, "#FFFBEB")
        self.assertEqual(ov.font_family, "'Montserrat', sans-serif")


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
        self.assertEqual(cfg.obs.port, 4455)
        self.assertEqual(cfg.overlay.port, 8765)
        self.assertTrue(cfg.censor.enabled)
        self.assertFalse(cfg.obs.auto_open_projector)
        self.assertEqual(cfg.obs.projector_monitor_index, 1)
        self.assertEqual(cfg.obs.projector_type, "preview")

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
        self.server = WebOverlayServer(self.cfg)
        return self.server.app

    async def test_manifest_endpoint(self):
        resp = await self.client.request('GET', '/manifest.json')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, 'application/manifest+json')
        manifest = await resp.json()
        self.assertEqual(manifest['name'], 'VoxStream Stage Display')
        self.assertEqual(manifest['start_url'], '/display')

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



if __name__ == "__main__":
    unittest.main()
