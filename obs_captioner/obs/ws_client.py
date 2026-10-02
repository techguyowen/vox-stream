"""OBS WebSocket v5 Client with Auto-Start Hooks."""

import asyncio
import datetime
import json
import logging
from typing import Any, Callable, Optional, Union
import websockets

from ..config import OBSConfig

logger = logging.getLogger("obs_captioner.obs")


def _generate_auth_string(password: str, salt: str, challenge: str) -> str:
    import base64
    import hashlib
    secret_hash = hashlib.sha256((password + salt).encode("utf-8")).digest()
    secret_base64 = base64.b64encode(secret_hash).decode("utf-8")
    auth_hash = hashlib.sha256((secret_base64 + challenge).encode("utf-8")).digest()
    return base64.b64encode(auth_hash).decode("utf-8")


class OBSWebSocketClient:
    """Async client for OBS Studio WebSocket v5 protocol with auto-reconnection and 24/7 projector keeper."""

    def __init__(self, config: Union[OBSConfig, Any]):
        self.config: OBSConfig = config.obs if hasattr(config, "obs") else config
        self.ws = None
        self.is_connected = False
        self.last_connect_error = None
        self.last_projector_error = None
        self._message_id = 1
        self._pending_requests = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self.watchdog_verifications_count: int = 0
        self.last_watchdog_check: Optional[str] = None
        self.last_watchdog_success: Optional[bool] = None
        self._closing = False
        self.on_stream_state_changed: Optional[Callable[[bool], None]] = None
        self.on_record_state_changed: Optional[Callable[[bool], None]] = None
        self.on_scene_changed: Optional[Callable[[str], None]] = None
        self.current_scene: Optional[str] = None

    async def connect(self) -> bool:
        """Connect and authenticate with OBS WebSocket v5, starting auto-reconnector and watchdog."""
        if not self.config.enabled:
            return False

        if not self._reconnect_task or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        if not self._watchdog_task or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._projector_watchdog_loop())

        return await self._attempt_connect()

    async def _attempt_connect(self) -> bool:
        """Single connection and authentication attempt."""
        if self.is_connected and self.ws:
            return True

        uri = f"ws://{self.config.host}:{self.config.port}"

        try:
            self.ws = await websockets.connect(uri, ping_interval=10, ping_timeout=5)

            # 1. Wait for OpCode 0 (Hello)
            raw_hello = await asyncio.wait_for(self.ws.recv(), timeout=3.0)
            hello_data = json.loads(raw_hello)
            if hello_data.get("op") != 0:
                self.last_connect_error = f"Expected OpCode 0 (Hello), got: {hello_data}"
                logger.warning(self.last_connect_error)
                await self.ws.close()
                return False

            hello_d = hello_data.get("d", {})
            auth_info = hello_d.get("authentication")

            # 2. Build OpCode 1 (Identify)
            identify_d = {
                "rpcVersion": 1,
                "eventSubscriptions": 1 | 4 | 64,  # General (1) | Scenes (4) | Outputs (64)
            }

            if auth_info:
                salt = auth_info.get("salt", "")
                challenge = auth_info.get("challenge", "")
                password = self.config.password or ""
                if not password:
                    self.last_connect_error = (
                        "OBS WebSocket has authentication enabled, but no password is configured in VoxStream! "
                        "In OBS Studio, go to Tools -> WebSocket Server Settings and uncheck 'Enable Authentication' "
                        "or enter your OBS password in VoxStream."
                    )
                    logger.warning(self.last_connect_error)
                else:
                    identify_d["authentication"] = _generate_auth_string(password, salt, challenge)

            # Send Identify
            await self.ws.send(json.dumps({"op": 1, "d": identify_d}))

            # 3. Wait for OpCode 2 (Identified)
            raw_identified = await asyncio.wait_for(self.ws.recv(), timeout=3.0)
            identified_data = json.loads(raw_identified)
            if identified_data.get("op") != 2:
                self.last_connect_error = f"OBS Identification failed: {identified_data}"
                logger.warning(self.last_connect_error)
                await self.ws.close()
                return False

            self.is_connected = True
            self.last_connect_error = None
            logger.info("Connected and authenticated with OBS Studio WebSocket successfully.")

            # 4. Start background listener loop for events & requests
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()
            self._listen_task = asyncio.create_task(self._listen_loop())

            if self.config.auto_open_projector:
                asyncio.create_task(self.handle_auto_projector())
            return True
        except Exception as e:
            self.last_connect_error = str(e)
            self.is_connected = False
            return False

    async def _reconnect_loop(self):
        """Silently attempt background reconnection to OBS Studio if disconnected."""
        while not self._closing and self.config.enabled:
            if not self.is_connected:
                try:
                    await self._attempt_connect()
                except Exception:
                    pass
            await asyncio.sleep(4.0)

    async def _listen_loop(self):
        """Listen for incoming OBS WebSocket events and responses."""
        try:
            async for raw_msg in self.ws:
                data = json.loads(raw_msg)
                op = data.get("op")

                # OpCode 5: Event
                if op == 5:
                    event_data = data.get("d", {})
                    event_type = event_data.get("eventType")
                    event_payload = event_data.get("eventData", {})

                    if event_type == "StreamStateChanged":
                        active = event_payload.get("outputActive", False)
                        logger.info(f"OBS Stream State Changed: active={active}")
                        if active and self.config.auto_open_projector:
                            asyncio.create_task(self.handle_auto_projector())
                        if self.on_stream_state_changed:
                            self.on_stream_state_changed(active)

                    elif event_type == "RecordStateChanged":
                        active = event_payload.get("outputActive", False)
                        output_path = event_payload.get("outputPath", "")
                        logger.info(f"OBS Record State Changed: active={active} outputPath='{output_path}'")
                        if self.on_record_state_changed:
                            try:
                                self.on_record_state_changed(active, output_path)
                            except TypeError:
                                self.on_record_state_changed(active)

                    elif event_type == "CurrentProgramSceneChanged":
                        scene_name = event_payload.get("sceneName", "")
                        self.current_scene = scene_name
                        logger.info(f"OBS Program Scene Changed: '{scene_name}'")
                        if self.on_scene_changed:
                            try:
                                self.on_scene_changed(scene_name)
                            except Exception as ex:
                                logger.error(f"Error in on_scene_changed callback: {ex}")

                # OpCode 7: RequestResponse
                elif op == 7:
                    req_id = data.get("d", {}).get("requestId")
                    if req_id in self._pending_requests:
                        future = self._pending_requests.pop(req_id)
                        if not future.done():
                            future.set_result(data.get("d", {}))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"OBS WebSocket listen loop closed: {e}")
        finally:
            self.is_connected = False

    async def send_request(self, request_type: str, request_data: dict) -> Optional[dict]:
        """Send an RPC request to OBS WebSocket v5 and wait for response."""
        if not self.is_connected or not self.ws:
            return None

        req_id = f"req_{self._message_id}"
        self._message_id += 1

        payload = {
            "op": 6,
            "d": {
                "requestType": request_type,
                "requestId": req_id,
                "requestData": request_data,
            },
        }

        future = asyncio.get_event_loop().create_future()
        self._pending_requests[req_id] = future

        try:
            await self.ws.send(json.dumps(payload))
            response = await asyncio.wait_for(future, timeout=2.0)
            return response
        except Exception as e:
            self._pending_requests.pop(req_id, None)
            logger.debug(f"OBS request '{request_type}' failed: {e}")
            return None

    async def update_text_source(self, source_name: str, text: str) -> bool:
        """Update the text content of a Text (GDI+) or FreeType 2 source in OBS."""
        if not self.is_connected:
            return False

        res = await self.send_request(
            "SetInputSettings",
            {
                "inputName": source_name,
                "inputSettings": {"text": text},
                "overlay": True,
            },
        )
        return res is not None and res.get("requestStatus", {}).get("result", False)

    async def send_stream_caption(self, caption_text: str) -> bool:
        """Send closed captions (CEA-608) directly into the RTMP stream."""
        if not self.is_connected:
            return False

        res = await self.send_request(
            "SendStreamCaption",
            {
                "captionText": caption_text,
            },
        )
        return res is not None and res.get("requestStatus", {}).get("result", False)

    async def open_projector(
        self,
        mix_type: str = "preview",
        monitor_index: int = 0,
        source_name: Optional[str] = None,
    ) -> bool:
        """Open a Fullscreen or Windowed Projector in OBS Studio."""
        if not self.is_connected:
            self.last_projector_error = self.last_connect_error or f"OBS WebSocket is not connected (ws://{self.config.host}:{self.config.port})."
            logger.warning(f"Cannot open projector: {self.last_projector_error}")
            return False

        mix_type = (mix_type or "preview").lower().strip()
        target_mon = monitor_index if monitor_index is not None else 0

        # 1. Source Projector (ONLY if explicitly mix_type == "source")
        if mix_type == "source":
            target_source = source_name or self.config.projector_source_name or "Captions Overlay"
            logger.info(f"Opening OBS Source Projector for '{target_source}' on monitor index {target_mon}...")
            res = await self.send_request(
                "OpenSourceProjector",
                {
                    "sourceName": target_source,
                    "monitorIndex": target_mon,
                },
            )
            success = res is not None and res.get("requestStatus", {}).get("result", False)
            if not success:
                err_comment = res.get("requestStatus", {}).get("comment", "") if res else "No response from OBS Studio."
                self.last_projector_error = f"OBS Source Projector failed for '{target_source}' (Monitor {target_mon}): {err_comment}"
                logger.warning(self.last_projector_error)
                if target_mon > 0:
                    logger.info(f"Retrying source projector on monitor 0...")
                    res = await self.send_request("OpenSourceProjector", {"sourceName": target_source, "monitorIndex": 0})
                    success = res is not None and res.get("requestStatus", {}).get("result", False)
                    if success:
                        self.last_projector_error = None
            else:
                self.last_projector_error = None
            return success

        # 2. Video Mix Projector (Preview / Program / Multiview)
        obs_mix_type = "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PREVIEW"
        if mix_type in ("program", "output"):
            obs_mix_type = "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PROGRAM"
        elif mix_type == "multiview":
            obs_mix_type = "OBS_WEBSOCKET_VIDEO_MIX_TYPE_MULTIVIEW"

        logger.info(f"Opening OBS Video Mix Projector ({obs_mix_type}) on monitor index {target_mon}...")
        res = await self.send_request(
            "OpenVideoMixProjector",
            {
                "videoMixType": obs_mix_type,
                "monitorIndex": target_mon,
            },
        )
        success = res is not None and res.get("requestStatus", {}).get("result", False)
        if not success:
            err_comment = res.get("requestStatus", {}).get("comment", "") if res else "No response from OBS Studio."
            self.last_projector_error = f"OBS Video Mix Projector ({mix_type}) failed on Monitor {target_mon}: {err_comment}"
            logger.warning(self.last_projector_error)
            if target_mon > 0:
                logger.info(f"Retrying video mix projector on monitor 0...")
                res = await self.send_request("OpenVideoMixProjector", {"videoMixType": obs_mix_type, "monitorIndex": 0})
                success = res is not None and res.get("requestStatus", {}).get("result", False)
                if success:
                    self.last_projector_error = None
            if not success and target_mon != -1:
                logger.info("Retrying with windowed projector (monitorIndex -1)...")
                res = await self.send_request("OpenVideoMixProjector", {"videoMixType": obs_mix_type, "monitorIndex": -1})
                success = res is not None and res.get("requestStatus", {}).get("result", False)
                if success:
                    self.last_projector_error = None
        else:
            self.last_projector_error = None
        return success

    async def get_monitors(self) -> list:
        """Query list of connected monitors from OBS."""
        if not self.is_connected:
            return []

        res = await self.send_request("GetMonitorList", {})
        if res and res.get("requestStatus", {}).get("result", False):
            monitors = res.get("responseData", {}).get("monitors", [])
            # Inject monitorIndex if missing (OBS v5 provides array indices)
            for idx, m in enumerate(monitors):
                if "monitorIndex" not in m:
                    m["monitorIndex"] = idx
            return monitors
        return []

    async def get_scene_list(self) -> dict:
        """Query current program scene and list of available scenes from OBS Studio."""
        if not self.is_connected:
            return {"connected": False, "current_scene": self.current_scene or "", "scenes": []}

        res = await self.send_request("GetSceneList", {})
        if res and res.get("requestStatus", {}).get("result", False):
            resp_data = res.get("responseData", {})
            current_scene = resp_data.get("currentProgramSceneName", "")
            if current_scene:
                self.current_scene = current_scene
            raw_scenes = resp_data.get("scenes", [])
            # In OBS WebSocket v5, scenes is a list of dicts: [{"sceneName": "...", "sceneIndex": 0}, ...]
            scene_names = []
            for s in raw_scenes:
                if isinstance(s, dict) and "sceneName" in s:
                    scene_names.append(s["sceneName"])
                elif isinstance(s, str):
                    scene_names.append(s)
            return {
                "connected": True,
                "current_scene": current_scene or self.current_scene or "",
                "scenes": scene_names,
            }
        return {"connected": self.is_connected, "current_scene": self.current_scene or "", "scenes": []}

    def is_projector_schedule_active(self, now: Optional[datetime.datetime] = None) -> bool:
        """Check if projector lock is currently active based on 24/7 setting or weekly schedule window."""
        if not getattr(self.config, "projector_persistent_lock", False):
            return False
        if not getattr(self.config, "projector_lock_schedule_enabled", False):
            return True  # 24/7 Continuous Mode

        if now is None:
            now = datetime.datetime.now()

        # 1. Day Check
        day_map = {
            "mon": "Mon", "monday": "Mon",
            "tue": "Tue", "tues": "Tue", "tuesday": "Tue",
            "wed": "Wed", "wednesday": "Wed",
            "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursday": "Thu",
            "fri": "Fri", "friday": "Fri",
            "sat": "Sat", "saturday": "Sat",
            "sun": "Sun", "sunday": "Sun"
        }
        current_day_abbr = now.strftime("%a")  # e.g. "Sun", "Wed"
        raw_days = getattr(self.config, "projector_lock_schedule_days", ["Sun", "Wed"]) or []
        configured_days = [
            day_map.get(str(d).strip().lower(), str(d).strip())
            for d in raw_days
        ]
        if configured_days and current_day_abbr not in configured_days:
            return False

        # 2. Time Window Check
        try:
            start_str = getattr(self.config, "projector_lock_schedule_start", "07:00") or "07:00"
            end_str = getattr(self.config, "projector_lock_schedule_end", "14:00") or "14:00"
            start_parts = [int(p) for p in start_str.split(":")]
            end_parts = [int(p) for p in end_str.split(":")]
            start_t = datetime.time(start_parts[0], start_parts[1] if len(start_parts) > 1 else 0)
            end_t = datetime.time(end_parts[0], end_parts[1] if len(end_parts) > 1 else 0)
            curr_t = now.time()

            if start_t <= end_t:
                return start_t <= curr_t <= end_t
            else:
                # Overnight window (e.g. 22:00 -> 04:00)
                return curr_t >= start_t or curr_t <= end_t
        except Exception as ex:
            logger.debug(f"Error checking projector schedule time window: {ex}")
            return True

    async def _projector_watchdog_loop(self):
        """Periodic heartbeat loop to verify and keep OBS projector locked to target monitor 24/7 or on schedule."""
        while not self._closing and getattr(self.config, "enabled", True):
            interval = max(5, int(getattr(self.config, "projector_lock_check_interval_seconds", 15) or 15))
            await asyncio.sleep(interval)

            if self._closing or not getattr(self.config, "enabled", True):
                break

            if not getattr(self.config, "projector_persistent_lock", False):
                continue

            if not self.is_connected or not self.ws:
                continue

            if self.is_projector_schedule_active():
                try:
                    success = await self.open_projector(
                        mix_type=self.config.projector_type,
                        monitor_index=self.config.projector_monitor_index,
                        source_name=self.config.projector_source_name,
                    )
                    self.watchdog_verifications_count += 1
                    self.last_watchdog_check = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.last_watchdog_success = success
                    if success:
                        logger.debug(f"Projector watchdog verified on monitor {self.config.projector_monitor_index}.")
                except Exception as ex:
                    self.last_watchdog_check = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.last_watchdog_success = False
                    logger.debug(f"Projector watchdog verification error: {ex}")

    def get_status(self) -> dict:
        """Return real-time OBS WebSocket connection status and diagnostic telemetry."""
        is_sched_active = self.is_projector_schedule_active()
        return {
            "enabled": self.config.enabled,
            "connected": self.is_connected,
            "host": self.config.host,
            "port": self.config.port,
            "has_password": bool(self.config.password),
            "current_scene": self.current_scene or "",
            "last_connect_error": self.last_connect_error,
            "last_projector_error": self.last_projector_error,
            "auto_open_projector": self.config.auto_open_projector,
            "projector_type": self.config.projector_type,
            "projector_monitor_index": self.config.projector_monitor_index,
            "projector_source_name": self.config.projector_source_name,
            "projector_persistent_lock": getattr(self.config, "projector_persistent_lock", False),
            "projector_lock_check_interval_seconds": getattr(self.config, "projector_lock_check_interval_seconds", 15),
            "projector_lock_schedule_enabled": getattr(self.config, "projector_lock_schedule_enabled", False),
            "projector_lock_schedule_days": getattr(self.config, "projector_lock_schedule_days", ["Sun", "Wed"]),
            "projector_lock_schedule_start": getattr(self.config, "projector_lock_schedule_start", "07:00"),
            "projector_lock_schedule_end": getattr(self.config, "projector_lock_schedule_end", "14:00"),
            "watchdog_active": getattr(self.config, "projector_persistent_lock", False) and is_sched_active,
            "watchdog_schedule_active": is_sched_active,
            "watchdog_verifications_count": self.watchdog_verifications_count,
            "last_watchdog_check": self.last_watchdog_check,
            "last_watchdog_success": self.last_watchdog_success,
        }

    async def reconnect(self, new_config: Optional[Union[OBSConfig, Any]] = None) -> bool:
        """Force close current connection and reconnect with updated config."""
        if new_config:
            self.config = new_config.obs if hasattr(new_config, "obs") else new_config
        self.is_connected = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        return await self._attempt_connect()

    async def handle_auto_projector(self):
        """Auto-open projector if configured."""
        if self.config.auto_open_projector:
            await asyncio.sleep(1.0)  # Brief delay to allow OBS video pipeline readiness
            await self.open_projector(
                mix_type=self.config.projector_type,
                monitor_index=self.config.projector_monitor_index,
                source_name=self.config.projector_source_name,
            )

    async def close(self):
        """Close WebSocket connection and stop reconnect loop and watchdog."""
        self._closing = True
        self.is_connected = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        logger.info("OBS WebSocket client closed.")
