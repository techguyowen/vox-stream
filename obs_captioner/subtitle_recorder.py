"""Synchronized Live Subtitle Recorder (.srt / .vtt) for OBS Recordings."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("obs_captioner.recorder")


def format_timestamp_srt(seconds: float) -> str:
    """Format seconds into SRT timecode: HH:MM:SS,mmm"""
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Format seconds into WebVTT timecode: HH:MM:SS.mmm"""
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def get_default_recordings_dir() -> Path:
    """Resolve the default system video recordings directory."""
    home = Path.home()
    if sys.platform == "win32":
        # Check standard Windows Videos folder
        candidates = [home / "Videos", home / "Documents" / "Videos"]
    elif sys.platform == "darwin":
        candidates = [home / "Movies", home / "Videos"]
    else:
        candidates = [home / "Videos", home / "Movies"]

    for c in candidates:
        if c.is_dir():
            return c

    # Fallback to local recordings dir
    local = Path("recordings")
    local.mkdir(parents=True, exist_ok=True)
    return local


class SubtitleRecorder:
    """Live subtitle file recorder synchronized with OBS recording start/stop times."""

    def __init__(
        self,
        enabled: bool = True,
        output_format: str = "srt",
        output_dir: str = "",
        on_status_changed: Optional[Callable[[dict], None]] = None,
    ):
        self.enabled = enabled
        self.output_format = (output_format or "srt").strip().lower()
        self.output_dir = output_dir.strip()
        self.on_status_changed = on_status_changed

        self._lock = threading.Lock()
        self.is_recording = False
        self.record_start_wall: float = 0.0
        self.record_start_mono: float = 0.0
        self.current_file_path: Optional[Path] = None
        self.current_vtt_path: Optional[Path] = None
        self._file_handle_srt = None
        self._file_handle_vtt = None
        self.entry_index: int = 0
        self.active_video_path: str = ""

    def update_config(self, obs_config) -> None:
        """Update recording settings from OBSConfig."""
        with self._lock:
            self.enabled = getattr(obs_config, "auto_record_subtitles", True)
            self.output_format = (getattr(obs_config, "record_subtitles_format", "srt") or "srt").lower()
            self.output_dir = getattr(obs_config, "record_subtitles_directory", "") or ""

    def start_recording(self, video_path: Optional[str] = None) -> Optional[Path]:
        """Start synchronized recording session."""
        with self._lock:
            if self.is_recording:
                logger.debug("Subtitle recorder already running. Restarting session...")
                self._stop_internal()

            now_wall = time.time()
            now_mono = time.monotonic()
            self.record_start_wall = now_wall
            self.record_start_mono = now_mono
            self.entry_index = 0
            self.active_video_path = video_path or ""

            # Determine destination directory & basename
            timestamp_str = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now_wall))
            if video_path and video_path.strip():
                v_path = Path(video_path.strip())
                base_dir = v_path.parent
                stem = v_path.stem
            else:
                if self.output_dir and Path(self.output_dir).is_dir():
                    base_dir = Path(self.output_dir)
                else:
                    base_dir = get_default_recordings_dir()
                stem = f"VoxStream_{timestamp_str}"

            base_dir.mkdir(parents=True, exist_ok=True)
            self.current_file_path = base_dir / f"{stem}.srt"
            self.current_vtt_path = base_dir / f"{stem}.vtt"

            # Open SRT handle
            if self.output_format in ("srt", "both"):
                try:
                    self._file_handle_srt = open(self.current_file_path, "w", encoding="utf-8")
                except Exception as e:
                    logger.error(f"Failed opening SRT output file {self.current_file_path}: {e}")
                    self._file_handle_srt = None

            # Open VTT handle
            if self.output_format in ("vtt", "both"):
                try:
                    self._file_handle_vtt = open(self.current_vtt_path, "w", encoding="utf-8")
                    self._file_handle_vtt.write("WEBVTT\n\n")
                    self._file_handle_vtt.flush()
                except Exception as e:
                    logger.error(f"Failed opening VTT output file {self.current_vtt_path}: {e}")
                    self._file_handle_vtt = None

            self.is_recording = bool(self._file_handle_srt or self._file_handle_vtt)
            primary_path = self.current_file_path if self._file_handle_srt else self.current_vtt_path

            if self.is_recording:
                logger.info(f"🔴 Live subtitle recorder started: {primary_path}")
            else:
                logger.warning("Failed to open any subtitle recording handles.")

        status = self.get_status()
        if self.on_status_changed:
            try:
                self.on_status_changed(status)
            except Exception:
                pass
        return primary_path

    def add_caption(self, text: str, start_time: float, end_time: Optional[float] = None) -> bool:
        """Append a finalized caption with relative timestamps."""
        clean_text = (text or "").strip()
        if not clean_text:
            return False

        with self._lock:
            if not self.is_recording or not (self._file_handle_srt or self._file_handle_vtt):
                return False

            now_wall = time.time()
            now_mono = time.monotonic()
            elapsed_session = max(0.0, now_mono - self.record_start_mono)

            # Compute relative offsets from recording start time
            # Fallback to current elapsed time if start_time precedes recording start
            if start_time and start_time >= self.record_start_wall:
                rel_start = max(0.0, start_time - self.record_start_wall)
            else:
                rel_start = max(0.0, elapsed_session - 2.5)

            if end_time and end_time > start_time:
                rel_end = max(rel_start + 0.8, end_time - self.record_start_wall)
            else:
                rel_end = rel_start + max(1.2, len(clean_text.split()) * 0.35)

            self.entry_index += 1

            # Write SRT
            if self._file_handle_srt:
                try:
                    srt_time_str = f"{format_timestamp_srt(rel_start)} --> {format_timestamp_srt(rel_end)}"
                    block = f"{self.entry_index}\n{srt_time_str}\n{clean_text}\n\n"
                    self._file_handle_srt.write(block)
                    self._file_handle_srt.flush()
                except Exception as e:
                    logger.debug(f"Error writing SRT block: {e}")

            # Write VTT
            if self._file_handle_vtt:
                try:
                    vtt_time_str = f"{format_timestamp_vtt(rel_start)} --> {format_timestamp_vtt(rel_end)}"
                    block = f"{self.entry_index}\n{vtt_time_str}\n{clean_text}\n\n"
                    self._file_handle_vtt.write(block)
                    self._file_handle_vtt.flush()
                except Exception as e:
                    logger.debug(f"Error writing VTT block: {e}")

            return True

    def _stop_internal(self) -> dict:
        """Internal worker to close handles."""
        saved_path = None
        count = self.entry_index
        elapsed = max(0.0, time.monotonic() - self.record_start_mono) if self.record_start_mono else 0.0

        if self._file_handle_srt:
            try:
                self._file_handle_srt.flush()
                self._file_handle_srt.close()
                saved_path = str(self.current_file_path)
            except Exception as e:
                logger.debug(f"Error closing SRT handle: {e}")
            self._file_handle_srt = None

        if self._file_handle_vtt:
            try:
                self._file_handle_vtt.flush()
                self._file_handle_vtt.close()
                if not saved_path:
                    saved_path = str(self.current_vtt_path)
            except Exception as e:
                logger.debug(f"Error closing VTT handle: {e}")
            self._file_handle_vtt = None

        self.is_recording = False
        logger.info(f"⏹ Subtitle recording stopped. Total entries: {count}, duration: {elapsed:.1f}s -> {saved_path}")

        return {
            "is_recording": False,
            "entry_count": count,
            "elapsed_seconds": round(elapsed, 1),
            "file_path": saved_path or "",
        }

    def stop_recording(self) -> dict:
        """Stop active recording session and close all files cleanly."""
        with self._lock:
            if not self.is_recording:
                return self.get_status()
            res = self._stop_internal()

        if self.on_status_changed:
            try:
                self.on_status_changed(res)
            except Exception:
                pass
        return res

    def get_status(self) -> dict:
        """Return current recording session telemetry."""
        with self._lock:
            elapsed = max(0.0, time.monotonic() - self.record_start_mono) if self.is_recording else 0.0
            primary_path = str(self.current_file_path) if self.current_file_path else ""
            return {
                "is_recording": self.is_recording,
                "elapsed_seconds": round(elapsed, 1),
                "elapsed_formatted": time.strftime("%H:%M:%S", time.gmtime(elapsed)),
                "entry_count": self.entry_index,
                "file_path": primary_path,
                "video_path": self.active_video_path,
                "format": self.output_format,
            }
