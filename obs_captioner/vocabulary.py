"""Custom Vocabulary and Glossary Word Replacer.

Allows correcting phonetically misheard proper nouns, jargon, speaker names,
and specialized technical/church terms live across all captions.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("obs_captioner.vocabulary")


@dataclass
class VocabularyConfig:
    """Configuration for custom vocabulary and glossary replacements."""
    enabled: bool = True
    terms: Dict[str, str] = field(default_factory=lambda: {
        "obs": "OBS",
        "voxstream": "VoxStream",
        "vox stream": "VoxStream",
    })


class VocabularyReplacer:
    """Applies custom phonetic glossary and proper noun replacements to text."""

    def __init__(self, config: Optional[VocabularyConfig] = None):
        self.config = config or VocabularyConfig()
        self._compiled_patterns: List[Tuple[re.Pattern, str]] = []
        self.rebuild()

    def rebuild(self):
        """Compile regex patterns from active terms sorted by phrase length descending."""
        self._compiled_patterns = []
        if not self.config.enabled or not self.config.terms:
            return

        # Sort terms by length descending so longer multi-word phrases match before single words
        sorted_terms = sorted(
            self.config.terms.items(),
            key=lambda item: len(item[0].strip()),
            reverse=True,
        )

        for original, replacement in sorted_terms:
            orig_clean = original.strip()
            if not orig_clean:
                continue

            escaped = re.escape(orig_clean)
            # Use word boundaries (\b) to match full words/phrases
            pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
            self._compiled_patterns.append((pattern, replacement.strip()))

    def replace(self, text: str) -> Tuple[str, bool]:
        """
        Apply vocabulary replacements to text.
        Returns:
            Tuple[str, bool]: (modified_text, was_modified)
        """
        if not self.config.enabled or not text or not self._compiled_patterns:
            return text, False

        result = text
        was_modified = False

        for pattern, replacement in self._compiled_patterns:
            matches = list(pattern.finditer(result))
            if not matches:
                continue

            # Process matches in reverse to keep string indices intact
            for match in reversed(matches):
                was_modified = True
                start, end = match.span()
                result = result[:start] + replacement + result[end:]

        return result, was_modified

    def add_term(self, original: str, replacement: str) -> bool:
        """Add or update a glossary replacement term."""
        orig_clean = original.strip().lower()
        rep_clean = replacement.strip()
        if not orig_clean or not rep_clean:
            return False

        self.config.terms[orig_clean] = rep_clean
        self.rebuild()
        logger.info(f"Added vocabulary replacement: '{orig_clean}' -> '{rep_clean}'")
        return True

    def remove_term(self, original: str) -> bool:
        """Remove a glossary replacement term."""
        orig_clean = original.strip().lower()
        if orig_clean in self.config.terms:
            del self.config.terms[orig_clean]
            self.rebuild()
            logger.info(f"Removed vocabulary replacement for: '{orig_clean}'")
            return True
        return False

    def clear(self):
        """Clear all custom glossary terms."""
        self.config.terms.clear()
        self.rebuild()
        logger.info("Cleared all custom vocabulary terms.")

    def export_csv(self) -> str:
        """Export all custom glossary terms as standard CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Misheard Phrase", "Correct Replacement"])
        for orig, rep in sorted(self.config.terms.items()):
            writer.writerow([orig, rep])
        return output.getvalue()

    def import_csv(self, content: str, replace_all: bool = False) -> int:
        """
        Parse and import glossary terms from CSV, TSV, or delimited text lines.
        Supports comma, tab, semicolon, arrow ('->'), and equal ('=').
        Returns number of successfully imported/updated terms.
        """
        if replace_all:
            self.config.terms.clear()

        imported_count = 0
        lines = content.strip().splitlines()

        for line_num, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            orig, rep = None, None

            # 1. Check for arrow or equals syntax (e.g. "box stream -> VoxStream" or "obs = OBS")
            for sep in ("->", "=>", "=", ":"):
                if sep in line:
                    parts = line.split(sep, 1)
                    if len(parts) == 2:
                        orig, rep = parts[0].strip(), parts[1].strip()
                        break

            # 2. Otherwise parse with CSV reader (comma, tab, semicolon)
            if orig is None or rep is None:
                try:
                    delim = "\t" if "\t" in line else (";" if ";" in line and "," not in line else ",")
                    reader = csv.reader([line], delimiter=delim)
                    row = next(reader, [])
                    if len(row) >= 2:
                        orig, rep = row[0].strip(), row[1].strip()
                except Exception:
                    continue

            if not orig or not rep:
                continue

            # Skip header row if present
            if line_num == 0 and orig.lower() in ("misheard phrase", "original", "source", "misheard", "from", "word") and rep.lower() in ("correct replacement", "replacement", "target", "correct", "to", "spelling"):
                continue

            orig_clean = orig.lower()
            rep_clean = rep
            if orig_clean and rep_clean:
                self.config.terms[orig_clean] = rep_clean
                imported_count += 1

        self.rebuild()
        logger.info(f"Imported {imported_count} glossary terms (total: {len(self.config.terms)}).")
        return imported_count

    def get_terms(self) -> Dict[str, str]:
        """Return a copy of the current glossary terms."""
        return dict(self.config.terms)
