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
        Handles standard CSV/TSV, arrows ('->', '=>'), and equals ('=').
        Supports quoted strings and preserves colons inside citations (e.g. John 3:16).
        Returns number of successfully imported/updated terms.
        """
        if replace_all:
            self.config.terms.clear()

        imported_count = 0
        raw_text = content.strip()
        if not raw_text:
            return 0

        # Attempt CSV reader first with tab or comma
        delim = "\t" if "\t" in raw_text and "," not in raw_text else ","
        try:
            reader = csv.reader(io.StringIO(raw_text), delimiter=delim)
            for line_num, row in enumerate(reader):
                if not row or all(not cell.strip() for cell in row):
                    continue

                orig, rep = None, None
                if len(row) >= 2:
                    orig, rep = row[0].strip(), row[1].strip()
                elif len(row) == 1:
                    line = row[0].strip()
                    for sep in ("->", "=>", "="):
                        if sep in line:
                            parts = line.split(sep, 1)
                            orig, rep = parts[0].strip(), parts[1].strip()
                            break

                if not orig or not rep or orig.startswith("#"):
                    continue

                # Skip header row if present
                if line_num == 0 and orig.lower() in ("misheard phrase", "original", "source", "misheard", "from", "word") and rep.lower() in ("correct replacement", "replacement", "target", "correct", "to", "spelling"):
                    continue

                if self.add_term(orig, rep):
                    imported_count += 1
        except Exception as e:
            logger.error(f"Error parsing glossary CSV: {e}")

        return imported_count

    def get_terms(self) -> Dict[str, str]:
        """Return a copy of the current glossary terms."""
        return dict(self.config.terms)
