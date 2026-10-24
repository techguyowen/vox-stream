from pathlib import Path
import re
from typing import Dict, Optional, Set, Tuple
from .church_lexicon import ChurchLexiconFormatter


# Precompiled hallucination and prompt-leak detection patterns
_LEAK_BULLET_PREFIX_PATTERN = re.compile(r"^[\u25a0-\u25ff\ufffd\u2022\[\]\s]*['\"]")
_LEAK_REPR_LIST_PATTERN = re.compile(r"['\"][^'\"]{1,50}['\"]\s*,\s*['\"][^'\"]{1,50}['\"]")
_LEAK_TOKEN_REPETITION_PATTERN = re.compile(r"\b(\w+)\b(?:\s*[, '\"]+\s*\1\b){3,}", re.IGNORECASE)

# Precompiled ASR disfluency and phonetic spelling artifact replacements
_DISFLUENCY_REPLACEMENTS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bI't\b"), "I don't"),
    (re.compile(r"\bby by\b", re.IGNORECASE), "bye-bye"),
    (re.compile(r"\bby-by\b", re.IGNORECASE), "bye-bye"),
    (re.compile(r"\bspll\b", re.IGNORECASE), "spell"),
    (re.compile(r"\bslipp\b", re.IGNORECASE), "slip"),
    (re.compile(r"\bgonners\b", re.IGNORECASE), "goners"),
    (re.compile(r"\bcovenn\b", re.IGNORECASE), "coven"),
    (re.compile(r"\bdopple gangers\b", re.IGNORECASE), "doppelgängers"),
    (re.compile(r"\bdoppel ganger\b", re.IGNORECASE), "doppelgänger"),
    (re.compile(r"\bdopple ganger\b", re.IGNORECASE), "doppelgänger"),
    (re.compile(r"\bgum my\b", re.IGNORECASE), "gummy"),
    (re.compile(r"\bbasic ally\b", re.IGNORECASE), "basically"),
    (re.compile(r"\bsun glasses\b", re.IGNORECASE), "sunglasses"),
)

# Precompiled deduplication patterns
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")
_PUNCT_STRIP_PATTERN = re.compile(r"[^\w\s]")
_STUTTER_PATTERN = re.compile(r"\b(\w+(?:[^\w\n]+(?:\w+)){1,5})\s*[,;]?\s+\1\b", re.IGNORECASE)

# Precompiled camelCase missing spaces pattern
_CAMELCASE_PATTERN = re.compile(r"([a-z])([A-Z])")

# Prefixes for concatenated word repair
_CONCAT_PREFIXES: Tuple[str, ...] = (
    # 5 chars
    "hired", "takes", "there", "whole",
    # 4 chars
    "dead", "feed", "from", "give", "said", "send", "that", "they", "this", "took", "with",
    # 3 chars
    "all", "and", "for", "her", "his", "not", "our", "she", "the", "you",
    # 2 chars
    "as", "at", "by", "he", "if", "in", "is", "it", "my", "no", "of", "on", "or", "so", "to", "we",
)

_CONCAT_SUFFIXES: Tuple[str, ...] = ("there", "here", "again", "also", "now", "away", "us")

_VALID_2LETTER_WORDS: Set[str] = {
    "go", "to", "in", "up", "on", "at", "do", "so", "me", "us", "no",
    "he", "we", "by", "if", "or", "as", "is", "it", "my", "am", "an", "be", "of",
}

_REPEATED_WORD_WHITELIST: Set[str] = {
    "murmur", "bonbon", "chacha", "cancan", "froufrou", "couscous", "beriberi",
    "tartar", "pawpaw", "pompon", "dumdum",
}

# Legitimate single words starting with these prefixes that must NEVER be split
_CONCAT_WHITELIST: Set[str] = {
    # Prompt explicitly specified words
    "today", "together", "towards", "forward", "island", "into", "onto", "inside",
    "often", "history", "formal", "format", "forbid", "forget", "forgive", "forest",
    "forever", "theme", "theory", "theater", "there", "their", "these", "those",
    "they", "then", "them", "other", "another", "otherwise",
    # Additional common single words and inflections
    "one", "ones", "only", "once", "onto", "too", "inn", "inns",
    "toward", "tonight", "tomorrow", "total", "totally", "touch", "touched", "touching",
    "tough", "tour", "tourist", "tournament", "towel", "tower", "town", "toxic", "toy",
    "toast", "toad", "topic", "topics", "tone", "tones", "tongue", "torch",
    "forbear", "forbade", "force", "forces", "forced", "forcing", "foreign", "foreigner",
    "forevermore", "forgetful", "forgets", "forgetting", "forgot", "forgotten", "forgives",
    "forgiving", "forgiven", "forgave", "forge", "forged", "forgery", "fork", "form",
    "forms", "formed", "forming", "formation", "former", "formerly", "formula", "forth",
    "fortress", "fortresses", "fortune", "forty", "forwards",
    "within", "without", "withdraw", "withdrawal", "withdrawn", "withdrew", "withhold",
    "withheld", "withstand", "withstood", "wither", "withered",
    "myself", "ourselves", "ourself", "herself", "himself",
    "indeed", "income", "insight", "instead", "inward", "inwards", "involve", "involved",
    "involvement", "involving", "intend", "intended", "intent", "intents", "intention",
    "intact", "invent", "invention", "inform", "information", "informed", "interest",
    "interested", "interesting", "interests", "injury", "injuries", "injured", "incident",
    "incidents", "include", "included", "including", "includes", "inclusive", "increase",
    "increased", "increases", "increasing", "indicate", "indicated", "indicates",
    "indication", "individual", "individuals", "industry", "industrial", "influence",
    "influenced", "influences", "initial", "initially", "initiative", "innocent", "innocence",
    "input", "inputs", "inquiry", "inquiries", "inquire", "inside", "insides", "insist",
    "insisted", "insists", "inspect", "inspection", "inspector", "inspire", "inspired",
    "inspiration", "install", "installed", "installation", "instance", "instances", "instant",
    "instantly", "institute", "institution", "instruct", "instruction", "instructions",
    "instructor", "instrument", "instruments", "insurance", "integrate", "integrated",
    "integration", "integrity", "intellect", "intelligence", "intelligent", "intense",
    "intensity", "interact", "interaction", "interactive", "interior", "internal", "interpret",
    "interpretation", "interval", "intervals", "interview", "interviews", "intimate",
    "introduce", "introduced", "introduction", "invade", "invalid", "invest", "invested",
    "investing", "investment", "investigate", "investigation", "invitation", "invitations",
    "invite", "invited", "inviting", "invites", "invoke", "invoked", "invoking",
    "attempt", "attempted", "attempts", "attend", "attended", "attending", "attends",
    "attention", "attitude", "attorney", "attract", "attracted", "attraction", "attribute",
    "atmosphere", "atom", "atomic", "attach", "attached", "attack", "attacked", "attacks",
    "onset", "ongoing", "online", "oneself", "only", "onion", "onward", "onwards",
    "bypass", "bypassed", "byway", "byways", "byword", "bywords", "bystander", "bystanders",
    "byline", "byproduct",
    "given", "gives", "giver", "givers", "giving",
    "sends", "sender", "senders", "sending",
    "hero", "heroes", "heroic", "herb", "herbs", "herd", "herds", "heritage", "here", "hers",
    "historic", "historical", "historian", "hiss", "hissed", "hissing",
    "islands", "isle", "isles", "isolate", "isolated", "isolation", "issue", "issues", "issued",
    # Guard words for expanded prefixes:
    # For 'not' / 'no':
    "notice", "nothing", "notify", "notion", "noble", "normal", "north", "novel", "nostril",
    "notable", "notch", "note", "noted", "notes", "notorious", "notwithstanding", "now",
    "nowhere", "nobody", "nod", "none", "noon",
    # For 'as':
    "asleep", "aside", "ask", "asked", "asking", "ashes", "aspect", "aspire", "assert",
    "assist", "assume", "assure", "astonish", "astray",
    # For 'so':
    "solar", "solid", "solve", "sought", "soul", "sound", "sour", "source", "south",
    "sovereign", "sorry", "sorrow", "soap", "sober", "some", "someone", "something",
    "somewhere", "sometimes", "somewhat", "somebody", "soon", "sole",
    # For 'all':
    "always", "allay", "allege", "allow", "alloy", "allude", "allure", "ally", "almost",
    "alone", "along", "already", "also", "altar", "alter", "although", "altogether", "alley",
    # For 'it':
    "item", "items", "itself", "iterate", "itinerary", "its",
    # For 'he':
    "help", "held", "hello", "heavy", "heart", "heat", "head", "health", "healthy",
    "heaven", "hearing", "height", "heal", "heals", "healing", "heel", "heels", "heavens",
    # For 'she':
    "sheep", "sheet", "shelter", "shepherd", "shed", "shell", "shelf", "shelves",
    # For 'we':
    "wear", "went", "wedding", "week", "weekend", "welcome", "weight", "west", "western", "wealth",
    # For 'they':
    "they're", "they've",
    # For 'this':
    "thistle",
    # For 'there':
    "thereby", "therefore", "therein", "thereof", "thereon", "thereto", "thereupon", "therewith",
    # For 'you':
    "young", "youth", "your", "yours", "yourself", "yourselves",
    # For 'away' / suffixes:
    "runaway", "castaway", "giveaway", "straightaway",
    # For 'or':
    "order", "ordered", "ordering", "orders", "ordinance", "ordinary", "organ", "origin", "original", "orphan", "orphans",
    # Calendar & broadcasting compound words:
    "broadcast", "broadcasting", "broadcasts", "broadcaster", "broadcasters", "broadcasted",
    "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "yesterday", "tomorrow", "tonight", "weekend", "weekdays", "weekday",
    "background", "foreground", "underground", "anyway", "anyhow", "anymore",
    # Guard words for 'us' suffix:
    "jesus", "bonus", "status", "focus", "minus", "virus", "chorus", "campus",
    "census", "fungus", "circus", "walrus", "plus", "thus", "versus", "genus",
    "sinus", "nexus", "lotus", "bogus", "hubris", "apparatus", "consensus",
    "impetus", "stimulus", "syllabus", "surplus", "fetus",
    # Compound / BPE-repaired words that must not be split:
    "gummy", "gummies", "basically", "sunglasses",
}

_CONCAT_WORD_PATTERN = re.compile(r"\b([A-Za-z]+)\b")
_KNOWN_WORDS: Set[str] = set()


def _get_known_words() -> Set[str]:
    """Lazy-load master English word list from common vocabulary and bible.db."""
    global _KNOWN_WORDS
    if _KNOWN_WORDS:
        return _KNOWN_WORDS

    words: Set[str] = {
        "raise", "angels", "laborers", "rejoicing", "my", "him", "that", "the", "a", "i",
        "to", "of", "in", "and", "is", "for", "with", "his", "her", "our", "give", "send",
        "from", "at", "on", "by", "you", "we", "they", "me", "us", "he", "she", "it", "them",
        "good", "all", "god", "lord", "church", "father", "son", "jesus", "christ", "spirit",
        "holy", "faith", "love", "grace", "peace", "hope", "glory", "kingdom", "word", "heart",
        "life", "light", "truth", "cross", "blood", "body", "soul", "name", "way", "world",
        "house", "heaven", "earth", "day", "time", "man", "men", "woman", "women", "child",
        "children", "people", "sheep", "lamb", "shepherd", "pastor", "servant", "disciple",
        "disciples", "apostle", "apostles", "brother", "brothers", "sister", "sisters",
        "friend", "friends", "enemy", "enemies", "king", "kings", "priest", "priests",
        "prophet", "prophets", "gospel", "bible", "scripture", "scriptures", "verse",
        "verses", "chapter", "chapters", "book", "books", "prayer", "prayers", "pray",
        "praying", "worship", "worshiping", "praise", "praises", "sing", "singing",
        "song", "songs", "bread", "wine", "cup", "table", "resurrection", "eternal",
        "forever", "salvation", "saved", "save", "mercy", "covenant", "blessing",
        "blessed", "bless", "righteousness", "righteous", "sin", "sins", "sinner",
        "sinners", "forgive", "forgiveness", "repent", "repentance", "power", "honor",
        "majesty", "throne", "crown", "reign", "reigns", "come", "comes", "came",
        "coming", "go", "goes", "went", "going", "gone", "see", "sees", "saw", "seeing",
        "seen", "hear", "hears", "heard", "hearing", "know", "knows", "knew", "knowing",
        "known", "think", "thinks", "thought", "say", "says", "said", "saying", "speak",
        "speaks", "spoke", "spoken", "speaking", "tell", "tells", "told", "telling",
        "ask", "asks", "asked", "asking", "take", "takes", "took", "taken", "taking",
        "make", "makes", "made", "making", "do", "does", "did", "done", "doing",
        "get", "gets", "got", "gotten", "getting", "put", "puts", "putting", "bring",
        "brings", "brought", "bringing", "lead", "leads", "led", "leading", "find",
        "finds", "found", "finding", "seek", "seeks", "sought", "seeking", "stand",
        "stands", "stood", "standing", "walk", "walks", "walked", "walking", "run",
        "runs", "ran", "running", "follow", "follows", "followed", "following", "hold",
        "holds", "held", "holding", "keep", "keeps", "kept", "keeping", "live", "lives",
        "lived", "living", "die", "dies", "died", "dying", "rise", "rises", "rose",
        "risen", "rising", "call", "calls", "called", "calling", "answer", "answers",
        "answered", "answering", "open", "opens", "opened", "opening", "close", "closes",
        "closed", "closing", "great", "small", "high", "low", "first", "last", "new",
        "old", "true", "false", "just", "evil", "wicked", "pure", "clean", "free",
        "many", "few", "much", "little", "more", "most", "less", "least", "every",
        "each", "any", "some", "no", "none", "one", "two", "three", "four", "five",
        "six", "seven", "eight", "nine", "ten", "now", "here", "where", "when", "how",
        "why", "what", "who", "whom", "whose", "which",
        # Broadcasting / live church / sermon domain vocabulary
        "country", "feed", "pigs", "dead", "said", "there", "whole", "even", "care",
        "hired", "himself", "wild", "self", "bread", "pods", "estate", "prodigal",
        "famine", "servants", "parable", "parables", "pastor", "pastors", "balcony",
        "window", "earthquake", "courtyard", "babysitter", "dowry", "communion",
        "doxology", "benediction", "can", "have", "has", "had", "will", "would",
        "could", "should", "are", "were", "been",
        "sermon", "sermons", "fidget", "fidgets",
        "gummy", "gummies", "basically", "sunglasses",
        # Broadcasting & calendar vocabulary
        "broadcast", "broadcasting", "broadcasts", "broadcaster", "broadcasters",
        "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
        "yesterday", "tomorrow", "tonight", "weekend", "weekdays", "weekday",
        # Common -less suffix words
        "effortless", "endless", "fearless", "flawless", "helpless", "hopeless",
        "restless", "relentless", "sleepless", "speechless", "tireless", "worthless",
        "careless", "harmless", "heartless", "nameless", "painless", "powerless",
        "priceless", "sinless", "timeless", "useless", "weightless",
    }

    db_path = Path(__file__).parent / "data" / "bible.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT text FROM verses")
            for row in cur:
                for w in re.findall(r"[a-zA-Z]+", row[0]):
                    words.add(w.lower())
            conn.close()
        except Exception:
            pass

    _KNOWN_WORDS = words
    return _KNOWN_WORDS


def _format_split(w: str, idx: int) -> str:
    """Format a 2-part word split preserving original casing according to grammar rules."""
    low = w.lower()
    part1 = low[:idx]
    part2 = low[idx:]
    if w.isupper():
        return f"{part1.upper()} {part2.upper()}"
    elif w[0].isupper():
        return f"{part1.capitalize()} {part2}"
    else:
        return f"{part1} {part2}"


def _split_concat_token(m: re.Match) -> str:
    w = m.group(1)
    low = w.lower()
    if low in _CONCAT_WHITELIST or len(low) < 3:
        return w
    known = _get_known_words()

    # Identical repeated-word check (stutter / glued identical word)
    if len(low) >= 6 and len(low) % 2 == 0:
        half = len(low) // 2
        if low[:half] == low[half:] and low[:half] in known and low not in _REPEATED_WORD_WHITELIST:
            if w[:half].istitle() and w[half:].istitle():
                return f"{w[:half]} {w[half:]}"
            return _format_split(w, half)

    if low in known:
        return w

    # 1. Prefix-based split
    for p in _CONCAT_PREFIXES:
        if low.startswith(p):
            rem = low[len(p):]
            if not rem:
                continue
            if len(rem) == 1 and rem not in ("a", "i"):
                continue
            if rem in known or rem in ("a", "i"):
                return _format_split(w, len(p))

    # 2. Suffix-based split (adverbial / locative trailing words)
    for s in _CONCAT_SUFFIXES:
        if low.endswith(s):
            pref = low[:-len(s)]
            if len(pref) >= 3 and pref in known and low not in _CONCAT_WHITELIST:
                return _format_split(w, len(pref))

    # 3. General 2-word compound fallback for unknown tokens
    if low not in known:
        for i in range(3, len(low) - 1):
            w1, w2 = low[:i], low[i:]
            if len(w2) == 2 and w2 not in _VALID_2LETTER_WORDS:
                continue
            if w1 in known and w2 in known:
                return _format_split(w, i)

    return w

# Precompiled punctuation spacing normalization patterns
_KNOWN_TLDS_STR = r"(?:com|org|net|edu|gov|io|co|tv|app|dev|church|ai|info|xyz|us|uk|ca)"
_PUNCT_LEADING_SPACE_PATTERN = re.compile(r"\s+([,.:;?!])")
_PUNCT_FOLLOWING_LETTER_PATTERN = re.compile(r"([,;?!])([a-zA-Z])")
_COLON_FOLLOWING_LETTER_PATTERN = re.compile(r"(:)([a-zA-Z])")
_PERIOD_CAP_PATTERN = re.compile(r"([a-zA-Z0-9]+)(\.)([A-Z])")
_PERIOD_LOWER_PATTERN = re.compile(r"\b([a-zA-Z]+)\.([a-z]+)\b")
_KNOWN_TLD_PATTERN = re.compile(rf"^{_KNOWN_TLDS_STR}\b", re.IGNORECASE)
_PUNCT_DOUBLE_QUOTE_PAREN_PATTERN = re.compile(r"((?<=\S)[.?!,;:]?[\"”\)\]\}])([a-zA-Z])")
_PUNCT_SINGLE_QUOTE_PATTERN = re.compile(r"([.?!,;:][\'’])([a-zA-Z])")
_PUNCT_QUOTE_PAREN_PATTERN = _PUNCT_DOUBLE_QUOTE_PAREN_PATTERN
_MULTI_SPACE_PATTERN = re.compile(r"[ \t]+")

# Precompiled ASR glitch patterns
_NINETY_NINE_GLITCH_PATTERN = re.compile(r"\b90:09\b")

# Precompiled spoken numbers normalization patterns & tables
_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return _ORDINAL_SUFFIXES.get(n % 10, "th")


_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "twenty-first": 21, "twenty-second": 22,
    "twenty-third": 23, "twenty-fourth": 24, "twenty-fifth": 25,
    "twenty-sixth": 26, "twenty-seventh": 27, "twenty-eighth": 28,
    "twenty-ninth": 29, "thirtieth": 30, "thirty-first": 31,
}

_ORDINAL_UNITS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
}

_TENS_PAT = "|".join(ChurchLexiconFormatter.TENS.keys())
_UNITS_PAT = "|".join(ChurchLexiconFormatter.UNITS.keys())
_ORD_UNITS_PAT = "|".join(_ORDINAL_UNITS.keys())
_MINUTE_WORDS = "|".join(list(ChurchLexiconFormatter.TENS.keys()) + list(ChurchLexiconFormatter.UNITS.keys()))
_HOUR_WORDS = {**ChurchLexiconFormatter.UNITS, **ChurchLexiconFormatter.TENS}

_COMPOUND_ORDINAL_PATTERN = re.compile(
    rf"\b({_TENS_PAT})[- ]+({_ORD_UNITS_PAT})\b(?!\s*:)",
    re.IGNORECASE,
)

_STANDALONE_ORDINAL_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_ORDINAL_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_SPOKEN_TIME_PATTERN = re.compile(
    rf"\b({_UNITS_PAT})[- ]+({_MINUTE_WORDS}|o'clock)\b(?=\s+(?:in the (?:morning|afternoon|evening)|am|pm|a\.m|p\.m)\b)",
    re.IGNORECASE,
)


def _replace_compound_ordinal(m):
    tens_word = m.group(1).lower()
    ord_word = m.group(2).lower()
    n = ChurchLexiconFormatter.TENS.get(tens_word, 0) + _ORDINAL_UNITS.get(ord_word, 0)
    if n <= 0:
        return m.group(0)
    return f"{n}{_ordinal_suffix(n)}"


def _replace_ordinal_word(m):
    word = m.group(0).lower().replace("-", "")
    n = _ORDINAL_WORDS.get(word) or _ORDINAL_WORDS.get(m.group(0).lower())
    if n is None:
        return m.group(0)
    return f"{n}{_ordinal_suffix(n)}"


def _replace_time(m):
    hour_word = m.group(1).lower()
    minute_word = m.group(2).lower() if m.group(2) else None
    hour = _HOUR_WORDS.get(hour_word, 0)
    if minute_word and minute_word != "o'clock":
        minute = _HOUR_WORDS.get(minute_word, 0)
    else:
        minute = 0
    if hour == 0 or hour > 12 or minute >= 60:
        return m.group(0)
    return f"{hour}:{minute:02d}"


def _replace_period_cap(m):
    prefix = m.group(1)
    dot = m.group(2)
    next_char = m.group(3)
    if len(prefix) == 1 and prefix.isupper():
        return m.group(0)
    return f"{prefix}{dot} {next_char}"


def _replace_period_lower(m):
    prefix = m.group(1)
    next_word = m.group(2)
    if _KNOWN_TLD_PATTERN.match(next_word):
        return m.group(0)
    if prefix.lower() in ("e", "i") and next_word.lower() in ("g", "e"):
        return m.group(0)
    if len(prefix) == 1 and prefix.isupper():
        return m.group(0)
    return f"{prefix}. {next_word}"


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
        church_name: str = "Waypoint Church",
    ):
        self.auto_capitalization = auto_capitalization
        self.auto_punctuation = auto_punctuation
        self.church_mode = church_mode
        self.church_name = church_name
        self.church_formatter = ChurchLexiconFormatter(enabled=church_mode, church_name=church_name)

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
            master_dict.update(self.church_formatter.get_all_church_terms())

        self._lookup = {k.lower(): v for k, v in master_dict.items()}
        sorted_keys = sorted(self._lookup.keys(), key=len, reverse=True)
        if sorted_keys:
            # (?!\w) and (?<!\w) so keys ending/starting with punctuation (e.g. "jesus'", "dog. solid g") match cleanly
            self._master_pattern = re.compile(rf"(?<!\w)({'|'.join(map(re.escape, sorted_keys))})(?!\w)", re.IGNORECASE)
        else:
            self._master_pattern = None

        # Pre-warm concatenated word vocabulary so first live utterance incurs zero DB latency
        _get_known_words()

    @staticmethod
    def de_duplicate_phrases(text: str) -> str:
        """Collapse immediate identical sentence repetitions and stuttered phrase loops.

        Examples:
          'I help our church to continue to be apart. I help our church to continue to be apart.'
          -> 'I help our church to continue to be apart.'
          'we want to, we want to make sure'
          -> 'we want to make sure'
        """
        if not text or len(text) < 6:
            return text

        # 1. Whole-sentence repetition (e.g. "Sentence A. Sentence A.")
        sentences = [s.strip() for s in _SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]
        if len(sentences) >= 2:
            deduped = [sentences[0]]
            for s in sentences[1:]:
                clean_curr = _PUNCT_STRIP_PATTERN.sub("", s).lower().strip()
                clean_prev = _PUNCT_STRIP_PATTERN.sub("", deduped[-1]).lower().strip()
                if clean_curr != clean_prev:
                    deduped.append(s)
            text = " ".join(deduped)

        # 2. Phrase-level stutters (2+ words repeated consecutively, e.g. "we want to we want to")
        for _ in range(2):
            prev = text
            text = _STUTTER_PATTERN.sub(r"\1", text)
            if text == prev:
                break

        return text

    @staticmethod
    def cleanup_asr_disfluencies(text: str) -> str:
        """Clean common acoustic ASR spelling artifacts, broken contractions, and syllable dropouts."""
        if not text or len(text) < 3:
            return text

        for pattern, replacement in _DISFLUENCY_REPLACEMENTS:
            text = pattern.sub(replacement, text)

        return text

    def format_text(self, text: str, is_final: bool = True) -> str:
        """Apply church terms, capitalization, and punctuation in an ultra-fast single-pass pipeline."""
        if not text:
            return ""

        text = text.strip()
        if not text:
            return ""

        # 0. De-duplicate stutters and clean common acoustic artifacts
        text = self.de_duplicate_phrases(text)
        text = self.cleanup_asr_disfluencies(text)
        text = _NINETY_NINE_GLITCH_PATTERN.sub("99", text)

        # 0b. Normalize all-caps transcription output (e.g. Sherpa-ONNX Zipformer CTC tokens)
        if text.isupper() and len(text) > 4:
            text = text.lower()

        # 1. Fast scripture reference parsing (if digits, book names, or citation markers present)
        if self.church_mode and self.church_formatter:
            if any(c.isdigit() for c in text) or getattr(self.church_formatter, "_citation_trigger_pattern", None) and self.church_formatter._citation_trigger_pattern.search(text):
                text = self.church_formatter._format_scripture_citations(text)

        # 1b. Normalize spoken ordinals/times AFTER scripture citations are resolved
        # (so verse numbers like "three sixteen" → "3:16" are protected from ordinal rewriting)
        if is_final:
            text = self._normalize_spoken_numbers(text)

        # 1c. Normalize punctuation spacing (fixes ASR concatenation artifacts like "like.Guys" -> "like. Guys")
        text = self.normalize_punctuation_spacing(text)

        # 1d. Fix camelCase missing spaces (e.g. "areOther" -> "are Other", "peopleTo" -> "people To")
        text = self.fix_camelcase_missing_spaces(text)

        # 1e. Fix concatenated common words (e.g. "toraise" -> "to raise", "theangels" -> "the angels")
        text = self.fix_concatenated_words(text)

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

    def format_caption(self, text: str, is_final: bool = True) -> str:
        """Format caption text, repairing glitches and applying standard normalization."""
        text = _NINETY_NINE_GLITCH_PATTERN.sub("99", text)
        return self.format_text(text, is_final=is_final)

    def _normalize_spoken_numbers(self, text: str) -> str:
        """Convert spoken ordinals and times to compact numeric form.

        Examples:
          'November twenty fourth'  → 'November 24th'
          'ten thirty in the morning' → '10:30 in the morning'
          'thirty second Sunday'    → '32nd Sunday'
        Skips tokens already formatted as scripture references (contain ':').
        """
        text = _NINETY_NINE_GLITCH_PATTERN.sub("99", text)
        text = _COMPOUND_ORDINAL_PATTERN.sub(_replace_compound_ordinal, text)
        text = _STANDALONE_ORDINAL_PATTERN.sub(_replace_ordinal_word, text)
        text = _SPOKEN_TIME_PATTERN.sub(_replace_time, text)
        return text

    KNOWN_TLDS: str = _KNOWN_TLDS_STR

    @classmethod
    def fix_camelcase_missing_spaces(cls, text: str) -> str:
        """Fix missing spaces between concatenated words (e.g. 'areOther' -> 'are Other')."""
        if not text:
            return ""
        return _CAMELCASE_PATTERN.sub(r"\1 \2", text)

    @classmethod
    def fix_concatenated_words(cls, text: str) -> str:
        """Detect and split concatenated common words (e.g. 'toraise' -> 'to raise')."""
        if not text:
            return ""
        return _CONCAT_WORD_PATTERN.sub(_split_concat_token, text)

    @classmethod
    def normalize_punctuation_spacing(cls, text: str) -> str:
        """Ensure standard whitespace after punctuation marks.

        Fixes ASR token concatenation artifacts like 'looks like.Guys' -> 'looks like. Guys',
        'faith,hope' -> 'faith, hope', and 'amen!Let' -> 'amen! Let', while strictly preserving:
          - Scripture citations and times (e.g. 'John 3:16', '10:30 AM')
          - Numeric decimals and currency (e.g. '$4.50', '3.14', '1,000')
          - Web domains and URLs (e.g. 'waypoint.church', 'google.com', 'https://github.com')
          - Common initialisms and abbreviations (e.g. 'U.S.A.', 'U.S.', 'e.g.', 'i.e.')
        """
        if not text:
            return ""

        # 1. Strip accidental whitespace before punctuation (e.g. "word ." -> "word.")
        text = _PUNCT_LEADING_SPACE_PATTERN.sub(r"\1", text)

        # 1b. Redundant trailing punctuation after quotes
        text = re.sub(r'([.?!]["\'”’\)\]\}]+)[.,;:…]+', r'\1', text)

        # 1c. Comma-period collision
        text = re.sub(r'\.,', r',', text)

        # 1d. Colliding question/exclamation with period
        text = re.sub(r'([!?]["\'”’]?)\.+', r'\1', text)

        # 2. Add space after commas, semicolons, exclamation marks, and question marks
        # when immediately followed by a letter (e.g. "said,yes" -> "said, yes", "amen!Let" -> "amen! Let")
        text = _PUNCT_FOLLOWING_LETTER_PATTERN.sub(r"\1 \2", text)

        # 3. Add space after colons when immediately followed by a letter (protecting 3:16 and https://)
        text = _COLON_FOLLOWING_LETTER_PATTERN.sub(r"\1 \2", text)

        # 4. Add space after periods when followed by a letter:
        # Case 4a: Period followed by a capital letter (e.g. "looks like.Guys" -> "looks like. Guys")
        text = _PERIOD_CAP_PATTERN.sub(_replace_period_cap, text)

        # Case 4b: Period followed by a lowercase letter, excluding known web domains and initialisms
        text = _PERIOD_LOWER_PATTERN.sub(_replace_period_lower, text)

        # 5. Double quotes and closing parens/brackets followed by letter
        # e.g. 'repents.”the angels' -> 'repents.” the angels', 'end."Next' -> 'end." Next'
        text = _PUNCT_DOUBLE_QUOTE_PAREN_PATTERN.sub(r"\1 \2", text)

        # 5b. Single quotes when preceded by punctuation (avoiding contractions like don’t, father’s)
        text = _PUNCT_SINGLE_QUOTE_PATTERN.sub(r"\1 \2", text)

        # 6. Normalize multiple consecutive spaces
        text = _MULTI_SPACE_PATTERN.sub(" ", text).strip()

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

    # Dangling connectors (conjunctions, prepositions, relative pronouns)
    # When an utterance boundary occurs on these words, suppress terminal punctuation
    # to avoid slicing unfinished clauses into fractured standalone sentences.
    DANGLING_CONNECTORS: Set[str] = {
        "and", "but", "or", "nor", "so", "because", "that", "which", "who", "whom",
        "where", "when", "if", "while", "though", "although", "to", "of", "in",
        "into", "unto", "with", "for", "as", "than", "then", "since", "until",
        "unless", "whether",
    }

    def _apply_punctuation(self, text: str) -> str:
        """Restores sentence-ending punctuation based on grammatical intent."""
        if not text:
            return ""

        words = text.split()
        if not words:
            return text

        # Check for conjunction clauses in long sentences (12+ words) without existing punctuation
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
            terminal_punct = "?"
        elif self.EXCLAMATION_STARTERS.search(text):
            terminal_punct = "!"
        else:
            terminal_punct = "."

        # Check if text ends on a dangling connector word (conjunction/preposition)
        last_word_clean = words[-1].lower().rstrip(".,?!;:…\"'”’)]}")
        if last_word_clean in self.DANGLING_CONNECTORS and terminal_punct == ".":
            # Strip false terminal punctuation if model appended it to a dangling connector
            return text.rstrip(".,?!;:…")

        # If terminal punctuation already exists inside closing quotation marks or parens/brackets
        if re.search(r'[.?!]["\'”’\)\]\}]+$', text):
            return text

        # If text ends with closing quotation marks or parens/brackets without terminal punctuation inside
        closing_quote_match = re.search(r'(["\'”’\)\]\}]+)$', text)
        if closing_quote_match:
            quotes = closing_quote_match.group(1)
            prefix = text[:-len(quotes)].rstrip(".,?!;:… \t")
            return f"{prefix}{terminal_punct}{quotes}"

        # Standard terminal punctuation
        if terminal_punct in ("?", "!"):
            return text.rstrip(".,?!;:…") + terminal_punct
        elif text[-1] in ".?!,:;…":
            return text
        else:
            return text + "."


def is_hallucinated_or_leaked_text(text: str) -> bool:
    """Detect raw prompt leaks, bracketed token arrays, or LLM degenerative repetition loops.

    Prevents raw Python/JSON list representations (e.g., "['1 Corinthians', 'Gospel']"),
    checkbox/markdown bullet artifacts ("□'Jesus'"), comma-separated quotes,
    or stutter loops ("'G', 'G', 'G', 'G'") from leaking into live display overlays.
    """
    if not text:
        return False
    s = text.strip()
    # 1. Box / checkbox / unprintable bullet prefix with quotes or brackets: e.g. □'...' or \u25a1'...' or [ ] '...'
    if _LEAK_BULLET_PREFIX_PATTERN.match(s):
        return True
    # 2. Raw Python / JSON list syntax: e.g. ["a", "b"] or ['a', 'b'].
    # Plain sound tags ("[Applause]", "[Music]") are legitimate SDH-style
    # captions, so brackets alone don't qualify — list leaks carry commas
    # and/or quote characters.
    if s.startswith("[") and "]" in s and ("," in s or "'" in s or '"' in s):
        return True
    # 3. Comma-separated quoted strings (repr list leakage): e.g. '1 Corinthians', 'Gospel'
    if _LEAK_REPR_LIST_PATTERN.search(s):
        return True
    # 4. Token repetition loop (degenerative decoding artifact: e.g. 'G', 'G', 'G' or G, G, G, G)
    if _LEAK_TOKEN_REPETITION_PATTERN.search(s):
        return True
    return False


fix_concatenated_words = TextFormatter.fix_concatenated_words


def format_caption(text: str, is_final: bool = True) -> str:
    """Format caption string with default TextFormatter."""
    return TextFormatter().format_caption(text, is_final=is_final)

