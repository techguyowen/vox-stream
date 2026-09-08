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

    def get_stats(self) -> dict:
        """Calculate live Words Per Minute (WPM), session WPM, total words, and active speaking time."""
        now = time.time()
        total_words = sum(len(e.text.split()) for e in self.entries)
        active_speaking_seconds = sum(e.duration_seconds() for e in self.entries)

        # 1. Session Average WPM (Total words / total speaking minutes)
        if active_speaking_seconds >= 2.0 and total_words > 0:
            session_wpm = round(total_words / (active_speaking_seconds / 60.0), 1)
        else:
            session_wpm = 0.0

        # 2. Live / Current Speaking Pace (Last 45-second window)
        recent_window_seconds = 45.0
        recent_entries = [e for e in self.entries if e.end_time >= (now - recent_window_seconds)]
        
        current_wpm = 0.0
        if recent_entries:
            # Check if user spoke within the last 12 seconds (otherwise speaker is currently silent/idle)
            time_since_last_speech = now - recent_entries[-1].end_time
            if time_since_last_speech <= 12.0:
                recent_words = sum(len(e.text.split()) for e in recent_entries)
                recent_speech_duration = sum(e.duration_seconds() for e in recent_entries)
                if recent_speech_duration >= 0.8:
                    current_wpm = round(recent_words / (recent_speech_duration / 60.0), 1)

        # 3. Speaking Pace Rating
        if current_wpm <= 0:
            pace_rating = "Idle"
            pace_color = "#94A3B8"
        elif current_wpm < 100:
            pace_rating = "Slow & Clear"
            pace_color = "#38BDF8"
        elif current_wpm <= 150:
            pace_rating = "Optimal (110–150 WPM)"
            pace_color = "#10B981"
        elif current_wpm <= 180:
            pace_rating = "Brisk (Fast)"
            pace_color = "#F59E0B"
        else:
            pace_rating = "Very Rapid (>180 WPM)"
            pace_color = "#EF4444"

        return {
            "current_wpm": current_wpm,
            "session_wpm": session_wpm,
            "total_words": total_words,
            "active_speaking_seconds": round(active_speaking_seconds, 1),
            "total_lines": len(self.entries),
            "pace_rating": pace_rating,
            "pace_color": pace_color,
        }

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
        """Format seconds into HH:MM:SS for YouTube chapter markers (backwards compatible)."""
        return self._format_timecode(seconds, format_style="hhmmss")

    def _format_timecode(self, seconds: float, format_style: str = "hhmmss", max_seconds: float = 0.0) -> str:
        """Format seconds into HH:MM:SS or MM:SS for YouTube chapter markers.
        
        YouTube supports both MM:SS (e.g. 00:00, 05:22) and HH:MM:SS (e.g. 00:00:00, 01:15:30).
        """
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if format_style == "mmss" or (format_style == "auto" and hours == 0 and max_seconds < 3600.0):
            total_minutes = int(seconds // 60)
            return f"{total_minutes:02d}:{secs:02d}"
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _synthesize_chapter_title(self, text: str) -> str:
        """Extract clean, meaningful chapter titles from spoken text without filler words or trailing ellipsis."""
        leading_fillers = {
            "and", "so", "because", "then", "well", "now", "you", "know", "i", "think",
            "like", "but", "or", "that", "this", "these", "those", "is", "was", "are", "were",
            "why", "we", "see", "in", "order", "to", "as", "if", "when", "today", "just",
            "really", "there", "here", "what", "about", "all", "our", "my", "your", "their",
            "his", "her", "can", "could", "have", "had", "has", "with", "from", "be", "been",
            "being", "of", "for", "the", "a", "an", "at", "by", "it", "its"
        }
        words = re.findall(r"\b[A-Za-z0-9'-]+\b", text)
        while words and words[0].lower() in leading_fillers:
            words.pop(0)

        chunk = words[:4]
        while chunk and chunk[-1].lower() in {"and", "or", "to", "in", "of", "with", "the", "a", "for", "on", "at"}:
            chunk.pop()

        if len(chunk) >= 2:
            title_core = " ".join(chunk).title()
            return f"Message: {title_core}"
        elif len(chunk) == 1:
            return f"Message: {chunk[0].title()}"
        return "Sermon Discussion"

    def generate_chapters(
        self,
        min_interval_seconds: float = 45.0,
        time_offset_seconds: float = 0.0,
        anchor: str = "first_speech",
        format_style: str = "hhmmss",
    ) -> List[dict]:
        """
        Generate YouTube-compliant timestamped chapter markers based on transcript
        cues (scripture citations, worship, prayers, topic shifts, and time blocks).

        Args:
            min_interval_seconds: Minimum seconds between consecutive chapter markers.
            time_offset_seconds: Preshow countdown offset in seconds (e.g. +300s for a 5min countdown).
            anchor: Timebase anchor: 'first_speech' (anchors relative to first spoken word) or 'session'.
            format_style: 'hhmmss' (00:00:00), 'mmss' (00:00), or 'auto'.
        """
        chapters = []

        if not self.entries:
            chapters.append({
                "seconds": 0.0,
                "timecode": self._format_timecode(0.0, format_style=format_style),
                "title": "Introduction & Welcome"
            })
            return chapters

        # Determine timebase anchor
        if anchor == "first_speech" and self.entries:
            base_time = self.entries[0].start_time
        else:
            base_time = self.session_start_time

        # Total session duration for YouTube minimum checks
        last_entry_end = max(e.end_time for e in self.entries)
        total_duration = max(0.0, last_entry_end - base_time + time_offset_seconds)

        # YouTube strictly requires the first chapter at 00:00:00 / 00:00
        if time_offset_seconds > 10.0:
            chapters.append({
                "seconds": 0.0,
                "timecode": self._format_timecode(0.0, format_style=format_style, max_seconds=total_duration),
                "title": "Preshow / Welcome"
            })
            # First actual speech entry at offset
            intro_sec = max(0.0, time_offset_seconds)
            chapters.append({
                "seconds": intro_sec,
                "timecode": self._format_timecode(intro_sec, format_style=format_style, max_seconds=total_duration),
                "title": "Introduction & Welcome"
            })
            last_chapter_time = intro_sec
        else:
            chapters.append({
                "seconds": 0.0,
                "timecode": self._format_timecode(0.0, format_style=format_style, max_seconds=total_duration),
                "title": "Introduction & Welcome"
            })
            last_chapter_time = 0.0

        canonical_books = [
            "Song of Solomon", "Song of Songs", "1 Thessalonians", "2 Thessalonians",
            "1 Corinthians", "2 Corinthians", "1 Chronicles", "2 Chronicles",
            "Ecclesiastes", "Lamentations", "Deuteronomy", "1 Timothy", "2 Timothy",
            "Philippians", "Colossians", "Zephaniah", "Zechariah", "Leviticus",
            "Nehemiah", "Habakkuk", "Ephesians", "Galatians", "Revelation",
            "Jeremiah", "Proverbs", "Numbers", "Genesis", "Exodus", "Joshua",
            "Judges", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "Matthew",
            "Ezekiel", "Philemon", "Hebrews", "Obadiah", "Malachi", "Psalms",
            "Psalm", "Esther", "Daniel", "Romans", "1 Peter", "2 Peter",
            "Isaiah", "Haggai", "Nahum", "Hosea", "Micah", "Jonah", "James",
            "1 John", "2 John", "3 John", "Titus", "Amos", "Joel", "Ruth",
            "Ezra", "Mark", "Luke", "John", "Acts", "Jude", "Job"
        ]
        books_pattern = "|".join(re.escape(b) for b in canonical_books)
        
        # Pattern 1: Book + Chapter:Verse (e.g. John 3:16, Romans 8:28-30)
        scripture_verse_pattern = re.compile(
            rf"\b(?:{books_pattern})\s+\d+[:\s]\d+(?:\s*(?:-|through|thru|to)\s*\d+)?\b",
            re.IGNORECASE
        )
        # Pattern 2: Psalms (e.g. Psalm 23, Psalms 91)
        psalm_pattern = re.compile(r"\bPsalms?\s+\d+\b", re.IGNORECASE)
        # Pattern 3: Unambiguous book with whole chapter (e.g. Romans 8, Romans chapter 8, 1 Corinthians 13, Hebrews 11)
        unambiguous_books = [b for b in canonical_books if b.lower() not in {"job", "mark", "acts", "numbers", "judges", "kings"}]
        unambiguous_books_pat = "|".join(re.escape(b) for b in unambiguous_books)
        whole_chapter_pattern = re.compile(
            rf"\b(?:{unambiguous_books_pat})\s+(?:chapter\s+)?\d+\b",
            re.IGNORECASE
        )

        scripture_seen = set()

        for e in self.entries:
            rel_sec = max(0.0, e.start_time - base_time + time_offset_seconds)
            if rel_sec - last_chapter_time < min_interval_seconds:
                continue

            # 1. Check Scripture Citations (Verse, Psalm, or Whole Chapter)
            match_verse = scripture_verse_pattern.search(e.text)
            match_psalm = psalm_pattern.search(e.text)
            match_whole = whole_chapter_pattern.search(e.text)
            scripture_match = match_verse or match_psalm or match_whole

            if scripture_match:
                citation = scripture_match.group(0).strip()
                # Clean multiple spaces
                citation = re.sub(r"\s+", " ", citation)
                if citation.lower() not in scripture_seen:
                    scripture_seen.add(citation.lower())
                    chapters.append({
                        "seconds": rel_sec,
                        "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                        "title": f"Scripture Reading ({citation})"
                    })
                    last_chapter_time = rel_sec
                    continue

            text_lower = e.text.lower()

            # 2. Sermon Outline Points
            if re.search(r"\b(?:point\s+(?:number\s+)?(?:one|1)|first\s+point)\b", text_lower):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Sermon: Point 1"
                })
                last_chapter_time = rel_sec
                continue
            elif re.search(r"\b(?:point\s+(?:number\s+)?(?:two|2)|second\s+point|secondly)\b", text_lower):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Sermon: Point 2"
                })
                last_chapter_time = rel_sec
                continue
            elif re.search(r"\b(?:point\s+(?:number\s+)?(?:three|3)|third\s+point|thirdly)\b", text_lower):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Sermon: Point 3"
                })
                last_chapter_time = rel_sec
                continue
            elif re.search(r"\b(?:point\s+(?:number\s+)?(?:four|4)|fourth\s+point)\b", text_lower):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Sermon: Point 4"
                })
                last_chapter_time = rel_sec
                continue
            elif re.search(r"\b(?:main\s+takeaway|key\s+takeaway|the\s+big\s+idea)\b", text_lower):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Sermon: Key Takeaway"
                })
                last_chapter_time = rel_sec
                continue

            # 3. Liturgical Service Cues
            if any(k in text_lower for k in [
                "tithes and offerings", "tithe and offering", "morning offering", "time of giving",
                "worship through giving", "ushers please come", "ways to give", "receive the offering"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Tithes & Offering"
                })
                last_chapter_time = rel_sec
            elif any(k in text_lower for k in [
                "communion", "lord's supper", "the lord's table", "take the bread",
                "cup of the new covenant", "in remembrance of me"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Holy Communion"
                })
                last_chapter_time = rel_sec
            elif any(k in text_lower for k in [
                "altar call", "every head bowed", "surrender your life", "surrender your heart",
                "accept jesus", "come to the altar", "prayer ministry", "decision time"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Altar Call & Ministry"
                })
                last_chapter_time = rel_sec
            elif any(k in text_lower for k in [
                "praise and worship", "worship the lord", "worship team", "let us worship",
                "let's worship", "sing praises", "sing together", "lift our voices", "stand and sing", "stand and worship"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Praise & Worship"
                })
                last_chapter_time = rel_sec
            elif any(k in text_lower for k in [
                "let us pray", "let's pray", "opening prayer", "bow our heads", "join me in prayer",
                "pray with me", "father we come before you", "heavenly father we pray", "lift our prayers"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Prayer & Invocation"
                })
                last_chapter_time = rel_sec
            elif any(k in text_lower for k in [
                "turn with me to", "today's message", "sermon title", "our topic today",
                "the message today", "open your bibles", "god's word today"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Sermon Message"
                })
                last_chapter_time = rel_sec
            elif any(k in text_lower for k in [
                "a few announcements", "church announcements", "upcoming events", "next sunday"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Welcome & Announcements"
                })
                last_chapter_time = rel_sec
            elif any(k in text_lower for k in [
                "in conclusion", "closing prayer", "benediction", "go in peace",
                "have a blessed week", "may the lord bless you", "you are dismissed"
            ]):
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Benediction & Closing"
                })
                last_chapter_time = rel_sec
            elif rel_sec - last_chapter_time >= max(300.0, min_interval_seconds * 3):
                # 4. Synthesize intelligent clean title without ellipsis or leading filler words
                summary_title = self._synthesize_chapter_title(e.text)
                chapters.append({
                    "seconds": rel_sec,
                    "timecode": self._format_timecode(rel_sec, format_style=format_style, max_seconds=total_duration),
                    "title": summary_title
                })
                last_chapter_time = rel_sec

        # 5. YouTube 3-Chapter Minimum Compliance Guarantee
        # YouTube requires at least 3 chapters in ascending order to activate video player chapters.
        if total_duration >= 60.0 and len(chapters) < 3:
            if len(chapters) == 1:
                mid_sec = max(30.0, total_duration * 0.45)
                end_sec = max(mid_sec + 20.0, total_duration * 0.90)
                chapters.append({
                    "seconds": mid_sec,
                    "timecode": self._format_timecode(mid_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Message & Teaching"
                })
                chapters.append({
                    "seconds": end_sec,
                    "timecode": self._format_timecode(end_sec, format_style=format_style, max_seconds=total_duration),
                    "title": "Closing Remarks"
                })
            elif len(chapters) == 2:
                sec2 = chapters[1]["seconds"]
                if total_duration - sec2 >= 30.0:
                    end_sec = max(sec2 + 20.0, total_duration * 0.90)
                    chapters.append({
                        "seconds": end_sec,
                        "timecode": self._format_timecode(end_sec, format_style=format_style, max_seconds=total_duration),
                        "title": "Closing Remarks"
                    })
                elif sec2 >= 60.0:
                    mid_sec = sec2 / 2.0
                    chapters.insert(1, {
                        "seconds": mid_sec,
                        "timecode": self._format_timecode(mid_sec, format_style=format_style, max_seconds=total_duration),
                        "title": "Message Discussion"
                    })

        return chapters

    def export_youtube_chapters(
        self,
        min_interval_seconds: float = 45.0,
        time_offset_seconds: float = 0.0,
        anchor: str = "first_speech",
        format_style: str = "hhmmss",
    ) -> str:
        """Export formatted YouTube chapter markers."""
        chapters = self.generate_chapters(
            min_interval_seconds=min_interval_seconds,
            time_offset_seconds=time_offset_seconds,
            anchor=anchor,
            format_style=format_style,
        )
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
