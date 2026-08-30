"""Security hardening, input validation, and API authentication."""

import html
import logging
import re
import time
from typing import Dict, Optional, Tuple

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger("obs_captioner.security")

# Maximum string lengths to prevent memory / ReDoS attacks
MAX_CUSTOM_WORD_LENGTH = 80
MAX_TEXT_PAYLOAD_LENGTH = 5000
FORBIDDEN_PATH_CHARS = re.compile(r'[\\/*?:"<>|]')


def sanitize_text(text: str, max_len: int = MAX_TEXT_PAYLOAD_LENGTH) -> str:
    """Sanitize and truncate generic text inputs."""
    if not isinstance(text, str):
        return ""
    clean = text.strip()[:max_len]
    return clean


def escape_html(text: str) -> str:
    """Strict HTML escaping for browser overlay safety."""
    if not text:
        return ""
    return html.escape(text, quote=True)


def sanitize_filename(filename: str, default: str = "captions.srt") -> str:
    """Sanitize filename to prevent directory traversal attacks."""
    if not filename:
        return default
    # Remove directory separators and path traversal sequences
    clean = filename.replace("..", "").replace("/", "").replace("\\", "")
    clean = FORBIDDEN_PATH_CHARS.sub("_", clean)
    clean = clean.strip()
    return clean if clean else default


def validate_censor_term(term: str) -> Tuple[bool, str]:
    """Validate and sanitize a custom blacklist/whitelist word."""
    if not term or not isinstance(term, str):
        return False, "Term cannot be empty."
    
    clean = term.strip()
    if len(clean) > MAX_CUSTOM_WORD_LENGTH:
        return False, f"Term is too long (max {MAX_CUSTOM_WORD_LENGTH} characters)."
    
    # Check for excessive special characters that could cause ReDoS
    special_char_count = sum(1 for c in clean if not c.isalnum() and not c.isspace())
    if special_char_count > 10:
        return False, "Term contains too many special characters."
    
    return True, clean


class SimpleRateLimiter:
    """Sliding-window in-memory rate limiter per IP address."""

    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clients: Dict[str, list] = {}

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        timestamps = self._clients.setdefault(client_ip, [])
        # Prune older than window
        timestamps[:] = [t for t in timestamps if now - t < self.window_seconds]
        
        if len(timestamps) >= self.max_requests:
            return False
        
        timestamps.append(now)
        return True


def require_api_auth(config_api_key: str):
    """Decorator / helper to verify API authentication if configured."""
    def check_auth(request: web.Request) -> bool:
        if not config_api_key:
            return True  # Auth not required if no key configured

        # Check Authorization header (Bearer <token>)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token == config_api_key:
                return True

        # Check query param (?api_key=<token>)
        query_key = request.query.get("api_key", "").strip()
        if query_key == config_api_key:
            return True

        return False
    return check_auth
