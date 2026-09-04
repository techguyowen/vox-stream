import re
from typing import Dict, Set
from .church_lexicon import ChurchLexiconFormatter


class TextFormatter:
    """Restores capitalization, contractions, proper nouns, acronyms, and sentence-ending punctuation."""

    # Words to always capitalize (Proper nouns, days, months, acronyms, tech brands)
    PROPER_NOUNS: Dict[str, str] = {
        # Days
        "monday": "Monday",
        "tuesday": "Tuesday",
        "wednesday": "Wednesday",
        "thursday": "Thursday",
        "friday": "Friday",
        "saturday": "Saturday",
        "sunday": "Sunday",
        # Months ("may" and "march" are omitted: they are far more often the
        # modal verb / the verb "to march" in live speech than the month)
        "january": "January",
        "february": "February",
        "april": "April",
        "june": "June",
        "july": "July",
        "august": "August",
        "september": "September",
        "october": "October",
        "november": "November",
        "december": "December",
        # Tech & Platforms
        "obs": "OBS",
        "youtube": "YouTube",
        "twitch": "Twitch",
        "discord": "Discord",
        "google": "Google",
        "apple": "Apple",
        "microsoft": "Microsoft",
        "mac": "Mac",
        "macbook": "MacBook",
        "macos": "macOS",
        "windows": "Windows",
        "linux": "Linux",
        "ios": "iOS",
        "android": "Android",
        "chatgpt": "ChatGPT",
        "gemini": "Gemini",
        "openai": "OpenAI",
        "whisper": "Whisper",
        "vosk": "Vosk",
        "moonshine": "Moonshine",
        "kaldi": "Kaldi",
        "opendyslexic": "OpenDyslexic",
        "silero": "Silero",
        "intel": "Intel",
        "nvidia": "NVIDIA",
        "amd": "AMD",
        "rtx": "RTX",
        "gtx": "GTX",
        "cpu": "CPU",
        "gpu": "GPU",
        "ram": "RAM",
        "ssd": "SSD",
        "usb": "USB",
        "hdmi": "HDMI",
        "bluetooth": "Bluetooth",
        "wifi": "Wi-Fi",
        "api": "API",
        "apis": "APIs",
        "sdk": "SDK",
        "html": "HTML",
        "css": "CSS",
        "javascript": "JavaScript",
        "python": "Python",
        "github": "GitHub",
        "reddit": "Reddit",
        "twitter": "Twitter",
        "tiktok": "TikTok",
        "instagram": "Instagram",
        "facebook": "Facebook",
        "stt": "STT",
        "vad": "VAD",
        "fps": "FPS",
        "db": "dB",
        "url": "URL",
        "http": "HTTP",
        "https": "HTTPS",
        "ws": "WS",
        "wss": "WSS",
        # Nationalities / Places / Common Names
        "america": "America",
        "american": "American",
        "usa": "USA",
        "uk": "UK",
        "canada": "Canada",
        "canadian": "Canadian",
        "english": "English",
        "spanish": "Spanish",
        "french": "French",
        "german": "German",
        "japanese": "Japanese",
        "chinese": "Chinese",
        "god": "God",
        "jesus": "Jesus",
        "christ": "Christ",
        "lord": "Lord",
    }

    # Standard spoken contractions dictionary
    CONTRACTIONS: Dict[str, str] = {
        "i": "I",
        "i'm": "I'm",
        "im": "I'm",
        "i'll": "I'll",
        "i'd": "I'd",
        "i've": "I've",
        "ive": "I've",
        "dont": "don't",
        "don't": "don't",
        "cant": "can't",
        "can't": "can't",
        "wont": "won't",
        "won't": "won't",
        "didnt": "didn't",
        "didn't": "didn't",
        "doesnt": "doesn't",
        "doesn't": "doesn't",
        "isnt": "isn't",
        "isn't": "isn't",
        "arent": "aren't",
        "aren't": "aren't",
        "wasnt": "wasn't",
        "wasn't": "wasn't",
        "werent": "weren't",
        "weren't": "weren't",
        "havent": "haven't",
        "haven't": "haven't",
        "hasnt": "hasn't",
        "hasn't": "hasn't",
        "hadnt": "hadn't",
        "hadn't": "hadn't",
        "couldnt": "couldn't",
        "couldn't": "couldn't",
        "shouldnt": "shouldn't",
        "shouldn't": "shouldn't",
        "wouldnt": "wouldn't",
        "wouldn't": "wouldn't",
        "thats": "that's",
        "that's": "that's",
        "whats": "what's",
        "what's": "what's",
        "hows": "how's",
        "how's": "how's",
        "wheres": "where's",
        "where's": "where's",
        "whos": "who's",
        "who's": "who's",
        "theres": "there's",
        "there's": "there's",
        "heres": "here's",
        "here's": "here's",
        # no "lets" -> "let's": "she lets him go" is valid English
        "let's": "let's",
        "youre": "you're",
        "you're": "you're",
        "theyre": "they're",
        "youve": "you've",
        "you've": "you've",
        "theyve": "they've",
        "they've": "they've",
        "weve": "we've",
        "we've": "we've",
        "youll": "you'll",
        "you'll": "you'll",
        "theyll": "they'll",
        "they'll": "they'll",
        "it's": "it's",
    }

    # Question starters. Wh-words alone are a strong signal; auxiliary verbs
    # only signal a question when followed by a subject pronoun (subject-aux
    # inversion: "will you pray"), otherwise they are usually imperatives or
    # statements ("do not be afraid", "have faith in God").
    QUESTION_STARTERS = re.compile(
        r"^(?:(?:what|why|how|when|where|who|whom|whose|which)\b"
        r"|(?:is|are|am|was|were|do|does|did|can|could|would|should|will|shall|"
        r"has|have|had|won't|aren't|isn't|wasn't|weren't|don't|doesn't|didn't|"
        r"can't|couldn't|wouldn't|shouldn't|hasn't|haven't|hadn't)"
        r"\s+(?:i|you|we|they|he|she|it|there|this|that|anyone|anybody|someone|somebody)\b)",
        re.IGNORECASE,
    )

    # Question ending tags. Deliberately narrow: filler endings like
    # "you know" / "right" are common in declarative sermon speech.
    QUESTION_ENDINGS = re.compile(
        r"\b(isn't it|aren't they|don't you think|huh)\s*$",
        re.IGNORECASE,
    )

    # Exclamation starters
    EXCLAMATION_STARTERS = re.compile(
        r"^(wow|omg|oh my god|holy cow|no way|awesome|incredible|congratulations|yay|hooray|look out|watch out|stop|help|hurray|bravo)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        auto_capitalization: bool = True,
        auto_punctuation: bool = True,
        church_mode: bool = True,
    ):
        self.auto_capitalization = auto_capitalization
        self.auto_punctuation = auto_punctuation
        self.church_mode = church_mode
        self.church_formatter = ChurchLexiconFormatter(enabled=church_mode)

        # Compile unified master dictionary for single-pass replacement
        master_dict = {}
        if self.auto_capitalization:
            master_dict.update(self.PROPER_NOUNS)
            master_dict.update(self.CONTRACTIONS)
        if self.church_mode:
            # Ambiguous book words (job, mark, acts, ...) are excluded from the
            # blind pass; the citation regexes still capitalize them in context.
            master_dict.update({
                k: v for k, v in ChurchLexiconFormatter.BOOKS_OF_BIBLE.items()
                if k not in ChurchLexiconFormatter.AMBIGUOUS_BOOK_WORDS
            })
            master_dict.update(ChurchLexiconFormatter.CHURCH_TERMS)

        self._lookup = {k.lower(): v for k, v in master_dict.items()}
        sorted_keys = sorted(self._lookup.keys(), key=len, reverse=True)
        if sorted_keys:
            # (?!\w) instead of a trailing \b so keys ending in an apostrophe
            # (e.g. "jesus'") can still match before a space or punctuation.
            self._master_pattern = re.compile(rf"\b({'|'.join(map(re.escape, sorted_keys))})(?!\w)", re.IGNORECASE)
        else:
            self._master_pattern = None

    def format_text(self, text: str, is_final: bool = True) -> str:
        """Apply church terms, capitalization, and punctuation in an ultra-fast single-pass pipeline."""
        if not text:
            return ""

        text = text.strip()
        if not text:
            return ""

        # 1. Fast scripture reference parsing (only if digits or numbers present)
        if self.church_mode and self.church_formatter:
            if any(c.isdigit() for c in text) or ("chapter" in text.lower()) or ("verse" in text.lower()) or ("psalm" in text.lower()):
                text = self.church_formatter._format_scripture_citations(text)

        # 1b. Normalize spoken ordinals/times AFTER scripture citations are resolved
        # (so verse numbers like "three sixteen" → "3:16" are protected from ordinal rewriting)
        if is_final:
            text = self._normalize_spoken_numbers(text)

        # 2. Single-pass unified dictionary substitution
        if self._master_pattern:
            text = self._master_pattern.sub(lambda m: self._lookup.get(m.group(0).lower(), m.group(0)), text)

        # 3. Capitalize start of sentence and standalone 'I'
        if self.auto_capitalization:
            text = self._capitalize_sentences(text)

        # 4. Final punctuation (only on finalized utterances)
        if is_final and self.auto_punctuation:
            text = self._apply_punctuation(text)

        return text

    def _normalize_spoken_numbers(self, text: str) -> str:
        """Convert spoken ordinals and times to compact numeric form.

        Examples:
          'November twenty fourth'  → 'November 24th'
          'ten thirty in the morning' → '10:30 in the morning'
          'thirty second Sunday'    → '32nd Sunday'
        Skips tokens already formatted as scripture references (contain ':').
        """
        from .church_lexicon import ChurchLexiconFormatter

        UNITS = ChurchLexiconFormatter.UNITS
        TENS = ChurchLexiconFormatter.TENS

        ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}

        def ordinal_suffix(n: int) -> str:
            if 11 <= (n % 100) <= 13:
                return "th"
            return ORDINAL_SUFFIXES.get(n % 10, "th")

        # Pattern: optional tens word + units word + "th/st/nd/rd" → ordinal digit
        # e.g. "twenty fourth" → "24th", "first" → "1st"
        ORDINAL_WORDS = {
            "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
            "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
            "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
            "nineteenth": 19, "twentieth": 20, "twenty-first": 21, "twenty-second": 22,
            "twenty-third": 23, "twenty-fourth": 24, "twenty-fifth": 25,
            "twenty-sixth": 26, "twenty-seventh": 27, "twenty-eighth": 28,
            "twenty-ninth": 29, "thirtieth": 30, "thirty-first": 31,
        }

        ORDINAL_UNITS = {
            "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
        }

        # Build compound ordinal pattern: "twenty fourth", "thirty second", etc.
        tens_pat = "|".join(TENS.keys())
        units_pat = "|".join(UNITS.keys())
        ord_units_pat = "|".join(ORDINAL_UNITS.keys())

        def replace_compound_ordinal(m):
            tens_word = m.group(1).lower()
            ord_word = m.group(2).lower()
            n = TENS.get(tens_word, 0) + ORDINAL_UNITS.get(ord_word, 0)
            if n <= 0:
                return m.group(0)
            return f"{n}{ordinal_suffix(n)}"

        text = re.sub(
            rf"\b({tens_pat})[- ]+({ord_units_pat})\b(?!\s*:)",
            replace_compound_ordinal,
            text,
            flags=re.IGNORECASE,
        )

        # Replace standalone ordinal words
        def replace_ordinal_word(m):
            word = m.group(0).lower().replace("-", "")
            n = ORDINAL_WORDS.get(word) or ORDINAL_WORDS.get(m.group(0).lower())
            if n is None:
                return m.group(0)
            return f"{n}{ordinal_suffix(n)}"

        ordinal_pattern = re.compile(
            r"\b(" + "|".join(re.escape(k) for k in sorted(ORDINAL_WORDS, key=len, reverse=True)) + r")\b",
            re.IGNORECASE,
        )
        text = ordinal_pattern.sub(replace_ordinal_word, text)

        # Spoken time: "ten thirty" / "ten o'clock" → "10:30" / "10:00"
        hour_words = {**UNITS, **TENS}

        def replace_time(m):
            hour_word = m.group(1).lower()
            minute_word = m.group(2).lower() if m.group(2) else None
            hour = hour_words.get(hour_word, 0)
            if minute_word and minute_word != "o'clock":
                minute = hour_words.get(minute_word, 0)
            else:
                minute = 0
            if hour == 0:
                return m.group(0)
            return f"{hour}:{minute:02d}"

        minute_words = "|".join(list(TENS.keys()) + list(UNITS.keys()))
        text = re.sub(
            rf"\b({tens_pat}|{units_pat})[- ]+({minute_words}|o'clock)\b(?=\s+(?:in the|am|pm|a\.m|p\.m))",
            replace_time,
            text,
            flags=re.IGNORECASE,
        )

        return text

    def _capitalize_sentences(self, text: str) -> str:
        """Capitalizes the first character of text and characters following sentence terminators."""
        if not text:
            return ""

        # Capitalize first character
        chars = list(text)
        for i, c in enumerate(chars):
            if c.isalpha():
                chars[i] = c.upper()
                break
        text = "".join(chars)

        # Capitalize after sentence terminators: . ? ! followed by whitespace
        def cap_match(m):
            return m.group(1) + m.group(2).upper()

        text = re.sub(r"([.?!]\s+)([a-z])", cap_match, text)

        # Ensure standalone 'i' is always capitalized
        text = re.sub(r"\b(i)\b", "I", text)

        return text

    def _apply_punctuation(self, text: str) -> str:
        """Restores sentence-ending punctuation based on grammatical intent."""
        if not text:
            return ""

        # If already ends in punctuation, preserve it
        if text[-1] in ".?!,:;…":
            return text

        # Check for conjunction clauses in long sentences (12+ words) without existing punctuation
        words = text.split()
        if len(words) >= 12:
            # Insert natural commas before major conjunctions if no commas present
            if "," not in text:
                text = re.sub(
                    r"(\s+)(but|however|although|whereas|yet|because)(\s+)",
                    r", \2\3",
                    text,
                    count=1,
                    flags=re.IGNORECASE,
                )

        # Determine terminal punctuation (? or ! or .)
        if self.QUESTION_STARTERS.search(text) or self.QUESTION_ENDINGS.search(text):
            return text + "?"
        elif self.EXCLAMATION_STARTERS.search(text):
            return text + "!"
        else:
            return text + "."
