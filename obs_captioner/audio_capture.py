"""Low-latency audio capture using sounddevice (WASAPI/DirectSound/MME) with live level meter."""

import asyncio
import logging
import math
import os
import queue
import sys
import threading
import time
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


def _hostapi_priority(hostapi_name: str) -> int:
    """Prioritize audio host APIs: WASAPI (lowest latency) > DirectSound > MME > WDM-KS."""
    h = (hostapi_name or "").upper()
    if "WASAPI" in h:
        return 4
    if "DIRECTSOUND" in h:
        return 3
    if "MME" in h:
        return 2
    if "WDM" in h or "KS" in h:
        return 1
    return 0


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
            
            # Standard Input Devices (Microphones, Line-in, Virtual Cables, Stereo Mix)
            if dev.get("max_input_channels", 0) > 0:
                is_loop = any(k in dev["name"].lower() for k in ("stereo mix", "what u hear", "wave out mix", "loopback", "cable output"))
                devices.append({
                    "index": idx,
                    "name": dev["name"],
                    "hostapi": api_name,
                    "channels": dev["max_input_channels"],
                    "default_samplerate": dev["default_samplerate"],
                    "is_loopback": is_loop,
                })
    except Exception as e:
        logger.error(f"Error querying audio devices: {e}")
    return devices


def find_audio_device(config: AudioConfig, wait_settle: bool = True) -> Tuple[Optional[int], Optional[Dict]]:
    """Resolve the appropriate input device based on index or name filter.

    Includes a boot settle delay for USB audio interfaces (Focusrite, Behringer, etc.)
    that may take a few seconds to enumerate on system startup.
    """
    settle_sec = getattr(config, "device_settle_seconds", 4.0) or 0.0
    is_specific = (config.device_index is not None) or (
        bool(config.device_name_filter) and config.device_name_filter.strip().lower() != "default"
    )

    start_time = time.monotonic()
    attempts = 0
    devices: List[Dict] = []

    while True:
        attempts += 1
        devices = list_audio_devices()
        if devices:
            # 1. Match by explicit index
            if config.device_index is not None:
                for d in devices:
                    if d["index"] == config.device_index:
                        if attempts > 1:
                            logger.info(
                                f"✅ Audio device index {config.device_index} ready after {time.monotonic() - start_time:.1f}s settle delay."
                            )
                        return d["index"], d

            # 2. Match by name filter
            query = (config.device_name_filter or "").strip().lower()
            if query and query != "default":
                matches = [d for d in devices if query in d["name"].lower()]
                if matches:
                    matches.sort(key=lambda d: _hostapi_priority(d.get("hostapi", "")), reverse=True)
                    selected = matches[0]
                    if attempts > 1:
                        logger.info(
                            f"✅ Audio device '{selected['name']}' ready after {time.monotonic() - start_time:.1f}s settle delay."
                        )
                    return selected["index"], selected

        elapsed = time.monotonic() - start_time
        if not wait_settle or not is_specific or elapsed >= settle_sec:
            break
        time.sleep(0.4)

    if not devices:
        return None, None

    if is_specific and settle_sec > 0:
        logger.warning(
            f"Configured audio device (filter='{config.device_name_filter}', index={config.device_index}) "
            f"not found after {settle_sec:.1f}s boot settle window. Falling back to system default input."
        )

    # 3. Default input device (prioritize modern low-latency WASAPI on Windows)
    if sys.platform == "win32":
        try:
            for h in sd.query_hostapis():
                if "WASAPI" in h.get("name", "").upper():
                    w_def = h.get("default_input_device", -1)
                    if w_def is not None and w_def >= 0:
                        for d in devices:
                            if d["index"] == w_def:
                                return d["index"], d
        except Exception:
            pass

    try:
        default_idx = sd.default.device[0]
        if default_idx is not None and default_idx >= 0:
            for d in devices:
                if d["index"] == default_idx:
                    return d["index"], d
    except Exception:
        pass

    devices_sorted = sorted(devices, key=lambda d: _hostapi_priority(d.get("hostapi", "")), reverse=True)
    return devices_sorted[0]["index"], devices_sorted[0]


async def find_audio_device_async(config: AudioConfig, wait_settle: bool = True) -> Tuple[Optional[int], Optional[Dict]]:
    """Asynchronously resolve the appropriate input device based on index or name filter.

    Includes a boot settle delay for USB audio interfaces (Focusrite, Behringer, etc.)
    that may take a few seconds to enumerate on system startup, without blocking the asyncio event loop.
    """
    settle_sec = getattr(config, "device_settle_seconds", 4.0) or 0.0
    is_specific = (config.device_index is not None) or (
        bool(config.device_name_filter) and config.device_name_filter.strip().lower() != "default"
    )

    start_time = time.monotonic()
    attempts = 0
    devices: List[Dict] = []

    while True:
        attempts += 1
        devices = list_audio_devices()
        if devices:
            # 1. Match by explicit index
            if config.device_index is not None:
                for d in devices:
                    if d["index"] == config.device_index:
                        if attempts > 1:
                            logger.info(
                                f"✅ Audio device index {config.device_index} ready after {time.monotonic() - start_time:.1f}s settle delay."
                            )
                        return d["index"], d

            # 2. Match by name filter
            query = (config.device_name_filter or "").strip().lower()
            if query and query != "default":
                matches = [d for d in devices if query in d["name"].lower()]
                if matches:
                    matches.sort(key=lambda d: _hostapi_priority(d.get("hostapi", "")), reverse=True)
                    selected = matches[0]
                    if attempts > 1:
                        logger.info(
                            f"✅ Audio device '{selected['name']}' ready after {time.monotonic() - start_time:.1f}s settle delay."
                        )
                    return selected["index"], selected

        elapsed = time.monotonic() - start_time
        if not wait_settle or not is_specific or elapsed >= settle_sec:
            break
        await asyncio.sleep(0.4)

    if not devices:
        return None, None

    if is_specific and settle_sec > 0:
        logger.warning(
            f"Configured audio device (filter='{config.device_name_filter}', index={config.device_index}) "
            f"not found after {settle_sec:.1f}s boot settle window. Falling back to system default input."
        )

    # 3. Default input device (prioritize modern low-latency WASAPI on Windows)
    if sys.platform == "win32":
        try:
            for h in sd.query_hostapis():
                if "WASAPI" in h.get("name", "").upper():
                    w_def = h.get("default_input_device", -1)
                    if w_def is not None and w_def >= 0:
                        for d in devices:
                            if d["index"] == w_def:
                                return d["index"], d
        except Exception:
            pass

    try:
        default_idx = sd.default.device[0]
        if default_idx is not None and default_idx >= 0:
            for d in devices:
                if d["index"] == default_idx:
                    return d["index"], d
    except Exception:
        pass

    devices_sorted = sorted(devices, key=lambda d: _hostapi_priority(d.get("hostapi", "")), reverse=True)
    return devices_sorted[0]["index"], devices_sorted[0]


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
        self._agc_gain: float = 1.0
        self.is_recovering: bool = False
        self._recovery_task: Optional[asyncio.Task] = None
        self.on_recovery_status: Optional[Callable[[str, str], None]] = None
        self._overflow_drops = 0
        self._last_overflow_log = 0.0

    def _flush_overflow_warning(self) -> None:
        """Log throttled queue-overflow drops and reset overflow drop count."""
        if self._overflow_drops <= 0:
            return
        now = time.monotonic()
        if self._last_overflow_log == 0.0 or now - self._last_overflow_log >= 5.0:
            self._last_overflow_log = now
            dropped = self._overflow_drops
            self._overflow_drops = 0
            logger.warning(
                f"Audio capture queue full; dropped {dropped} oldest chunk(s) "
                f"to keep up (downstream consumer is stalled)."
            )

    def _note_queue_overflow(self) -> None:
        """Record a queue-overflow chunk drop with a throttled warning.

        Each overflow discards the oldest buffered audio (~100ms), i.e. a
        gap in the middle of speech. Loud-but-throttled logging keeps such
        gaps diagnosable without spamming the log during a stall.
        """
        self._overflow_drops += 1
        self._flush_overflow_warning()

    def _apply_agc_and_limiter(self, audio_f: np.ndarray) -> np.ndarray:
        """
        Apply Broadcast Automatic Gain Control (AGC) and soft-knee peak limiter.
        Normalizes quiet speech and loud shouting into the optimal speech recognition
        window (-22 dBFS to -16 dBFS) before inference.
        """
        if audio_f is None or len(audio_f) == 0:
            return audio_f

        # Calculate RMS level
        rms = float(np.sqrt(np.mean(audio_f ** 2)))
        db = 20.0 * math.log10(rms) if rms > 1e-5 else -100.0
        self.current_rms_db = max(-100.0, min(0.0, db))
        if self.on_level_meter:
            try:
                self.on_level_meter(self.current_rms_db)
            except Exception:
                pass

        if not getattr(self.config, "enable_agc", True):
            return np.clip(audio_f, -1.0, 1.0)

        # Broadcast AGC Target & Gains
        target_db = getattr(self.config, "agc_target_db", -18.0)
        target_linear = 10.0 ** (target_db / 20.0)  # ~0.1259 for -18 dBFS
        max_gain_db = getattr(self.config, "agc_max_gain_db", 18.0)
        max_gain_linear = 10.0 ** (max_gain_db / 20.0)  # ~7.94 for +18 dB
        min_gain_linear = 10.0 ** (-12.0 / 20.0)  # ~0.25 (-12 dB cut)

        noise_floor_db = getattr(self.config, "noise_gate_db", -52.0)
        if db > noise_floor_db:
            desired_gain = target_linear / max(rms, 1e-4)
            clamped_gain = max(min_gain_linear, min(max_gain_linear, desired_gain))

            # Fast attack (~50ms) when volume surges, gentle release (~800ms) when volume dips
            if clamped_gain < self._agc_gain:
                alpha = 0.35
            else:
                alpha = 0.05
            self._agc_gain = (1.0 - alpha) * self._agc_gain + alpha * clamped_gain
        else:
            # Below noise gate: preserve silence/ambient floor without boosting hiss
            self._agc_gain = (1.0 - 0.05) * self._agc_gain + 0.05 * 1.0

        boosted = audio_f * self._agc_gain

        # Soft-knee peak limiter (threshold T = 0.85, smooth saturation with tanh curve)
        # Prevents harsh digital clipping on sudden shouts or plosives
        threshold = 0.85
        abs_val = np.abs(boosted)
        over_idx = abs_val > threshold
        if np.any(over_idx):
            headroom = 1.0 - threshold
            ratio = (abs_val[over_idx] - threshold) / headroom
            limited = threshold + headroom * np.tanh(ratio)
            boosted[over_idx] = limited * np.sign(boosted[over_idx])

        return np.clip(boosted, -1.0, 1.0)

    def _open_stream(self) -> bool:
        """Attempt to open hardware audio stream with 4-tier fallback for multi-channel / ASIO interfaces."""
        if sd is None:
            return False

        dev_name = self.device_info["name"] if self.device_info else "Default"
        dev_api = self.device_info["hostapi"] if self.device_info else ""
        native_rate = int(self.device_info.get("default_samplerate", self.target_rate)) if self.device_info else self.target_rate
        is_loopback = self.device_info.get("is_loopback", False) if self.device_info else False
        max_in = int(self.device_info.get("channels", 1)) if self.device_info else 1

        extra_settings = None
        if "WASAPI" in dev_api.upper():
            try:
                if hasattr(sd, "WasapiSettings"):
                    extra_settings = sd.WasapiSettings(auto_convert=True)
                    if is_loopback:
                        logger.info(f"🔊 WASAPI Loopback/Stereo Mix active: capturing on '{dev_name}' ({max_in}ch @ {native_rate}Hz)")
            except Exception as we:
                logger.debug(f"WASAPI auto_convert settings exception: {we}")

        # Tiered attempts:
        # Tier 1: 16kHz Mono int16 (Standard / lowest overhead)
        # Tier 2: Native rate Mono int16 (If device clock doesn't support 16kHz)
        # Tier 3: Native rate Stereo/Multichannel int16 (If ASIO/interface rejects mono)
        # Tier 4: Native rate Stereo/Multichannel float32 (Universal compatibility)
        attempts = []
        if not is_loopback:
            attempts.append({"rate": self.target_rate, "channels": 1, "dtype": np.int16, "tier": "16kHz Mono"})
            if native_rate != self.target_rate:
                attempts.append({"rate": native_rate, "channels": 1, "dtype": np.int16, "tier": f"Native {native_rate}Hz Mono"})
            if max_in > 1:
                stereo_ch = min(2, max_in)
                attempts.append({"rate": native_rate, "channels": stereo_ch, "dtype": np.int16, "tier": f"Native {native_rate}Hz Stereo ({stereo_ch}ch)"})
                attempts.append({"rate": native_rate, "channels": stereo_ch, "dtype": np.float32, "tier": f"Native {native_rate}Hz Float32 ({stereo_ch}ch)"})
        else:
            loop_ch = max(1, max_in)
            attempts.append({"rate": native_rate, "channels": loop_ch, "dtype": np.int16, "tier": f"WASAPI Loopback {loop_ch}ch"})
            attempts.append({"rate": native_rate, "channels": loop_ch, "dtype": np.float32, "tier": f"WASAPI Loopback Float32 {loop_ch}ch"})

        for plan in attempts:
            stream_rate = plan["rate"]
            plan_channels = plan["channels"]
            plan_dtype = plan["dtype"]
            plan_tier = plan["tier"]

            def make_audio_callback(s_rate):
                def audio_callback(indata, frames, time_info, status):
                    if not self._running:
                        return

                    # Convert input buffer to float array in range [-1.0, 1.0]
                    if indata.dtype == np.float32:
                        audio_f = indata.copy()
                    else:
                        audio_f = indata.astype(np.float32) / 32768.0

                    # Downmix multi-channel to mono
                    if audio_f.ndim > 1 and audio_f.shape[1] > 1:
                        audio_f = np.mean(audio_f, axis=1)
                    elif audio_f.ndim > 1:
                        audio_f = audio_f.flatten()

                    # Resample down to 16kHz if captured at native rate (e.g. 48kHz -> 16kHz)
                    if s_rate != self.target_rate and len(audio_f) > 0:
                        step = int(s_rate / self.target_rate)
                        if step > 1 and s_rate % self.target_rate == 0:
                            audio_f = audio_f[::step]
                        else:
                            target_len = int(len(audio_f) * (self.target_rate / s_rate))
                            if target_len > 0:
                                indices = np.linspace(0, len(audio_f) - 1, target_len).astype(int)
                                audio_f = audio_f[indices]

                    # Condition audio with Broadcast AGC & Dynamic Peak Limiter
                    conditioned_f = self._apply_agc_and_limiter(audio_f)
                    pcm16 = (conditioned_f * 32767.0).astype(np.int16)
                    pcm_bytes = pcm16.tobytes()

                    try:
                        self._queue.put_nowait(pcm_bytes)
                    except queue.Full:
                        try:
                            self._queue.get_nowait()
                            self._queue.put_nowait(pcm_bytes)
                        except Exception:
                            pass
                        self._overflow_drops += 1

                return audio_callback

            try:
                block_size = self.chunk_samples if stream_rate == self.target_rate else int(stream_rate * (self.config.chunk_duration_ms / 1000.0))
                stream = sd.InputStream(
                    device=self.device_index,
                    channels=plan_channels,
                    samplerate=stream_rate,
                    blocksize=block_size,
                    dtype=plan_dtype,
                    extra_settings=extra_settings,
                    callback=make_audio_callback(stream_rate),
                )
                stream.start()
                self.stream = stream
                logger.info(f"✅ Audio capture stream started using [{plan_tier}] on '{dev_name}' ({dev_api})")
                return True
            except Exception as e:
                logger.debug(f"Plan [{plan_tier}] on '{dev_name}' failed: {e}")
                continue

        logger.error(f"❌ Failed to open audio device '{dev_name}' ({dev_api}) with any supported format.")
        return False

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
        """Start the audio capture stream."""
        if self._running and self.stream is not None:
            return True

        if sd is None:
            logger.error("sounddevice is not installed. Cannot capture audio.")
            return False

        if loop is not None:
            self._loop = loop
        elif self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    self._loop = asyncio.get_event_loop()
                except RuntimeError:
                    self._loop = None

        self._running = True
        ok = self._open_stream()
        if not ok:
            self.is_recovering = True
            dev_name = self.device_info["name"] if self.device_info else "Default"
            logger.warning(
                f"⚠️ Initial audio device opening failed on '{dev_name}'. Audio watchdog initiating background auto-recovery..."
            )
            if self.on_recovery_status:
                try:
                    self.on_recovery_status("recovering", dev_name)
                except Exception:
                    pass
            if not self._recovery_task or self._recovery_task.done():
                try:
                    if self._loop:
                        self._recovery_task = self._loop.create_task(self._auto_recovery_loop())
                    else:
                        self._recovery_task = asyncio.create_task(self._auto_recovery_loop())
                except RuntimeError:
                    try:
                        self._recovery_task = asyncio.get_event_loop().create_task(self._auto_recovery_loop())
                    except Exception:
                        pass
                except Exception:
                    pass
            return False

        if self.on_level_meter:
            try:
                self.on_level_meter(self.current_rms_db)
            except Exception:
                pass
        return True

    def inject_audio_chunk(self, pcm_bytes: bytes):
        """Directly inject 16kHz 16-bit linear PCM audio chunk (e.g. from OBS native filter or network stream)."""
        if not pcm_bytes:
            return

        # Calculate VU meter level and apply AGC/limiter
        even_len = len(pcm_bytes) & ~1
        if np is not None and even_len > 0:
            try:
                audio_np = np.frombuffer(pcm_bytes[:even_len], dtype=np.int16).astype(np.float32) / 32768.0
                conditioned_f = self._apply_agc_and_limiter(audio_np)
                pcm_bytes = (conditioned_f * 32767.0).astype(np.int16).tobytes()
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
            self._note_queue_overflow()

    def stop(self):
        """Stop the audio capture stream."""
        self._running = False
        self.is_recovering = False
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
        # Unblock any thread or generator waiting on the queue
        try:
            self._queue.put_nowait(b"")
        except Exception:
            pass
        if self.stream is not None:
            s = self.stream
            self.stream = None
            try:
                s.stop()
                s.close()
            except Exception as e:
                logger.debug(f"Error closing audio stream: {e}")
        self.current_rms_db = -100.0
        logger.info("Audio capture stream stopped.")

    def update_device(self, new_config: Optional[AudioConfig] = None) -> bool:
        """Hot-switch input device without breaking continuous speech recognition loop."""
        if new_config is not None:
            self.config = new_config
        new_idx, new_info = find_audio_device(self.config)

        self.is_recovering = False
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()

        if new_idx == self.device_index and self.stream is not None:
            logger.debug(f"Audio device unchanged (index {self.device_index}).")
            return True

        dev_title = new_info["name"] if new_info else f"Index {new_idx}"
        logger.info(f"Hot-switching audio capture device from [{self.device_index}] to [{new_idx}]: '{dev_title}'...")

        # Stop previous hardware stream
        if self.stream is not None:
            s = self.stream
            self.stream = None
            try:
                s.stop()
                s.close()
            except Exception as e:
                logger.debug(f"Error closing previous stream during device switch: {e}")

        self.device_index = new_idx
        self.device_info = new_info

        # Reopen hardware stream with new device while KEEPING self._running = True
        # This ensures stream_generator() never terminates its loop!
        if self._running:
            ok = self._open_stream()
            if not ok:
                logger.error(f"Could not open newly selected audio device [{new_idx}].")
                return False
            return True
        return True

    class AudioStreamError(Exception):
        pass

    async def _auto_recovery_loop(self):
        """Background watchdog worker that periodically attempts to rebind the audio device upon disconnect."""
        retry_count = 0
        while self._running and self.is_recovering:
            retry_count += 1
            await asyncio.sleep(1.5)
            if not self.is_recovering or not self._running:
                break
            try:
                # Refresh sounddevice hardware device table if supported
                if sd is not None and hasattr(sd, "_terminate") and hasattr(sd, "_initialize"):
                    try:
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        pass

                new_idx, new_info = await find_audio_device_async(self.config)
                if new_idx is not None:
                    if self.stream is not None:
                        s = self.stream
                        self.stream = None
                        try:
                            s.stop()
                            s.close()
                        except Exception:
                            pass

                    self.device_index = new_idx
                    self.device_info = new_info
                    ok = self._open_stream()
                    if ok:
                        self.is_recovering = False
                        dev_name = self.device_info["name"] if self.device_info else f"Index {new_idx}"
                        logger.info(f"✅ Audio watchdog reconnected to '{dev_name}' (attempt #{retry_count})!")
                        if self.on_recovery_status:
                            try:
                                self.on_recovery_status("recovered", dev_name)
                            except Exception:
                                pass
                        if self.on_level_meter:
                            try:
                                self.on_level_meter(self.current_rms_db)
                            except Exception:
                                pass
                        break
            except Exception as ex:
                logger.debug(f"Audio recovery attempt #{retry_count} failed: {ex}")

    async def stream_generator(self) -> AsyncGenerator[bytes, None]:
        """Asynchronously yield audio chunks as raw 16kHz 16-bit PCM bytes with auto-watchdog recovery."""
        loop = asyncio.get_event_loop()
        empty_strikes = 0
        silence_chunk = b"\x00" * (self.chunk_samples * 2)

        # Drain stale pre-pause chunks so resuming captioning doesn't flush
        # outdated audio or log a false queue-overflow warning.
        try:
            for _ in range(self._queue.qsize()):
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self._overflow_drops = 0

        while self._running:
            self._flush_overflow_warning()
            try:
                chunk = await loop.run_in_executor(None, self._queue.get, True, 0.2)
                self._flush_overflow_warning()
                empty_strikes = 0
                if self.is_recovering:
                    self.is_recovering = False
                    dev_name = self.device_info["name"] if self.device_info else "Default"
                    logger.info(f"✅ Audio capture resumed normal streaming on '{dev_name}'.")
                    if self.on_recovery_status:
                        try:
                            self.on_recovery_status("recovered", dev_name)
                        except Exception:
                            pass
                if chunk:
                    yield chunk
            except queue.Empty:
                empty_strikes += 1
                # If starved for > 1.2s (6 strikes * 0.2s):
                if empty_strikes >= 6 and not self.is_recovering:
                    self.is_recovering = True
                    dev_name = self.device_info["name"] if self.device_info else "Audio device"
                    logger.warning(
                        f"⚠️ Audio capture starved ({empty_strikes * 0.2:.1f}s). "
                        f"'{dev_name}' disconnected or buffer starved. Audio watchdog initiating recovery..."
                    )
                    if self.on_recovery_status:
                        try:
                            self.on_recovery_status("recovering", dev_name)
                        except Exception:
                            pass
                    if not self._recovery_task or self._recovery_task.done():
                        self._recovery_task = asyncio.create_task(self._auto_recovery_loop())

                if self.is_recovering:
                    # Provide silence padding to keep downstream STT engine alive and warm
                    yield silence_chunk
                    await asyncio.sleep(0.1)
                else:
                    await asyncio.sleep(0.01)
            except Exception as e:
                if self._running:
                    logger.debug(f"Stream generator error: {e}")
                break
