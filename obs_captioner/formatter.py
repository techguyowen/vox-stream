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
        # Months
        "january": "January",
        "february": "February",
        "march": "March",
        "april": "April",
        "may": "May",
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
        "lets": "let's",
        "let's": "let's",
        "youre": "you're",
        "you're": "you're",
        "theyre": "they're",
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

    # Question starter words/phrases
    QUESTION_STARTERS = re.compile(
        r"^(what|why|how|when|where|who|whom|whose|which|is|are|am|was|were|"
        r"do|does|did|can|could|would|should|will|won't|shall|has|have|had|"
        r"aren't|isn't|wasn't|weren't|don't|doesn't|didn't|can't|couldn't|"
        r"wouldn't|shouldn't|hasn't|haven't|hadn't)\b",
        re.IGNORECASE,
    )

    # Question ending tags
    QUESTION_ENDINGS = re.compile(
        r"\b(right|correct|you know|isn't it|aren't they|don't you think|huh)\s*$",
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
            master_dict.update(ChurchLexiconFormatter.BOOKS_OF_BIBLE)
            master_dict.update(ChurchLexiconFormatter.CHURCH_TERMS)

        self._lookup = {k.lower(): v for k, v in master_dict.items()}
        sorted_keys = sorted(self._lookup.keys(), key=len, reverse=True)
        if sorted_keys:
            self._master_pattern = re.compile(rf"\b({'|'.join(map(re.escape, sorted_keys))})\b", re.IGNORECASE)
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

        # Check for conjunction clauses in long sentences (>14 words) without existing punctuation
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
