"""Content and Profanity Filter for Church-Friendly and Family-Safe Captions."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


# Tier 1: Standard Profanities, vulgarities, and common expletives
DEFAULT_STANDARD_PROFANITIES = [
    "fuck", "fucking", "fucked", "fucker", "fuckers", "motherfucker", "motherfucking",
    "shit", "shitty", "shitting", "bullshit", "horseshit",
    "bitch", "bitches", "bitching", "bitchy",
    "ass", "asshole", "assholes", "dumbass", "jackass", "badass",
    "bastard", "bastards",
    "cunt", "cunts",
    "dick", "dicks", "dickhead",
    "cock", "cocks", "cocksucker",
    "pussy", "pussies",
    "slut", "sluts", "whore", "whores",
    "prick", "pricks", "twat", "twats",
    "wanker", "wankers",
]

# Tier 2: Vulgar Blasphemies & Harsh Curses
DEFAULT_CHURCH_BLASPHEMIES = [
    "goddamn", "goddammit", "god damn", "god dammit", "god damned",
    "holy shit", "holy fuck", "holy hell",
    "damn", "dammit", "damned", "damning",
    "hell", "hellish",
]

# Tier 3: Crude / Sexual / Inappropriate Talk
DEFAULT_CRUDE_TERMS = [
    "tits", "boobs", "boner", "dildo", "blowjob", "handjob", "cum", "cumming",
    "orgasm", "masturbate", "masturbation", "horny", "retard", "retarded",
    "nigger", "nigga", "faggot", "fag", "dyke", "kike", "chink", "spic",
]

# Family-Friendly / Church-Friendly wholesome word replacements
DEFAULT_WHOLESOME_REPLACEMENTS = {
    "fuck": "fudge",
    "fucking": "flipping",
    "fucked": "messed up",
    "fucker": "rascal",
    "motherfucker": "monster",
    "shit": "shoot",
    "shitty": "lousy",
    "bullshit": "nonsense",
    "bitch": "complainer",
    "bitches": "people",
    "bitching": "grumbling",
    "ass": "bottom",
    "asshole": "jerk",
    "dumbass": "silly person",
    "jackass": "fool",
    "bastard": "rogue",
    "cunt": "scoundrel",
    "dick": "pest",
    "dickhead": "fool",
    "cock": "rooster",
    "pussy": "wimp",
    "whore": "traitor",
    "slut": "wild one",
    "damn": "darn",
    "dammit": "drat",
    "damned": "blasted",
    "goddamn": "gosh darn",
    "goddammit": "gosh darn it",
    "god damn": "gosh darn",
    "holy shit": "holy cow",
    "holy fuck": "my word",
    "holy hell": "my goodness",
    "hell": "heck",
    "hellish": "rough",
}

# Whitelist of words to prevent false positives and protect sacred terms
DEFAULT_WHITELIST = [
    "jesus christ", "jesus", "christ", "god", "god's", "lord", "lord's", "holy spirit", "holy ghost",
    "heavenly father", "almighty god", "son of god", "lamb of god", "prince of peace",
    "amen", "hallelujah", "alleluia", "hosanna", "bible", "holy bible", "scripture", "scriptures",
    "pastor", "preacher", "worship", "sanctuary", "apostle", "apostles", "disciple", "disciples",
    "gospel", "old testament", "new testament", "resurrection", "crucifixion", "communion",
    "heaven and hell", "gates of hell", "cast into hell", "saved from hell", "power of hell",
    "pass", "passed", "passing", "passport", "compass", "surpass", "trespass",
    "grass", "glass", "class", "classic", "mass", "massive", "bass", "brass",
    "assume", "asset", "assist", "assistant", "assess", "assessment", "associate",
    "dickens", "hitchcock", "peacock", "cockpit", "cockatoo", "shuttlecock",
    "scrap", "scrappy", "therapist", "title", "butter", "button", "document",
    "push", "pushed", "bullet", "analysis", "canal", "county", "country",
]


@dataclass
class CensorConfig:
    enabled: bool = True
    mode: str = "asterisk"  # "asterisk", "replacement", "censored_label", "drop_sentence"
    filter_standard_profanity: bool = True
    filter_church_blasphemy: bool = True
    filter_crude_terms: bool = True
    custom_blacklist: List[str] = field(default_factory=list)
    custom_whitelist: List[str] = field(default_factory=list)
    custom_replacements: Dict[str, str] = field(default_factory=dict)


# Theological vocabulary that is ordinary sermon speech, not profanity.
# Exempted from the default tier-2 list when church mode is active.
CHURCH_MODE_EXEMPT_TERMS = {
    "hell", "hellish", "damn", "dammit", "damned", "damning",
}


class ContentFilter:
    """Multi-tier profanity, blasphemy, and inappropriate content filter."""

    def __init__(self, config: CensorConfig, church_mode: bool = False):
        self.config = config
        self.church_mode = church_mode
        self._blacklist_patterns: List[Tuple[re.Pattern, str]] = []
        self._whitelist_set: Set[str] = set()
        self._whitelist_patterns: List[re.Pattern] = []
        self._replacements: Dict[str, str] = {}
        self.rebuild_dictionary()

    def rebuild_dictionary(self):
        """Compile regex patterns from active tiers and custom wordlists."""
        self._whitelist_set = {w.lower().strip() for w in DEFAULT_WHITELIST}
        if self.config.custom_whitelist:
            for w in self.config.custom_whitelist:
                if w.strip():
                    self._whitelist_set.add(w.lower().strip())

        # Compile whitelist phrases so protected spans can be located in context
        self._whitelist_patterns = [
            re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE)
            for w in sorted(self._whitelist_set, key=len, reverse=True)
        ]

        # Build active replacement map
        self._replacements = dict(DEFAULT_WHOLESOME_REPLACEMENTS)
        if self.config.custom_replacements:
            for k, v in self.config.custom_replacements.items():
                self._replacements[k.lower().strip()] = v.strip()

        # Build blacklist terms
        terms = set()
        if self.config.filter_standard_profanity:
            terms.update(DEFAULT_STANDARD_PROFANITIES)
        if self.config.filter_church_blasphemy:
            tier2 = set(DEFAULT_CHURCH_BLASPHEMIES)
            if self.church_mode:
                # "hell is real" / "you shall not be damned" are ordinary sermon
                # speech; explicit blasphemies ("goddamn", "holy hell") stay filtered.
                tier2 -= CHURCH_MODE_EXEMPT_TERMS
            terms.update(tier2)
        if self.config.filter_crude_terms:
            terms.update(DEFAULT_CRUDE_TERMS)
        if self.config.custom_blacklist:
            for w in self.config.custom_blacklist:
                if w.strip():
                    terms.add(w.lower().strip())

        # Include custom substitutions in scanned terms (church-mode exemptions
        # apply to the default map, but a user-defined replacement always wins)
        custom_keys = {k.lower().strip() for k in (self.config.custom_replacements or {})}
        for key in self._replacements:
            if self.church_mode and key in CHURCH_MODE_EXEMPT_TERMS and key not in custom_keys:
                continue
            terms.add(key)

        # Sort terms by length descending so multi-word phrases match before individual words
        sorted_terms = sorted(list(terms), key=lambda x: len(x), reverse=True)

        self._blacklist_patterns = []
        for term in sorted_terms:
            # Match term with word boundaries, handling optional punctuation inside terms
            escaped = re.escape(term)
            pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
            self._blacklist_patterns.append((pattern, term))

    def _mask_word(self, word: str) -> str:
        """Mask a single word into asterisks keeping first letter (e.g. f***)."""
        if len(word) <= 2:
            return "*" * len(word)
        return word[0] + ("*" * (len(word) - 1))

    def _protected_spans(self, text: str) -> List[Tuple[int, int]]:
        """Locate every whitelisted word/phrase occurrence in the text.

        A blacklist match inside a protected span (e.g. "hell" inside
        "gates of hell") is left untouched — this is what makes the
        whitelist context-aware rather than a plain word comparison.
        """
        spans = []
        for pattern in self._whitelist_patterns:
            for m in pattern.finditer(text):
                spans.append(m.span())
        return spans

    def filter_text(self, text: str) -> Tuple[str, bool]:
        """
        Filters input text based on configuration.
        Returns (filtered_text, was_censored).
        """
        if not self.config.enabled or not text:
            return text, False

        mode = self.config.mode
        protected = self._protected_spans(text)

        # Collect all blacklist matches against the ORIGINAL text so whitelist
        # spans stay valid. Patterns are sorted longest-term-first, so longer
        # phrases claim their span before contained single words.
        kept: List[Tuple[int, int, str]] = []  # (start, end, matched_str)
        for pattern, _raw_term in self._blacklist_patterns:
            for match in pattern.finditer(text):
                start, end = match.span()
                if any(start >= ps and end <= pe for ps, pe in protected):
                    continue
                if any(start < ke and end > ks for ks, ke, _ in kept):
                    continue  # overlaps a longer, already-claimed match
                kept.append((start, end, match.group(0)))

        if not kept:
            return text, False

        if mode == "drop_sentence":
            return "", True

        # Apply replacements right-to-left to keep indices valid
        result = text
        for start, end, matched_str in sorted(kept, reverse=True):
            clean_lower = matched_str.lower()
            if mode == "censored_label":
                replacement = "[CENSORED]"
            elif mode == "replacement":
                # Look up wholesome substitution
                sub = self._replacements.get(clean_lower)
                if not sub:
                    # Fallback to single word lookup
                    sub = self._mask_word(matched_str)
                else:
                    # Preserve uppercase / capitalization
                    if matched_str.isupper():
                        sub = sub.upper()
                    elif matched_str[0].isupper():
                        sub = sub.capitalize()
                replacement = sub
            else:
                # Default: Asterisk masking
                if " " in matched_str:
                    # Multi-word phrase: mask each word
                    replacement = " ".join(self._mask_word(w) for w in matched_str.split())
                else:
                    replacement = self._mask_word(matched_str)

            result = result[:start] + replacement + result[end:]

        return result, True

    def add_blacklist_term(self, term: str) -> bool:
        term_clean = term.strip().lower()
        if not term_clean:
            return False
        if term_clean not in [x.lower() for x in self.config.custom_blacklist]:
            self.config.custom_blacklist.append(term.strip())
            self.rebuild_dictionary()
            return True
        return False

    def remove_blacklist_term(self, term: str) -> bool:
        term_clean = term.strip().lower()
        original_len = len(self.config.custom_blacklist)
        self.config.custom_blacklist = [
            x for x in self.config.custom_blacklist if x.strip().lower() != term_clean
        ]
        if len(self.config.custom_blacklist) < original_len:
            self.rebuild_dictionary()
            return True
        return False

    def add_whitelist_term(self, term: str) -> bool:
        term_clean = term.strip().lower()
        if not term_clean:
            return False
        if term_clean not in [x.lower() for x in self.config.custom_whitelist]:
            self.config.custom_whitelist.append(term.strip())
            self.rebuild_dictionary()
            return True
        return False

    def remove_whitelist_term(self, term: str) -> bool:
        term_clean = term.strip().lower()
        original_len = len(self.config.custom_whitelist)
        self.config.custom_whitelist = [
            x for x in self.config.custom_whitelist if x.strip().lower() != term_clean
        ]
        if len(self.config.custom_whitelist) < original_len:
            self.rebuild_dictionary()
            return True
        return False

    def set_replacement(self, original: str, replacement: str) -> bool:
        orig_clean = original.strip().lower()
        rep_clean = replacement.strip()
        if not orig_clean or not rep_clean:
            return False
        self.config.custom_replacements[orig_clean] = rep_clean
        self.rebuild_dictionary()
        return True

    def remove_replacement(self, original: str) -> bool:
        orig_clean = original.strip().lower()
        if orig_clean in self.config.custom_replacements:
            del self.config.custom_replacements[orig_clean]
            self.rebuild_dictionary()
            return True
        return False

    def get_filter_state(self) -> dict:
        """Return structured state of active filters and word lists."""
        return {
            "enabled": self.config.enabled,
            "mode": self.config.mode,
            "categories": {
                "standard_profanity": self.config.filter_standard_profanity,
                "church_blasphemy": self.config.filter_church_blasphemy,
                "crude_terms": self.config.filter_crude_terms,
            },
            "custom_blacklist": self.config.custom_blacklist,
            "custom_whitelist": self.config.custom_whitelist,
            "custom_replacements": self.config.custom_replacements,
            "active_rules_count": len(self._blacklist_patterns),
        }

