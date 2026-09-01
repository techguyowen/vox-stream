"""Offline Scripture Verse Auto-Lookup Engine for Live Broadcasting & Church AV."""

from __future__ import annotations

import logging
import re
import sqlite3
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BibleEngine:
    """High-speed offline scripture verse resolver and auto-prompter."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH

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

        try:
            conn = sqlite3.connect(self.db_path)
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
                    conn.close()
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
                    conn.close()
                    return None

                combined_text = row[0].strip()
                citation = f"{canonical_book} {chapter}:{verse_start}"

            conn.close()

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
        if not citations:
            return None

        book, ch, v_start, v_end = citations[0]
        return self.lookup_citation(book, ch, v_start, v_end, version=version)

    @classmethod
    def extract_citations_from_text(cls, text: str) -> List[Tuple[str, int, int, Optional[int]]]:
        """Extract all standard formatted scripture references from text (e.g. 'John 3:16', '1 Corinthians 13:4-7')."""
        if not text:
            return []

        # First normalize spoken numbers/formats if needed
        try:
            from .church_lexicon import ChurchLexiconFormatter
            formatted = ChurchLexiconFormatter().format_church_text(text)
        except Exception:
            formatted = text

        pattern = re.compile(
            r"\b((?:(?:1st|2nd|3rd|first|second|third|[1-3])\s+)?[A-Za-z]+(?:\s+of\s+[A-Za-z]+)?)\s+(\d+)[:\.](\d+)(?:[-–—](\d+))?\b",
            re.IGNORECASE,
        )

        results = []
        for match in pattern.finditer(formatted):
            raw_book = match.group(1).strip()
            canonical = cls.normalize_book_name(raw_book)
            if canonical:
                ch = int(match.group(2))
                v_start = int(match.group(3))
                v_end = int(match.group(4)) if match.group(4) else None
                results.append((canonical, ch, v_start, v_end))

        # Check for Psalm chapter references (e.g. "Psalm 23")
        if not results:
            psalm_match = re.search(r"\b(psalms?)\s+(\d+)\b", formatted, re.IGNORECASE)
            if psalm_match:
                ch = int(psalm_match.group(2))
                if 1 <= ch <= 150:
                    results.append(("Psalms", ch, 1, None))

        return results
