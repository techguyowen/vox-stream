"""Hardened Web and WebSocket Server for OBS Browser Source, Dashboard, and REST API."""

import asyncio
import json
import logging
import mimetypes
import re
import time
import urllib.parse
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

from aiohttp import web

# Explicit MIME mappings to guard against corrupted Windows registry MIME associations
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("application/manifest+json", ".webmanifest")

from ..config import AppConfig, save_config
from ..audio_capture import list_audio_devices
from ..censor import ContentFilter
from ..history import TranscriptHistory
from ..themes import THEME_PRESETS, get_all_presets
from ..translator import SUPPORTED_LANGUAGES, SubtitleTranslator
from ..summary_engine import SermonSummaryEngine
from ..vocabulary import VocabularyReplacer
from ..model_downloader import ModelDownloadManager
from ..bible_engine import BibleEngine, ScriptureLookupResult
from ..security import (
    SimpleRateLimiter,
    escape_html,
    require_api_auth,
    sanitize_filename,
    sanitize_text,
    validate_censor_term,
)

logger = logging.getLogger("obs_captioner.web")


class WebOverlayServer:
    """Provides WebSocket and HTTP endpoints for OBS overlay, stage display, and control dashboard."""

    def __init__(
        self,
        config: AppConfig,
        history: Optional[TranscriptHistory] = None,
        on_config_updated: Optional[Callable[[AppConfig], None]] = None,
        on_start_requested: Optional[Callable[[], None]] = None,
        on_stop_requested: Optional[Callable[[], None]] = None,
        on_restart_requested: Optional[Callable[[], None]] = None,
        on_shutdown_requested: Optional[Callable[[], None]] = None,
        get_app_status: Optional[Callable[[], dict]] = None,
        obs_client: Optional[Any] = None,
        audio_capture: Optional[Any] = None,
        updater: Optional[Any] = None,
        subtitle_recorder: Optional[Any] = None,
        on_trim_memory: Optional[Callable[[], float]] = None,
    ):
        self.config = config
        self.history = history or TranscriptHistory()
        self.on_config_updated = on_config_updated
        self.on_start_requested = on_start_requested
        self.on_stop_requested = on_stop_requested
        self.on_restart_requested = on_restart_requested
        self.on_shutdown_requested = on_shutdown_requested
        self.on_trim_memory = on_trim_memory
        self.get_app_status = get_app_status
        self.obs_client = obs_client
        self.audio_capture = audio_capture
        self.updater = updater
        self.subtitle_recorder = subtitle_recorder
        self.scheduler = None  # Set by main.py after scheduler is instantiated
        self._updater_task: Optional[asyncio.Task] = None
        self.translator = SubtitleTranslator(
            self.config.translation,
            api_key_resolver=lambda: (
                getattr(self.config.gemini_live, "api_key", "")
                or getattr(self.config.summary, "gemini_api_key", "")
            ),
        )
        self.server_start_time = time.time()
        self.instance_id = str(uuid.uuid4())[:8]

        if self.subtitle_recorder:
            def _on_rec_status(st: dict):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self.broadcast_control({"type": "recording_state_changed", "recording": st}),
                            loop,
                        )
                except Exception:
                    pass
            self.subtitle_recorder.on_status_changed = _on_rec_status

        # Generous: a dashboard (4s poll) + stage display (5s poll) + restart
        # polling from the same IP must not starve each other into 429s.
        self.rate_limiter = SimpleRateLimiter(max_requests=300, window_seconds=60.0)
        self.model_downloader = ModelDownloadManager()
        self.bible_engine = BibleEngine()
        self.summary_engine = SermonSummaryEngine(getattr(self.config, "summary", None), self.history, app_config=self.config)
        # Rolling snapshot of recent final caption payloads, replayed to newly
        # connected /ws clients so refreshed views aren't blank until the next utterance.
        self._recent_finals: list = []
        self._max_snapshot_lines = 10

        @web.middleware
        async def cors_middleware(request, handler):
            if request.method == "OPTIONS":
                response = web.Response()
            else:
                response = await handler(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Key"
            return response

        self.app = web.Application(middlewares=[cors_middleware])
        self.runner: web.AppRunner = None
        self.site: web.TCPSite = None
        self.caption_sockets: Dict[web.WebSocketResponse, str] = {}
        self.socket_roles: Dict[web.WebSocketResponse, str] = {}
        self.control_sockets: Set[web.WebSocketResponse] = set()

        self._setup_routes()

    def _check_auth(self, request: web.Request) -> bool:
        auth_func = require_api_auth(self.config.api.api_key)
        return auth_func(request)

    def _make_filter(self) -> ContentFilter:
        return ContentFilter(self.config.censor, church_mode=getattr(self.config.general, "church_mode", True))

    def _setup_routes(self):
        static_dir = Path(__file__).parent / "static"
        
        # HTML Pages with comprehensive aliases
        for path in ("/", "/index", "/index.html", "/overlay", "/overlay.html"):
            self.app.router.add_get(path, self._handle_index)

        for path in ("/bible", "/bible/", "/bible.html"):
            self.app.router.add_get(path, self._handle_bible_page)
        
        for path in ("/dashboard", "/dashboard/", "/dashboard.html", "/dock", "/dock/", "/settings", "/settings/", "/control"):
            self.app.router.add_get(path, self._handle_dashboard)

        for path in ("/display", "/display/", "/display.html", "/monitor", "/monitor/", "/stage", "/confidence"):
            self.app.router.add_get(path, self._handle_display)

        # Favicons & Icons
        self.app.router.add_get("/favicon.ico", self._handle_favicon)
        self.app.router.add_get("/apple-touch-icon.png", self._handle_apple_touch_icon)
        self.app.router.add_get("/apple-touch-icon-precomposed.png", self._handle_apple_touch_icon)

        # Progressive Web App (PWA) Manifest & Service Worker
        self.app.router.add_get("/manifest.json", self._handle_manifest)
        self.app.router.add_get("/manifest.webmanifest", self._handle_manifest)
        self.app.router.add_get("/manifest-display.json", self._handle_manifest_display)
        self.app.router.add_get("/manifest-dashboard.json", self._handle_manifest_dashboard)
        self.app.router.add_get("/sw.js", self._handle_service_worker)
        
        # WebSockets
        self.app.router.add_get("/ws", self._handle_caption_ws)
        self.app.router.add_get("/ws/bible", self._handle_caption_ws)
        self.app.router.add_get("/ws/stage", self._handle_caption_ws)
        self.app.router.add_get("/api/control/ws", self._handle_control_ws)
        self.app.router.add_get("/api/audio/stream", self._handle_audio_stream_ws)
        self.app.router.add_post("/api/audio/chunk", self._handle_audio_chunk_post)

        # REST API Routes
        self.app.router.add_get("/api/status", self._handle_get_status)
        self.app.router.add_get("/api/config", self._handle_get_config)
        self.app.router.add_post("/api/config", self._handle_post_config)
        self.app.router.add_get("/api/devices", self._handle_get_devices)
        self.app.router.add_get("/api/audio/devices", self._handle_get_devices)
        self.app.router.add_get("/api/hardware/gpus", self._handle_get_hardware_gpus)
        self.app.router.add_get("/api/presets", self._handle_get_presets)
        self.app.router.add_post("/api/presets/apply", self._handle_apply_preset)
        self.app.router.add_post("/api/presets/save", self._handle_save_preset)
        self.app.router.add_post("/api/presets/delete", self._handle_delete_preset)
        self.app.router.add_get("/api/languages", self._handle_get_languages)
        self.app.router.add_get("/api/engine/benchmark", self._handle_get_benchmark)
        
        # Control Endpoints
        self.app.router.add_post("/api/control/start", self._handle_control_start)
        self.app.router.add_post("/api/control/stop", self._handle_control_stop)
        self.app.router.add_post("/api/control/toggle", self._handle_control_toggle)
        self.app.router.add_post("/api/control/panic", self._handle_control_panic)
        self.app.router.add_post("/api/control/restart", self._handle_control_restart)
        self.app.router.add_post("/api/control/shutdown", self._handle_control_shutdown)
        self.app.router.add_post("/api/control/reopen-screen", self._handle_control_reopen_screen)
        self.app.router.add_post("/api/control/restore-display", self._handle_control_reopen_screen)

        # Auto-Stop Scheduler API
        self.app.router.add_get("/api/scheduler/status", self._handle_scheduler_status)
        self.app.router.add_post("/api/scheduler/timer", self._handle_scheduler_set_timer)
        self.app.router.add_post("/api/scheduler/timer/cancel", self._handle_scheduler_cancel_timer)
        self.app.router.add_get("/api/scheduler/schedules", self._handle_scheduler_get_schedules)
        self.app.router.add_post("/api/scheduler/schedules", self._handle_scheduler_post_schedule)
        self.app.router.add_delete("/api/scheduler/schedules/{schedule_id}", self._handle_scheduler_delete_schedule)
        self.app.router.add_post("/api/scheduler/config", self._handle_scheduler_update_config)


        # OBS Projector & Display Automation
        self.app.router.add_post("/api/obs/projector/open", self._handle_open_projector)
        self.app.router.add_get("/api/obs/monitors", self._handle_get_monitors)
        self.app.router.add_get("/api/obs/scenes", self._handle_get_scenes)
        
        # Transcript, Chapters, Summary, Translation & Export
        self.app.router.add_get("/api/transcript/history", self._handle_get_history)
        self.app.router.add_get("/api/transcript/stats", self._handle_get_transcript_stats)
        self.app.router.add_get("/api/transcript/chapters", self._handle_get_chapters)
        self.app.router.add_get("/api/transcript/ai-chapters", self._handle_ai_chapters)
        self.app.router.add_post("/api/transcript/ai-chapters", self._handle_ai_chapters)
        self.app.router.add_get("/api/transcript/summary", self._handle_sermon_summary)
        self.app.router.add_post("/api/transcript/summary", self._handle_sermon_summary)
        self.app.router.add_get("/api/transcript/summary/status", self._handle_summary_status)
        self.app.router.add_get("/api/transcript/export", self._handle_export_transcript)
        self.app.router.add_get("/api/translate", self._handle_translate_text)
        self.app.router.add_post("/api/translate", self._handle_translate_text)
        self.app.router.add_post("/api/translation/prewarm", self._handle_translation_prewarm)
        self.app.router.add_get("/api/translation/stats", self._handle_translation_stats)
        
        # Filter Management CRUD
        self.app.router.add_get("/api/filter/state", self._handle_filter_state)
        self.app.router.add_post("/api/filter/test", self._handle_filter_test)
        self.app.router.add_post("/api/filter/blacklist/add", self._handle_add_blacklist)
        self.app.router.add_post("/api/filter/blacklist/remove", self._handle_remove_blacklist)
        self.app.router.add_post("/api/filter/whitelist/add", self._handle_add_whitelist)
        self.app.router.add_post("/api/filter/whitelist/remove", self._handle_remove_whitelist)
        self.app.router.add_post("/api/filter/replacements/set", self._handle_set_replacement)
        self.app.router.add_post("/api/filter/replacements/remove", self._handle_remove_replacement)

        # Custom Vocabulary & Glossary CRUD
        self.app.router.add_get("/api/vocabulary", self._handle_get_vocabulary)
        self.app.router.add_post("/api/vocabulary/set", self._handle_set_vocabulary)
        self.app.router.add_post("/api/vocabulary/remove", self._handle_remove_vocabulary)
        self.app.router.add_post("/api/vocabulary/test", self._handle_test_vocabulary)
        self.app.router.add_post("/api/vocabulary/bulk", self._handle_bulk_vocabulary)
        self.app.router.add_get("/api/vocabulary/export", self._handle_export_vocabulary)
        self.app.router.add_post("/api/vocabulary/clear", self._handle_clear_vocabulary)

        # Model Downloader & Cache Manager
        self.app.router.add_get("/api/models/status", self._handle_get_models_status)
        self.app.router.add_post("/api/models/download", self._handle_download_model)
        self.app.router.add_post("/api/models/cancel", self._handle_cancel_download_model)
        self.app.router.add_post("/api/models/delete", self._handle_delete_model)
        self.app.router.add_delete("/api/models", self._handle_delete_model)

        # Offline Bible & Scripture Engine
        self.app.router.add_get("/api/bible/versions", self._handle_bible_versions)
        self.app.router.add_get("/api/bible/lookup", self._handle_bible_lookup)
        self.app.router.add_post("/api/bible/display", self._handle_bible_display)
        self.app.router.add_post("/api/bible/dismiss", self._handle_bible_dismiss)

        # Software Updater
        self.app.router.add_get("/api/updater/status", self._handle_updater_status)
        self.app.router.add_post("/api/updater/check", self._handle_updater_check)
        self.app.router.add_post("/api/updater/apply", self._handle_updater_apply)

        # Network Info & Stage QR Code
        self.app.router.add_get("/api/network/info", self._handle_get_network_info)
        self.app.router.add_get("/api/display/qr", self._handle_get_display_qr)
        self.app.router.add_get("/api/qr", self._handle_get_display_qr)

        # Synchronized Subtitle Sidecar Recording (.srt / .vtt)
        self.app.router.add_get("/api/recording/status", self._handle_get_recording_status)
        self.app.router.add_post("/api/recording/start", self._handle_recording_start)
        self.app.router.add_post("/api/recording/stop", self._handle_recording_stop)

        # Hardware & Memory System Controls
        self.app.router.add_post("/api/system/trim_memory", self._handle_trim_memory)

        # Caddy Reverse Proxy & Local SSL Endpoints
        self.app.router.add_get("/api/caddy/status", self._handle_get_caddy_status)
        self.app.router.add_post("/api/caddy/start", self._handle_post_caddy_start)
        self.app.router.add_post("/api/caddy/stop", self._handle_post_caddy_stop)
        self.app.router.add_post("/api/caddy/download", self._handle_post_caddy_download)
        self.app.router.add_post("/api/caddy/trust", self._handle_post_caddy_trust)
        self.app.router.add_get("/api/caddy/ca.crt", self._handle_get_caddy_ca)
        self.app.router.add_get("/api/ssl/ca.crt", self._handle_get_caddy_ca)

        # Static Assets
        self.app.router.add_static("/static/", path=str(static_dir), name="static")

    async def _handle_index(self, request: web.Request) -> web.FileResponse:
        index_file = Path(__file__).parent / "static" / "index.html"
        return web.FileResponse(index_file, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    async def _handle_bible_page(self, request: web.Request) -> web.FileResponse:
        bible_file = Path(__file__).parent / "static" / "bible.html"
        return web.FileResponse(bible_file, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    async def _handle_dashboard(self, request: web.Request) -> web.FileResponse:
        dash_file = Path(__file__).parent / "static" / "dashboard.html"
        return web.FileResponse(dash_file)

    async def _handle_display(self, request: web.Request) -> web.FileResponse:
        display_file = Path(__file__).parent / "static" / "display.html"
        return web.FileResponse(display_file)

    async def _handle_manifest(self, request: web.Request) -> web.FileResponse:
        manifest_file = Path(__file__).parent / "static" / "manifest-display.json"
        return web.FileResponse(manifest_file, headers={"Content-Type": "application/manifest+json"})

    async def _handle_manifest_display(self, request: web.Request) -> web.FileResponse:
        manifest_file = Path(__file__).parent / "static" / "manifest-display.json"
        return web.FileResponse(manifest_file, headers={"Content-Type": "application/manifest+json"})

    async def _handle_manifest_dashboard(self, request: web.Request) -> web.FileResponse:
        manifest_file = Path(__file__).parent / "static" / "manifest-dashboard.json"
        return web.FileResponse(manifest_file, headers={"Content-Type": "application/manifest+json"})

    async def _handle_service_worker(self, request: web.Request) -> web.FileResponse:
        sw_file = Path(__file__).parent / "static" / "sw.js"
        return web.FileResponse(sw_file, headers={
            "Content-Type": "application/javascript",
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache"
        })

    async def _handle_get_status(self, request: web.Request) -> web.Response:
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        from ..hardware import get_ram_usage_mb, get_gpu_info, get_available_gpus, get_local_ip
        lan_ip = get_local_ip()
        req_host = (request.host or "").split(":")[0].strip()
        if lan_ip == "127.0.0.1" and req_host and req_host not in ("127.0.0.1", "localhost", "0.0.0.0"):
            lan_ip = req_host
        port = self.config.overlay.port or 8765
        preferred_gpu = getattr(self.config.general, "preferred_gpu", "auto")
        status_info = {
            "engine": self.config.general.engine,
            "language": self.config.general.language,
            "audio_device": self.config.audio.device_name_filter,
            "total_transcript_lines": len(self.history.entries),
            "theme": self.config.overlay.theme_id,
            "translation_enabled": self.config.translation.enabled,
            "timestamp": time.time(),
            "instance_id": self.instance_id,
            "server_start_time": self.server_start_time,
            "uptime_seconds": round(time.time() - self.server_start_time, 1),
            "ram_usage_mb": get_ram_usage_mb(),
            "gpu": get_gpu_info(preferred_gpu),
            "preferred_gpu": preferred_gpu,
            "available_gpus": get_available_gpus(),
            "lan_ip": lan_ip,
            "port": port,
        }

        # Caddy reverse proxy detection
        from ..caddy_manager import is_caddy_running
        caddy_cfg = getattr(self.config, "caddy", None)
        port_http = getattr(caddy_cfg, "port_http", 80) if caddy_cfg else 80
        port_https = getattr(caddy_cfg, "port_https", 443) if caddy_cfg else 443
        caddy_ssl = getattr(caddy_cfg, "ssl", True) if caddy_cfg else True
        caddy_active = is_caddy_running(port_http, port_https)

        if caddy_active:
            p_http_str = "" if port_http == 80 else f":{port_http}"
            p_https_str = "" if port_https == 443 else f":{port_https}"
            status_info["display_url"] = f"http://{lan_ip}{p_http_str}/display"
            status_info["display_https_url"] = f"https://{lan_ip}{p_https_str}/display"
            status_info["dashboard_url"] = f"https://{lan_ip}{p_https_str}/dashboard" if caddy_ssl else f"http://{lan_ip}{p_http_str}/dashboard"
            status_info["overlay_url"] = f"http://{lan_ip}{p_http_str}/"
            status_info["bible_url"] = f"http://{lan_ip}{p_http_str}/bible"
        else:
            status_info["display_url"] = f"http://{lan_ip}:{port}/display"
            status_info["display_https_url"] = ""
            status_info["dashboard_url"] = f"http://{lan_ip}:{port}/dashboard"
            status_info["overlay_url"] = f"http://{lan_ip}:{port}/"
            status_info["bible_url"] = f"http://{lan_ip}:{port}/bible"

        status_info["caddy_active"] = caddy_active
        status_info["caddy_ssl"] = caddy_ssl

        if self.subtitle_recorder:
            status_info["recording"] = self.subtitle_recorder.get_status()
        if self.history:
            status_info.update(self.history.get_stats())
        if self.get_app_status:
            try:
                status_info.update(self.get_app_status())
            except Exception:
                logger.warning("get_app_status hook failed", exc_info=True)
        # Never report "running" unless the app-status hook confirmed it
        status_info.setdefault("is_running", False)
        return web.json_response(status_info)

    async def _handle_get_network_info(self, request: web.Request) -> web.Response:
        """Return LAN IP address and direct mobile/stage display links."""
        from ..hardware import get_local_ip
        from ..caddy_manager import is_caddy_running
        lan_ip = get_local_ip()
        req_host = (request.host or "").split(":")[0].strip()
        if lan_ip == "127.0.0.1" and req_host and req_host not in ("127.0.0.1", "localhost", "0.0.0.0"):
            lan_ip = req_host
        port = self.config.overlay.port or 8765

        caddy_cfg = getattr(self.config, "caddy", None)
        port_http = getattr(caddy_cfg, "port_http", 80) if caddy_cfg else 80
        port_https = getattr(caddy_cfg, "port_https", 443) if caddy_cfg else 443
        caddy_ssl = getattr(caddy_cfg, "ssl", True) if caddy_cfg else True
        caddy_active = is_caddy_running(port_http, port_https)

        if caddy_active:
            p_http_str = "" if port_http == 80 else f":{port_http}"
            p_https_str = "" if port_https == 443 else f":{port_https}"
            # Stage / mobile display defaults to clean HTTP (port 80) so mobile phones never show certificate warnings
            display_url = f"http://{lan_ip}{p_http_str}/display"
            dashboard_url = f"https://{lan_ip}{p_https_str}/dashboard" if caddy_ssl else f"http://{lan_ip}{p_http_str}/dashboard"
            overlay_url = f"http://{lan_ip}{p_http_str}/"
            bible_url = f"http://{lan_ip}{p_http_str}/bible"
        else:
            display_url = f"http://{lan_ip}:{port}/display"
            dashboard_url = f"http://{lan_ip}:{port}/dashboard"
            overlay_url = f"http://{lan_ip}:{port}/"
            bible_url = f"http://{lan_ip}:{port}/bible"

        return web.json_response({
            "lan_ip": lan_ip,
            "port": port,
            "caddy_active": caddy_active,
            "caddy_ssl": caddy_ssl,
            "display_url": display_url,
            "display_https_url": f"https://{lan_ip}:{port_https}/display" if (caddy_active and port_https != 443) else f"https://{lan_ip}/display",
            "dashboard_url": dashboard_url,
            "overlay_url": overlay_url,
            "bible_url": bible_url,
        })

    async def _handle_get_display_qr(self, request: web.Request) -> web.Response:
        """Return scalable vector SVG QR code for the Stage Confidence Monitor / Reader Display or Dashboard."""
        from ..hardware import get_local_ip
        from ..caddy_manager import is_caddy_running
        from ..qr_generator import generate_qr_svg
        lan_ip = sanitize_text(request.query.get("host", "")).strip() or sanitize_text(request.query.get("ip", "")).strip()
        if not lan_ip:
            lan_ip = get_local_ip()
            req_host = (request.host or "").split(":")[0].strip()
            if lan_ip == "127.0.0.1" and req_host and req_host not in ("127.0.0.1", "localhost", "0.0.0.0"):
                lan_ip = req_host
        port = self.config.overlay.port or 8765

        caddy_cfg = getattr(self.config, "caddy", None)
        port_http = getattr(caddy_cfg, "port_http", 80) if caddy_cfg else 80
        port_https = getattr(caddy_cfg, "port_https", 443) if caddy_cfg else 443
        caddy_ssl = getattr(caddy_cfg, "ssl", True) if caddy_cfg else True
        caddy_active = is_caddy_running(port_http, port_https)

        want_ssl = request.query.get("ssl", "").strip().lower() in ("1", "true", "yes")

        if caddy_active:
            if want_ssl and caddy_ssl:
                p_str = "" if port_https == 443 else f":{port_https}"
                base_url = f"https://{lan_ip}{p_str}"
            else:
                p_str = "" if port_http == 80 else f":{port_http}"
                base_url = f"http://{lan_ip}{p_str}"
        else:
            base_url = f"http://{lan_ip}:{port}"

        custom_url = request.query.get("url", "").strip()
        qr_type = request.query.get("type", "").strip().lower()
        if custom_url:
            target_url = custom_url
        elif qr_type == "dashboard":
            target_url = f"{base_url}/dashboard"
        elif qr_type == "overlay":
            target_url = f"{base_url}/"
        elif qr_type == "bible":
            target_url = f"{base_url}/bible"
        else:
            lang = sanitize_text(request.query.get("lang", "")).lower().strip()
            if lang and lang not in ("en", "original", "none"):
                target_url = f"{base_url}/display?lang={urllib.parse.quote(lang)}"
            else:
                target_url = f"{base_url}/display"

        svg_content = generate_qr_svg(target_url)
        return web.Response(
            body=svg_content,
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=60"},
        )

    # --- Caddy Reverse Proxy Endpoints ---
    async def _handle_get_caddy_status(self, request: web.Request) -> web.Response:
        """Return Caddy reverse proxy telemetry, status, and clean URLs."""
        from ..caddy_manager import get_caddy_telemetry
        data = get_caddy_telemetry(self.config)
        return web.json_response(data)

    async def _handle_post_caddy_start(self, request: web.Request) -> web.Response:
        """Start Caddy reverse proxy."""
        from ..caddy_manager import start_caddy
        success, msg = start_caddy()
        if success and getattr(self.config, "caddy", None):
            self.config.caddy.enabled = True
            from ..config import save_config
            save_config(self.config)
        return web.json_response({"success": success, "message": msg}, status=200 if success else 400)

    async def _handle_post_caddy_stop(self, request: web.Request) -> web.Response:
        """Stop Caddy reverse proxy."""
        from ..caddy_manager import stop_caddy
        success, msg = stop_caddy()
        if success and getattr(self.config, "caddy", None):
            self.config.caddy.enabled = False
            from ..config import save_config
            save_config(self.config)
        return web.json_response({"success": success, "message": msg}, status=200 if success else 400)

    async def _handle_post_caddy_download(self, request: web.Request) -> web.Response:
        """Download Caddy binary."""
        from ..caddy_manager import download_caddy
        success, msg = download_caddy()
        return web.json_response({"success": success, "message": msg}, status=200 if success else 400)

    async def _handle_post_caddy_trust(self, request: web.Request) -> web.Response:
        """Install Caddy root CA into system trust store."""
        from ..caddy_manager import trust_caddy_ca
        success, msg = trust_caddy_ca()
        return web.json_response({"success": success, "message": msg}, status=200 if success else 400)

    async def _handle_get_caddy_ca(self, request: web.Request) -> web.Response:
        """Download Caddy's auto-generated local Root CA certificate."""
        from ..caddy_manager import get_caddy_root_ca_path
        ca_path = get_caddy_root_ca_path()
        if not ca_path or not ca_path.is_file():
            return web.Response(text="Caddy Root CA certificate not found. Start Caddy with SSL first.", status=404)
        return web.FileResponse(
            ca_path,
            headers={
                "Content-Type": "application/x-x509-ca-cert",
                "Content-Disposition": 'attachment; filename="voxstream-caddy-ca.crt"',
            },
        )

    async def _handle_get_recording_status(self, request: web.Request) -> web.Response:
        """Return current live subtitle recording telemetry."""
        if not self.subtitle_recorder:
            return web.json_response({"is_recording": False, "error": "Recorder not configured"})
        return web.json_response(self.subtitle_recorder.get_status())

    async def _handle_recording_start(self, request: web.Request) -> web.Response:
        """Manually trigger synchronized subtitle sidecar recording."""
        if not self.subtitle_recorder:
            return web.json_response({"error": "Recorder not configured"}, status=400)
        try:
            body = await request.json()
        except Exception:
            body = {}
        video_path = body.get("video_path")
        self.subtitle_recorder.start_recording(video_path=video_path)
        status = self.subtitle_recorder.get_status()
        await self.broadcast_control({"type": "recording_state_changed", "recording": status})
        return web.json_response({"status": "started", "recording": status})

    async def _handle_recording_stop(self, request: web.Request) -> web.Response:
        """Manually stop synchronized subtitle sidecar recording and finalize files."""
        if not self.subtitle_recorder:
            return web.json_response({"error": "Recorder not configured"}, status=400)
        res = self.subtitle_recorder.stop_recording()
        await self.broadcast_control({"type": "recording_state_changed", "recording": res})
        return web.json_response({"status": "stopped", "recording": res})

    async def _handle_trim_memory(self, request: web.Request) -> web.Response:
        """Trigger on-demand garbage collection and physical working set memory trim."""
        from ..hardware import release_stt_memory, get_ram_usage_mb
        if self.on_trim_memory and callable(self.on_trim_memory):
            try:
                freed = self.on_trim_memory()
            except Exception as e:
                logger.error(f"Error in on_trim_memory hook: {e}")
                freed = release_stt_memory(log_details=True)
        else:
            freed = release_stt_memory(log_details=True)

        current_ram = get_ram_usage_mb()
        await self.broadcast_control({
            "type": "ram_trimmed",
            "freed_mb": freed,
            "current_ram_mb": current_ram,
        })
        return web.json_response({
            "status": "success",
            "freed_mb": freed,
            "current_ram_mb": current_ram,
        })

    async def _handle_get_transcript_stats(self, request: web.Request) -> web.Response:
        """Return live Words Per Minute (WPM), total words, and speaking session statistics."""
        return web.json_response(self.history.get_stats())

    async def _handle_get_benchmark(self, request: web.Request) -> web.Response:
        """Return church sermon benchmark rankings and evaluation metrics."""
        rankings_file = Path(__file__).resolve().parent / "static" / "engine_rankings.json"
        if rankings_file.exists():
            try:
                with open(rankings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return web.json_response(data)
            except Exception as e:
                logger.error(f"Error reading engine_rankings.json: {e}")
        return web.json_response({"error": "Benchmark rankings not found", "rankings": []}, status=404)

    # Sentinel used in place of secret values in GET /api/config responses.
    # POSTing the sentinel back leaves the stored secret unchanged.
    SECRET_SENTINEL = "•••"
    SECRET_FIELDS = {
        "gemini_live": ("api_key",),
        "bandwidth": ("api_key",),
        "twitch": ("oauth_token",),
        "obs": ("password",),
        "api": ("api_key",),
        "summary": ("gemini_api_key",),
        "translation": ("gemini_api_key",),
    }

    def get_masked_config_dict(self) -> dict:
        """Return configuration dict with sensitive credentials masked."""
        data = asdict(self.config)
        for section, fields in self.SECRET_FIELDS.items():
            for f in fields:
                if data.get(section, {}).get(f):
                    data[section][f] = self.SECRET_SENTINEL
        return data

    async def _handle_get_config(self, request: web.Request) -> web.Response:
        return web.json_response(self.get_masked_config_dict())

    async def _handle_post_config(self, request: web.Request) -> web.Response:
        """Update live settings and persist to config.json."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            body = await request.json()
            for key, val in body.items():
                if hasattr(self.config, key) and isinstance(val, dict):
                    section = getattr(self.config, key)
                    secret_fields = self.SECRET_FIELDS.get(key, ())
                    for sec_k, sec_v in val.items():
                        if not hasattr(section, sec_k):
                            continue
                        # Handle secret / credential fields
                        if sec_k in secret_fields:
                            # 1) If masked sentinel ("•••"), leave existing secret unchanged
                            if sec_v == self.SECRET_SENTINEL:
                                continue
                            # 2) If explicit clear command, wipe the key
                            if sec_v == "__CLEAR__":
                                setattr(section, sec_k, "")
                                continue
                            # 3) If empty string or None, and an existing key is present:
                            # preserve existing key unless explicit clear flag is set
                            if (sec_v == "" or sec_v is None) and getattr(section, sec_k, ""):
                                clear_flag = val.get(f"clear_{sec_k}", False) or body.get(f"clear_{key}_{sec_k}", False)
                                if not clear_flag:
                                    continue
                                sec_v = ""

                        setattr(section, sec_k, sec_v)

            save_config(self.config)
            if hasattr(self, "translator") and self.translator:
                self.translator.config = self.config.translation
                if getattr(self.config.translation, "enabled", False) and getattr(self.config.translation, "provider", "") == "nllb":
                    asyncio.create_task(self.translator.prewarm_async())

            if self.on_config_updated:
                self.on_config_updated(self.config)

            await self.broadcast_control({"type": "config_updated", "config": self.get_masked_config_dict()})
            return web.json_response({"status": "success", "message": "Configuration updated and saved."})
        except Exception as e:
            logger.error(f"Error saving config via API: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def _handle_get_devices(self, request: web.Request) -> web.Response:
        devices = list_audio_devices()
        return web.json_response({"devices": devices})

    async def _handle_get_hardware_gpus(self, request: web.Request) -> web.Response:
        """Return list of available system GPUs, selected preference, and active GPU."""
        from ..hardware import get_available_gpus, get_gpu_info
        preferred = getattr(self.config.general, "preferred_gpu", "auto")
        return web.json_response({
            "gpus": get_available_gpus(),
            "preferred_gpu": preferred,
            "active_gpu": get_gpu_info(preferred),
        })

    async def _handle_get_presets(self, request: web.Request) -> web.Response:
        return web.json_response({"presets": get_all_presets(self.config.custom_presets)})

    async def _handle_apply_preset(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            theme_id = sanitize_text(data.get("theme_id", ""))

            applied = self.config.overlay.apply_theme(theme_id, self.config.custom_presets)
            if not applied:
                return web.json_response({"error": f"Unknown theme preset '{theme_id}'"}, status=404)

            save_config(self.config)

            if self.on_config_updated:
                self.on_config_updated(self.config)

            await self.broadcast_control({"type": "config_updated", "config": self.get_masked_config_dict()})

            # Find applied preset info
            applied_preset = None
            for p in get_all_presets(self.config.custom_presets):
                if p["id"] == theme_id:
                    applied_preset = p
                    break

            return web.json_response({"status": "success", "theme": applied_preset or {"id": theme_id}})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_save_preset(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            name = sanitize_text(data.get("name", "").strip(), max_len=60)
            if not name:
                return web.json_response({"error": "Preset name is required."}, status=400)

            # Auto-generate preset ID
            raw_id = sanitize_text(data.get("id", "").strip().lower(), max_len=50)
            if not raw_id:
                raw_id = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
            if not raw_id:
                raw_id = f"custom_{int(time.time())}"

            desc = sanitize_text(data.get("description", "Custom user preset."), max_len=150)

            preset_entry = {
                "id": raw_id,
                "name": name,
                "description": desc,
                "font_family": data.get("font_family", self.config.overlay.font_family),
                "font_size": data.get("font_size", self.config.overlay.font_size),
                "font_weight": data.get("font_weight", self.config.overlay.font_weight),
                "line_height": data.get("line_height", self.config.overlay.line_height),
                "text_color": data.get("text_color", self.config.overlay.text_color),
                "interim_color": data.get("interim_color", self.config.overlay.interim_color),
                "highlight_color": data.get("highlight_color", self.config.overlay.highlight_color),
                "background_box_color": data.get("background_box_color", self.config.overlay.background_box_color),
                "border_radius": data.get("border_radius", self.config.overlay.border_radius),
                "box_padding": data.get("box_padding", self.config.overlay.box_padding),
                "text_shadow": data.get("text_shadow", self.config.overlay.text_shadow),
                "text_stroke": data.get("text_stroke", self.config.overlay.text_stroke),
                "animation_style": data.get("animation_style", self.config.overlay.animation_style),
                "is_custom": True,
            }

            self.config.custom_presets[raw_id] = preset_entry
            save_config(self.config)

            if self.on_config_updated:
                self.on_config_updated(self.config)

            return web.json_response({
                "status": "success",
                "message": f"Preset '{name}' saved successfully.",
                "preset": preset_entry,
                "presets": get_all_presets(self.config.custom_presets),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_delete_preset(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            preset_id = sanitize_text((data.get("id") or data.get("preset_id") or "").strip())
            if preset_id in THEME_PRESETS:
                return web.json_response({"error": "Cannot delete built-in theme presets."}, status=400)

            if preset_id not in self.config.custom_presets:
                return web.json_response({"error": f"Custom preset '{preset_id}' not found."}, status=404)

            deleted = self.config.custom_presets.pop(preset_id, None)
            save_config(self.config)

            if self.on_config_updated:
                self.on_config_updated(self.config)

            return web.json_response({
                "status": "success",
                "message": "Preset deleted.",
                "presets": get_all_presets(self.config.custom_presets),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_get_languages(self, request: web.Request) -> web.Response:
        return web.json_response({"languages": SUPPORTED_LANGUAGES})

    async def _handle_control_toggle(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        is_running = False
        if self.get_app_status:
            try:
                st = self.get_app_status()
                is_running = st.get("is_running", False)
            except Exception:
                pass
        if is_running:
            if self.on_stop_requested:
                self.on_stop_requested()
            new_state = False
            msg = "Captioner stopped."
        else:
            if self.on_start_requested:
                self.on_start_requested()
            new_state = True
            msg = "Captioner started."
        return web.json_response({"status": "success", "is_running": new_state, "message": msg})

    async def _handle_control_panic(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        self._recent_finals.clear()
        panic_payload = {"text": "", "is_final": True, "is_censored": True, "panic": True, "timestamp": time.time()}
        msg = json.dumps(panic_payload)
        for ws in list(self.caption_sockets.keys()):
            try:
                await ws.send_str(msg)
            except Exception:
                pass
        if self.obs_client and self.config.obs.update_text_source and self.config.obs.text_source_name:
            try:
                await self.obs_client.update_text_source(self.config.obs.text_source_name, "")
            except Exception:
                pass
        logger.info("[PANIC BUTTON] Live captions wiped from all screens.")
        return web.json_response({"status": "success", "message": "Panic button triggered: live captions cleared."})

    async def _handle_get_chapters(self, request: web.Request) -> web.Response:
        try:
            raw_interval = float(request.query.get("min_interval", 45.0))
            if re.search(r"nan|inf", str(raw_interval).lower()):
                min_interval = 45.0
            else:
                min_interval = max(10.0, min(3600.0, raw_interval))
        except (ValueError, TypeError):
            min_interval = 45.0

        try:
            raw_offset = float(request.query.get("offset", 0.0))
            if re.search(r"nan|inf", str(raw_offset).lower()):
                offset = 0.0
            else:
                offset = max(-86400.0, min(86400.0, raw_offset))
        except (ValueError, TypeError):
            offset = 0.0

        anchor = sanitize_text(request.query.get("anchor", "first_speech")).strip().lower()
        if anchor not in ("first_speech", "session"):
            anchor = "first_speech"

        format_style = sanitize_text(request.query.get("format", "hhmmss")).strip().lower()
        if format_style not in ("hhmmss", "mmss", "auto"):
            format_style = "hhmmss"

        engine = sanitize_text(request.query.get("engine", "") or request.query.get("provider", "")).strip().lower()
        model = sanitize_text(request.query.get("model", "")).strip() or None

        if engine in ("gemini", "ai"):
            result = await self.summary_engine.generate_ai_chapters(
                entries=self.history.entries,
                min_interval_seconds=min_interval,
                time_offset_seconds=offset,
                anchor=anchor,
                format_style=format_style,
                provider_override="gemini",
                model_override=model,
            )
            return web.json_response(result)

        if engine in ("heuristic", "offline", "local"):
            chapters = self.history.generate_chapters(
                min_interval_seconds=min_interval,
                time_offset_seconds=offset,
                anchor=anchor,
                format_style=format_style,
            )
            formatted = self.history.export_youtube_chapters(
                min_interval_seconds=min_interval,
                time_offset_seconds=offset,
                anchor=anchor,
                format_style=format_style,
            )
            youtube_compliant = len(chapters) >= 3 and (chapters[0]["seconds"] == 0.0 if chapters else False)
            return web.json_response({
                "chapters": chapters,
                "formatted": formatted,
                "count": len(chapters),
                "provider_used": "heuristic",
                "youtube_compliant": youtube_compliant,
                "min_interval": min_interval,
                "offset": offset,
                "anchor": anchor,
                "format": format_style,
            })

        # Default / Auto: If Gemini API key is configured, use Gemini, else fallback to heuristic
        if hasattr(self, "summary_engine") and self.summary_engine and self.summary_engine.get_api_key():
            result = await self.summary_engine.generate_ai_chapters(
                entries=self.history.entries,
                min_interval_seconds=min_interval,
                time_offset_seconds=offset,
                anchor=anchor,
                format_style=format_style,
                provider_override="gemini",
                model_override=model,
            )
            return web.json_response(result)

        chapters = self.history.generate_chapters(
            min_interval_seconds=min_interval,
            time_offset_seconds=offset,
            anchor=anchor,
            format_style=format_style,
        )
        formatted = self.history.export_youtube_chapters(
            min_interval_seconds=min_interval,
            time_offset_seconds=offset,
            anchor=anchor,
            format_style=format_style,
        )
        youtube_compliant = len(chapters) >= 3 and (chapters[0]["seconds"] == 0.0 if chapters else False)

        return web.json_response({
            "chapters": chapters,
            "formatted": formatted,
            "count": len(chapters),
            "provider_used": "heuristic",
            "youtube_compliant": youtube_compliant,
            "min_interval": min_interval,
            "offset": offset,
            "anchor": anchor,
            "format": format_style,
        })

    async def _handle_summary_status(self, request: web.Request) -> web.Response:
        """Return availability and configuration of AI sermon summary & chapter providers."""
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)
        return web.json_response(self.summary_engine.get_status())

    async def _handle_ai_chapters(self, request: web.Request) -> web.Response:
        """Generate semantic, YouTube-compliant timestamped video chapters using AI or heuristics."""
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        min_interval = 45.0
        offset = 0.0
        anchor = "first_speech"
        format_style = "hhmmss"
        provider = None
        model = None

        if request.method == "POST":
            try:
                body = await request.json()
                min_interval = float(body.get("min_interval", min_interval))
                offset = float(body.get("offset", offset))
                anchor = sanitize_text(body.get("anchor", anchor)).strip().lower()
                format_style = sanitize_text(body.get("format", format_style)).strip().lower()
                provider = sanitize_text(body.get("provider", "") or body.get("engine", "")).strip().lower() or None
                model = sanitize_text(body.get("model", "")).strip() or None
            except Exception:
                pass
        else:
            try:
                min_interval = float(request.query.get("min_interval", 45.0))
                offset = float(request.query.get("offset", 0.0))
                anchor = sanitize_text(request.query.get("anchor", "first_speech")).strip().lower()
                format_style = sanitize_text(request.query.get("format", "hhmmss")).strip().lower()
                provider = sanitize_text(request.query.get("provider", "") or request.query.get("engine", "")).strip().lower() or None
                model = sanitize_text(request.query.get("model", "")).strip() or None
            except Exception:
                pass

        if anchor not in ("first_speech", "session"):
            anchor = "first_speech"
        if format_style not in ("hhmmss", "mmss", "auto"):
            format_style = "hhmmss"

        result = await self.summary_engine.generate_ai_chapters(
            entries=self.history.entries,
            min_interval_seconds=min_interval,
            time_offset_seconds=offset,
            anchor=anchor,
            format_style=format_style,
            provider_override=provider,
            model_override=model,
        )
        return web.json_response(result)

    async def _handle_sermon_summary(self, request: web.Request) -> web.Response:
        """Generate comprehensive sermon summary, bulletin outline, and discussion questions."""
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        provider = None
        model = None
        if request.method == "POST":
            try:
                body = await request.json()
                provider = sanitize_text(body.get("provider", "")).strip().lower() or None
                model = sanitize_text(body.get("model", "")).strip() or None
            except Exception:
                pass
        else:
            provider = sanitize_text(request.query.get("provider", "")).strip().lower() or None
            model = sanitize_text(request.query.get("model", "")).strip() or None

        result = await self.summary_engine.generate_sermon_summary(
            entries=self.history.entries,
            provider_override=provider,
            model_override=model,
        )
        return web.json_response(result)

    async def _handle_translate_text(self, request: web.Request) -> web.Response:
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = {}
            text = sanitize_text(body.get("text", "")).strip()
            target = sanitize_text(body.get("target") or body.get("target_lang", "es")).strip().lower()
            source = sanitize_text(body.get("source") or body.get("source_lang", "auto")).strip().lower()
            provider = sanitize_text(body.get("provider", "")).strip().lower() or None
        else:
            text = sanitize_text(request.query.get("text", "")).strip()
            target = sanitize_text(request.query.get("target", "es")).strip().lower()
            source = sanitize_text(request.query.get("source", "auto")).strip().lower()
            provider = sanitize_text(request.query.get("provider", "")).strip().lower() or None

        if not text:
            return web.json_response({"original": "", "translated": "", "target": target})
        translated = await self.translator.translate_to_language(
            text, target_lang=target, source_lang=source, provider_override=provider
        )
        return web.json_response({
            "original": text,
            "translated": translated or text,
            "target": target,
            "source": source
        })

    async def _handle_translation_prewarm(self, request: web.Request) -> web.Response:
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)
        try:
            data = await request.json()
        except Exception:
            data = {}
        provider = sanitize_text(data.get("provider", "")).strip().lower() or getattr(self.config.translation, "provider", "nllb")
        if hasattr(self, "translator") and self.translator:
            asyncio.create_task(self.translator.prewarm_async())
        return web.json_response({"status": "prewarming", "provider": provider})

    async def _handle_translation_stats(self, request: web.Request) -> web.Response:
        stats = {}
        if hasattr(self, "translator") and self.translator and hasattr(self.translator, "disk_cache") and self.translator.disk_cache:
            stats = self.translator.disk_cache.get_stats()
        return web.json_response({"status": "ok", "stats": stats})

    async def _handle_control_start(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.on_start_requested:
            self.on_start_requested()
        return web.json_response({"status": "success", "message": "Captioner started."})

    async def _handle_control_stop(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.on_stop_requested:
            self.on_stop_requested()
        return web.json_response({"status": "success", "message": "Captioner stopped."})

    # ──────────────────────────────────────────────────────────────────────────
    # Scheduler API Handlers
    # ──────────────────────────────────────────────────────────────────────────

    def _scheduler_status_payload(self) -> dict:
        """Build a full scheduler status dict for API responses."""
        from dataclasses import asdict
        sched = self.scheduler
        if sched is None:
            return {
                "scheduler_enabled": False,
                "timer": {"active": False, "remaining_seconds": 0, "target_timestamp": None, "target_formatted": None},
                "schedules": [],
                "next_event": None,
                "obs_auto_stop_on_stream": getattr(self.config.obs, "auto_stop_on_stream", False),
                "obs_auto_stop_on_record": getattr(self.config.obs, "auto_stop_on_record", False),
            }
        return {
            "scheduler_enabled": sched._config.enabled,
            "timer": sched.get_timer_status(),
            "schedules": [asdict(s) for s in sched.get_schedules()],
            "next_event": sched.get_next_event(),
            "obs_auto_stop_on_stream": getattr(self.config.obs, "auto_stop_on_stream", False),
            "obs_auto_stop_on_record": getattr(self.config.obs, "auto_stop_on_record", False),
        }

    async def _handle_scheduler_status(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        return web.json_response(self._scheduler_status_payload())

    async def _handle_scheduler_set_timer(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.scheduler is None:
            return web.json_response({"error": "Scheduler not initialized."}, status=503)
        try:
            data = await request.json()
        except Exception:
            data = {}

        target_ts = None
        action_msg = "Auto-stop timer set."
        if "add_minutes" in data:
            target_ts = self.scheduler.add_duration(float(data["add_minutes"]) * 60.0)
            action_msg = f"Added {data['add_minutes']}m to timer."
        elif "add_seconds" in data:
            target_ts = self.scheduler.add_duration(float(data["add_seconds"]))
            action_msg = f"Added {data['add_seconds']}s to timer."
        elif "end_time" in data and data["end_time"]:
            target_ts = self.scheduler.set_end_time(str(data["end_time"]))
            if target_ts is None:
                return web.json_response({"error": f"Could not parse end_time: '{data['end_time']}'"}, status=400)
        elif "duration_seconds" in data:
            target_ts = self.scheduler.set_duration(float(data["duration_seconds"]))
        elif "duration_minutes" in data:
            target_ts = self.scheduler.set_duration(float(data["duration_minutes"]) * 60.0)
        else:
            return web.json_response({"error": "Provide 'add_minutes', 'duration_minutes', 'duration_seconds', or 'end_time'."}, status=400)

        from .config import save_config
        save_config(self.config)
        return web.json_response({
            "status": "success",
            "message": action_msg,
            "timer": self.scheduler.get_timer_status(),
        })

    async def _handle_scheduler_cancel_timer(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.scheduler is None:
            return web.json_response({"error": "Scheduler not initialized."}, status=503)
        self.scheduler.cancel_timer()
        return web.json_response({"status": "success", "message": "Timer cancelled.", "timer": self.scheduler.get_timer_status()})

    async def _handle_scheduler_get_schedules(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        from dataclasses import asdict
        schedules = [asdict(s) for s in (self.scheduler.get_schedules() if self.scheduler else [])]
        return web.json_response({"status": "success", "schedules": schedules})

    async def _handle_scheduler_post_schedule(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.scheduler is None:
            return web.json_response({"error": "Scheduler not initialized."}, status=503)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body."}, status=400)

        schedule_id = data.get("id", "")
        if schedule_id:
            # Update existing
            updated = self.scheduler.update_schedule(schedule_id, data)
            if not updated:
                return web.json_response({"error": f"Schedule '{schedule_id}' not found."}, status=404)
            msg = "Schedule updated."
        else:
            # Create new
            updated = self.scheduler.add_schedule(data)
            msg = "Schedule created."

        from dataclasses import asdict
        from obs_captioner.config import save_config
        save_config(self.config)
        return web.json_response({"status": "success", "message": msg, "schedule": asdict(updated)})

    async def _handle_scheduler_delete_schedule(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if self.scheduler is None:
            return web.json_response({"error": "Scheduler not initialized."}, status=503)
        schedule_id = request.match_info.get("schedule_id", "")
        removed = self.scheduler.remove_schedule(schedule_id)
        if not removed:
            return web.json_response({"error": f"Schedule '{schedule_id}' not found."}, status=404)
        from obs_captioner.config import save_config
        save_config(self.config)
        return web.json_response({"status": "success", "message": f"Schedule '{schedule_id}' deleted."})

    async def _handle_scheduler_update_config(self, request: web.Request) -> web.Response:
        """Update OBS auto-stop toggles and global scheduler enabled flag."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body."}, status=400)

        if "auto_stop_on_stream" in data:
            self.config.obs.auto_stop_on_stream = bool(data["auto_stop_on_stream"])
        if "auto_stop_on_record" in data:
            self.config.obs.auto_stop_on_record = bool(data["auto_stop_on_record"])
        if "scheduler_enabled" in data and self.scheduler is not None:
            self.scheduler._config.enabled = bool(data["scheduler_enabled"])
            self.config.scheduler.enabled = self.scheduler._config.enabled

        from obs_captioner.config import save_config
        save_config(self.config)
        return web.json_response({
            "status": "success",
            "message": "Scheduler config updated.",
            **self._scheduler_status_payload(),
        })

    async def _handle_control_restart(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        logger.info("Received request to restart application.")
        if self.on_restart_requested:
            # Notify connected clients via WebSocket
            try:
                await self.broadcast_control({
                    "type": "server_restarting",
                    "instance_id": self.instance_id,
                    "message": "Application is restarting now...",
                })
            except Exception:
                pass

            asyncio.get_event_loop().call_later(0.2, self.on_restart_requested)
            return web.json_response({
                "status": "restarting",
                "instance_id": self.instance_id,
                "message": "Application is restarting...",
            })
        return web.json_response({"error": "Restart handler not configured."}, status=500)

    async def _handle_control_shutdown(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        logger.info("Received request to shut down application.")
        if self.on_shutdown_requested:
            asyncio.get_event_loop().call_later(0.2, self.on_shutdown_requested)
            return web.json_response({"status": "shutting_down", "message": "Application is shutting down..."})
        return web.json_response({"error": "Shutdown handler not configured."}, status=500)

    async def _handle_control_reopen_screen(self, request: web.Request) -> web.Response:
        """1-Click Emergency Trigger: Restores the screen projector and starts live captions."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        try:
            mon_idx = int(request.query.get("monitor", data.get("monitor_index", self.config.obs.projector_monitor_index or 1)))
        except (ValueError, TypeError):
            mon_idx = 1
        mix_type = sanitize_text(request.query.get("mix_type", data.get("mix_type", self.config.obs.projector_type or "preview")))

        # 1. Start / Resume live captioning
        if self.on_start_requested:
            self.on_start_requested()

        # 2. Trigger OBS Projector Open
        projector_ok = False
        if self.obs_client:
            projector_ok = await self.obs_client.open_projector(mix_type=mix_type, monitor_index=mon_idx)

        return web.json_response({
            "status": "success",
            "action": "reopen_screen",
            "message": f"Screen Projector ({mix_type} on Monitor {mon_idx}) triggered and Live Captions active.",
            "monitor_index": mon_idx,
            "projector_opened": projector_ok,
            "captions_active": True,
        })

    async def _handle_open_projector(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if not self.obs_client:
            return web.json_response({"error": "OBS WebSocket client not initialized"}, status=503)

        try:
            data = await request.json()
        except Exception:
            data = {}

        mix_type = sanitize_text(data.get("mix_type", self.config.obs.projector_type or "preview"))
        try:
            monitor_index = int(data.get("monitor_index", self.config.obs.projector_monitor_index or 1))
        except (ValueError, TypeError):
            monitor_index = 1
        source_name = sanitize_text(data.get("source_name", self.config.obs.projector_source_name or ""))

        success = await self.obs_client.open_projector(
            mix_type=mix_type,
            monitor_index=monitor_index,
            source_name=source_name if source_name else None,
        )

        if success:
            return web.json_response({
                "status": "success",
                "message": f"Projector ({mix_type}) opened on monitor {monitor_index}."
            })
        else:
            return web.json_response({
                "status": "error",
                "message": "Failed to open projector. Check that OBS Studio is running and WebSocket is connected."
            }, status=500)

    async def _handle_get_monitors(self, request: web.Request) -> web.Response:
        if not self.obs_client:
            return web.json_response({"monitors": []})
        monitors = await self.obs_client.get_monitors()
        return web.json_response({"monitors": monitors})

    async def _handle_get_scenes(self, request: web.Request) -> web.Response:
        if not self.obs_client:
            return web.json_response({"connected": False, "current_scene": "", "scenes": []})
        scene_info = await self.obs_client.get_scene_list()
        return web.json_response(scene_info)

    async def _handle_get_history(self, request: web.Request) -> web.Response:
        search = sanitize_text(request.query.get("search", ""), max_len=100)
        try:
            limit = min(500, max(1, int(request.query.get("limit", 100))))
        except ValueError:
            limit = 100
        entries = self.history.get_history(limit=limit, search=search)
        return web.json_response({"history": entries})

    async def _handle_clear_history(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        self.history.clear()
        return web.json_response({"status": "success", "message": "History cleared."})

    async def _handle_export_transcript(self, request: web.Request) -> web.Response:
        fmt = sanitize_text(request.query.get("format", "srt").lower(), max_len=5)
        raw_filename = f"captions_{int(time.time())}.{fmt}"
        safe_filename = sanitize_filename(raw_filename)

        if fmt == "vtt":
            content = self.history.export_vtt()
            content_type = "text/vtt"
        elif fmt == "txt":
            content = self.history.export_txt()
            content_type = "text/plain"
        else:
            content = self.history.export_srt()
            content_type = "text/plain"

        headers = {
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
        }
        return web.Response(text=content, content_type=content_type, headers=headers)

    async def _handle_filter_state(self, request: web.Request) -> web.Response:
        censor = self._make_filter()
        return web.json_response(censor.get_filter_state())

    async def _handle_filter_test(self, request: web.Request) -> web.Response:
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)
        try:
            data = await request.json()
            test_text = sanitize_text(data.get("text", ""))
            censor = self._make_filter()
            filtered, was_censored = censor.filter_text(test_text)
            return web.json_response({
                "original": test_text,
                "filtered": filtered,
                "was_censored": was_censored,
                "mode": self.config.censor.mode,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_add_blacklist(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            term = data.get("term", "")
            valid, msg = validate_censor_term(term)
            if not valid:
                return web.json_response({"error": msg}, status=400)
            
            censor = self._make_filter()
            censor.add_blacklist_term(msg)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_remove_blacklist(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            term = data.get("term", "")
            censor = self._make_filter()
            censor.remove_blacklist_term(term)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_add_whitelist(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            term = data.get("term", "")
            valid, msg = validate_censor_term(term)
            if not valid:
                return web.json_response({"error": msg}, status=400)
            
            censor = self._make_filter()
            censor.add_whitelist_term(msg)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_remove_whitelist(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            term = data.get("term", "")
            censor = self._make_filter()
            censor.remove_whitelist_term(term)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_set_replacement(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            original = data.get("original", "")
            replacement = data.get("replacement", "")
            v1, orig_clean = validate_censor_term(original)
            v2, rep_clean = validate_censor_term(replacement)
            if not v1 or not v2:
                return web.json_response({"error": "Invalid replacement term."}, status=400)

            censor = self._make_filter()
            censor.set_replacement(orig_clean, rep_clean)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_remove_replacement(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            original = data.get("original", "")
            censor = self._make_filter()
            censor.remove_replacement(original)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_get_vocabulary(self, request: web.Request) -> web.Response:
        vocab = VocabularyReplacer(self.config.vocabulary)
        return web.json_response({
            "enabled": self.config.vocabulary.enabled,
            "terms": vocab.get_terms(),
            "count": len(self.config.vocabulary.terms),
        })

    async def _handle_set_vocabulary(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            original = sanitize_text(data.get("original", "")).strip()
            replacement = sanitize_text(data.get("replacement", "")).strip()
            if not original or not replacement:
                return web.json_response({"error": "Both original and replacement are required."}, status=400)

            vocab = VocabularyReplacer(self.config.vocabulary)
            vocab.add_term(original, replacement)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "terms": vocab.get_terms()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_remove_vocabulary(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            original = sanitize_text(data.get("original", "")).strip()
            vocab = VocabularyReplacer(self.config.vocabulary)
            vocab.remove_term(original)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "terms": vocab.get_terms()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_test_vocabulary(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            sample_text = data.get("text", "")
            vocab = VocabularyReplacer(self.config.vocabulary)
            modified, was_changed = vocab.replace(sample_text)
            return web.json_response({
                "original": sample_text,
                "modified": modified,
                "was_modified": was_changed,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_bulk_vocabulary(self, request: web.Request) -> web.Response:
        """Bulk import custom vocabulary terms from CSV, TSV, or dictionary."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            csv_data = data.get("csv_data", "")
            terms_dict = data.get("terms", {})
            replace_all = bool(data.get("replace_all", False))

            vocab = VocabularyReplacer(self.config.vocabulary)
            imported = 0

            if csv_data:
                imported = vocab.import_csv(csv_data, replace_all=replace_all)
            elif terms_dict and isinstance(terms_dict, dict):
                if replace_all:
                    vocab.clear()
                for orig, rep in terms_dict.items():
                    if vocab.add_term(str(orig), str(rep)):
                        imported += 1

            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)

            return web.json_response({
                "status": "success",
                "imported_count": imported,
                "total_count": len(self.config.vocabulary.terms),
                "terms": vocab.get_terms(),
            })
        except Exception as e:
            logger.error(f"Bulk vocabulary import error: {e}")
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_export_vocabulary(self, request: web.Request) -> web.Response:
        """Export all custom glossary terms as CSV file download."""
        vocab = VocabularyReplacer(self.config.vocabulary)
        csv_text = vocab.export_csv()
        filename = f"voxstream_glossary_{int(time.time())}.csv"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        return web.Response(text=csv_text, content_type="text/csv", headers=headers)

    async def _handle_clear_vocabulary(self, request: web.Request) -> web.Response:
        """Clear all custom vocabulary terms."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        vocab = VocabularyReplacer(self.config.vocabulary)
        vocab.clear()
        save_config(self.config)
        if self.on_config_updated:
            self.on_config_updated(self.config)
        return web.json_response({"status": "success", "message": "Glossary cleared.", "terms": {}})

    async def _handle_caption_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)
        lang = sanitize_text(request.query.get("lang", "en")).lower().strip() or "en"
        role = sanitize_text(request.query.get("role") or request.query.get("type") or "").lower().strip()
        referer = request.headers.get("Referer", "").lower()
        if not role:
            if "/ws/bible" in request.path or "/bible" in referer:
                role = "bible"
            elif "/ws/stage" in request.path or "/display" in referer or "/stage" in referer:
                role = "display"
            else:
                role = "caption"

        self.caption_sockets[ws] = lang
        self.socket_roles[ws] = role
        try:
            try:
                snapshot_lines = list(self._recent_finals)
                if not snapshot_lines and self.history:
                    # Fallback to persistent transcript history so display screens always populate immediately
                    entries = self.history.get_history(limit=10)
                    for e in reversed(entries):
                        txt = e.get("text", "").strip()
                        if txt:
                            snapshot_lines.append({"text": txt, "is_final": True, "timestamp": e.get("timestamp", 0)})

                if lang in ("en", "original", "none", ""):
                    await ws.send_str(json.dumps({"type": "snapshot", "lines": snapshot_lines}))
                else:
                    translated_lines = []
                    for line in snapshot_lines:
                        t_text = await self.translator.translate_to_language(line.get("text", ""), target_lang=lang)
                        translated_lines.append({**line, "text": t_text})
                    await ws.send_str(json.dumps({"type": "snapshot", "lines": translated_lines}))
            except Exception:
                pass
            async for _ in ws:
                pass
        finally:
            self.caption_sockets.pop(ws, None)
            self.socket_roles.pop(ws, None)
        return ws

    async def _handle_control_ws(self, request: web.Request) -> web.WebSocketResponse:
        # Control actions (start/stop/restart/shutdown) require the same auth
        # as their HTTP counterparts (?api_key= works for WebSocket URLs).
        authorized = self._check_auth(request)
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)
        self.control_sockets.add(ws)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        cmd = json.loads(msg.data)
                        action = cmd.get("action")
                        if action and not authorized:
                            await ws.send_str(json.dumps({"type": "error", "message": "Unauthorized"}))
                            continue
                        if action == "start" and self.on_start_requested:
                            self.on_start_requested()
                        elif action == "stop" and self.on_stop_requested:
                            self.on_stop_requested()
                        elif action == "restart" and self.on_restart_requested:
                            self.on_restart_requested()
                        elif action == "shutdown" and self.on_shutdown_requested:
                            self.on_shutdown_requested()
                    except Exception:
                        pass
        finally:
            self.control_sockets.discard(ws)
        return ws

    async def _handle_audio_stream_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket intake for raw 16kHz linear PCM audio bytes (e.g. streamed directly from OBS)."""
        if not self._check_auth(request):
            ws = web.WebSocketResponse(heartbeat=25.0)
            await ws.prepare(request)
            await ws.close(code=web.WSCloseCode.POLICY_VIOLATION, message=b"Unauthorized")
            return ws
        ws = web.WebSocketResponse(max_msg_size=1024 * 1024)
        await ws.prepare(request)
        logger.info("Direct OBS Audio stream connected via WebSocket.")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    if self.audio_capture:
                        self.audio_capture.inject_audio_chunk(msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    logger.debug(f"Audio stream ws closed with exception {ws.exception()}")
        finally:
            logger.info("Direct OBS Audio stream disconnected.")
        return ws

    async def _handle_audio_chunk_post(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        data = await request.read()
        if self.audio_capture and data:
            self.audio_capture.inject_audio_chunk(data)
        return web.Response(text="OK")

    def _record_snapshot(self, payload: dict):
        """Track recent finals for replay to newly connected clients."""
        if not payload.get("is_final"):
            return
        text = (payload.get("text") or "").strip()
        if text:
            if payload.get("replace_last") and self._recent_finals:
                self._recent_finals[-1] = payload
            else:
                self._recent_finals.append(payload)
                if len(self._recent_finals) > self._max_snapshot_lines:
                    self._recent_finals.pop(0)

    async def broadcast_caption(self, payload: dict):
        self._record_snapshot(payload)
        if not self.caption_sockets:
            return
        raw_text = (payload.get("text") or "").strip()
        is_final = bool(payload.get("is_final"))

        lang_payloads = {}
        stale = []
        for ws, lang in list(self.caption_sockets.items()):
            try:
                if lang in ("en", "original", "none", "") or not raw_text:
                    if "en" not in lang_payloads:
                        lang_payloads["en"] = json.dumps(payload)
                    await ws.send_str(lang_payloads["en"])
                else:
                    if lang not in lang_payloads:
                        if is_final:
                            # Only execute network translation on finalized sentences
                            t_text = await self.translator.translate_to_language(raw_text, target_lang=lang)
                        else:
                            # Deliver interim text with zero blocking network delay
                            t_text = raw_text
                        custom_payload = {**payload, "text": t_text, "original_text": raw_text}
                        lang_payloads[lang] = json.dumps(custom_payload)
                    await ws.send_str(lang_payloads[lang])
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.caption_sockets.pop(ws, None)

        if is_final and self.control_sockets and self.history:
            try:
                stats = self.history.get_stats()
                asyncio.create_task(self.broadcast_control({"type": "stats_update", **stats}))
            except Exception:
                pass

    async def broadcast_control(self, payload: dict):
        """Broadcast telemetry/config events to dashboard control websockets."""
        if not self.control_sockets:
            return
        message = json.dumps(payload)
        stale = []
        for ws in list(self.control_sockets):
            try:
                await ws.send_str(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.control_sockets.discard(ws)

    async def broadcast_vu_level(self, db_level: float):
        """Broadcast audio VU level to dashboard clients."""
        await self.broadcast_control({"type": "vu_meter", "level_db": db_level})

    async def start(self) -> bool:
        """Start the web server."""
        if not self.config.overlay.enabled:
            return False

        host = self.config.overlay.host
        initial_port = self.config.overlay.port or 8765

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        # Attempt to bind to requested port, auto-fallback if port is occupied (e.g. by REAPER)
        for offset in range(10):
            current_port = initial_port + offset
            try:
                self.site = web.TCPSite(self.runner, host, current_port, shutdown_timeout=1.0)
                await self.site.start()
                self.config.overlay.port = current_port
                
                from ..hardware import get_local_ip
                lan_ip = get_local_ip()
                logger.info(f"✅ Local Dashboard:       http://127.0.0.1:{current_port}/dashboard")
                logger.info(f"✅ OBS Browser Source:     http://127.0.0.1:{current_port}/")
                if lan_ip and lan_ip != "127.0.0.1":
                    logger.info(f"🌐 Network Dashboard:     http://{lan_ip}:{current_port}/dashboard")
                    logger.info(f"📱 Mobile / Stage View:   http://{lan_ip}:{current_port}/display")
                if getattr(self.config, "update", None) and self.config.update.auto_check and self.updater:
                    self._updater_task = asyncio.create_task(self._auto_check_updates_loop())
                return True
            except OSError as e:
                logger.debug(f"Port {current_port} busy ({e}), trying next port...")
                continue
            except Exception as e:
                logger.error(f"Failed to start overlay server: {e}")
                return False

        logger.error(f"Failed to bind web server to any port between {initial_port} and {initial_port + 9}")
        return False

    async def stop(self):
        """Stop the web server cleanly with fast timeout."""
        if self._updater_task:
            self._updater_task.cancel()
            self._updater_task = None

        all_sockets = list(set(self.caption_sockets.keys()) | self.control_sockets)
        self.caption_sockets.clear()
        self.socket_roles.clear()
        self.control_sockets.clear()

        for ws in all_sockets:
            try:
                await asyncio.wait_for(ws.close(), timeout=0.3)
            except Exception:
                pass

        if self.runner:
            try:
                await asyncio.wait_for(self.runner.cleanup(), timeout=1.5)
            except Exception as e:
                logger.debug(f"Runner cleanup error or timeout: {e}")
            self.runner = None
        logger.info("Overlay and API server stopped.")


    async def _handle_get_models_status(self, request: web.Request) -> web.Response:
        """Return catalog of all offline speech recognition models and their download status."""
        summary = self.model_downloader.get_summary()
        return web.json_response(summary)

    async def _handle_download_model(self, request: web.Request) -> web.Response:
        """Trigger background pre-download of a specific model or all models."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        model_id = sanitize_text(data.get("model_id", "all")).strip()
        if self.model_downloader.is_downloading:
            return web.json_response({"status": "already_downloading", "message": "A model download is already in progress."}, status=409)

        async def _broadcast_cb(evt: dict):
            await self.broadcast_control(evt)

        def _sync_cb(evt: dict):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(_broadcast_cb(evt), loop)
            except Exception:
                pass

        asyncio.create_task(self.model_downloader.download_model(model_id, _sync_cb))

        return web.json_response({
            "status": "started",
            "model_id": model_id,
            "message": f"Pre-download for model '{model_id}' started in background.",
        })

    async def _handle_cancel_download_model(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        self.model_downloader.cancel_download()
        return web.json_response({"status": "canceled", "message": "Model download cancellation requested."})


    async def _handle_favicon(self, request: web.Request) -> web.FileResponse:
        fav_file = Path(__file__).parent / "static" / "favicon.ico"
        return web.FileResponse(fav_file, headers={"Content-Type": "image/x-icon", "Cache-Control": "public, max-age=86400"})

    async def _handle_apple_touch_icon(self, request: web.Request) -> web.FileResponse:
        icon_file = Path(__file__).parent / "static" / "apple-touch-icon.png"
        return web.FileResponse(icon_file, headers={"Content-Type": "image/png", "Cache-Control": "public, max-age=86400"})


    async def _handle_delete_model(self, request: web.Request) -> web.Response:
        """Delete one or all offline speech recognition models from disk cache."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        model_id = sanitize_text(data.get("model_id", "")).strip()
        if not model_id:
            return web.json_response({"error": "Missing 'model_id' parameter."}, status=400)

        ok, msg, freed_mb = self.model_downloader.delete_model(model_id)
        if ok:
            await self.broadcast_control({
                "type": "model_cache_updated",
                "model_id": model_id,
                "freed_mb": freed_mb,
                "message": msg,
            })
            return web.json_response({
                "status": "success",
                "message": msg,
                "freed_mb": freed_mb,
                "model_id": model_id,
            })
        else:
            return web.json_response({"status": "error", "message": msg}, status=400)


    async def _handle_bible_versions(self, request: web.Request) -> web.Response:
        """Return all available offline Bible translations."""
        versions = self.bible_engine.get_available_versions()
        return web.json_response({"versions": versions})

    async def _handle_bible_lookup(self, request: web.Request) -> web.Response:
        """Lookup a Bible verse or citation string offline."""
        citation = request.query.get("citation", "").strip()
        version = request.query.get("version", getattr(self.config.bible, "default_version", "bsb")).strip()
        
        if not citation:
            return web.json_response({"error": "Missing 'citation' parameter."}, status=400)
            
        res = self.bible_engine.parse_and_lookup_first(citation, version=version)
        if not res:
            return web.json_response({"error": f"Scripture citation '{citation}' not found."}, status=404)
            
        return web.json_response(res.to_dict())

    async def _handle_bible_display(self, request: web.Request) -> web.Response:
        """Manually trigger display of a scripture passage across OBS overlays and stage monitors."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            data = await request.json()
        except Exception:
            data = {}
            
        citation = sanitize_text(data.get("citation", "")).strip()
        version = sanitize_text(data.get("version", getattr(self.config.bible, "default_version", "bsb"))).strip()
        duration = float(data.get("duration", getattr(self.config.bible, "display_duration_seconds", 14.0)))
        
        if not citation:
            return web.json_response({"error": "Missing 'citation' parameter."}, status=400)
            
        res = self.bible_engine.parse_and_lookup_first(citation, version=version)
        if not res:
            return web.json_response({"error": f"Scripture citation '{citation}' not found."}, status=404)
            
        await self.broadcast_scripture(res, duration_seconds=duration)
        return web.json_response({
            "status": "success",
            "message": f"Displayed {res.citation} [{res.version}] on scripture overlay and stage monitors.",
            "scripture": res.to_dict(),
        })

    async def _handle_bible_dismiss(self, request: web.Request) -> web.Response:
        """Dismiss any active scripture popup on stream and stage screens."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
            
        await self.dismiss_scripture()
        return web.json_response({"status": "success", "message": "Scripture card dismissed."})

    async def trigger_scripture_lookup(self, text: str):
        """Auto-lookup scripture citation from finalized transcript and broadcast if found."""
        if not getattr(self.config, "bible", None) or not self.config.bible.enabled:
            return
            
        version = getattr(self.config.bible, "default_version", "bsb")
        res = self.bible_engine.parse_and_lookup_first(text, version=version)
        if res:
            logger.info(f"📖 [BIBLE AUTO-LOOKUP] Found {res.citation} [{res.version}]: {res.text[:60]}...")
            await self.broadcast_scripture(res, duration_seconds=getattr(self.config.bible, "display_duration_seconds", 14.0))

    async def broadcast_scripture(self, res: ScriptureLookupResult, duration_seconds: float = 14.0):
        """Broadcast scripture passage payload to all connected caption overlays and control dashboards."""
        show_on_stream = bool(getattr(self.config.bible, "show_on_stream_overlay", False)) if getattr(self.config, "bible", None) else False
        show_on_stage = bool(getattr(self.config.bible, "show_on_stage_display", True)) if getattr(self.config, "bible", None) else True

        msg = {
            "type": "scripture_verse",
            "citation": res.citation,
            "book": res.book,
            "chapter": res.chapter,
            "verse_start": res.verse_start,
            "verse_end": res.verse_end,
            "text": res.text,
            "version": res.version,
            "version_name": res.version_name,
            "duration_seconds": duration_seconds,
            "timestamp": time.time(),
            "show_on_stream_overlay": show_on_stream,
            "show_on_stage_display": show_on_stage,
        }
        
        # Broadcast to overlay WebSockets (/ws, /ws/bible, /ws/stage)
        dead_caps = []
        for ws in list(self.caption_sockets.keys()):
            role = self.socket_roles.get(ws, "caption")
            # Dedicated scripture overlay ALWAYS receives scripture
            if role == "bible":
                pass
            # Stage prompter receives scripture only if stage display is enabled
            elif role == "display" and not show_on_stage:
                continue
            # Main caption overlay (OBS overlay) receives scripture ONLY if explicitly enabled
            elif role == "caption" and not show_on_stream:
                continue

            try:
                await ws.send_json(msg)
            except Exception:
                dead_caps.append(ws)
        for ws in dead_caps:
            self.caption_sockets.pop(ws, None)
            self.socket_roles.pop(ws, None)
            
        # Broadcast to control dashboards & docks (/api/control/ws)
        dead_ctrls = []
        for ws in self.control_sockets:
            try:
                await ws.send_json(msg)
            except Exception:
                dead_ctrls.append(ws)
        for ws in dead_ctrls:
            self.control_sockets.discard(ws)

    async def dismiss_scripture(self):
        """Dismiss active scripture popup."""
        msg = {"type": "scripture_dismiss", "timestamp": time.time()}
        for ws in list(self.caption_sockets.keys()):
            try:
                await ws.send_json(msg)
            except Exception:
                pass
        for ws in list(self.control_sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def _handle_updater_status(self, request: web.Request) -> web.Response:
        """Return current version and cached update status."""
        if not self.updater:
            return web.json_response({"error": "Updater not configured", "update_available": False})
        force = request.query.get("force", "false").lower() in ("true", "1", "yes")
        status = await self.updater.check_update(force=force)
        return web.json_response(status)

    async def _handle_updater_check(self, request: web.Request) -> web.Response:
        """Force immediate GitHub update check."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if not self.updater:
            return web.json_response({"error": "Updater not configured", "update_available": False})
        status = await self.updater.check_update(force=True)
        if status.get("update_available"):
            await self.broadcast_control({"type": "update_available", "status": status})
        return web.json_response(status)

    async def _handle_updater_apply(self, request: web.Request) -> web.Response:
        """Download latest updates, sync dependencies, and restart VoxStream."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if not self.updater:
            return web.json_response({"error": "Updater not configured"}, status=503)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

        def progress_cb(msg: str):
            logger.info(f"[UPDATER] {msg}")
            try:
                loop.call_soon_threadsafe(
                    lambda m=msg: asyncio.create_task(
                        self.broadcast_control({"type": "updater_progress", "message": m})
                    )
                )
            except Exception as ex:
                logger.debug(f"Failed to dispatch progress broadcast: {ex}")

        try:
            success, message = await self.updater.apply_update(progress_cb=progress_cb)
            if success:
                return web.json_response({"status": "restarting", "message": message})
            else:
                return web.json_response({"status": "error", "message": message}, status=500)
        except Exception as e:
            logger.error(f"Error executing update apply: {e}", exc_info=True)
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def _auto_check_updates_loop(self):
        """Background periodic update checker."""
        try:
            # Wait 10 seconds after server startup
            await asyncio.sleep(10.0)
            while True:
                try:
                    if self.updater and getattr(self.config, "update", None) and self.config.update.auto_check:
                        status = await self.updater.check_update(force=False)
                        if status.get("update_available"):
                            logger.info(
                                f"✨ [UPDATER] New VoxStream version available: {status.get('latest_commit')} "
                                f"('{status.get('commit_message')}')"
                            )
                            await self.broadcast_control({"type": "update_available", "status": status})
                except Exception as e:
                    logger.debug(f"Background update check failed: {e}")
                hours = getattr(getattr(self.config, "update", None), "check_interval_hours", 6) or 6
                await asyncio.sleep(max(1, hours) * 3600)
        except asyncio.CancelledError:
            pass


# Convenience alias
WebServer = WebOverlayServer

