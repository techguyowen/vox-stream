"""Real-time multi-language subtitle translation engine."""

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger("obs_captioner.translator")

SUPPORTED_LANGUAGES = {
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "pt": "Portuguese",
    "it": "Italian",
    "zh": "Chinese (Simplified)",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
}


@dataclass
class TranslationConfig:
    enabled: bool = False
    source_language: str = "auto"
    target_language: str = "es"  # e.g., "es" for Spanish
    display_mode: str = "dual"  # "dual" (original + translated), "translated_only"


class SubtitleTranslator:
    """Performs low-latency subtitle translation with memory caching."""

    def __init__(self, config: TranslationConfig):
        self.config = config
        self._cache: Dict[str, str] = {}

    async def translate_text(self, text: str) -> Tuple[str, Optional[str]]:
        """
        Translate input text based on configuration.
        Returns: (primary_text, translated_text_if_dual)
        """
        if not self.config.enabled or not text.strip():
            return text, None

        clean_text = text.strip()
        target = self.config.target_language or "es"
        source = self.config.source_language or "auto"
        cache_key = f"{source}:{target}:{clean_text}"

        if cache_key in self._cache:
            translated = self._cache[cache_key]
        else:
            translated = await self._fetch_translation(clean_text, source, target)
            if translated:
                self._cache[cache_key] = translated
                if len(self._cache) > 2000:
                    self._cache.clear()

        if not translated:
            return clean_text, None

        if self.config.display_mode == "translated_only":
            return translated, None
        else:
            # Dual mode: return original and translated
            return clean_text, translated

    async def _fetch_translation(self, text: str, source: str, target: str) -> Optional[str]:
        """Fetch translation via async thread executor using lightweight REST endpoint."""
        loop = asyncio.get_event_loop()

        def _sync_fetch():
            try:
                encoded_text = urllib.parse.quote(text)
                url = (
                    f"https://translate.googleapis.com/translate_a/single?"
                    f"client=gtx&sl={source}&tl={target}&dt=t&q={encoded_text}"
                )
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                with urllib.request.urlopen(req, timeout=1.5) as response:
                    raw = response.read().decode("utf-8")
                    data = json.loads(raw)
                    if data and data[0]:
                        translated_parts = [part[0] for part in data[0] if part and part[0]]
                        return "".join(translated_parts).strip()
            except Exception as e:
                logger.debug(f"Translation request failed: {e}")
            return None

        return await loop.run_in_executor(None, _sync_fetch)
