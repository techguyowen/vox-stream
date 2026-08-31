"""Real-time multi-language subtitle translation engine with multi-fallback providers."""

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

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


class SubtitleTranslator:
    """Performs low-latency subtitle translation with memory caching and multi-provider fallbacks."""

    def __init__(self, config: Optional[TranslationConfig] = None):
        self.config = config or TranslationConfig()
        self._cache: Dict[str, str] = {}

    async def translate_to_language(self, text: str, target_lang: str, source_lang: str = "auto") -> str:
        """Translate text directly into a specified target language."""
        if not text or not text.strip():
            return ""

        clean_text = text.strip()
        t_code = target_lang.lower().strip() if target_lang else "en"

        if t_code in ("en", "original", "none", ""):
            return clean_text

        # Map language code if necessary (e.g. zh -> zh-CN)
        resolved_target = LANG_CODE_MAP.get(t_code, t_code)
        cache_key = f"{source_lang}:{resolved_target}:{clean_text}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        translated = await self._fetch_translation(clean_text, source_lang, resolved_target)
        if translated:
            self._cache[cache_key] = translated
            if len(self._cache) > 3000:
                self._cache.clear()
            return translated

        return clean_text

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
