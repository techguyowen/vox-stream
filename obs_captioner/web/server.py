"""Hardened Web and WebSocket Server for OBS Browser Source, Dashboard, and REST API."""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional, Set

from aiohttp import web

from ..config import AppConfig, save_config
from ..audio_capture import list_audio_devices
from ..censor import ContentFilter
from ..history import TranscriptHistory
from ..themes import THEME_PRESETS, get_all_presets
from ..translator import SUPPORTED_LANGUAGES
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
    """Async web server serving overlay, settings dashboard, and hardened REST/WebSocket API."""

    def __init__(
        self,
        config: AppConfig,
        history: Optional[TranscriptHistory] = None,
        on_config_updated: Optional[Callable[[AppConfig], None]] = None,
        on_start_requested: Optional[Callable[[], None]] = None,
        on_stop_requested: Optional[Callable[[], None]] = None,
        get_app_status: Optional[Callable[[], dict]] = None,
        obs_client: Optional[any] = None,
    ):
        self.config = config
        self.history = history or TranscriptHistory()
        self.on_config_updated = on_config_updated
        self.on_start_requested = on_start_requested
        self.on_stop_requested = on_stop_requested
        self.get_app_status = get_app_status
        self.obs_client = obs_client
        self.rate_limiter = SimpleRateLimiter(max_requests=120, window_seconds=60.0)

        self.app = web.Application()
        self.runner: web.AppRunner = None
        self.site: web.TCPSite = None
        self.caption_sockets: Set[web.WebSocketResponse] = set()
        self.control_sockets: Set[web.WebSocketResponse] = set()

        self._setup_routes()

    def _check_auth(self, request: web.Request) -> bool:
        auth_func = require_api_auth(self.config.api.api_key)
        return auth_func(request)

    def _setup_routes(self):
        static_dir = Path(__file__).parent / "static"
        
        # HTML Pages with comprehensive aliases
        for path in ("/", "/index", "/index.html", "/overlay", "/overlay.html"):
            self.app.router.add_get(path, self._handle_index)
        
        for path in ("/dashboard", "/dashboard/", "/dashboard.html", "/dock", "/dock/", "/settings", "/settings/", "/control"):
            self.app.router.add_get(path, self._handle_dashboard)
        
        # WebSockets
        self.app.router.add_get("/ws", self._handle_caption_ws)
        self.app.router.add_get("/api/control/ws", self._handle_control_ws)

        # REST API Routes
        self.app.router.add_get("/api/status", self._handle_get_status)
        self.app.router.add_get("/api/config", self._handle_get_config)
        self.app.router.add_post("/api/config", self._handle_post_config)
        self.app.router.add_get("/api/devices", self._handle_get_devices)
        self.app.router.add_get("/api/presets", self._handle_get_presets)
        self.app.router.add_post("/api/presets/apply", self._handle_apply_preset)
        self.app.router.add_get("/api/languages", self._handle_get_languages)
        
        # Control Endpoints
        self.app.router.add_post("/api/control/start", self._handle_control_start)
        self.app.router.add_post("/api/control/stop", self._handle_control_stop)
        self.app.router.add_post("/api/control/reopen-screen", self._handle_control_reopen_screen)
        self.app.router.add_post("/api/control/restore-display", self._handle_control_reopen_screen)
        
        # OBS Projector & Display Automation
        self.app.router.add_post("/api/obs/projector/open", self._handle_open_projector)
        self.app.router.add_get("/api/obs/monitors", self._handle_get_monitors)
        
        # Transcript & Export
        self.app.router.add_get("/api/transcript/history", self._handle_get_history)
        self.app.router.add_get("/api/transcript/export", self._handle_export_transcript)
        self.app.router.add_post("/api/transcript/clear", self._handle_clear_history)
        
        # Filter Management CRUD
        self.app.router.add_get("/api/filter/state", self._handle_filter_state)
        self.app.router.add_post("/api/filter/test", self._handle_filter_test)
        self.app.router.add_post("/api/filter/blacklist/add", self._handle_add_blacklist)
        self.app.router.add_post("/api/filter/blacklist/remove", self._handle_remove_blacklist)
        self.app.router.add_post("/api/filter/whitelist/add", self._handle_add_whitelist)
        self.app.router.add_post("/api/filter/whitelist/remove", self._handle_remove_whitelist)
        self.app.router.add_post("/api/filter/replacements/set", self._handle_set_replacement)
        self.app.router.add_post("/api/filter/replacements/remove", self._handle_remove_replacement)

        # Static Assets
        self.app.router.add_static("/static/", path=str(static_dir), name="static")

    async def _handle_index(self, request: web.Request) -> web.FileResponse:
        index_file = Path(__file__).parent / "static" / "index.html"
        return web.FileResponse(index_file)

    async def _handle_dashboard(self, request: web.Request) -> web.FileResponse:
        dash_file = Path(__file__).parent / "static" / "dashboard.html"
        return web.FileResponse(dash_file)

    async def _handle_get_status(self, request: web.Request) -> web.Response:
        client_ip = request.remote or "127.0.0.1"
        if not self.rate_limiter.is_allowed(client_ip):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        status_info = {
            "is_running": True,
            "engine": self.config.general.engine,
            "language": self.config.general.language,
            "audio_device": self.config.audio.device_name_filter,
            "total_transcript_lines": len(self.history.entries),
            "theme": self.config.overlay.theme_id,
            "translation_enabled": self.config.translation.enabled,
            "timestamp": time.time(),
        }
        if self.get_app_status:
            try:
                status_info.update(self.get_app_status())
            except Exception:
                pass
        return web.json_response(status_info)

    async def _handle_get_config(self, request: web.Request) -> web.Response:
        return web.json_response(asdict(self.config))

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
                        if hasattr(section, sec_k):
                            setattr(section, sec_k, sec_v)

            save_config(self.config)

            if self.on_config_updated:
                self.on_config_updated(self.config)

            await self.broadcast_control({"type": "config_updated", "config": asdict(self.config)})
            return web.json_response({"status": "success", "message": "Configuration updated and saved."})
        except Exception as e:
            logger.error(f"Error saving config via API: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def _handle_get_devices(self, request: web.Request) -> web.Response:
        devices = list_audio_devices()
        return web.json_response({"devices": devices})

    async def _handle_get_presets(self, request: web.Request) -> web.Response:
        return web.json_response({"presets": get_all_presets()})

    async def _handle_apply_preset(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            theme_id = data.get("theme_id", "")
            if theme_id not in THEME_PRESETS:
                return web.json_response({"error": f"Unknown theme '{theme_id}'"}, status=404)

            self.config.overlay.apply_theme(theme_id)
            save_config(self.config)

            if self.on_config_updated:
                self.on_config_updated(self.config)

            await self.broadcast_control({"type": "config_updated", "config": asdict(self.config)})
            return web.json_response({"status": "success", "theme": asdict(THEME_PRESETS[theme_id])})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_get_languages(self, request: web.Request) -> web.Response:
        return web.json_response({"languages": SUPPORTED_LANGUAGES})

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

    async def _handle_control_reopen_screen(self, request: web.Request) -> web.Response:
        """1-Click Emergency Trigger: Restores the screen projector and starts live captions."""
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        mon_idx = int(request.query.get("monitor", data.get("monitor_index", self.config.obs.projector_monitor_index or 1)))
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
        monitor_index = int(data.get("monitor_index", self.config.obs.projector_monitor_index))
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
        censor = ContentFilter(self.config.censor)
        return web.json_response(censor.get_filter_state())

    async def _handle_filter_test(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            test_text = sanitize_text(data.get("text", ""))
            censor = ContentFilter(self.config.censor)
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
        try:
            data = await request.json()
            term = data.get("term", "")
            valid, msg = validate_censor_term(term)
            if not valid:
                return web.json_response({"error": msg}, status=400)
            
            censor = ContentFilter(self.config.censor)
            censor.add_blacklist_term(msg)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_remove_blacklist(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            term = data.get("term", "")
            censor = ContentFilter(self.config.censor)
            censor.remove_blacklist_term(term)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_add_whitelist(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            term = data.get("term", "")
            valid, msg = validate_censor_term(term)
            if not valid:
                return web.json_response({"error": msg}, status=400)
            
            censor = ContentFilter(self.config.censor)
            censor.add_whitelist_term(msg)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_remove_whitelist(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            term = data.get("term", "")
            censor = ContentFilter(self.config.censor)
            censor.remove_whitelist_term(term)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_set_replacement(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            original = data.get("original", "")
            replacement = data.get("replacement", "")
            v1, orig_clean = validate_censor_term(original)
            v2, rep_clean = validate_censor_term(replacement)
            if not v1 or not v2:
                return web.json_response({"error": "Invalid replacement term."}, status=400)

            censor = ContentFilter(self.config.censor)
            censor.set_replacement(orig_clean, rep_clean)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_remove_replacement(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            original = data.get("original", "")
            censor = ContentFilter(self.config.censor)
            censor.remove_replacement(original)
            save_config(self.config)
            if self.on_config_updated:
                self.on_config_updated(self.config)
            return web.json_response({"status": "success", "filter_state": censor.get_filter_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_caption_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.caption_sockets.add(ws)
        try:
            async for _ in ws:
                pass
        finally:
            self.caption_sockets.discard(ws)
        return ws

    async def _handle_control_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.control_sockets.add(ws)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        cmd = json.loads(msg.data)
                        action = cmd.get("action")
                        if action == "start" and self.on_start_requested:
                            self.on_start_requested()
                        elif action == "stop" and self.on_stop_requested:
                            self.on_stop_requested()
                    except Exception:
                        pass
        finally:
            self.control_sockets.discard(ws)
        return ws

    async def broadcast_caption(self, payload: dict):
        """Broadcast live captions to overlay and dashboard."""
        if not self.caption_sockets:
            return
        message = json.dumps(payload)
        stale = []
        for ws in self.caption_sockets:
            try:
                await ws.send_str(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.caption_sockets.discard(ws)

    async def broadcast_control(self, payload: dict):
        """Broadcast telemetry/config events to dashboard control websockets."""
        if not self.control_sockets:
            return
        message = json.dumps(payload)
        stale = []
        for ws in self.control_sockets:
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
        for ws in list(self.caption_sockets | self.control_sockets):
            try:
                await ws.close()
            except Exception:
                pass
        self.caption_sockets.clear()
        self.control_sockets.clear()

        if self.runner:
            await self.runner.cleanup()
        logger.info("Overlay and API server stopped.")
