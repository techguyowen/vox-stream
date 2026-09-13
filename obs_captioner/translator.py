"""Real-time multi-language subtitle translation engine with multi-fallback providers."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, Any, Optional, Tuple

if TYPE_CHECKING:
    from obs_captioner.engines.nllb_translator import NLLBTranslator

logger = logging.getLogger("obs_captioner.translator")

SUPPORTED_LANGUAGES = {
    "es": "Spanish (Español)",
    "fr": "French (Français)",
    "de": "German (Deutsch)",
    "pt": "Portuguese (Português)",
    "it": "Italian (Italiano)",
    "zh": "Chinese Simplified (中文)",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "ru": "Russian (Русский)",
    "ar": "Arabic (العربية)",
    "hi": "Hindi (हिन्दी)",
    "nl": "Dutch (Nederlands)",
    "pl": "Polish (Polski)",
    "sv": "Swedish (Svenska)",
    "tr": "Turkish (Türkçe)",
    "uk": "Ukrainian (Українська)",
    "vi": "Vietnamese (Tiếng Việt)",
    "tl": "Tagalog / Filipino",
}

LANG_CODE_MAP = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
}


@dataclass
class TranslationConfig:
    enabled: bool = False
    source_language: str = "auto"
    target_language: str = "es"  # e.g., "es" for Spanish
    display_mode: str = "dual"  # "dual" (original + translated), "translated_only"
    provider: str = "google_free"  # "google_free", "nllb", "gemini_live"
    gemini_api_key: str = ""  # If empty, automatically falls back to gemini_live API key
    temperature: float = 0.1
    dual_subtitle_color: str = "#FFD700"  # Classic Subtitle Gold/Yellow
    dual_subtitle_scale: float = 0.85  # Secondary subtitle font size scale (0.6 - 1.0)
    dual_subtitle_format: str = "clean"  # "clean" (clean 2-line stack) or "parentheses" ((Texto))
    enable_disk_cache: bool = True  # Persistent SQLite disk cache in ~/.cache/voxstream/


class SubtitleTranslator:
    """Performs low-latency subtitle translation with memory caching and multi-provider fallbacks."""

    def __init__(
        self,
        config: Optional[TranslationConfig] = None,
        api_key_resolver: Optional[Callable[[], str]] = None,
        nllb_translator: Optional[NLLBTranslator] = None,
        disk_cache: Optional[Any] = None,
    ):
        self.config = config or TranslationConfig()
        self.api_key_resolver = api_key_resolver
        self._cache: Dict[str, str] = {}
        self._nllb_translator: Optional[NLLBTranslator] = nllb_translator
        self._disk_cache = disk_cache

    @property
    def disk_cache(self) -> Any:
        """Lazily obtain or create persistent SQLite disk cache."""
        if self._disk_cache is False or not getattr(self.config, "enable_disk_cache", True):
            return None
        if self._disk_cache is None:
            try:
                from obs_captioner.translation_cache import TranslationDiskCache
                self._disk_cache = TranslationDiskCache()
            except Exception as e:
                logger.debug(f"Failed to initialize TranslationDiskCache: {e}")
        return self._disk_cache

    @property
    def nllb_translator(self) -> Any:
        """Lazily obtain or create local NLLBTranslator instance."""
        if self._nllb_translator is None:
            from obs_captioner.engines.nllb_translator import NLLBTranslator
            self._nllb_translator = NLLBTranslator()
        return self._nllb_translator

    def get_gemini_api_key(self) -> str:
        """Resolve Gemini API key from translation config or global key resolver."""
        explicit_key = (getattr(self.config, "gemini_api_key", "") or "").strip()
        if explicit_key and explicit_key != "•••":
            return explicit_key
        if self.api_key_resolver:
            try:
                resolved = (self.api_key_resolver() or "").strip()
                if resolved and resolved != "•••":
                    return resolved
            except Exception as e:
                logger.debug(f"Error resolving fallback Gemini API key: {e}")
        return ""

    async def _fetch_gemini_translation(
        self, text: str, source: str, target: str, api_key: str
    ) -> Optional[str]:
        """Translate text using Gemini REST API with fallback."""
        if not api_key:
            return None

        loop = asyncio.get_event_loop()
        target_name = SUPPORTED_LANGUAGES.get(target.lower(), target)

        def _sync_gemini_call() -> Optional[str]:
            payload = {
                "contents": [{"parts": [{"text": f"Translate to {target_name}: {text}"}]}],
            }
            data_bytes = json.dumps(payload).encode("utf-8")
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
            try:
                req = urllib.request.Request(
                    endpoint,
                    data=data_bytes,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3.5) as resp:
                    resp_bytes = resp.read()
                    data = json.loads(resp_bytes.decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            res = parts[0].get("text", "").strip()
                            if (res.startswith('"') and res.endswith('"')) or (res.startswith("'") and res.endswith("'")):
                                res = res[1:-1].strip()
                            return res
            except Exception:
                return None
            return None

        try:
            return await loop.run_in_executor(None, _sync_gemini_call)
        except Exception:
            return None

    async def translate_to_language(
        self, text: str, target_lang: str, source_lang: str = "auto", provider_override: Optional[str] = None
    ) -> str:
        """Translate text directly into a specified target language."""
        if not text or not text.strip():
            return ""

        clean_text = text.strip()
        t_code = target_lang.lower().strip() if target_lang else "en"

        if t_code in ("en", "original", "none", ""):
            return clean_text

        # Map language code if necessary (e.g. zh -> zh-CN)
        resolved_target = LANG_CODE_MAP.get(t_code, t_code)
        provider = provider_override or getattr(self.config, "provider", "google_free") or "google_free"
        cache_key = f"{provider}:{source_lang}:{resolved_target}:{clean_text}"

        # 1. Check L1 in-memory cache (<0.1ms)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 2. Check L2 persistent SQLite disk cache (<1ms on warm reboot)
        if self.disk_cache:
            cached_disk = self.disk_cache.get(cache_key)
            if cached_disk:
                self._cache[cache_key] = cached_disk
                return cached_disk

        translated = None
        if provider == "nllb":
            try:
                src_code = source_lang if source_lang and source_lang != "auto" else "en"
                translated = await self.nllb_translator.translate(
                    clean_text,
                    target_lang=resolved_target,
                    source_lang=src_code,
                )
                if not translated:
                    logger.debug("NLLB model not loaded or returned None. Falling back to default provider.")
            except Exception as e:
                logger.warning(f"NLLB-200 translation failed: {e}. Falling back to default provider.")
                translated = None

        elif provider in ("gemini", "gemini_live"):
            api_key = self.get_gemini_api_key()
            if api_key:
                translated = await self._fetch_gemini_translation(clean_text, source_lang, resolved_target, api_key)
            else:
                logger.debug("Gemini translation requested but no API key available. Falling back to default provider.")

        # Fallback to Google / MyMemory free endpoints if provider was not used or failed
        if not translated:
            translated = await self._fetch_translation(clean_text, source_lang, resolved_target)

        if translated:
            self._cache[cache_key] = translated
            if self.disk_cache:
                self.disk_cache.set(
                    cache_key=cache_key,
                    source_text=clean_text,
                    translated_text=translated,
                    provider=provider,
                    source_lang=source_lang,
                    target_lang=resolved_target,
                )
            if len(self._cache) > 3000:
                self._cache.clear()
            return translated

        return clean_text

    async def prewarm_async(self) -> bool:
        """Eagerly warm up the selected translation engine in the background."""
        provider = getattr(self.config, "provider", "google_free")
        if provider == "nllb":
            if self.nllb_translator.is_available():
                logger.info("Pre-warming Meta NLLB-200 translation engine...")
                return await self.nllb_translator.prewarm()
            return False
        return False

    async def translate_text(self, text: str) -> Tuple[str, Optional[str]]:
        """
        Translate input text based on global configuration.
        Returns: (primary_text, translated_text_if_dual)
        """
        if not self.config.enabled or not text.strip():
            return text, None

        clean_text = text.strip()
        target = self.config.target_language or "es"
        source = self.config.source_language or "auto"

        translated = await self.translate_to_language(clean_text, target_lang=target, source_lang=source)
        if not translated or translated == clean_text:
            return clean_text, None

        if self.config.display_mode == "translated_only":
            return translated, None
        else:
            # Dual mode: return original and translated
            return clean_text, translated

    async def _fetch_translation(self, text: str, source: str, target: str) -> Optional[str]:
        """Fetch translation using resilient multi-endpoint fallback pipeline."""
        loop = asyncio.get_event_loop()

        def _sync_fetch() -> Optional[str]:
            encoded_text = urllib.parse.quote(text)
            
            # List of high-speed free endpoints to try in order
            endpoints = [
                # 1. Google Chrome extension endpoint (fastest, no quota block)
                (
                    f"https://translate.googleapis.com/translate_a/single?"
                    f"client=dict-chrome-ex&sl={source}&tl={target}&dt=t&q={encoded_text}",
                    "google"
                ),
                # 2. Google WebApp client
                (
                    f"https://translate.googleapis.com/translate_a/single?"
                    f"client=webapp&sl={source}&tl={target}&dt=t&q={encoded_text}",
                    "google"
                ),
                # 3. MyMemory Free API
                (
                    f"https://api.mymemory.translated.net/get?q={encoded_text}&langpair=en|{target}",
                    "mymemory"
                )
            ]

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
            }

            for url, provider in endpoints:
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=2.0) as response:
                        raw = response.read().decode("utf-8")
                        data = json.loads(raw)
                        
                        if provider == "google":
                            if isinstance(data, list) and data and data[0]:
                                parts = [part[0] for part in data[0] if part and part[0]]
                                result = "".join(parts).strip()
                                if result:
                                    return result
                        elif provider == "mymemory":
                            if isinstance(data, dict) and "responseData" in data:
                                result = data["responseData"].get("translatedText", "").strip()
                                if result and not result.startswith("MYMEMORY WARNING:"):
                                    return result
                except Exception as e:
                    logger.debug(f"Translation provider {provider} failed: {e}")
                    continue

            return None

        return await loop.run_in_executor(None, _sync_fetch)
