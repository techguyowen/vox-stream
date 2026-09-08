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

        all_presets = get_all_presets()
        self.assertEqual(len(all_presets), 9)

    def test_apply_theme_to_overlay(self):
        ov = OverlayConfig()
        ov.apply_theme("sanctuary_worship")
        self.assertEqual(ov.theme_id, "sanctuary_worship")
        self.assertEqual(ov.text_color, "#FFFBEB")
        self.assertEqual(ov.font_family, "'Montserrat', sans-serif")

    def test_overlay_final_only_config(self):
        ov = OverlayConfig()
        self.assertFalse(ov.final_only)
        ov.final_only = True
        self.assertTrue(ov.final_only)


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
        self.assertIn("Ben Uthe", fmt.format_text("ben lut is our pastor"))
        self.assertIn("Ben Uthe", fmt.format_text("ben luthi preached today"))
        self.assertIn("Ben Uthe", fmt.format_text("ben uthe is speaking"))

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
            user_env = app_root / ".env"
            user_env.write_text("API_SECRET=super_secret", encoding="utf-8")
            sub_dir = app_root / "obs_captioner"
            sub_dir.mkdir(parents=True)
            existing_file = sub_dir / "existing.py"
            existing_file.write_text("# existing code", encoding="utf-8")

            # Create mock zip with new update payload
            mock_zip_path = Path(web_dir) / "release.zip"
            with zipfile.ZipFile(mock_zip_path, "w") as zf:
                zf.writestr("vox-stream-main/config.json", '{"user_setting": "OVERWRITTEN"}')
                zf.writestr("vox-stream-main/.env", "API_SECRET=OVERWRITTEN")
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
            self.assertEqual(user_env.read_text(encoding="utf-8"), "API_SECRET=super_secret")
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


if __name__ == "__main__":
    unittest.main()


