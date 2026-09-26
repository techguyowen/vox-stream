"""Gemini Model Catalog, Scanner, Deprecation Registry, and Sanitization."""

import asyncio
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("obs_captioner.gemini_models")

# Known Google Gemini deprecations & sunset dates with recommended replacements
KNOWN_GEMINI_DEPRECATIONS: Dict[str, Dict[str, Any]] = {
    "gemini-2.0-flash": {
        "shutdown_date": "June 1, 2026",
        "recommended_replacement": "gemini-3.6-flash",
        "status": "deprecated",
        "category": "general",
        "notice": "Google announced sunset on June 1, 2026. Upgrade to Gemini 3.6 Flash.",
    },
    "gemini-2.0-flash-001": {
        "shutdown_date": "June 1, 2026",
        "recommended_replacement": "gemini-3.6-flash",
        "status": "deprecated",
        "category": "general",
        "notice": "Google announced sunset on June 1, 2026. Upgrade to Gemini 3.6 Flash.",
    },
    "gemini-2.0-flash-lite": {
        "shutdown_date": "June 1, 2026",
        "recommended_replacement": "gemini-3.1-flash-lite",
        "status": "deprecated",
        "category": "general",
        "notice": "Google announced sunset on June 1, 2026. Upgrade to Gemini 3.1 Flash-Lite.",
    },
    "gemini-2.0-flash-lite-001": {
        "shutdown_date": "June 1, 2026",
        "recommended_replacement": "gemini-3.1-flash-lite",
        "status": "deprecated",
        "category": "general",
        "notice": "Google announced sunset on June 1, 2026. Upgrade to Gemini 3.1 Flash-Lite.",
    },
    "gemini-2.0-flash-live-001": {
        "shutdown_date": "December 9, 2025",
        "recommended_replacement": "gemini-3.5-transcribe-live",
        "status": "shutdown",
        "category": "transcribe_live",
        "notice": "Shutdown December 9, 2025. Replacement: gemini-3.5-transcribe-live.",
    },
    "gemini-live-2.5-flash-preview": {
        "shutdown_date": "December 9, 2025",
        "recommended_replacement": "gemini-3.1-flash-live-preview",
        "status": "shutdown",
        "category": "live",
        "notice": "Shutdown December 9, 2025. Replacement: gemini-3.1-flash-live-preview.",
    },
    "gemini-2.5-flash-native-audio-preview-12-2025": {
        "shutdown_date": "2026",
        "recommended_replacement": "gemini-3.1-flash-live-preview",
        "status": "deprecated",
        "category": "live",
        "notice": "Preview model deprecated. Replacement: gemini-3.1-flash-live-preview.",
    },
    "gemini-2.0-flash-lite-preview": {
        "shutdown_date": "December 9, 2025",
        "recommended_replacement": "gemini-2.5-flash-lite",
        "status": "shutdown",
        "category": "general",
        "notice": "Shutdown December 9, 2025.",
    },
}

# Canonical fallback model when an unresolvable or sunset model ID fails
CANONICAL_TRANSCRIBE_FALLBACK = "gemini-3.5-transcribe-live"
CANONICAL_TRANSLATE_FALLBACK = "gemini-3.5-live-translate-preview"
CANONICAL_GENERAL_FALLBACK = "gemini-3.8-flash"

# Default built-in catalog for speech-to-text / live transcription
DEFAULT_TRANSCRIBE_MODELS: List[Dict[str, Any]] = [
    {
        "id": "gemini-3.5-transcribe-live",
        "name": "Gemini 3.5 Transcribe Live (Official Realtime STT)",
        "description": "Low-latency streaming speech-to-text over bidirectional WebSockets with custom vocabulary biasing and disfluency cleanup.",
        "is_live": True,
        "is_default": True,
        "is_deprecated": False,
    },
    {
        "id": "gemini-3.5-transcribe-live-preview",
        "name": "Gemini 3.5 Transcribe Live Preview",
        "description": "Enterprise agent preview endpoint for live streaming transcription.",
        "is_live": True,
        "is_default": False,
        "is_deprecated": False,
    },
    {
        "id": "gemini-3.5-transcribe",
        "name": "Gemini 3.5 Transcribe (Batch Audio)",
        "description": "Accurate non-streaming batch audio transcription model.",
        "is_live": False,
        "is_default": False,
        "is_deprecated": False,
    },
    {
        "id": "gemini-3.1-flash-live-preview",
        "name": "Gemini 3.1 Flash Live Preview (Voice Agent)",
        "description": "Next-generation low-latency multimodal realtime audio model.",
        "is_live": True,
        "is_default": False,
        "is_deprecated": False,
    },
]

# Default built-in catalog for live translation
DEFAULT_TRANSLATE_MODELS: List[Dict[str, Any]] = [
    {
        "id": "gemini-3.5-live-translate-preview",
        "name": "Gemini 3.5 Live Translate Preview",
        "description": "Dedicated low-latency speech-to-speech / speech-to-translated-text translation (70+ languages).",
        "is_live": True,
        "is_default": True,
        "is_deprecated": False,
    },
]

# Default built-in catalog for general text / sermon summary / chapters
DEFAULT_GENERAL_MODELS: List[Dict[str, Any]] = [
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash (Recommended)",
        "description": "Best speed and reasoning for sermon summaries, chapters, and church outlines.",
        "is_default": True,
        "is_deprecated": False,
    },
    {
        "id": "gemini-3.6-flash",
        "name": "Gemini 3.6 Flash",
        "description": "High-throughput production multimodal generation.",
        "is_default": False,
        "is_deprecated": False,
    },
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash-Lite",
        "description": "Lightweight, ultra-fast, and cost-effective generation model.",
        "is_default": False,
        "is_deprecated": False,
    },
    {
        "id": "gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "description": "Balanced speed and accuracy reasoning model.",
        "is_default": False,
        "is_deprecated": False,
    },
    {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "description": "Deep contextual reasoning for complex theology and long sermons.",
        "is_default": False,
        "is_deprecated": False,
    },
]

# Cached scan result
_cached_scan_data: Optional[Dict[str, Any]] = None
_cached_scan_time: float = 0.0
CACHE_TTL_SECONDS = 300.0  # 5 minutes


def sanitize_gemini_api_key(key: Optional[str]) -> str:
    """Sanitize Google AI Studio / Gemini API key.

    Removes:
      - Leading and trailing whitespace, newlines, tabs, zero-width spaces
      - Surrounding quotes (' or \") or backticks
      - Common accidental paste prefixes (e.g. 'GEMINI_API_KEY=', 'key=')
    """
    if not key:
        return ""
    cleaned = str(key).strip()
    # Strip invisible zero-width unicode characters
    cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", "", cleaned).strip()

    # Strip prefixes like "GEMINI_API_KEY=", "key=", "Bearer "
    for prefix in ("gemini_api_key=", "api_key=", "key=", "bearer "):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    # Strip surrounding quotes or backticks
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        cleaned = cleaned[1:-1].strip()
    if cleaned.startswith("`") and cleaned.endswith("`"):
        cleaned = cleaned[1:-1].strip()

    return cleaned


def validate_gemini_api_key(key: Optional[str]) -> Tuple[bool, str]:
    """Validate whether an API key appears valid without making a network call."""
    cleaned = sanitize_gemini_api_key(key)
    if not cleaned:
        return False, "API key is empty."
    if len(cleaned) < 15:
        return False, "API key is too short to be a valid Google AI Studio key."
    if cleaned.startswith("•••") or "your_key" in cleaned.lower():
        return False, "API key appears to be a placeholder or masked value."
    if not cleaned.startswith("AIza"):
        # Google AI keys usually start with AIzaSy...
        return True, "Note: Google AI Studio keys typically start with 'AIza'."
    return True, "Valid format."


async def check_gemini_dns_liveness(host: str = "generativelanguage.googleapis.com", timeout: float = 2.5) -> bool:
    """Perform a fast non-blocking DNS query to confirm internet connectivity to Google Gemini.

    Fails early to avoid long socket connect timeouts when the system is offline.
    """
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.getaddrinfo(host, 443), timeout=timeout)
        return True
    except Exception as e:
        logger.debug(f"Gemini DNS check failed for {host}: {e}")
        return False


def get_fallback_model_id(configured_model: str) -> str:
    """Return a guaranteed working fallback model if configured_model is sunset or fails."""
    clean_id = configured_model.replace("models/", "").strip()
    if clean_id in KNOWN_GEMINI_DEPRECATIONS:
        replacement = KNOWN_GEMINI_DEPRECATIONS[clean_id].get("recommended_replacement")
        if replacement:
            return replacement
    if "translate" in clean_id:
        return CANONICAL_TRANSLATE_FALLBACK
    return CANONICAL_TRANSCRIBE_FALLBACK


def check_model_deprecation(model_id: str) -> Optional[Dict[str, Any]]:
    """Check if a model ID is known to be sunset or deprecated."""
    clean_id = model_id.replace("models/", "").strip()
    return KNOWN_GEMINI_DEPRECATIONS.get(clean_id)


async def scan_gemini_models(api_key: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """Query Google AI Studio Models API and categorize available models.

    Identifies:
      - Speech-to-text / Transcribe models (containing 'transcribe')
      - Live Translation models (containing 'translate')
      - General multimodal models (supporting generateContent)
      - Deprecation & sunsetting flags with upgrade recommendations
    """
    global _cached_scan_data, _cached_scan_time

    now = time.monotonic()
    if not force_refresh and _cached_scan_data and (now - _cached_scan_time) < CACHE_TTL_SECONDS:
        return _cached_scan_data

    clean_key = sanitize_gemini_api_key(api_key)
    result: Dict[str, Any] = {
        "success": False,
        "from_cache": False,
        "online_scan": False,
        "message": "",
        "transcribe_models": list(DEFAULT_TRANSCRIBE_MODELS),
        "translate_models": list(DEFAULT_TRANSLATE_MODELS),
        "general_models": list(DEFAULT_GENERAL_MODELS),
        "deprecations": KNOWN_GEMINI_DEPRECATIONS,
    }

    if not clean_key:
        result["message"] = "No API key configured. Displaying standard catalog."
        _cached_scan_data = result
        _cached_scan_time = now
        return result

    # Test DNS before making request
    dns_ok = await check_gemini_dns_liveness()
    if not dns_ok:
        result["message"] = "Network offline or DNS unreachable. Using offline catalog."
        _cached_scan_data = result
        _cached_scan_time = now
        return result

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
    timeout = aiohttp.ClientTimeout(total=6.0, connect=3.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    models_list = payload.get("models", [])

                    transcribe_found: List[Dict[str, Any]] = []
                    translate_found: List[Dict[str, Any]] = []
                    general_found: List[Dict[str, Any]] = []
                    seen_ids = set()

                    for m in models_list:
                        raw_name = m.get("name", "")
                        model_id = raw_name.replace("models/", "")
                        if model_id in seen_ids:
                            continue
                        seen_ids.add(model_id)

                        display_name = m.get("displayName") or model_id
                        description = m.get("description", "")
                        gen_methods = m.get("supportedGenerationMethods", [])

                        dep_info = check_model_deprecation(model_id)
                        is_dep = dep_info is not None

                        item = {
                            "id": model_id,
                            "name": display_name,
                            "description": description,
                            "is_live": "bidiGenerateContent" in gen_methods or "live" in model_id,
                            "is_deprecated": is_dep,
                            "deprecation_notice": dep_info["notice"] if dep_info else "",
                            "recommended_replacement": dep_info["recommended_replacement"] if dep_info else "",
                        }

                        # 1. Transcribe models: matches 'transcribe' in id or description
                        if "transcribe" in model_id.lower() or "transcribe" in description.lower():
                            transcribe_found.append(item)
                        # 2. Live Translate models: matches 'translate' in id or description
                        elif "translate" in model_id.lower() or "translation" in description.lower():
                            translate_found.append(item)
                        # 3. Live agent models: 'live' in id
                        elif "live" in model_id.lower() and "bidiGenerateContent" in gen_methods:
                            transcribe_found.append(item)
                        # 4. General generation models
                        elif "generateContent" in gen_methods:
                            # Prioritize gemini models
                            if model_id.startswith("gemini"):
                                general_found.append(item)

                    # Ensure standard canonical models exist in list even if not explicitly returned by API
                    for std in DEFAULT_TRANSCRIBE_MODELS:
                        if not any(x["id"] == std["id"] for x in transcribe_found):
                            transcribe_found.insert(0, std)

                    for std in DEFAULT_TRANSLATE_MODELS:
                        if not any(x["id"] == std["id"] for x in translate_found):
                            translate_found.insert(0, std)

                    for std in DEFAULT_GENERAL_MODELS:
                        if not any(x["id"] == std["id"] for x in general_found):
                            general_found.insert(0, std)

                    result["success"] = True
                    result["online_scan"] = True
                    result["message"] = f"Successfully scanned {len(models_list)} models from Google AI Studio."
                    result["transcribe_models"] = transcribe_found
                    result["translate_models"] = translate_found
                    result["general_models"] = general_found
                    _cached_scan_data = result
                    _cached_scan_time = now
                    logger.info(f"Gemini model scan succeeded: {len(transcribe_found)} transcribe, {len(translate_found)} translate, {len(general_found)} general.")
                    return result
                else:
                    err_text = await resp.text()
                    logger.warning(f"Google Models API returned HTTP {resp.status}: {err_text[:200]}")
                    result["message"] = f"Google API returned HTTP {resp.status}. Using standard catalog."
    except Exception as e:
        logger.warning(f"Error scanning Gemini models from Google AI Studio: {e}")
        result["message"] = f"Scan error: {e}. Using standard catalog."

    _cached_scan_data = result
    _cached_scan_time = now
    return result
