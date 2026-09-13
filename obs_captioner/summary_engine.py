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

    def __init__(
        self,
        config: Optional[SummaryConfig] = None,
        history: Optional[TranscriptHistory] = None,
        app_config: Optional[Any] = None,
    ):
        self.config = config or SummaryConfig()
        self.history = history
        self.app_config = app_config

    def get_api_key(self) -> str:
        """Retrieve Gemini API key from summary config, app_config, general env, or GeminiLiveConfig."""
        if self.config.gemini_api_key and self.config.gemini_api_key.strip():
            return self.config.gemini_api_key.strip()
        if os.environ.get("GEMINI_API_KEY") and os.environ["GEMINI_API_KEY"].strip():
            return os.environ["GEMINI_API_KEY"].strip()
        if self.app_config and hasattr(self.app_config, "gemini_live") and getattr(self.app_config.gemini_live, "api_key", None):
            return self.app_config.gemini_live.api_key.strip()
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
        """Prompt Gemini Flash for semantic YouTube chapters across the entire recording."""
        api_key = self.get_api_key()
        if not api_key:
            return None

        transcript_text, base_time = self._format_transcript_for_prompt(
            entries, anchor=anchor, time_offset_seconds=time_offset_seconds
        )

        church_context = ""
        if self.app_config:
            church_name = (getattr(getattr(self.app_config, "general", None), "church_name", "") or "").strip()
            church_mode = getattr(getattr(self.app_config, "general", None), "church_mode", True)
            if church_mode:
                church_context = f" This is a church worship service at {church_name}." if church_name else " This is a church worship service."
                church_context += " Identify scripture citations, sermon main points, prayers, worship, and benediction."

        prompt = (
            f"You are an expert church media and video director. Analyze this COMPLETE live sermon transcript with timestamps.{church_context}\n"
            "Generate YouTube-compliant video chapters distributed across the entire duration of the service from beginning to end.\n"
            "Follow these strict rules:\n"
            "1. First chapter MUST start at 00:00:00 (or 00:00) with a title like 'Welcome & Opening' or 'Welcome & Praise'.\n"
            "2. Timestamps must be strictly ascending.\n"
            "3. Space chapters evenly across the entire recording (typically 2 to 6 minutes apart).\n"
            "4. Total chapters between 4 and 12, reflecting major movements of the service (Worship, Welcome, Scripture Reading, Sermon Points, Invitation, Benediction).\n"
            "5. Titles must be concise, engaging, and professional (under 45 characters), capturing topic shifts, scripture readings, and worship.\n"
            "Output ONLY valid JSON array with objects containing 'timecode' and 'title', e.g.:\n"
            '[{"timecode": "00:00:00", "title": "Welcome & Opening"}, {"timecode": "00:04:15", "title": "Scripture Reading (Romans 8)"}]\n\n'
            f"COMPLETE TRANSCRIPT:\n{transcript_text}"
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
        """Prompt Gemini Flash for a comprehensive, descriptive, and actionable sermon recap."""
        api_key = self.get_api_key()
        if not api_key:
            return None

        transcript_text, _ = self._format_transcript_for_prompt(entries, anchor="first_speech", time_offset_seconds=0.0)

        church_context = ""
        if self.app_config:
            church_name = (getattr(getattr(self.app_config, "general", None), "church_name", "") or "").strip()
            church_mode = getattr(getattr(self.app_config, "general", None), "church_mode", True)
            if church_mode:
                church_context = f" at {church_name}" if church_name else ""
                church_context = f"This is a live church worship service and sermon{church_context}. "

        prompt = (
            f"You are an expert theologian, pastor, and church communications director.\n"
            f"{church_context}Analyze the following COMPLETE sermon transcript from beginning to end.\n\n"
            "Generate an in-depth, descriptive, highly practical sermon recap and study guide.\n"
            "Do NOT provide brief, superficial, or generic summaries. Capture the specific biblical arguments, stories, "
            "theological context, and pastoral heart communicated by the speaker throughout the entire message.\n\n"
            "Output ONLY a valid JSON object matching this schema:\n"
            "{\n"
            '  "title": "A compelling, biblically faithful sermon title",\n'
            '  "scriptures": ["Primary Book Chapter:Verse-Verse", "Secondary Scripture citations..."],\n'
            '  "big_idea": "A clear, memorable 2-3 sentence thesis statement defining the central theological message.",\n'
            '  "overview": "A thorough, descriptive 2-3 paragraph narrative summary of the sermon. Explain the biblical text, the cultural/historical context, the human problem addressed, and the gospel resolution.",\n'
            '  "key_points": [\n'
            '    {\n'
            '      "timecode": "HH:MM:SS",\n'
            '      "title": "Descriptive Point Title",\n'
            '      "scripture": "Scripture reference for this point (e.g. Romans 8:1-4)",\n'
            '      "description": "Thorough 3-5 sentence explanation detailing the biblical exposition, the speaker\'s illustrations or analogies, and theological significance.",\n'
            '      "practical_application": "Concrete, practical guidance for how to live this truth out in daily life."\n'
            '    }\n'
            '  ],\n'
            '  "action_steps": [\n'
            '    "Concrete action step or spiritual discipline for the listener this week (3 to 5 steps)"\n'
            '  ],\n'
            '  "quotes": [\n'
            '    "3 to 5 memorable, inspiring, or challenging spoken quotes from the transcript"\n'
            '  ],\n'
            '  "discussion_questions": [\n'
            '    "4 to 6 thoughtful small group application questions ranging from understanding the text to personal vulnerability and prayer"\n'
            '  ]\n'
            "}\n\n"
            f"COMPLETE TRANSCRIPT:\n{transcript_text}"
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
                from google.genai import types
                client = genai.Client(api_key=api_key)
                config = types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                )
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                logger.debug(f"google.genai SDK with config failed, trying basic call: {e}")
                try:
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt,
                    )
                    if response and response.text:
                        return response.text
                except Exception as e2:
                    logger.debug(f"google.genai SDK fallback failed: {e2}")
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
                    "maxOutputTokens": 4096,
                }
            }
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=35.0) as resp:
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

        # 6. Derive Narrative Overview
        scrip_str = f" anchored in {', '.join(scriptures)}" if scriptures else ""
        overview = f"In this service, the message focused on '{title}'{scrip_str}. The teaching explored God's truth for faithful living and community, calling believers to deeper trust and obedience. {big_idea}"

        # 7. Action Steps
        primary_ref = scriptures[0] if scriptures else "the scriptures"
        action_steps = [
            f"Read and meditate on {primary_ref} throughout the week.",
            "Identify one specific area in your life where you need to surrender fear and step out in faith.",
            "Reach out to an individual or family in your community to share encouragement and pray with them.",
        ]

        # Enhance key points with practical applications if not already set
        for idx, pt in enumerate(key_points, 1):
            if "practical_application" not in pt:
                pt["practical_application"] = f"Reflect on how {pt.get('title', 'this point')} applies to your everyday routines."

        return {
            "title": title,
            "scriptures": scriptures,
            "big_idea": big_idea,
            "overview": overview,
            "key_points": key_points,
            "action_steps": action_steps,
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
                    "practical_application": "Apply this teaching in your personal walk this week.",
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
                    "practical_application": "Reflect on God's direction for this area of your life.",
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
        scriptures_list = data.get("scriptures", [])
        scriptures = ", ".join(scriptures_list) if scriptures_list else "Scripture Reading"
        big_idea = data.get("big_idea", "")
        overview = data.get("overview", "")
        key_points = data.get("key_points", [])
        action_steps = data.get("action_steps", [])
        quotes = data.get("quotes", [])
        questions = data.get("discussion_questions", [])

        # 1. Full Markdown
        md_lines = [
            f"# 📖 {title}",
            f"**Primary Scripture:** {scriptures}",
            f"**The Big Idea:** {big_idea}",
            "",
        ]

        if overview:
            md_lines.extend([
                "### 📝 Message Overview",
                overview,
                "",
            ])

        md_lines.append("### 📌 Key Teaching Points")
        for p in key_points:
            tc = p.get("timecode", "00:00:00")
            p_title = p.get("title", "")
            p_scrip = p.get("scripture", "")
            scrip_str = f" *({p_scrip})*" if p_scrip else ""
            md_lines.append(f"- **[{tc}] {p_title}**{scrip_str}")
            if p.get("description"):
                md_lines.append(f"  {p.get('description')}")
            if p.get("practical_application"):
                md_lines.append(f"  *💡 Application:* {p.get('practical_application')}")
        md_lines.append("")

        if action_steps:
            md_lines.append("### 🎯 Weekly Action Steps & Life Application")
            for idx, step in enumerate(action_steps, 1):
                md_lines.append(f"{idx}. {step}")
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
            f"📖 {title}",
            "",
            big_idea,
            "",
            f"Primary Scripture: {scriptures}",
            "",
        ]
        if overview:
            yt_lines.extend([
                "ABOUT THIS MESSAGE:",
                overview,
                "",
            ])
        yt_lines.append("SERMON OUTLINE & TIMESTAMPS:")
        for p in key_points:
            yt_lines.append(f"{p.get('timecode', '00:00:00')} - {p.get('title', '')}")
        yt_lines.append("")

        if action_steps:
            yt_lines.append("THIS WEEK'S CHALLENGE:")
            for s in action_steps:
                yt_lines.append(f"• {s}")
            yt_lines.append("")

        if questions:
            yt_lines.append("REFLECTION QUESTION:")
            yt_lines.append(f"• {questions[0]}")

        data["youtube_description"] = "\n".join(yt_lines)

        # 3. Compact Bulletin / Newsletter Block
        bulletin_lines = [
            f"SERMON RECAP: {title.upper()}",
            f"Key Scripture: {scriptures}",
            "",
            f"Main Idea: {big_idea}",
            "",
        ]
        if overview:
            bulletin_lines.extend([
                "Overview:",
                overview,
                "",
            ])
        bulletin_lines.append("Key Points:")
        for idx, p in enumerate(key_points, 1):
            p_desc = p.get("description", "")
            bulletin_lines.append(f"{idx}. {p.get('title', '')} — {p_desc}")
            if p.get("practical_application"):
                bulletin_lines.append(f"   Takeaway: {p.get('practical_application')}")
        bulletin_lines.append("")

        if action_steps:
            bulletin_lines.append("Life Applications for This Week:")
            for idx, s in enumerate(action_steps, 1):
                bulletin_lines.append(f"  {idx}. {s}")
            bulletin_lines.append("")

        if questions:
            bulletin_lines.append("Small Group Discussion:")
            for idx, q in enumerate(questions[:3], 1):
                bulletin_lines.append(f"  {idx}. {q}")

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
        self,
        entries: List[HistoryEntry],
        anchor: str = "first_speech",
        time_offset_seconds: float = 0.0,
        group_interval_seconds: float = 25.0,
        max_chars: int = 350000,
    ) -> Tuple[str, float]:
        """Format and group transcript into readable timestamped paragraphs for LLM reasoning.

        Aggregates fragmented speech recognition entries within ~25s windows into
        coherent narrative blocks, eliminating repetitive timestamp token bloat
        while preserving precise temporal anchors for chapters and outline points.
        """
        if not entries:
            return "", 0.0

        base_time = (
            entries[0].start_time
            if anchor == "first_speech"
            else (self.history.session_start_time if self.history else entries[0].start_time)
        )

        blocks = []
        current_block_texts = []
        current_block_start_sec = None
        last_entry_end_time = None

        for e in entries:
            text = e.text.strip()
            if not text:
                continue

            rel_sec = max(0, int(e.start_time - base_time + time_offset_seconds))

            # Trigger a new block on first entry, interval boundary (~25s), or >4s silence pause
            is_new_block = (
                current_block_start_sec is None
                or (rel_sec - current_block_start_sec) >= group_interval_seconds
                or (last_entry_end_time is not None and (e.start_time - last_entry_end_time) > 4.0)
            )

            if is_new_block and current_block_texts:
                tc = f"{current_block_start_sec // 3600:02d}:{(current_block_start_sec % 3600) // 60:02d}:{current_block_start_sec % 60:02d}"
                joined = " ".join(current_block_texts)
                blocks.append(f"[{tc}] {joined}")
                current_block_texts = []
                current_block_start_sec = rel_sec
            elif current_block_start_sec is None:
                current_block_start_sec = rel_sec

            current_block_texts.append(text)
            last_entry_end_time = e.end_time

        if current_block_texts and current_block_start_sec is not None:
            tc = f"{current_block_start_sec // 3600:02d}:{(current_block_start_sec % 3600) // 60:02d}:{current_block_start_sec % 60:02d}"
            joined = " ".join(current_block_texts)
            blocks.append(f"[{tc}] {joined}")

        full_transcript = "\n".join(blocks)
        if len(full_transcript) > max_chars:
            full_transcript = full_transcript[:max_chars]

        return full_transcript, base_time

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
        """Extract JSON array from LLM response text with markdown fence stripping and bracket matching."""
        if not text:
            return None
        cleaned = text.strip()

        # 1. Look for ```json ... ``` markdown block
        fence_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", cleaned, re.DOTALL)
        if fence_match:
            try:
                res = json.loads(fence_match.group(1))
                if isinstance(res, list):
                    return res
            except Exception:
                pass

        # 2. Try direct parsing
        try:
            res = json.loads(cleaned)
            if isinstance(res, list):
                return res
        except Exception:
            pass

        # 3. Find outermost square brackets
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                res = json.loads(cleaned[start:end+1])
                if isinstance(res, list):
                    return res
            except Exception:
                pass

        # 4. Fallback regex
        match = re.search(r"\[\s*\{.*\}\s*\]", cleaned, re.DOTALL)
        if match:
            try:
                res = json.loads(match.group(0))
                if isinstance(res, list):
                    return res
            except Exception:
                pass

        return None

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON object from LLM response text with markdown fence stripping and bracket matching."""
        if not text:
            return None
        cleaned = text.strip()

        # 1. Look for ```json ... ``` markdown block
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if fence_match:
            try:
                res = json.loads(fence_match.group(1))
                if isinstance(res, dict):
                    return res
            except Exception:
                pass

        # 2. Try direct parsing
        try:
            res = json.loads(cleaned)
            if isinstance(res, dict):
                return res
        except Exception:
            pass

        # 3. Find outermost curly braces
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                res = json.loads(cleaned[start:end+1])
                if isinstance(res, dict):
                    return res
            except Exception:
                pass

        # 4. Fallback regex
        match = re.search(r"\{\s*\".*\"\s*:.*\}", cleaned, re.DOTALL)
        if match:
            try:
                res = json.loads(match.group(0))
                if isinstance(res, dict):
                    return res
            except Exception:
                pass

        return None
