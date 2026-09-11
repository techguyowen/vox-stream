"""AI Sermon Summary & Semantic YouTube Chapters Engine.

Supports multi-provider execution:
1. Google Gemini Flash (Cloud AI Studio, free unmetered tier)
2. Local Ollama (Self-hosted private LLM, e.g. Llama 3)
3. Built-in Offline Heuristics (100% local, no API key, church lexicon & scripture detection)
"""

import asyncio
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .church_lexicon import ChurchLexiconFormatter
from .config import SummaryConfig
from .history import HistoryEntry, TranscriptHistory

logger = logging.getLogger("obs_captioner.summary_engine")


class SermonSummaryEngine:
    """Generates structured sermon recaps and YouTube-compliant chapters."""

    def __init__(self, config: Optional[SummaryConfig] = None, history: Optional[TranscriptHistory] = None):
        self.config = config or SummaryConfig()
        self.history = history

    def get_api_key(self) -> str:
        """Retrieve Gemini API key from summary config, general env, or GeminiLiveConfig."""
        if self.config.gemini_api_key and self.config.gemini_api_key.strip():
            return self.config.gemini_api_key.strip()
        if os.environ.get("GEMINI_API_KEY"):
            return os.environ["GEMINI_API_KEY"].strip()
        return ""

    def get_status(self) -> Dict[str, Any]:
        """Check provider readiness."""
        api_key = self.get_api_key()
        has_gemini = bool(api_key and len(api_key) > 5)
        return {
            "gemini_available": has_gemini,
            "ollama_available": False,  # Probed asynchronously if needed
            "heuristic_available": True,
            "default_provider": self.config.provider,
            "gemini_model": self.config.gemini_model,
            "ollama_model": self.config.ollama_model,
        }

    # -------------------------------------------------------------------------
    # YouTube Chapter Generation
    # -------------------------------------------------------------------------

    async def generate_ai_chapters(
        self,
        entries: Optional[List[HistoryEntry]] = None,
        min_interval_seconds: float = 45.0,
        time_offset_seconds: float = 0.0,
        anchor: str = "first_speech",
        format_style: str = "hhmmss",
        provider_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate semantic, YouTube-compliant timestamped chapters."""
        active_entries = entries or (self.history.entries if self.history else [])
        if not active_entries:
            return {
                "chapters": [{
                    "seconds": 0.0,
                    "timecode": "00:00:00" if format_style == "hhmmss" else "00:00",
                    "title": "Welcome & Opening"
                }],
                "formatted": "00:00:00 - Welcome & Opening",
                "provider_used": "none",
                "count": 1,
                "youtube_compliant": False,
            }

        provider = provider_override or self.config.provider
        api_key = self.get_api_key()

        # Decide provider
        selected_provider = "heuristic"
        if provider == "gemini" or (provider == "auto" and api_key):
            selected_provider = "gemini"
        elif provider == "ollama":
            selected_provider = "ollama"

        chapters = None
        provider_used = "heuristic"

        if selected_provider == "gemini":
            try:
                chapters = await self._generate_gemini_chapters(
                    active_entries,
                    min_interval_seconds=min_interval_seconds,
                    time_offset_seconds=time_offset_seconds,
                    anchor=anchor,
                    format_style=format_style,
                )
                if chapters and len(chapters) >= 3:
                    provider_used = "gemini"
            except Exception as e:
                logger.warning(f"Gemini chapter generation failed, falling back to heuristics: {e}")
                chapters = None

        elif selected_provider == "ollama":
            try:
                chapters = await self._generate_ollama_chapters(
                    active_entries,
                    min_interval_seconds=min_interval_seconds,
                    time_offset_seconds=time_offset_seconds,
                    anchor=anchor,
                    format_style=format_style,
                )
                if chapters and len(chapters) >= 3:
                    provider_used = "ollama"
            except Exception as e:
                logger.warning(f"Ollama chapter generation failed, falling back to heuristics: {e}")
                chapters = None

        # Fallback to smart heuristics if AI provider failed or was chosen
        if not chapters:
            provider_used = "heuristic"
            chapters = self._generate_heuristic_chapters(
                active_entries,
                min_interval_seconds=min_interval_seconds,
                time_offset_seconds=time_offset_seconds,
                anchor=anchor,
                format_style=format_style,
            )

        # Enforce strict YouTube compliance
        chapters = self._ensure_youtube_compliance(chapters, format_style=format_style)
        formatted = "\n".join(f"{c['timecode']} - {c['title']}" for c in chapters)

        return {
            "chapters": chapters,
            "formatted": formatted,
            "provider_used": provider_used,
            "count": len(chapters),
            "youtube_compliant": len(chapters) >= 3 and chapters[0]["seconds"] == 0.0,
            "min_interval": min_interval_seconds,
            "offset": time_offset_seconds,
            "anchor": anchor,
            "format": format_style,
        }

    # -------------------------------------------------------------------------
    # Sermon Summary & Bulletin Generation
    # -------------------------------------------------------------------------

    async def generate_sermon_summary(
        self,
        entries: Optional[List[HistoryEntry]] = None,
        provider_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate structured sermon summary, bulletin outline, and discussion questions."""
        active_entries = entries or (self.history.entries if self.history else [])
        if not active_entries:
            return {
                "title": "No Spoken Transcript Recorded",
                "scriptures": [],
                "big_idea": "No speech entries were found in the current session.",
                "key_points": [],
                "quotes": [],
                "discussion_questions": [],
                "markdown": "## 📖 Sermon Summary\n*No spoken transcript recorded in this session.*",
                "youtube_description": "",
                "bulletin_text": "",
                "provider_used": "none",
            }

        provider = provider_override or self.config.provider
        api_key = self.get_api_key()

        selected_provider = "heuristic"
        if provider == "gemini" or (provider == "auto" and api_key):
            selected_provider = "gemini"
        elif provider == "ollama":
            selected_provider = "ollama"

        summary_data = None
        provider_used = "heuristic"

        if selected_provider == "gemini":
            try:
                summary_data = await self._generate_gemini_summary(active_entries)
                if summary_data and summary_data.get("big_idea"):
                    provider_used = "gemini"
            except Exception as e:
                logger.warning(f"Gemini sermon summary failed, falling back to heuristics: {e}")
                summary_data = None

        elif selected_provider == "ollama":
            try:
                summary_data = await self._generate_ollama_summary(active_entries)
                if summary_data and summary_data.get("big_idea"):
                    provider_used = "ollama"
            except Exception as e:
                logger.warning(f"Ollama sermon summary failed, falling back to heuristics: {e}")
                summary_data = None

        if not summary_data:
            provider_used = "heuristic"
            summary_data = self._generate_heuristic_summary(active_entries)

        summary_data["provider_used"] = provider_used
        self._format_summary_outputs(summary_data)
        return summary_data

    # -------------------------------------------------------------------------
    # Gemini AI Providers
    # -------------------------------------------------------------------------

    async def _generate_gemini_chapters(
        self,
        entries: List[HistoryEntry],
        min_interval_seconds: float,
        time_offset_seconds: float,
        anchor: str,
        format_style: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Prompt Gemini Flash for semantic YouTube chapters."""
        api_key = self.get_api_key()
        if not api_key:
            return None

        transcript_text, base_time = self._format_transcript_for_prompt(
            entries, anchor=anchor, time_offset_seconds=time_offset_seconds
        )

        prompt = (
            "You are an expert church media and video director. Analyze this live sermon transcript with timestamps.\n"
            "Generate YouTube-compliant video chapters. Follow these strict rules:\n"
            "1. First chapter MUST start at 00:00:00 (or 00:00) with a title like 'Welcome & Opening Prayer' or 'Welcome & Praise'.\n"
            "2. Timestamps must be strictly ascending.\n"
            "3. Space chapters at least 2 to 5 minutes apart.\n"
            "4. Total chapters between 4 and 10.\n"
            "5. Titles must be concise, engaging, and professional (under 45 characters), capturing topic shifts, scripture readings, and worship.\n"
            "Output ONLY valid JSON array with objects containing 'timecode' and 'title', e.g.:\n"
            '[{"timecode": "00:00:00", "title": "Welcome & Opening"}, {"timecode": "00:04:15", "title": "Scripture Reading (Romans 8)"}]\n\n'
            f"TRANSCRIPT:\n{transcript_text[:12000]}"
        )

        response_text = await self._call_gemini_api(prompt, api_key=api_key, model=self.config.gemini_model)
        if not response_text:
            return None

        # Parse JSON from response
        chapters_raw = self._extract_json_array(response_text)
        if not chapters_raw:
            return None

        chapters = []
        for c in chapters_raw:
            tc = str(c.get("timecode", "")).strip()
            title = str(c.get("title", "")).strip()
            sec = self._parse_timecode_seconds(tc)
            if title:
                chapters.append({
                    "seconds": sec,
                    "timecode": tc,
                    "title": title[:50]
                })

        return chapters if len(chapters) >= 3 else None

    async def _generate_gemini_summary(self, entries: List[HistoryEntry]) -> Optional[Dict[str, Any]]:
        """Prompt Gemini Flash for a comprehensive sermon recap."""
        api_key = self.get_api_key()
        if not api_key:
            return None

        transcript_text, _ = self._format_transcript_for_prompt(entries, anchor="first_speech", time_offset_seconds=0.0)

        prompt = (
            "You are an expert church pastor and communications director. Analyze this sermon transcript.\n"
            "Generate a high-quality sermon recap package. Output ONLY a valid JSON object with the following keys:\n"
            "{\n"
            '  "title": "A captivating, biblical sermon title",\n'
            '  "scriptures": ["Romans 8:28-39", "Matthew 5:14-16"],\n'
            '  "big_idea": "One or two sentences capturing the central message of the sermon",\n'
            '  "key_points": [\n'
            '    {"timecode": "00:14:20", "title": "Point 1 Title", "description": "Brief 1-2 sentence explanation"},\n'
            '    {"timecode": "00:27:45", "title": "Point 2 Title", "description": "Brief 1-2 sentence explanation"}\n'
            "  ],\n"
            '  "quotes": ["1 to 3 memorable or inspiring spoken quotes from the transcript"],\n'
            '  "discussion_questions": ["3 to 4 thoughtful small group application questions"]\n'
            "}\n\n"
            f"TRANSCRIPT:\n{transcript_text[:14000]}"
        )

        response_text = await self._call_gemini_api(prompt, api_key=api_key, model=self.config.gemini_model)
        if not response_text:
            return None

        summary_json = self._extract_json_object(response_text)
        return summary_json

    async def _call_gemini_api(self, prompt: str, api_key: str, model: str = "gemini-2.0-flash") -> Optional[str]:
        """Execute Gemini completion with google.genai or direct HTTPS fallback."""
        loop = asyncio.get_event_loop()

        # Try google.genai SDK if available
        def _run_sdk() -> Optional[str]:
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                logger.debug(f"google.genai SDK failed, trying REST API fallback: {e}")
            return None

        result = await loop.run_in_executor(None, _run_sdk)
        if result:
            return result

        # Direct HTTPS REST API Fallback
        def _run_rest() -> Optional[str]:
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 2048,
                }
            }
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                resp_bytes = resp.read()
                data = json.loads(resp_bytes.decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
            return None

        try:
            return await loop.run_in_executor(None, _run_rest)
        except Exception as e:
            logger.error(f"Gemini REST call failed: {e}")
            return None

    # -------------------------------------------------------------------------
    # Ollama Local Providers
    # -------------------------------------------------------------------------

    async def _generate_ollama_chapters(
        self,
        entries: List[HistoryEntry],
        min_interval_seconds: float,
        time_offset_seconds: float,
        anchor: str,
        format_style: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Prompt local Ollama for YouTube chapters."""
        transcript_text, _ = self._format_transcript_for_prompt(
            entries, anchor=anchor, time_offset_seconds=time_offset_seconds
        )
        prompt = (
            "Analyze this sermon transcript with timestamps. Output a JSON array of YouTube chapters starting at 00:00:00.\n"
            "Format: [{\"timecode\": \"00:00:00\", \"title\": \"Welcome\"}, ...]\n\n"
            f"{transcript_text[:8000]}"
        )
        response_text = await self._call_ollama_api(prompt)
        if not response_text:
            return None
        chapters_raw = self._extract_json_array(response_text)
        if not chapters_raw:
            return None
        chapters = []
        for c in chapters_raw:
            tc = str(c.get("timecode", "")).strip()
            title = str(c.get("title", "")).strip()
            sec = self._parse_timecode_seconds(tc)
            if title:
                chapters.append({"seconds": sec, "timecode": tc, "title": title[:50]})
        return chapters if len(chapters) >= 3 else None

    async def _generate_ollama_summary(self, entries: List[HistoryEntry]) -> Optional[Dict[str, Any]]:
        """Prompt local Ollama for sermon summary."""
        transcript_text, _ = self._format_transcript_for_prompt(entries, anchor="first_speech", time_offset_seconds=0.0)
        prompt = (
            "Analyze this sermon transcript. Return a JSON object with keys: title, scriptures, big_idea, key_points, quotes, discussion_questions.\n\n"
            f"{transcript_text[:8000]}"
        )
        response_text = await self._call_ollama_api(prompt)
        if not response_text:
            return None
        return self._extract_json_object(response_text)

    async def _call_ollama_api(self, prompt: str) -> Optional[str]:
        """Execute local Ollama completion."""
        loop = asyncio.get_event_loop()

        def _run_sync():
            url = f"{self.config.ollama_url.rstrip('/')}/api/generate"
            payload = {
                "model": self.config.ollama_model,
                "prompt": prompt,
                "stream": False,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", "")

        try:
            return await loop.run_in_executor(None, _run_sync)
        except Exception as e:
            logger.debug(f"Ollama call failed: {e}")
            return None

    # -------------------------------------------------------------------------
    # Offline Smart Heuristics Fallback (100% Local, Zero API Key)
    # -------------------------------------------------------------------------

    def _generate_heuristic_chapters(
        self,
        entries: List[HistoryEntry],
        min_interval_seconds: float,
        time_offset_seconds: float,
        anchor: str,
        format_style: str,
    ) -> List[Dict[str, Any]]:
        """Leverage history.generate_chapters() with enhanced church lexicon awareness."""
        if not self.history:
            temp_history = TranscriptHistory()
            for e in entries:
                temp_history.add_entry(e.text, start_time=e.start_time, end_time=e.end_time, is_censored=e.is_censored)
            return temp_history.generate_chapters(
                min_interval_seconds=min_interval_seconds,
                time_offset_seconds=time_offset_seconds,
                anchor=anchor,
                format_style=format_style,
            )
        return self.history.generate_chapters(
            min_interval_seconds=min_interval_seconds,
            time_offset_seconds=time_offset_seconds,
            anchor=anchor,
            format_style=format_style,
        )

    def _generate_heuristic_summary(self, entries: List[HistoryEntry]) -> Dict[str, Any]:
        """Analyze sermon using regex patterns, canonical Bible books, and spoken outline cues."""
        base_time = entries[0].start_time if entries else 0.0

        # 1. Extract Scripture Citations
        scriptures = self._extract_scripture_citations(entries)

        # 2. Extract Sermon Points
        key_points = self._extract_sermon_points(entries, base_time)

        # 3. Derive Sermon Title & Big Idea
        title, big_idea = self._derive_title_and_big_idea(entries, scriptures)

        # 4. Extract Memorable Spoken Quotes
        quotes = self._extract_notable_quotes(entries)

        # 5. Generate Small Group Questions
        questions = self._generate_discussion_questions(scriptures, title)

        return {
            "title": title,
            "scriptures": scriptures,
            "big_idea": big_idea,
            "key_points": key_points,
            "quotes": quotes,
            "discussion_questions": questions,
        }

    # -------------------------------------------------------------------------
    # Heuristic Extraction Utilities
    # -------------------------------------------------------------------------

    def _extract_scripture_citations(self, entries: List[HistoryEntry]) -> List[str]:
        """Scan entries for canonical scripture verses."""
        books_regex = "|".join(re.escape(b) for b in ChurchLexiconFormatter.BOOKS_OF_BIBLE.keys())
        pattern = re.compile(
            rf"\b({books_regex})\s+(?:chapter\s+)?(\d+)(?:(?::|\s+verse\s+)(\d+(?:[-–]\d+)?))?\b",
            re.IGNORECASE,
        )
        seen = set()
        citations = []
        for e in entries:
            for match in pattern.finditer(e.text):
                book_raw = match.group(1).lower()
                canonical_book = ChurchLexiconFormatter.BOOKS_OF_BIBLE.get(book_raw, book_raw.title())
                chapter = match.group(2)
                verse = match.group(3)
                if verse:
                    citation = f"{canonical_book} {chapter}:{verse}"
                else:
                    citation = f"{canonical_book} {chapter}"
                if citation.lower() not in seen:
                    seen.add(citation.lower())
                    citations.append(citation)
        return citations[:8]

    def _extract_sermon_points(self, entries: List[HistoryEntry], base_time: float) -> List[Dict[str, str]]:
        """Identify distinct sermon outline points from spoken markers."""
        points = []
        point_pattern = re.compile(
            r"\b(?:point\s+(?:number\s+)?([1-5]|one|two|three|four|five)|first\s+point|second\s+point|third\s+point|firstly|secondly|thirdly|the\s+takeaway|key\s+takeaway|lesson\s+([1-5]))\b",
            re.IGNORECASE,
        )
        for e in entries:
            match = point_pattern.search(e.text)
            if match:
                rel_sec = max(0, int(e.start_time - base_time))
                timecode = f"{rel_sec // 3600:02d}:{(rel_sec % 3600) // 60:02d}:{rel_sec % 60:02d}"
                desc = e.text.strip()
                points.append({
                    "timecode": timecode,
                    "title": f"Key Teaching Point ({timecode})",
                    "description": desc[:140] + ("..." if len(desc) > 140 else ""),
                })
                if len(points) >= 5:
                    break

        if not points and entries:
            stride = max(1, len(entries) // 3)
            for i in range(0, len(entries), stride):
                e = entries[i]
                rel_sec = max(0, int(e.start_time - base_time))
                timecode = f"{rel_sec // 3600:02d}:{(rel_sec % 3600) // 60:02d}:{rel_sec % 60:02d}"
                points.append({
                    "timecode": timecode,
                    "title": f"Teaching Section ({timecode})",
                    "description": e.text[:120] + "...",
                })
                if len(points) >= 3:
                    break
        return points

    def _derive_title_and_big_idea(self, entries: List[HistoryEntry], scriptures: List[str]) -> Tuple[str, str]:
        """Derive sermon title and core takeaway."""
        title_cue = re.compile(r"\b(?:today'?s\s+message|the\s+title\s+of\s+the\s+sermon|sermon\s+title|we\s+are\s+talking\s+about)\s+(?:is\s+)?([A-Za-z0-9\s',-]+)", re.IGNORECASE)
        for e in entries:
            m = title_cue.search(e.text)
            if m:
                extracted = m.group(1).strip()
                words = extracted.split()[:6]
                if words:
                    title = " ".join(words).title()
                    return title, f"Living out God's word with conviction and faith through {title}."

        if scriptures:
            primary_scrip = scriptures[0]
            return f"Walking in Faith: {primary_scrip}", f"Trusting God's promises and responding to Christ's calling through {primary_scrip}."

        return "Sunday Message & Reflection", "Applying God's truth to our everyday lives and walking in faithful community."

    def _extract_notable_quotes(self, entries: List[HistoryEntry]) -> List[str]:
        """Find inspiring, punchy sentences that serve as memorable quotes."""
        candidates = []
        for e in entries:
            txt = e.text.strip()
            word_count = len(txt.split())
            if 8 <= word_count <= 25 and not any(txt.lower().startswith(p) for p in ["and", "so", "um", "uh", "okay"]):
                if any(w in txt.lower() for w in ["god", "faith", "hope", "love", "grace", "trust", "prayer", "cross", "jesus", "christ", "glory"]):
                    candidates.append(f'"{txt}"')
                    if len(candidates) >= 2:
                        break
        return candidates or ['"Faith is not the absence of doubt, but trusting God through the storm."']

    def _generate_discussion_questions(self, scriptures: List[str], title: str) -> List[str]:
        """Generate small group questions."""
        primary_ref = scriptures[0] if scriptures else "today's message"
        return [
            f"What stood out to you most from {primary_ref} during today's service?",
            f"How does the message of '{title}' challenge how you live your everyday life this week?",
            "In what area of your life is God calling you to step out in deeper faith or trust?",
            "How can our community or small group pray for and support you this week?",
        ]

    # -------------------------------------------------------------------------
    # Formatters & Compliance
    # -------------------------------------------------------------------------

    def _format_summary_outputs(self, data: Dict[str, Any]):
        """Generate ready-to-copy Markdown, YouTube Description, and Bulletin text."""
        title = data.get("title", "Sunday Message")
        scriptures = ", ".join(data.get("scriptures", [])) or "Scripture Reading"
        big_idea = data.get("big_idea", "")
        key_points = data.get("key_points", [])
        quotes = data.get("quotes", [])
        questions = data.get("discussion_questions", [])

        # 1. Full Markdown
        md_lines = [
            f"# 📖 {title}",
            f"**Primary Scripture:** {scriptures}",
            f"**The Big Idea:** {big_idea}",
            "",
            "### 📌 Key Teaching Points",
        ]
        for p in key_points:
            md_lines.append(f"- **[{p.get('timecode', '00:00:00')}] {p.get('title', '')}**")
            if p.get("description"):
                md_lines.append(f"  {p.get('description')}")
        md_lines.append("")

        if quotes:
            md_lines.append("### 💬 Notable Quotes")
            for q in quotes:
                md_lines.append(f"> {q}")
            md_lines.append("")

        if questions:
            md_lines.append("### 🤝 Small Group Discussion Questions")
            for idx, q in enumerate(questions, 1):
                md_lines.append(f"{idx}. {q}")
            md_lines.append("")

        data["markdown"] = "\n".join(md_lines)

        # 2. YouTube Description Block
        yt_lines = [
            title,
            "",
            big_idea,
            "",
            f"Scriptures: {scriptures}",
            "",
            "TIMESTAMPS:",
        ]
        for p in key_points:
            yt_lines.append(f"{p.get('timecode', '00:00:00')} - {p.get('title', '')}")
        data["youtube_description"] = "\n".join(yt_lines)

        # 3. Compact Bulletin / Newsletter Block
        bulletin_lines = [
            f"SERMON RECAP: {title.upper()}",
            f"Key Scripture: {scriptures}",
            "",
            f"Main Idea: {big_idea}",
            "",
            "Key Points:",
        ]
        for idx, p in enumerate(key_points, 1):
            bulletin_lines.append(f"{idx}. {p.get('title', '')} — {p.get('description', '')}")
        data["bulletin_text"] = "\n".join(bulletin_lines)

    def _ensure_youtube_compliance(self, chapters: List[Dict[str, Any]], format_style: str = "hhmmss") -> List[Dict[str, Any]]:
        """Guarantee YouTube chapters rule: strictly ascending, >= 3 chapters, first at 00:00:00."""
        if not chapters:
            zero_tc = "00:00:00" if format_style == "hhmmss" else "00:00"
            return [{"seconds": 0.0, "timecode": zero_tc, "title": "Welcome & Opening"}]

        sorted_chapters = sorted(chapters, key=lambda c: c.get("seconds", 0.0))

        first = sorted_chapters[0]
        if first.get("seconds", 0.0) > 0.0:
            zero_tc = "00:00:00" if format_style == "hhmmss" else "00:00"
            sorted_chapters.insert(0, {
                "seconds": 0.0,
                "timecode": zero_tc,
                "title": "Welcome & Opening"
            })

        while len(sorted_chapters) < 3:
            last_sec = sorted_chapters[-1].get("seconds", 0.0)
            next_sec = last_sec + 180.0
            next_tc = self._format_seconds(next_sec, format_style=format_style)
            title = "Sermon Teaching" if len(sorted_chapters) == 1 else "Closing Benediction"
            sorted_chapters.append({
                "seconds": next_sec,
                "timecode": next_tc,
                "title": title
            })

        return sorted_chapters

    def _format_transcript_for_prompt(
        self, entries: List[HistoryEntry], anchor: str, time_offset_seconds: float
    ) -> Tuple[str, float]:
        """Compress transcript into formatted timestamped blocks."""
        if not entries:
            return "", 0.0
        base_time = entries[0].start_time if anchor == "first_speech" else (self.history.session_start_time if self.history else entries[0].start_time)

        lines = []
        for e in entries:
            rel_sec = max(0, int(e.start_time - base_time + time_offset_seconds))
            tc = f"{rel_sec // 3600:02d}:{(rel_sec % 3600) // 60:02d}:{rel_sec % 60:02d}"
            lines.append(f"[{tc}] {e.text}")
        return "\n".join(lines), base_time

    def _format_seconds(self, seconds: float, format_style: str = "hhmmss") -> str:
        sec = max(0, int(seconds))
        if format_style == "mmss":
            return f"{sec // 60:02d}:{sec % 60:02d}"
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"

    def _parse_timecode_seconds(self, tc: str) -> float:
        """Parse HH:MM:SS or MM:SS into total seconds."""
        parts = tc.strip().split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
        except Exception:
            pass
        return 0.0

    def _extract_json_array(self, text: str) -> Optional[List[Dict[str, Any]]]:
        """Extract JSON array from LLM response text."""
        match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        return None

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON object from LLM response text."""
        match = re.search(r"\{\s*\".*\"\s*:.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        return None
