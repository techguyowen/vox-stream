"""ProPresenter & Faithlife Proclaim display sync for VoxStream.

Lightweight, dependency-safe asyncio client that watches the active worship
presentation slide and reports when a lyrics slide is showing so captioning
can auto-mute during worship songs.

Supported providers (``ProPresenterConfig.provider``):

- ``"propresenter"`` — ProPresenter Stage Display WebSocket
  (``ws://host:port/stagedisplay``)
- ``"proclaim"`` — Faithlife Proclaim Local App Command HTTP API
  (default port 52195) plus webhook / slide-state sync via
  :meth:`ProPresenterClient.handle_external_slide_event`
- ``"both"`` — run both providers simultaneously
"""

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

try:
    import aiohttp
except Exception:  # Optional dependency — client degrades gracefully without it.
    aiohttp = None  # type: ignore

logger = logging.getLogger("obs_captioner.propresenter")

DEFAULT_LYRICS_KEYWORDS: List[str] = [
    "worship", "song", "lyrics", "chorus", "verse", "bridge", "hymn", "praise",
]

DEFAULT_NON_LYRICS_KEYWORDS: Tuple[str, ...] = (
    "sermon", "message", "preach", "notes", "announcement", "welcome", "scripture",
    "bible", "off air", "video", "slideshow", "imageslideshow", "giving", "tithe", "offering",
)

_SCRIPTURE_CITATION_RE = re.compile(r"\b\d+:\d+\b")
_BULLET_RE = re.compile(r"^(\d+[\.\)]|[-•*])\s+")

_MUSICAL_SYMBOLS = ("♪", "♫")
_SECTION_TAGS = ("[chorus]", "[verse", "[bridge]", "(chorus)")

_SLIDE_TEXT_KEYS = ("cs", "csn", "txt")
_SLIDE_LABEL_KEYS = ("lbl", "grp", "label")


@dataclass
class ProPresenterConfig:
    enabled: bool = False
    provider: str = "propresenter"  # "propresenter", "proclaim", or "both"
    host: str = "127.0.0.1"
    port: int = 1025
    password: str = ""
    proclaim_host: str = "auto"
    proclaim_port: int = 52195
    proclaim_password: str = "proclaim"
    auto_mute_on_lyrics: bool = True
    auto_display_scripture_slides: bool = True
    lyrics_keywords: List[str] = field(default_factory=lambda: ["worship", "song", "lyrics", "chorus", "verse", "bridge", "hymn", "praise"])


def classify_slide_as_lyrics(
    slide_text: str,
    slide_label: str = "",
    keywords: Optional[List[str]] = None,
) -> bool:
    """Return True when a presentation slide looks like song lyrics.

    Scripture slides (text containing a ``chapter:verse`` citation) are never
    lyrics. Otherwise a slide is lyrics when its label matches a worship
    keyword, when its text carries musical symbols / section tags, or when its
    text is shaped like short poetic lines.
    """
    text = slide_text or ""
    label = slide_label or ""
    if not text.strip() and not label.strip():
        return False
    if _SCRIPTURE_CITATION_RE.search(text) or _SCRIPTURE_CITATION_RE.search(label):
        return False
    label_lower = label.lower()
    if any(nl in label_lower for nl in DEFAULT_NON_LYRICS_KEYWORDS):
        if not any(k in label_lower for k in ("song", "worship", "lyrics", "hymn")):
            return False
    active_keywords = DEFAULT_LYRICS_KEYWORDS if keywords is None else keywords
    for keyword in active_keywords:
        if keyword and keyword.lower() in label_lower:
            return True
    if any(symbol in text for symbol in _MUSICAL_SYMBOLS):
        return True
    text_lower = text.lower()
    if any(tag in text_lower for tag in _SECTION_TAGS):
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        if any(_BULLET_RE.match(line) for line in lines):
            return False
        poetic = True
        for line in lines:
            words = line.split()
            if not (2 <= len(words) <= 10) or line.rstrip().endswith("."):
                poetic = False
                break
        if poetic:
            return True
    return False


class ProPresenterClient:
    """Async ProPresenter / Faithlife Proclaim slide-sync client."""

    def __init__(
        self,
        config: ProPresenterConfig,
        on_lyrics_state_changed: Optional[Callable[[bool, str], None]] = None,
        on_slide_changed: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self.config = config
        self.on_lyrics_state_changed = on_lyrics_state_changed
        self.on_slide_changed = on_slide_changed
        self._ws_connected = False
        self._proclaim_connected = False
        self._is_lyrics_slide_active = False
        self._current_slide_text = ""
        self._current_slide_label = ""
        self._proclaim_auth_token = ""
        self._session: Optional["aiohttp.ClientSession"] = None
        self._ws: Optional["aiohttp.ClientWebSocketResponse"] = None
        self._running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None

        self._resolved_proclaim_host: Optional[str] = None
        self._proclaim_session_id: Optional[str] = None
        self._proclaim_conn_id: Optional[str] = None
        self._proclaim_presentation_id: Optional[str] = None
        self._proclaim_presentation_revision: int = 0
        self._proclaim_items: Dict[str, Dict[str, Any]] = {}
        self._proclaim_current_item_id: Optional[str] = None
        self._proclaim_current_slide_index: int = 0

    @property
    def is_connected(self) -> bool:
        return self._ws_connected or self._proclaim_connected

    @property
    def is_lyrics_slide_active(self) -> bool:
        return self._is_lyrics_slide_active

    @is_lyrics_slide_active.setter
    def is_lyrics_slide_active(self, value: bool) -> None:
        self._is_lyrics_slide_active = bool(value)

    @property
    def current_slide_text(self) -> str:
        return self._current_slide_text

    @current_slide_text.setter
    def current_slide_text(self, value: str) -> None:
        self._current_slide_text = value or ""

    @property
    def current_slide_label(self) -> str:
        return self._current_slide_label

    @current_slide_label.setter
    def current_slide_label(self, value: str) -> None:
        self._current_slide_label = value or ""

    @property
    def proclaim_auth_token(self) -> str:
        return self._proclaim_auth_token

    def _uses_propresenter(self) -> bool:
        return str(self.config.provider or "propresenter").lower() in ("propresenter", "both")

    def _uses_proclaim(self) -> bool:
        return str(self.config.provider or "propresenter").lower() in ("proclaim", "both")

    async def start(self) -> None:
        """Start provider listeners (no-op when disabled)."""
        if not self.config.enabled or self._running:
            return
        self._running = True
        if self._uses_propresenter():
            self._ws_task = asyncio.create_task(self._ws_listen_loop())
        if self._uses_proclaim():
            try:
                await self.authenticate_proclaim()
            except Exception:
                logger.debug("Proclaim authentication skipped/failed.", exc_info=True)
            self._poll_task = asyncio.create_task(self._proclaim_poll_loop())

    async def stop(self) -> None:
        """Stop all listeners and release HTTP/WebSocket resources."""
        self._running = False
        for task in (self._ws_task, self._poll_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._ws_task = None
        self._poll_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        was_lyrics = self._is_lyrics_slide_active
        self._is_lyrics_slide_active = False
        if was_lyrics and self.on_lyrics_state_changed:
            try:
                self.on_lyrics_state_changed(False, "sync_stopped")
            except Exception:
                pass
        self._ws_connected = False
        self._proclaim_connected = False
        self._resolved_proclaim_host = None
        self._proclaim_session_id = None
        self._proclaim_conn_id = None
        self._proclaim_presentation_id = None
        self._proclaim_presentation_revision = 0
        self._proclaim_items.clear()
        self._proclaim_current_item_id = None
        self._proclaim_current_slide_index = 0

    async def _get_session(self) -> Optional["aiohttp.ClientSession"]:
        if aiohttp is None:
            return None
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=100)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def _ws_listen_loop(self) -> None:
        """Maintain the ProPresenter Stage Display WebSocket with reconnect."""
        if aiohttp is None:
            logger.warning("aiohttp is not installed; ProPresenter stage sync is disabled.")
            return
        url = f"ws://{self.config.host}:{self.config.port}/stagedisplay"
        while self._running:
            try:
                session = await self._get_session()
                if session is None:
                    return
                ws = await session.ws_connect(url, heartbeat=30)
                self._ws = ws
                self._ws_connected = True
                logger.info(f"Connected to ProPresenter stage display at {url}")
                try:
                    await ws.send_json({"pwd": self.config.password, "ptl": 610, "acn": "ath"})
                except Exception:
                    logger.debug("ProPresenter stage auth send failed.", exc_info=True)
                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            frame = json.loads(msg.data)
                        except Exception:
                            continue
                        if isinstance(frame, dict):
                            try:
                                self._handle_stage_frame(frame)
                            except Exception:
                                logger.debug("Stage frame handling failed.", exc_info=True)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"ProPresenter stage connection issue: {e}")
            finally:
                self._ws_connected = False
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
            if self._running:
                await asyncio.sleep(5.0)

    def _handle_stage_frame(self, frame: Dict[str, Any]) -> None:
        """Parse one Stage Display JSON frame into slide state."""
        if not isinstance(frame, dict):
            return
        slideish_keys = _SLIDE_TEXT_KEYS + _SLIDE_LABEL_KEYS + ("ary",)
        if frame.get("acn") not in ("fv", "cs", "msg") and not any(k in frame for k in slideish_keys):
            return
        slide_text, slide_label = self._extract_slide(frame)
        self.update_slide_state(slide_text, slide_label, source="propresenter")

    @staticmethod
    def _extract_slide(frame: Dict[str, Any]) -> Tuple[str, str]:
        """Extract (slide_text, slide_label) from a stage-display frame."""
        texts: List[str] = []
        label = ""
        ary = frame.get("ary")
        if isinstance(ary, list):
            for item in ary:
                if not isinstance(item, dict):
                    continue
                for key in _SLIDE_TEXT_KEYS:
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        texts.append(value.strip())
                        break
                if not label:
                    for key in _SLIDE_LABEL_KEYS:
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            label = value.strip()
                            break
        if not texts:
            for key in _SLIDE_TEXT_KEYS:
                value = frame.get(key)
                if isinstance(value, str) and value.strip():
                    texts.append(value.strip())
                    break
        if not label:
            for key in _SLIDE_LABEL_KEYS:
                value = frame.get(key)
                if isinstance(value, str) and value.strip():
                    label = value.strip()
                    break
        return "\n".join(texts), label

    async def _probe_endpoint(
        self,
        session: "aiohttp.ClientSession",
        host: str,
        port: int,
        check_signature: bool = False,
    ) -> bool:
        if aiohttp is None:
            return False
        try:
            timeout = aiohttp.ClientTimeout(total=0.5)
            async with session.get(f"http://{host}:{port}/", timeout=timeout) as resp:
                if resp.status == 200:
                    if check_signature:
                        text = await resp.text()
                        return "Proclaim" in text or "EmbedIO" in text
                    return True
                return not check_signature and resp.status < 500
        except Exception:
            return False

    @staticmethod
    def _get_arp_candidates() -> List[str]:
        raw_output = ""
        for cmd in (["arp", "-an"], ["arp", "-a"]):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
                if proc.stdout:
                    raw_output = proc.stdout
                    break
            except Exception:
                continue
        if not raw_output:
            return []
        ips = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", raw_output)
        candidates: List[str] = []
        for ip in set(ips):
            parts = ip.split(".")
            if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                first = int(parts[0])
                last = int(parts[3])
                if first in (127, 224, 239) or first >= 224:
                    continue
                if last in (0, 255):
                    continue
                candidates.append(ip)
        return sorted(candidates)

    async def _resolve_proclaim_host(self) -> Optional[str]:
        session = await self._get_session()
        if session is None:
            return None
        port = self.config.proclaim_port

        # 1. If self._resolved_proclaim_host: probe GET http://{self._resolved_proclaim_host}:{port}/ (timeout 0.5s)
        if self._resolved_proclaim_host:
            if await self._probe_endpoint(session, self._resolved_proclaim_host, port):
                return self._resolved_proclaim_host
            self._resolved_proclaim_host = None

        # 2. If self.config.proclaim_host is set to an explicit IP/hostname (not "auto", "", or "127.0.0.1")
        cfg_host = (self.config.proclaim_host or "").strip()
        if cfg_host and cfg_host.lower() not in ("auto", "127.0.0.1", "localhost"):
            if await self._probe_endpoint(session, cfg_host, port):
                self._resolved_proclaim_host = cfg_host
                return cfg_host

        # 3. If host is "auto", "", or configured host failed:
        # a. Test 127.0.0.1:{port}
        if await self._probe_endpoint(session, "127.0.0.1", port):
            self._resolved_proclaim_host = "127.0.0.1"
            return "127.0.0.1"

        # b. If localhost unreachable, perform fast LAN discovery
        candidates = self._get_arp_candidates()
        if candidates:
            async def _probe_cand(cand_ip: str) -> Optional[str]:
                if await self._probe_endpoint(session, cand_ip, port, check_signature=True):
                    return cand_ip
                return None

            tasks = [asyncio.create_task(_probe_cand(ip)) for ip in candidates]
            try:
                for coro in asyncio.as_completed(tasks):
                    found = await coro
                    if found:
                        for t in tasks:
                            if not t.done():
                                t.cancel()
                        logger.info(f"Discovered Faithlife Proclaim on LAN at {found}:{port}")
                        self._resolved_proclaim_host = found
                        return found
            except Exception:
                pass
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()

        return None

    async def authenticate_proclaim(self) -> Optional[str]:
        """Authenticate with Proclaim's local HTTP API and store the auth token.

        Localhost requests are typically allowed without a token; remote hosts
        require one. Returns the token, or None when unavailable.
        """
        if aiohttp is None:
            return None
        host = self._resolved_proclaim_host or await self._resolve_proclaim_host()
        if not host:
            return None
        url = f"http://{host}:{self.config.proclaim_port}/appCommand/authenticate"
        try:
            session = await self._get_session()
            if session is None:
                return None
            async with session.post(url, json={"Password": self.config.proclaim_password}, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status >= 400:
                    return None
                self._proclaim_connected = True
                try:
                    data = await resp.json()
                except Exception:
                    return None
                if isinstance(data, dict):
                    for key in ("proclaimAuthToken", "ProclaimAuthToken", "authToken", "token"):
                        token = data.get(key)
                        if token:
                            self._proclaim_auth_token = str(token)
                            return self._proclaim_auth_token
        except Exception:
            logger.debug("Proclaim authentication failed.", exc_info=True)
        return None

    async def send_proclaim_command(self, command_name: str, index: Optional[int] = None) -> bool:
        """Send a Proclaim local App Command (nextSlide, blankScreen, ...)."""
        if aiohttp is None:
            return False
        host = self._resolved_proclaim_host or await self._resolve_proclaim_host()
        if not host:
            return False
        if not self._proclaim_auth_token:
            await self.authenticate_proclaim()
        url = (
            f"http://{host}:{self.config.proclaim_port}"
            f"/appCommand/perform?appCommandName={quote(str(command_name), safe='')}"
        )
        if index is not None:
            url += f"&index={int(index)}"
        headers: Dict[str, str] = {}
        if self._proclaim_auth_token:
            headers["ProclaimAuthToken"] = self._proclaim_auth_token
        try:
            session = await self._get_session()
            if session is None:
                return False
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status in (401, 403):
                    new_token = await self.authenticate_proclaim()
                    if new_token:
                        headers["ProclaimAuthToken"] = new_token
                        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5.0)) as retry_resp:
                            return retry_resp.status < 400
                return resp.status < 400
        except Exception:
            logger.debug(f"Proclaim command '{command_name}' failed.", exc_info=True)
            return False

    async def _proclaim_poll_loop(self) -> None:
        if aiohttp is None:
            logger.warning("aiohttp is not installed; Proclaim slide polling is disabled.")
            return
        while self._running:
            try:
                await self._poll_proclaim_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("Proclaim poll failed.", exc_info=True)
            try:
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                break

    async def _poll_proclaim_once(self) -> None:
        session = await self._get_session()
        if session is None:
            return

        host = await self._resolve_proclaim_host()
        if not host:
            self._proclaim_connected = False
            return
        port = self.config.proclaim_port
        timeout_4s = aiohttp.ClientTimeout(total=4.0)

        # 2) Query GET http://{host}:{port}/onair/session (timeout 4s)
        try:
            async with session.get(f"http://{host}:{port}/onair/session", timeout=timeout_4s) as resp:
                if resp.status == 204:
                    self._proclaim_connected = True
                    if self._current_slide_label != "Off Air":
                        self.update_slide_state("", "Off Air", source="proclaim")
                    return
                elif resp.status == 200:
                    body = await resp.text()
                    sid = body.strip().strip('"')
                    if not sid:
                        self._proclaim_connected = True
                        if self._current_slide_label != "Off Air":
                            self.update_slide_state("", "Off Air", source="proclaim")
                        return
                    self._proclaim_connected = True
                    if sid != self._proclaim_session_id:
                        self._proclaim_session_id = sid
                        self._proclaim_conn_id = None
                        self._proclaim_items.clear()
                else:
                    self._proclaim_connected = False
                    return
        except Exception:
            self._proclaim_connected = False
            self._resolved_proclaim_host = None
            return

        if not self._proclaim_session_id:
            return

        # 3) Fetch Presentation (if not self._proclaim_items)
        if not self._proclaim_items:
            try:
                headers = {"OnAirSessionId": self._proclaim_session_id}
                async with session.get(f"http://{host}:{port}/presentations/onair", headers=headers, timeout=timeout_4s) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict):
                            self._proclaim_items = {
                                it["id"]: it
                                for it in data.get("serviceItems", [])
                                if isinstance(it, dict) and "id" in it
                            }
                            self._proclaim_presentation_revision = data.get("localRevision", 0)
                            self._proclaim_presentation_id = data.get("id")
                            logger.info(f"Loaded Proclaim presentation '{data.get('title')}' ({len(self._proclaim_items)} items)")
                    elif resp.status in (401, 404):
                        self._proclaim_session_id = None
                        self._proclaim_conn_id = None
                        self._proclaim_items.clear()
                        return
            except Exception:
                logger.debug("Failed fetching Proclaim onair presentation.", exc_info=True)
                return

        # 4) Request Controller Connection (if not self._proclaim_conn_id)
        if not self._proclaim_conn_id:
            try:
                pwd = (
                    self.config.proclaim_password
                    if self.config.proclaim_password and self.config.proclaim_password != "proclaim"
                    else "Proclaim"
                )
                auth_body = {
                    "FaithlifeUserId": None,
                    "UserName": "VoxStream",
                    "RemoteDeviceName": "VoxStream Captioner",
                    "Password": pwd,
                }
                headers = {"OnAirSessionId": self._proclaim_session_id}
                auth_url = f"http://{host}:{port}/auth/control"
                async with session.post(auth_url, json=auth_body, headers=headers, timeout=timeout_4s) as resp:
                    if resp.status in (401, 403):
                        alt_pwd = "proclaim" if pwd == "Proclaim" else "Proclaim"
                        auth_body["Password"] = alt_pwd
                        async with session.post(auth_url, json=auth_body, headers=headers, timeout=timeout_4s) as retry_resp:
                            if retry_resp.status == 200:
                                resp_data = await retry_resp.json()
                                if isinstance(resp_data, dict):
                                    self._proclaim_conn_id = resp_data.get("connectionId")
                                    token = resp_data.get("authenticationToken")
                                    if token:
                                        self._proclaim_auth_token = str(token)
                    elif resp.status == 200:
                        resp_data = await resp.json()
                        if isinstance(resp_data, dict):
                            self._proclaim_conn_id = resp_data.get("connectionId")
                            token = resp_data.get("authenticationToken")
                            if token:
                                self._proclaim_auth_token = str(token)
            except Exception:
                logger.debug("Failed requesting Proclaim controller connection.", exc_info=True)
                return

        # 5) Poll Active Slide Status
        if self._proclaim_conn_id:
            try:
                status_url = f"http://{host}:{port}/onair/statusChanged?localrevision=0&step=0"
                headers = {
                    "OnAirSessionId": self._proclaim_session_id,
                    "ConnectionId": self._proclaim_conn_id,
                }
                async with session.get(status_url, headers=headers, timeout=timeout_4s) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        st = data.get("status", {}) if isinstance(data, dict) else {}
                        item_id = st.get("itemId")
                        slide_idx = st.get("slideIndex", 0)
                        quick_screen = st.get("quickScreenKind", "None")
                        self._proclaim_current_item_id = item_id
                        self._proclaim_current_slide_index = slide_idx

                        slide_text = ""
                        slide_label = ""
                        if quick_screen and quick_screen.lower() != "none":
                            slide_text = ""
                            slide_label = f"Quick Screen: {quick_screen}"
                        elif item_id in self._proclaim_items:
                            item = self._proclaim_items[item_id]
                            item_title = item.get("title", "")
                            item_kind = item.get("kind", "")
                            item_notes = item.get("notes", "")
                            total_slides = len(item.get("slides", []))
                            slide_label = f"{item_kind}: {item_title}" if item_kind else item_title
                            slide_text = item_notes.strip() if item_notes and item_notes.strip() else item_title
                            if total_slides > 1 and not item_notes:
                                slide_text = f"{item_title} (Slide {slide_idx + 1}/{total_slides})"
                        elif item_id:
                            self._proclaim_items.clear()

                        if slide_text or slide_label:
                            self.update_slide_state(slide_text, slide_label, source="proclaim")
                    elif resp.status in (401, 403, 404):
                        self._proclaim_conn_id = None
            except Exception:
                logger.debug("Failed polling Proclaim statusChanged.", exc_info=True)

    def _handle_proclaim_status(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        text, label = self._extract_proclaim_fields(data)
        if not text or not label:
            for nested_key in ("current", "currentSlide", "onAir", "session", "slide", "item"):
                nested = data.get(nested_key)
                if isinstance(nested, dict):
                    nested_text, nested_label = self._extract_proclaim_fields(nested)
                    text = text or nested_text
                    label = label or nested_label
        if text or label:
            self.update_slide_state(text, label, source="proclaim")

    @staticmethod
    def _extract_proclaim_fields(data: Dict[str, Any]) -> Tuple[str, str]:
        text = ""
        label = ""
        for key in ("slide_text", "slideText", "currentSlideText", "text", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        for key in ("slide_label", "slideLabel", "label", "item_type", "itemType", "title", "serviceItem"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                label = value.strip()
                break
        return text, label

    def handle_external_slide_event(self, payload: dict) -> dict:
        """Ingest a webhook/bridge slide-state event (usually from Proclaim).

        Expected payload::

            {"provider": "proclaim", "item_type": "Song" | "Bible" | "Sermon",
             "slide_text": "...", "slide_label": "...", "command": "..."}
        """
        if not isinstance(payload, dict):
            return {"ok": False, "error": "payload must be an object"}
        provider = str(payload.get("provider") or "proclaim")
        slide_text = str(payload.get("slide_text") or payload.get("text") or "")
        slide_label = str(
            payload.get("slide_label")
            or payload.get("label")
            or payload.get("item_type")
            or payload.get("itemType")
            or ""
        )
        command = payload.get("command") or ""
        is_lyrics = self.update_slide_state(slide_text, slide_label, source=provider)
        return {
            "ok": True,
            "provider": provider,
            "is_lyrics": is_lyrics,
            "is_lyrics_slide_active": self._is_lyrics_slide_active,
            "slide_label": self._current_slide_label,
            "slide_text": self._current_slide_text,
            "command": command,
        }

    def update_slide_state(self, slide_text: str, slide_label: str = "", source: str = "propresenter") -> bool:
        """Record the current slide and fire the lyrics callback on transitions."""
        clean_text = (slide_text or "").strip()
        clean_label = (slide_label or "").strip()
        has_changed = (clean_text != self._current_slide_text or clean_label != self._current_slide_label)
        self.last_slide_changed = has_changed
        self.current_slide_text = clean_text
        self.current_slide_label = clean_label
        is_lyrics = classify_slide_as_lyrics(
            self.current_slide_text, self.current_slide_label, self.config.lyrics_keywords
        )
        if is_lyrics != self.is_lyrics_slide_active:
            self.is_lyrics_slide_active = is_lyrics
            if self.on_lyrics_state_changed:
                if is_lyrics:
                    if self.config.auto_mute_on_lyrics:
                        try:
                            self.on_lyrics_state_changed(True, f"{source}:{clean_label or 'lyrics'}")
                        except Exception:
                            logger.debug("on_lyrics_state_changed callback failed.", exc_info=True)
                else:
                    # Always signal un-mute when leaving a lyrics slide, even if auto_mute was just toggled off.
                    try:
                        self.on_lyrics_state_changed(False, f"{source}:{clean_label or 'non_lyrics'}")
                    except Exception:
                        logger.debug("on_lyrics_state_changed callback failed.", exc_info=True)
        if has_changed and self.on_slide_changed and (clean_text or clean_label):
            try:
                self.on_slide_changed(clean_text, clean_label, source)
            except Exception:
                logger.debug("on_slide_changed callback failed.", exc_info=True)
        return is_lyrics

    async def send_stage_message(self, text: str) -> bool:
        """Push a message to the stage display (ProPresenter and/or Proclaim)."""
        sent = False
        if self._uses_propresenter():
            ws = self._ws
            try:
                if ws is not None and not getattr(ws, "closed", False):
                    await ws.send_json({"acn": "msg", "txt": text})
                    sent = True
            except Exception:
                logger.debug("ProPresenter stage message send failed.", exc_info=True)
        if self._uses_proclaim():
            try:
                if await self.send_proclaim_command("showText"):
                    sent = True
            except Exception:
                logger.debug("Proclaim showText command failed.", exc_info=True)
        return sent

    def get_status(self) -> dict:
        """Return a JSON-serializable snapshot of sync state."""
        return {
            "enabled": self.config.enabled,
            "provider": self.config.provider,
            "is_connected": self.is_connected,
            "is_lyrics_slide_active": self.is_lyrics_slide_active,
            "current_slide_label": self.current_slide_label,
            "current_slide_text": self.current_slide_text[:200],
            "resolved_proclaim_host": self._resolved_proclaim_host,
        }
