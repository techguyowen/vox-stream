"""Transcript history tracker and subtitle exporter (SRT, VTT, TXT)."""

import datetime
import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class HistoryEntry:
    id: int
    start_time: float
    end_time: float
    text: str
    is_censored: bool = False

    def duration_seconds(self) -> float:
        return max(0.5, self.end_time - self.start_time)


class TranscriptHistory:
    """Manages session transcript history with instant subtitle format exports."""

    def __init__(self, max_entries: int = 5000):
        self.max_entries = max_entries
        self.entries: List[HistoryEntry] = []
        self._counter = 1
        self.session_start_time = time.time()

    def add_entry(self, text: str, start_time: float, end_time: Optional[float] = None, is_censored: bool = False):
        """Add a finalized transcript line to history."""
        text = text.strip()
        if not text:
            return

        if end_time is None or end_time <= start_time:
            end_time = time.time()

        entry = HistoryEntry(
            id=self._counter,
            start_time=start_time,
            end_time=end_time,
            text=text,
            is_censored=is_censored,
        )
        self._counter += 1
        self.entries.append(entry)

        if len(self.entries) > self.max_entries:
            self.entries.pop(0)

    def get_history(self, limit: int = 100, search: str = "") -> List[dict]:
        """Query recent history items with optional search filter."""
        results = []
        search_lower = search.lower().strip()

        for e in reversed(self.entries):
            if search_lower and search_lower not in e.text.lower():
                continue
            
            # Format relative time since session start
            rel_sec = int(e.start_time - self.session_start_time)
            rel_time = str(datetime.timedelta(seconds=max(0, rel_sec)))

            results.append({
                "id": e.id,
                "timestamp": e.start_time,
                "relative_time": rel_time,
                "text": e.text,
                "is_censored": e.is_censored,
            })
            if len(results) >= limit:
                break

        return results

    def clear(self):
        """Clear history and reset session time."""
        self.entries.clear()
        self._counter = 1
        self.session_start_time = time.time()

    def _format_time_srt(self, seconds: float) -> str:
        """Format seconds to SRT timecode: HH:MM:SS,mmm"""
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            millis = 999
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _format_time_vtt(self, seconds: float) -> str:
        """Format seconds to WebVTT timecode: HH:MM:SS.mmm"""
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            millis = 999
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

    def export_srt(self) -> str:
        """Export history to SubRip (.srt) subtitle format."""
        if not self.entries:
            return ""

        lines = []
        for idx, e in enumerate(self.entries, start=1):
            rel_start = e.start_time - self.session_start_time
            rel_end = max(rel_start + 1.0, e.end_time - self.session_start_time)
            
            timecode = f"{self._format_time_srt(rel_start)} --> {self._format_time_srt(rel_end)}"
            lines.append(f"{idx}\n{timecode}\n{e.text}\n")

        return "\n".join(lines)

    def export_vtt(self) -> str:
        """Export history to WebVTT (.vtt) format."""
        lines = ["WEBVTT\n"]
        for idx, e in enumerate(self.entries, start=1):
            rel_start = e.start_time - self.session_start_time
            rel_end = max(rel_start + 1.0, e.end_time - self.session_start_time)
            
            timecode = f"{self._format_time_vtt(rel_start)} --> {self._format_time_vtt(rel_end)}"
            lines.append(f"{idx}\n{timecode}\n{e.text}\n")

        return "\n".join(lines)

    def export_txt(self) -> str:
        """Export history to plain text with timestamps."""
        lines = []
        for e in self.entries:
            rel_sec = int(e.start_time - self.session_start_time)
            time_str = str(datetime.timedelta(seconds=max(0, rel_sec)))
            lines.append(f"[{time_str}] {e.text}")
        return "\n".join(lines)
