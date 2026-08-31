"""Transcript history tracker and subtitle exporter (SRT, VTT, TXT)."""

import datetime
import re
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

    def _format_time_hhmmss(self, seconds: float) -> str:
        """Format seconds into HH:MM:SS for YouTube chapter markers."""
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def generate_chapters(self, min_interval_seconds: float = 45.0) -> List[dict]:
        """
        Generate YouTube-compliant timestamped chapter markers based on transcript
        cues (scripture citations, worship, prayers, topic shifts, and time blocks).
        """
        chapters = []
        # YouTube requires first chapter at 00:00:00
        chapters.append({
            "seconds": 0.0,
            "timecode": "00:00:00",
            "title": "Introduction & Welcome"
        })

        if not self.entries:
            return chapters

        scripture_pattern = re.compile(
            r'\b(?:Genesis|Exodus|Leviticus|Numbers|Deuteronomy|Joshua|Judges|Ruth|1 Samuel|2 Samuel|'
            r'1 Kings|2 Kings|1 Chronicles|2 Chronicles|Ezra|Nehemiah|Esther|Job|Psalms?|Proverbs|'
            r'Ecclesiastes|Song of Solomon|Isaiah|Jeremiah|Lamentations|Ezekiel|Daniel|Hosea|Joel|'
            r'Amos|Obadiah|Jonah|Micah|Nahum|Habakkuk|Zephaniah|Haggai|Zechariah|Malachi|Matthew|'
            r'Mark|Luke|John|Acts|Romans|1 Corinthians|2 Corinthians|Galatians|Ephesians|Philippians|'
            r'Colossians|1 Thessalonians|2 Thessalonians|1 Timothy|2 Timothy|Titus|Philemon|Hebrews|'
            r'James|1 Peter|2 Peter|1 John|2 John|3 John|Jude|Revelation)\s+\d+[:\s]\d+(?:-\d+)?\b',
            re.IGNORECASE
        )

        last_chapter_time = 0.0
        scripture_seen = set()

        for e in self.entries:
            rel_sec = max(0.0, e.start_time - self.session_start_time)
            
            # Check scripture citations
            match = scripture_pattern.search(e.text)
            if match and (rel_sec - last_chapter_time >= min_interval_seconds):
                citation = match.group(0).strip()
                if citation.lower() not in scripture_seen:
                    scripture_seen.add(citation.lower())
                    chapters.append({
                        "seconds": rel_sec,
                        "timecode": self._format_time_hhmmss(rel_sec),
                        "title": f"Scripture Reading ({citation})"
                    })
                    last_chapter_time = rel_sec
                    continue

            # Check key phrases (prayer, worship, sermon, offering, conclusion)
            text_lower = e.text.lower()
            if rel_sec - last_chapter_time >= min_interval_seconds:
                if any(k in text_lower for k in ["let us pray", "let's pray", "opening prayer", "bow our heads"]):
                    chapters.append({
                        "seconds": rel_sec,
                        "timecode": self._format_time_hhmmss(rel_sec),
                        "title": "Prayer & Invocation"
                    })
                    last_chapter_time = rel_sec
                elif any(k in text_lower for k in ["turn with me to", "today's message", "sermon title", "our topic today"]):
                    chapters.append({
                        "seconds": rel_sec,
                        "timecode": self._format_time_hhmmss(rel_sec),
                        "title": "Sermon Message"
                    })
                    last_chapter_time = rel_sec
                elif any(k in text_lower for k in ["in conclusion", "closing prayer", "benediction", "go in peace", "have a blessed week"]):
                    chapters.append({
                        "seconds": rel_sec,
                        "timecode": self._format_time_hhmmss(rel_sec),
                        "title": "Benediction & Closing"
                    })
                    last_chapter_time = rel_sec
                elif rel_sec - last_chapter_time >= 300.0:  # 5-minute periodic chunk
                    words = e.text.split()
                    summary_title = " ".join(words[:5]).capitalize() + "..." if len(words) >= 5 else "Session Discussion"
                    chapters.append({
                        "seconds": rel_sec,
                        "timecode": self._format_time_hhmmss(rel_sec),
                        "title": summary_title
                    })
                    last_chapter_time = rel_sec

        return chapters

    def export_youtube_chapters(self) -> str:
        """Export formatted YouTube chapter markers."""
        chapters = self.generate_chapters()
        lines = [f"{c['timecode']} - {c['title']}" for c in chapters]
        return "\n".join(lines)

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
