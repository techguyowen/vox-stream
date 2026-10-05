"""Offline Scripture Verse Auto-Lookup Engine for Live Broadcasting & Church AV."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("obs_captioner.bible_engine")

DB_PATH = Path(__file__).parent / "data" / "bible.db"


VERSION_METADATA: Dict[str, Dict[str, str]] = {
    "bsb": {
        "code": "bsb",
        "name": "Berean Standard Bible",
        "style": "Modern / ESV Equivalent",
        "description": "Accurate modern translation with formal equivalence (reads like ESV).",
    },
    "web": {
        "code": "web",
        "name": "World English Bible",
        "style": "Modern / NIV Equivalent",
        "description": "Clean, highly readable contemporary English translation (reads like NIV/NLT).",
    },
    "kjv": {
        "code": "kjv",
        "name": "King James Version",
        "style": "Traditional Classic",
        "description": "Classic 1611 authorized English translation.",
    },
}

BOOK_ALIASES: Dict[str, str] = {
    # Old Testament
    "genesis": "Genesis", "gen": "Genesis", "gn": "Genesis",
    "exodus": "Exodus", "ex": "Exodus", "exo": "Exodus",
    "leviticus": "Leviticus", "lev": "Leviticus", "lv": "Leviticus",
    "numbers": "Numbers", "num": "Numbers", "nm": "Numbers",
    "deuteronomy": "Deuteronomy", "deut": "Deuteronomy", "dt": "Deuteronomy",
    "joshua": "Joshua", "josh": "Joshua", "jos": "Joshua",
    "judges": "Judges", "judg": "Judges", "jdg": "Judges",
    "ruth": "Ruth", "rth": "Ruth", "ru": "Ruth",
    "1 samuel": "1 Samuel", "1st samuel": "1 Samuel", "first samuel": "1 Samuel", "1sam": "1 Samuel", "1 sm": "1 Samuel",
    "2 samuel": "2 Samuel", "2nd samuel": "2 Samuel", "second samuel": "2 Samuel", "2sam": "2 Samuel", "2 sm": "2 Samuel",
    "1 kings": "1 Kings", "1st kings": "1 Kings", "first kings": "1 Kings", "1kgs": "1 Kings", "1 kgs": "1 Kings", "1 ki": "1 Kings",
    "2 kings": "2 Kings", "2nd kings": "2 Kings", "second kings": "2 Kings", "2kgs": "2 Kings", "2 kgs": "2 Kings", "2 ki": "2 Kings",
    "1 chronicles": "1 Chronicles", "1st chronicles": "1 Chronicles", "first chronicles": "1 Chronicles", "1chron": "1 Chronicles", "1 chr": "1 Chronicles",
    "2 chronicles": "2 Chronicles", "2nd chronicles": "2 Chronicles", "second chronicles": "2 Chronicles", "2chron": "2 Chronicles", "2 chr": "2 Chronicles",
    "ezra": "Ezra", "ezr": "Ezra",
    "nehemiah": "Nehemiah", "neh": "Nehemiah", "ne": "Nehemiah",
    "esther": "Esther", "est": "Esther", "esth": "Esther",
    "job": "Job", "jb": "Job",
    "psalm": "Psalms", "psalms": "Psalms", "ps": "Psalms", "psa": "Psalms", "psm": "Psalms",
    "proverbs": "Proverbs", "prov": "Proverbs", "prv": "Proverbs", "pr": "Proverbs",
    "ecclesiastes": "Ecclesiastes", "eccl": "Ecclesiastes", "ecc": "Ecclesiastes",
    "song of solomon": "Song of Solomon", "song of songs": "Song of Solomon", "canticles": "Song of Solomon", "sos": "Song of Solomon",
    "isaiah": "Isaiah", "isa": "Isaiah", "is": "Isaiah",
    "jeremiah": "Jeremiah", "jer": "Jeremiah", "jr": "Jeremiah",
    "lamentations": "Lamentations", "lam": "Lamentations", "la": "Lamentations",
    "ezekiel": "Ezekiel", "ezek": "Ezekiel", "eze": "Ezekiel",
    "daniel": "Daniel", "dan": "Daniel", "dn": "Daniel",
    "hosea": "Hosea", "hos": "Hosea", "ho": "Hosea",
    "joel": "Joel", "jl": "Joel",
    "amos": "Amos", "am": "Amos",
    "obadiah": "Obadiah", "obad": "Obadiah", "ob": "Obadiah",
    "jonah": "Jonah", "jon": "Jonah", "jnh": "Jonah",
    "micah": "Micah", "mic": "Micah", "mc": "Micah",
    "nahum": "Nahum", "nah": "Nahum", "na": "Nahum",
    "habakkuk": "Habakkuk", "hab": "Habakkuk", "hb": "Habakkuk",
    "zephaniah": "Zephaniah", "zeph": "Zephaniah", "zep": "Zephaniah",
    "haggai": "Haggai", "hag": "Haggai", "hg": "Haggai",
    "zechariah": "Zechariah", "zech": "Zechariah", "zec": "Zechariah",
    "malachi": "Malachi", "mal": "Malachi", "ml": "Malachi",
    # New Testament
    "matthew": "Matthew", "matt": "Matthew", "mt": "Matthew",
    "mark": "Mark", "mrk": "Mark", "mk": "Mark",
    "luke": "Luke", "luk": "Luke", "lk": "Luke",
    "john": "John", "jhn": "John", "jn": "John",
    "acts": "Acts", "act": "Acts", "ac": "Acts",
    "romans": "Romans", "rom": "Romans", "ro": "Romans", "rm": "Romans",
    "1 corinthians": "1 Corinthians", "1st corinthians": "1 Corinthians", "first corinthians": "1 Corinthians", "1cor": "1 Corinthians", "1 cor": "1 Corinthians",
    "2 corinthians": "2 Corinthians", "2nd corinthians": "2 Corinthians", "second corinthians": "2 Corinthians", "2cor": "2 Corinthians", "2 cor": "2 Corinthians",
    "galatians": "Galatians", "gal": "Galatians", "ga": "Galatians",
    "ephesians": "Ephesians", "eph": "Ephesians", "ep": "Ephesians",
    "philippians": "Philippians", "phil": "Philippians", "php": "Philippians",
    "colossians": "Colossians", "col": "Colossians", "cl": "Colossians",
    "1 thessalonians": "1 Thessalonians", "1st thessalonians": "1 Thessalonians", "first thessalonians": "1 Thessalonians", "1thess": "1 Thessalonians", "1 th": "1 Thessalonians",
    "2 thessalonians": "2 Thessalonians", "2nd thessalonians": "2 Thessalonians", "second thessalonians": "2 Thessalonians", "2thess": "2 Thessalonians", "2 th": "2 Thessalonians",
    "1 timothy": "1 Timothy", "1st timothy": "1 Timothy", "first timothy": "1 Timothy", "1tim": "1 Timothy", "1 ti": "1 Timothy",
    "2 timothy": "2 Timothy", "2nd timothy": "2 Timothy", "second timothy": "2 Timothy", "2tim": "2 Timothy", "2 ti": "2 Timothy",
    "titus": "Titus", "tit": "Titus", "ti": "Titus",
    "philemon": "Philemon", "phlm": "Philemon", "phm": "Philemon",
    "hebrews": "Hebrews", "heb": "Hebrews", "he": "Hebrews",
    "james": "James", "jas": "James", "jm": "James",
    "1 peter": "1 Peter", "1st peter": "1 Peter", "first peter": "1 Peter", "1pet": "1 Peter", "1 pe": "1 Peter",
    "2 peter": "2 Peter", "2nd peter": "2 Peter", "second peter": "2 Peter", "2pet": "2 Peter", "2 pe": "2 Peter",
    "1 john": "1 John", "1st john": "1 John", "first john": "1 John", "1jn": "1 John", "1 jn": "1 John",
    "2 john": "2 John", "2nd john": "2 John", "second john": "2 John", "2jn": "2 John", "2 jn": "2 John",
    "3 john": "3 John", "3rd john": "3 John", "third john": "3 John", "3jn": "3 John", "3 jn": "3 John",
    "jude": "Jude", "jud": "Jude", "jd": "Jude",
    "revelation": "Revelation", "revelations": "Revelation", "rev": "Revelation", "rv": "Revelation",
}

# Pre-compiled module-level regexes for citation extraction and cleanup
_CITATION_PATTERN = re.compile(
    r"\b((?:(?:1st|2nd|3rd|first|second|third|[1-3])\s+)?[A-Za-z]+(?:\s+of\s+[A-Za-z]+)?)\s+(\d+)[:\.](\d+)(?:[-–—](\d+))?\b",
    re.IGNORECASE,
)
_PSALM_PATTERN = re.compile(r"\b(psalms?)\s+(\d+)\b", re.IGNORECASE)
_BOOK_CHAP_PATTERN = re.compile(
    rf"\b({'|'.join(re.escape(b) for b in BOOK_ALIASES.keys())})\s+(?:chapter\s+)?(\d+)\b",
    re.IGNORECASE,
)
_CHAP_V_PATTERN = re.compile(
    r"\bchapter\s+(\d+|[a-z]+)\s+verses?\s+(\d+|[a-z]+)(?:\s+(?:to|through|thru|until|-)\s+(\d+|[a-z]+))?\b",
    re.IGNORECASE,
)
_V_PATTERN = re.compile(
    r"\bverses?\s+(\d+|[a-z]+)(?:\s+(?:to|through|thru|until|-)\s+(\d+|[a-z]+))?\b",
    re.IGNORECASE,
)
_STRONGS_PATTERN = re.compile(r"<S>\d+</S>")
_MARKUP_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_cached_lexicon_formatter = None


def _get_lexicon_formatter():
    """Module-level cached ChurchLexiconFormatter instance to prevent per-line re-instantiation."""
    global _cached_lexicon_formatter
    if _cached_lexicon_formatter is None:
        try:
            from .church_lexicon import ChurchLexiconFormatter
            _cached_lexicon_formatter = ChurchLexiconFormatter()
        except Exception:
            pass
    return _cached_lexicon_formatter


# Eagerly initialize once at module import
_get_lexicon_formatter()




@dataclass
class ScriptureLookupResult:
    citation: str
    book: str
    chapter: int
    verse_start: int
    verse_end: Optional[int]
    text: str
    version: str
    version_name: str
    inferred_context: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


WORD_TO_NUM: Dict[str, int] = {
    "zero": 0, "one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
    "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6,
    "seven": 7, "seventh": 7, "eight": 8, "eighth": 8, "nine": 9, "ninth": 9,
    "ten": 10, "tenth": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100
}


class BibleEngine:
    """High-speed offline scripture verse resolver and context-aware auto-prompter."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.primary_book: Optional[str] = None
        self.primary_chapter: Optional[int] = None
        self.recent_book: Optional[str] = None
        self.recent_chapter: Optional[int] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        """Get or lazily open the persistent read-only SQLite connection (caller must hold _db_lock)."""
        if self._conn is not None:
            return self._conn
        if not self.db_path.exists():
            logger.warning(f"Bible database not found at {self.db_path}")
            return None
        try:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            return self._conn
        except Exception as e:
            logger.error(f"Error connecting to bible.db: {e}", exc_info=True)
            return None

    def close(self):
        """Cleanly close persistent SQLite database connection if opened."""
        with self._db_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                finally:
                    self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


    def set_context(self, book: str, chapter: int, is_primary: bool = False):
        canonical = self.normalize_book_name(book)
        if not canonical:
            return
        self.recent_book = canonical
        self.recent_chapter = chapter
        if is_primary or not self.primary_book:
            self.primary_book = canonical
            self.primary_chapter = chapter

    def clear_context(self):
        self.primary_book = None
        self.primary_chapter = None
        self.recent_book = None
        self.recent_chapter = None

    def get_context_status(self) -> Dict[str, Any]:
        return {
            "primary_book": self.primary_book,
            "primary_chapter": self.primary_chapter,
            "primary_citation": f"{self.primary_book} {self.primary_chapter}" if self.primary_book and self.primary_chapter else None,
            "recent_book": self.recent_book,
            "recent_chapter": self.recent_chapter,
            "recent_citation": f"{self.recent_book} {self.recent_chapter}" if self.recent_book and self.recent_chapter else None,
        }

    @classmethod
    def get_available_versions(cls) -> List[Dict[str, str]]:
        return list(VERSION_METADATA.values())

    @classmethod
    def normalize_book_name(cls, raw_book: str) -> Optional[str]:
        if not raw_book:
            return None
        clean = raw_book.strip().lower()
        clean = re.sub(r"[^\w\s]", "", clean)
        return BOOK_ALIASES.get(clean)

    def lookup_citation(
        self,
        book: str,
        chapter: int,
        verse_start: int,
        verse_end: Optional[int] = None,
        version: str = "bsb",
    ) -> Optional[ScriptureLookupResult]:
        """Query offline SQLite database for a specific verse or verse range."""
        canonical_book = self.normalize_book_name(book)
        if not canonical_book:
            return None

        ver_code = version.lower().strip()
        if ver_code not in VERSION_METADATA:
            ver_code = "bsb"

        if not self.db_path.exists():
            logger.warning(f"Bible database not found at {self.db_path}")
            return None

        with self._db_lock:
            conn = self._get_connection()
            if not conn:
                return None
            try:
                cur = conn.cursor()

                if verse_end and verse_end > verse_start:
                    # Multi-verse range (e.g. John 3:16-18)
                    cur.execute(
                        """
                        SELECT verse, text FROM verses
                        WHERE translation = ? AND (book_name = ? OR book_name = ?) AND chapter = ? AND verse >= ? AND verse <= ?
                        ORDER BY verse ASC
                        """,
                        (ver_code, canonical_book, canonical_book.replace("Psalms", "Psalm"), chapter, verse_start, verse_end),
                    )
                    rows = cur.fetchall()
                    if not rows:
                        return None

                    combined_text = " ".join([r[1].strip() for r in rows])
                    citation = f"{canonical_book} {chapter}:{verse_start}-{verse_end}"
                else:
                    # Single verse (e.g. John 3:16)
                    cur.execute(
                        """
                        SELECT text FROM verses
                        WHERE translation = ? AND (book_name = ? OR book_name = ?) AND chapter = ? AND verse = ?
                        LIMIT 1
                        """,
                        (ver_code, canonical_book, canonical_book.replace("Psalms", "Psalm"), chapter, verse_start),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None

                    combined_text = row[0].strip()
                    citation = f"{canonical_book} {chapter}:{verse_start}"

                # Clean Strong's numbers (e.g. <S>1063</S>) and markup annotations
                combined_text = _STRONGS_PATTERN.sub("", combined_text)
                combined_text = _MARKUP_PATTERN.sub("", combined_text)
                combined_text = _WHITESPACE_PATTERN.sub(" ", combined_text).strip()

                ver_meta = VERSION_METADATA.get(ver_code, VERSION_METADATA["bsb"])
                return ScriptureLookupResult(
                    citation=citation,
                    book=canonical_book,
                    chapter=chapter,
                    verse_start=verse_start,
                    verse_end=verse_end if (verse_end and verse_end > verse_start) else None,
                    text=combined_text,
                    version=ver_meta["code"].upper(),
                    version_name=ver_meta["name"],
                )
            except Exception as e:
                logger.error(f"Error querying bible.db: {e}", exc_info=True)
                return None


    def parse_and_lookup_first(self, text: str, version: str = "bsb") -> Optional[ScriptureLookupResult]:
        """Scan a transcript string for scripture references and lookup the first match."""
        citations = self.extract_citations_from_text(text)
        if citations:
            book, ch, v_start, v_end = citations[0]
            res = self.lookup_citation(book, ch, v_start, v_end, version=version)
            if res:
                self.set_context(book, ch)
                return res

        # If no explicit full citation match, attempt contextual verse lookup (e.g. "verse 8")
        return self.lookup_contextual_verse(text, version=version)

    def lookup_contextual_verse(self, text: str, version: str = "bsb") -> Optional[ScriptureLookupResult]:
        if not text:
            return None

        parsed = self.extract_contextual_verse_reference(text)
        if not parsed:
            return None

        explicit_chap, v_start, v_end = parsed

        # Candidates to try in order: recent context first, then primary sermon context
        candidates: List[Tuple[str, int]] = []
        if explicit_chap is not None:
            if self.recent_book:
                candidates.append((self.recent_book, explicit_chap))
            if self.primary_book and (self.primary_book, explicit_chap) not in candidates:
                candidates.append((self.primary_book, explicit_chap))
        else:
            if self.recent_book and self.recent_chapter:
                candidates.append((self.recent_book, self.recent_chapter))
            if self.primary_book and self.primary_chapter and (self.primary_book, self.primary_chapter) not in candidates:
                candidates.append((self.primary_book, self.primary_chapter))

        for book, ch in candidates:
            res = self.lookup_citation(book, ch, v_start, v_end, version=version)
            if res:
                res.inferred_context = f"{book} {ch}"
                self.recent_book = book
                self.recent_chapter = ch
                return res

        return None

    @classmethod
    def parse_spoken_num(cls, val: str) -> Optional[int]:
        if not val:
            return None
        clean = val.strip().lower()
        if clean.isdigit():
            return int(clean)
        if clean in WORD_TO_NUM:
            return WORD_TO_NUM[clean]
        parts = re.split(r"[\s\-]+", clean)
        total = 0
        for p in parts:
            if p in WORD_TO_NUM:
                n = WORD_TO_NUM[p]
                if n == 100 and total > 0:
                    total *= 100
                else:
                    total += n
            else:
                return None
        return total if total > 0 else None

    @classmethod
    def extract_contextual_verse_reference(cls, text: str) -> Optional[Tuple[Optional[int], int, Optional[int]]]:
        if not text:
            return None
        norm = text.lower().strip()

        # 1. "chapter X verse Y [to Z]"
        m = _CHAP_V_PATTERN.search(norm)
        if m:
            ch = cls.parse_spoken_num(m.group(1))
            v1 = cls.parse_spoken_num(m.group(2))
            v2 = cls.parse_spoken_num(m.group(3)) if m.group(3) else None
            if ch and v1:
                return (ch, v1, v2)

        # 2. "verse X [to Y]" or "verses X to Y"
        m = _V_PATTERN.search(norm)
        if m:
            v1 = cls.parse_spoken_num(m.group(1))
            v2 = cls.parse_spoken_num(m.group(2)) if m.group(2) else None
            if v1:
                return (None, v1, v2)

        return None

    @classmethod
    def extract_citations_from_text(cls, text: str) -> List[Tuple[str, int, int, Optional[int]]]:
        """Extract all standard formatted scripture references from text (e.g. 'John 3:16', '1 Corinthians 13:4-7')."""
        if not text:
            return []

        # First normalize spoken numbers/formats if needed
        formatter = _get_lexicon_formatter()
        if formatter is not None:
            try:
                formatted = formatter.format_church_text(text)
            except Exception:
                formatted = text
        else:
            formatted = text

        results = []
        for match in _CITATION_PATTERN.finditer(formatted):
            raw_book = match.group(1).strip()
            canonical = cls.normalize_book_name(raw_book)
            if canonical:
                ch = int(match.group(2))
                v_start = int(match.group(3))
                v_end = int(match.group(4)) if match.group(4) else None
                results.append((canonical, ch, v_start, v_end))

        # Check for Psalm chapter references (e.g. "Psalm 23")
        if not results:
            psalm_match = _PSALM_PATTERN.search(formatted)
            if psalm_match:
                ch = int(psalm_match.group(2))
                if 1 <= ch <= 150:
                    results.append(("Psalms", ch, 1, None))

        # Check for Book + Chapter references (e.g. "John 3", "Romans 8")
        if not results:
            match = _BOOK_CHAP_PATTERN.search(formatted)
            if match:
                raw_book = match.group(1).strip()
                canonical = cls.normalize_book_name(raw_book)
                if canonical:
                    ch = int(match.group(2))
                    results.append((canonical, ch, 1, None))

        return results

