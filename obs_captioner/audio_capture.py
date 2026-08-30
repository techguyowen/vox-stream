"""Low-latency audio capture using sounddevice (WASAPI/DirectSound/MME) with live level meter."""

import asyncio
import logging
import math
import queue
import threading
from typing import AsyncGenerator, Callable, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    np = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

from .config import AudioConfig

logger = logging.getLogger("obs_captioner.audio")


def list_audio_devices() -> List[Dict]:
    """Query and return all available audio input devices."""
    if sd is None:
        logger.error("sounddevice is not installed.")
        return []
    
    devices = []
    try:
        hostapis = sd.query_hostapis()
        device_list = sd.query_devices()
        for idx, dev in enumerate(device_list):
            api_name = hostapis[dev["hostapi"]]["name"] if dev.get("hostapi") < len(hostapis) else "Unknown"
            
            # 1. Standard Input Devices (Microphones, Line-in, Virtual Cables)
            if dev.get("max_input_channels", 0) > 0:
                devices.append({
                    "index": idx,
                    "name": dev["name"],
                    "hostapi": api_name,
                    "channels": dev["max_input_channels"],
                    "default_samplerate": dev["default_samplerate"],
                    "is_loopback": False,
                })
            
            # 2. Windows WASAPI Loopback (Monitors, HDMI Displays, TVs, Speakers)
            elif "WASAPI" in api_name.upper() and dev.get("max_output_channels", 0) > 0:
                devices.append({
                    "index": idx,
                    "name": f"{dev['name']} [Display/Speaker Loopback]",
                    "hostapi": api_name,
                    "channels": dev["max_output_channels"],
                    "default_samplerate": dev["default_samplerate"],
                    "is_loopback": True,
                })
    except Exception as e:
        logger.error(f"Error querying audio devices: {e}")
    return devices


def find_audio_device(config: AudioConfig) -> Tuple[Optional[int], Optional[Dict]]:
    """Resolve the appropriate input device based on index or name filter."""
    devices = list_audio_devices()
    if not devices:
        return None, None

    # 1. Match by explicit index
    if config.device_index is not None:
        for d in devices:
            if d["index"] == config.device_index:
                return d["index"], d
        logger.warning(f"Device index {config.device_index} not found. Falling back to search.")

    # 2. Match by name filter
    query = (config.device_name_filter or "").strip().lower()
    if query and query != "default":
        matches = [d for d in devices if query in d["name"].lower()]
        if matches:
            wasapi_matches = [d for d in matches if "wasapi" in d["hostapi"].lower()]
            selected = wasapi_matches[0] if wasapi_matches else matches[0]
            return selected["index"], selected

    # 3. Default input device
    try:
        default_idx = sd.default.device[0]
        if default_idx is not None and default_idx >= 0:
            for d in devices:
                if d["index"] == default_idx:
                    return d["index"], d
    except Exception:
        pass

    return devices[0]["index"], devices[0]


class AudioCapture:
    """Captures continuous 16kHz mono 16-bit PCM audio stream with real-time level metering."""

    def __init__(self, config: AudioConfig):
        self.config = config
        self.target_rate = config.sample_rate  # 16000 Hz
        self.chunk_samples = int(self.target_rate * (config.chunk_duration_ms / 1000.0))
        
        self.device_index, self.device_info = find_audio_device(config)
        self.stream: Optional[sd.InputStream] = None
        self._queue: queue.Queue = queue.Queue(maxsize=100)
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.current_rms_db: float = -100.0
        self.on_level_meter: Optional[Callable[[float], None]] = None

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
        """Start the audio capture stream."""
        if self._running:
            return True

        if sd is None:
            logger.error("sounddevice is not installed. Cannot capture audio.")
            return False

        self._loop = loop or asyncio.get_event_loop()
        dev_name = self.device_info["name"] if self.device_info else "Default"
        dev_api = self.device_info["hostapi"] if self.device_info else ""
        logger.info(f"Opening audio input [{self.device_index}]: '{dev_name}' ({dev_api}) at {self.target_rate}Hz mono")

        native_rate = int(self.device_info["default_samplerate"]) if self.device_info else self.target_rate
        is_loopback = self.device_info.get("is_loopback", False) if self.device_info else False
        stream_rate = self.target_rate
        channels = 1
        extra_settings = None

        if is_loopback:
            try:
                if hasattr(sd, "WasapiSettings"):
                    extra_settings = sd.WasapiSettings(loopback=True)
                    channels = max(1, int(self.device_info.get("channels", 2)))
                    stream_rate = native_rate
                    logger.info(f"🔊 WASAPI Loopback active: capturing audio playing to '{dev_name}' ({channels}ch @ {native_rate}Hz)")
            except Exception as we:
                logger.debug(f"WASAPI loopback settings exception: {we}")

        def audio_callback(indata, frames, time_info, status):
            if not self._running:
                return

            # Compute RMS Level for VU Meter
            try:
                if indata.dtype == np.float32:
                    rms = np.sqrt(np.mean(indata ** 2))
                    db = 20 * math.log10(rms) if rms > 1e-5 else -100.0
                else:
                    audio_f = indata.astype(np.float32) / 32768.0
                    rms = np.sqrt(np.mean(audio_f ** 2))
                    db = 20 * math.log10(rms) if rms > 1e-5 else -100.0
                self.current_rms_db = max(-100.0, min(0.0, float(db)))
                if self.on_level_meter:
                    self.on_level_meter(self.current_rms_db)
            except Exception:
                pass

            # Convert numpy float32/int16 array to 16-bit linear PCM bytes
            if indata.dtype == np.float32:
                clipped = np.clip(indata, -1.0, 1.0)
                pcm16 = (clipped * 32767.0).astype(np.int16)
            elif indata.dtype == np.int16:
                pcm16 = indata
            else:
                pcm16 = indata.astype(np.int16)

            # Downmix multi-channel to mono
            if pcm16.ndim > 1 and pcm16.shape[1] > 1:
                pcm16 = np.mean(pcm16, axis=1, dtype=np.int16)

            # Resample down to 16kHz if captured at native rate (e.g. 48kHz -> 16kHz)
            if stream_rate != self.target_rate and len(pcm16) > 0:
                step = int(stream_rate / self.target_rate)
                if step > 1 and stream_rate % self.target_rate == 0:
                    pcm16 = pcm16[::step]
                else:
                    target_len = int(len(pcm16) * (self.target_rate / stream_rate))
                    if target_len > 0:
                        indices = np.linspace(0, len(pcm16) - 1, target_len).astype(int)
                        pcm16 = pcm16[indices]

            pcm_bytes = pcm16.tobytes()
            try:
                self._queue.put_nowait(pcm_bytes)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(pcm_bytes)
                except Exception:
                    pass

        try:
            self.stream = sd.InputStream(
                device=self.device_index,
                channels=channels,
                samplerate=stream_rate,
                blocksize=self.chunk_samples if stream_rate == self.target_rate else int(stream_rate * (self.config.chunk_duration_ms / 1000.0)),
                dtype=np.int16,
                extra_settings=extra_settings,
                callback=audio_callback,
            )
            self.stream.start()
            self._running = True
            logger.info("Audio capture stream started.")
            return True
        except Exception as e:
            logger.error(f"Failed to open audio stream: {e}")
            try:
                self.stream = sd.InputStream(
                    device=self.device_index,
                    channels=channels,
                    samplerate=native_rate,
                    dtype=np.float32,
                    extra_settings=extra_settings,
                    callback=audio_callback,
                )
                self.stream.start()
                self._running = True
                logger.info(f"Audio capture stream started at native rate {native_rate}Hz.")
                return True
            except Exception as e2:
                logger.error(f"Failed to open audio stream at native rate: {e2}")
                return False

    def inject_audio_chunk(self, pcm_bytes: bytes):
        """Directly inject 16kHz 16-bit linear PCM audio chunk (e.g. from OBS native filter or network stream)."""
        if not pcm_bytes:
            return

        # Calculate VU meter level from PCM bytes
        try:
            if np is not None:
                audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                rms = np.sqrt(np.mean(audio_np ** 2)) if len(audio_np) > 0 else 0.0
                db = 20 * math.log10(rms) if rms > 1e-5 else -100.0
                self.current_rms_db = max(-100.0, min(0.0, float(db)))
                if self.on_level_meter:
                    self.on_level_meter(self.current_rms_db)
        except Exception:
            pass

        try:
            self._queue.put_nowait(pcm_bytes)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(pcm_bytes)
            except Exception:
                pass

    def stop(self):
        """Stop the audio capture stream."""
        self._running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.debug(f"Error closing audio stream: {e}")
            self.stream = None
        self.current_rms_db = -100.0
        logger.info("Audio capture stream stopped.")

    async def stream_generator(self) -> AsyncGenerator[bytes, None]:
        """Asynchronously yield audio chunks as raw 16kHz 16-bit PCM bytes."""
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                chunk = await loop.run_in_executor(None, self._queue.get, True, 0.2)
                if chunk:
                    yield chunk
            except queue.Empty:
                await asyncio.sleep(0.01)
            except Exception as e:
                if self._running:
                    logger.debug(f"Stream generator error: {e}")
                break
