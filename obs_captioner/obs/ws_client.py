"""OBS WebSocket v5 Client with Auto-Start Hooks."""

import asyncio
import json
import logging
from typing import Callable, Optional
import websockets

from ..config import OBSConfig

logger = logging.getLogger("obs_captioner.obs")


class OBSWebSocketClient:
    """Async client for OBS Studio WebSocket v5 protocol."""

    def __init__(self, config: OBSConfig):
        self.config = config
        self.ws = None
        self.is_connected = False
        self._message_id = 1
        self._pending_requests = {}
        self._listen_task: Optional[asyncio.Task] = None
        self.on_stream_state_changed: Optional[Callable[[bool], None]] = None
        self.on_record_state_changed: Optional[Callable[[bool], None]] = None

    async def connect(self) -> bool:
        """Connect and authenticate with OBS WebSocket v5."""
        if not self.config.enabled:
            return False

        uri = f"ws://{self.config.host}:{self.config.port}"
        logger.info(f"Connecting to OBS WebSocket at {uri}...")

        try:
            self.ws = await websockets.connect(uri, ping_interval=10, ping_timeout=5)
            self._listen_task = asyncio.create_task(self._listen_loop())

            # Perform OBS v5 handshake
            # 1. Wait for OpCode 0 (Hello)
            # 2. Send OpCode 1 (Identify) with event subscriptions
            identify_payload = {
                "op": 1,
                "d": {
                    "rpcVersion": 1,
                    # EventSubscription: General (1) | Outputs (64)
                    "eventSubscriptions": 1 | 64,
                },
            }
            if self.config.password:
                import base64
                import hashlib
                # Simple password identification (if OBS requests auth)
                # Note: Full auth challenge resolution if salt/challenge present in Hello
                identify_payload["d"]["authentication"] = self.config.password

            await self.ws.send(json.dumps(identify_payload))
            self.is_connected = True
            logger.info("Connected to OBS Studio WebSocket successfully.")
            if self.config.auto_open_projector:
                asyncio.create_task(self.handle_auto_projector())
            return True
        except Exception as e:
            logger.warning(f"Could not connect to OBS WebSocket: {e}. (Is OBS running and WebSocket server enabled?)")
            self.is_connected = False
            return False

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
                        logger.info(f"OBS Record State Changed: active={active}")
                        if self.on_record_state_changed:
                            self.on_record_state_changed(active)

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
        monitor_index: int = 1,
        source_name: Optional[str] = None,
    ) -> bool:
        """Open a Fullscreen or Windowed Projector in OBS Studio."""
        if not self.is_connected:
            logger.warning("Cannot open projector: OBS WebSocket is not connected.")
            return False

        mix_type = (mix_type or "preview").lower()

        # Source Projector (e.g. for Captions Overlay source)
        if mix_type == "source" or source_name:
            target_source = source_name or self.config.projector_source_name or "Captions Overlay"
            logger.info(f"Opening OBS Source Projector for '{target_source}' on monitor index {monitor_index}...")
            res = await self.send_request(
                "OpenSourceProjector",
                {
                    "sourceName": target_source,
                    "monitorIndex": monitor_index,
                },
            )
            return res is not None and res.get("requestStatus", {}).get("result", False)

        # Video Mix Projector (Preview / Program / Multiview)
        obs_mix_type = "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PREVIEW"
        if mix_type in ("program", "output"):
            obs_mix_type = "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PROGRAM"
        elif mix_type == "multiview":
            obs_mix_type = "OBS_WEBSOCKET_VIDEO_MIX_TYPE_MULTIVIEW"

        logger.info(f"Opening OBS Video Mix Projector ({obs_mix_type}) on monitor index {monitor_index}...")
        res = await self.send_request(
            "OpenVideoMixProjector",
            {
                "videoMixType": obs_mix_type,
                "monitorIndex": monitor_index,
            },
        )
        return res is not None and res.get("requestStatus", {}).get("result", False)

    async def get_monitors(self) -> list:
        """Query list of connected monitors from OBS."""
        if not self.is_connected:
            return []

        res = await self.send_request("GetMonitorList", {})
        if res and res.get("requestStatus", {}).get("result", False):
            return res.get("responseData", {}).get("monitors", [])
        return []

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
        """Close WebSocket connection."""
        self.is_connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        logger.info("OBS WebSocket client closed.")
