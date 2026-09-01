"""Hardened Web and WebSocket Server for OBS Browser Source, Dashboard, and REST API."""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional, Set

from aiohttp import web

from ..config import AppConfig, save_config
from ..audio_capture import list_audio_devices
from ..censor import ContentFilter
from ..history import TranscriptHistory
from ..themes import THEME_PRESETS, get_all_presets
from ..translator import SUPPORTED_LANGUAGES, SubtitleTranslator
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
        obs_client: Optional[any] = None,
        audio_capture: Optional[any] = None,
    ):
        self.config = config
        self.history = history or TranscriptHistory()
        self.on_config_updated = on_config_updated
        self.on_start_requested = on_start_requested
        self.on_stop_requested = on_stop_requested
        self.on_restart_requested = on_restart_requested
        self.on_shutdown_requested = on_shutdown_requested
        self.get_app_status = get_app_status
        self.obs_client = obs_client
        self.audio_capture = audio_capture
        self.translator = SubtitleTranslator(self.config.translation)
        self.server_start_time = time.time()
        self.instance_id = str(uuid.uuid4())[:8]
        # Generous: a dashboard (4s poll) + stage display (5s poll) + restart
        # polling from the same IP must not starve each other into 429s.
        self.rate_limiter = SimpleRateLimiter(max_requests=300, window_seconds=60.0)
        self.model_downloader = ModelDownloadManager()
        self.bible_engine = BibleEngine()
        # Rolling snapshot of recent final caption payloads, replayed to newly
        # connected /ws clients so refreshed views aren't blank until the next utterance.
        self._recent_finals: list = []
        self._max_snapshot_lines = 10

        self.app = web.Application()
        self.runner: web.AppRunner = None
        self.site: web.TCPSite = None
        self.caption_sockets: Dict[web.WebSocketResponse, str] = {}
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
        self.app.router.add_get("/sw.js", self._handle_service_worker)
        
        # WebSockets
        self.app.router.add_get("/ws", self._handle_caption_ws)
        self.app.router.add_get("/api/control/ws", self._handle_control_ws)
        self.app.router.add_get("/api/audio/stream", self._handle_audio_stream_ws)
        self.app.router.add_post("/api/audio/chunk", self._handle_audio_chunk_post)

        # REST API Routes
        self.app.router.add_get("/api/status", self._handle_get_status)
        self.app.router.add_get("/api/config", self._handle_get_config)
        self.app.router.add_post("/api/config", self._handle_post_config)
        self.app.router.add_get("/api/devices", self._handle_get_devices)
        self.app.router.add_get("/api/presets", self._handle_get_presets)
        self.app.router.add_post("/api/presets/apply", self._handle_apply_preset)
        self.app.router.add_post("/api/presets/save", self._handle_save_preset)
        self.app.router.add_post("/api/presets/delete", self._handle_delete_preset)
        self.app.router.add_get("/api/languages", self._handle_get_languages)
        
        # Control Endpoints
        self.app.router.add_post("/api/control/start", self._handle_control_start)
        self.app.router.add_post("/api/control/stop", self._handle_control_stop)
        self.app.router.add_post("/api/control/toggle", self._handle_control_toggle)
        self.app.router.add_post("/api/control/panic", self._handle_control_panic)
        self.app.router.add_post("/api/control/restart", self._handle_control_restart)
        self.app.router.add_post("/api/control/shutdown", self._handle_control_shutdown)
        self.app.router.add_post("/api/control/reopen-screen", self._handle_control_reopen_screen)
        self.app.router.add_post("/api/control/restore-display", self._handle_control_reopen_screen)
        
        # OBS Projector & Display Automation
        self.app.router.add_post("/api/obs/projector/open", self._handle_open_projector)
        self.app.router.add_get("/api/obs/monitors", self._handle_get_monitors)
        
        # Transcript, Chapters, Translation & Export
        self.app.router.add_get("/api/transcript/history", self._handle_get_history)
        self.app.router.add_get("/api/transcript/chapters", self._handle_get_chapters)
        self.app.router.add_get("/api/transcript/export", self._handle_export_transcript)
        self.app.router.add_post("/api/transcript/clear", self._handle_clear_history)
        self.app.router.add_get("/api/translate", self._handle_translate_text)
        
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

        # Static Assets
        self.app.router.add_static("/static/", path=str(static_dir), name="static")

    async def _handle_index(self, request: web.Request) -> web.FileResponse:
        index_file = Path(__file__).parent / "static" / "index.html"
        return web.FileResponse(index_file)

    async def _handle_bible_page(self, request: web.Request) -> web.FileResponse:
        bible_file = Path(__file__).parent / "static" / "bible.html"
        return web.FileResponse(bible_file)

    async def _handle_dashboard(self, request: web.Request) -> web.FileResponse:
        dash_file = Path(__file__).parent / "static" / "dashboard.html"
        return web.FileResponse(dash_file)

    async def _handle_display(self, request: web.Request) -> web.FileResponse:
        display_file = Path(__file__).parent / "static" / "display.html"
        return web.FileResponse(display_file)

    async def _handle_manifest(self, request: web.Request) -> web.FileResponse:
        manifest_file = Path(__file__).parent / "static" / "manifest.json"
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
        }
        if self.get_app_status:
            try:
                status_info.update(self.get_app_status())
            except Exception:
                logger.warning("get_app_status hook failed", exc_info=True)
        # Never report "running" unless the app-status hook confirmed it
        status_info.setdefault("is_running", False)
        return web.json_response(status_info)

    # Sentinel used in place of secret values in GET /api/config responses.
    # POSTing the sentinel back leaves the stored secret unchanged.
    SECRET_SENTINEL = "•••"
    SECRET_FIELDS = {
        "gemini_live": ("api_key",),
        "bandwidth": ("api_key",),
        "twitch": ("oauth_token",),
        "obs": ("password",),
        "api": ("api_key",),
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
                    for sec_k, sec_v in val.items():
                        if not hasattr(section, sec_k):
                            continue
                        # A masked secret round-tripped from GET /api/config
                        # means "keep the existing value"
                        if sec_v == self.SECRET_SENTINEL and sec_k in self.SECRET_FIELDS.get(key, ()):
                            continue
                        setattr(section, sec_k, sec_v)

            save_config(self.config)

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
        chapters = self.history.generate_chapters(min_interval_seconds=min_interval)
        formatted = self.history.export_youtube_chapters()
        return web.json_response({
            "chapters": chapters,
            "formatted": formatted,
            "count": len(chapters)
        })

    async def _handle_translate_text(self, request: web.Request) -> web.Response:
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)
        text = sanitize_text(request.query.get("text", "")).strip()
        target = sanitize_text(request.query.get("target", "es")).strip().lower()
        source = sanitize_text(request.query.get("source", "auto")).strip().lower()
        if not text:
            return web.json_response({"original": "", "translated": "", "target": target})
        translated = await self.translator.translate_to_language(text, target_lang=target, source_lang=source)
        return web.json_response({
            "original": text,
            "translated": translated or text,
            "target": target,
            "source": source
        })

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

            asyncio.get_event_loop().call_later(0.3, self.on_restart_requested)
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
            asyncio.get_event_loop().call_later(0.5, self.on_shutdown_requested)
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
        self.caption_sockets[ws] = lang
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
            ws = web.WebSocketResponse()
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
        """HTTP POST intake for raw PCM audio chunks."""
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
                self.site = web.TCPSite(self.runner, host, current_port)
                await self.site.start()
                self.config.overlay.port = current_port
                
                if offset > 0:
                    logger.warning(f"⚠️ Port {initial_port} was already in use by another app (e.g. REAPER). Auto-switched to port {current_port}!")
                
                logger.info(f"✅ Web Control Dashboard live at: http://{host}:{current_port}/dashboard")
                logger.info(f"✅ Overlay URL: http://{host}:{current_port}/")
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
        """Stop the web server."""
        for ws in list(set(self.caption_sockets.keys()) | self.control_sockets):
            try:
                await ws.close()
            except Exception:
                pass
        self.caption_sockets.clear()
        self.control_sockets.clear()

        if self.runner:
            await self.runner.cleanup()
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
            "message": f"Displayed {res.citation} [{res.version}] on stream and stage monitors.",
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
        }
        
        # Broadcast to stream overlay WebSockets (/ws)
        dead_caps = []
        for ws in self.caption_sockets:
            try:
                await ws.send_json(msg)
            except Exception:
                dead_caps.append(ws)
        for ws in dead_caps:
            self.caption_sockets.pop(ws, None)
            
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
