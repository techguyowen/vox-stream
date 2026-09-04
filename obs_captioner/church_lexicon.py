"""Church, Ministry, and Biblical Vocabulary & Scripture Citation Formatter."""

import re
from typing import Dict, List, Tuple


class ChurchLexiconFormatter:
    """Comprehensive church vocabulary, sacred names, books of the Bible, and scripture citation restorer."""

    # Numbers dictionary
    UNITS: Dict[str, int] = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
        "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    }

    TENS: Dict[str, int] = {
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    }

    # Books of the Bible (Canonical 66 Books + Variations)
    BOOKS_OF_BIBLE: Dict[str, str] = {
        # Old Testament
        "genesis": "Genesis",
        "exodus": "Exodus",
        "leviticus": "Leviticus",
        "numbers": "Numbers",
        "deuteronomy": "Deuteronomy",
        "joshua": "Joshua",
        "judges": "Judges",
        "ruth": "Ruth",
        "1 samuel": "1 Samuel",
        "first samuel": "1 Samuel",
        "1st samuel": "1 Samuel",
        "2 samuel": "2 Samuel",
        "second samuel": "2 Samuel",
        "2nd samuel": "2 Samuel",
        "samuel": "Samuel",
        "1 kings": "1 Kings",
        "first kings": "1 Kings",
        "1st kings": "1 Kings",
        "2 kings": "2 Kings",
        "second kings": "2 Kings",
        "2nd kings": "2 Kings",
        "kings": "Kings",
        "1 chronicles": "1 Chronicles",
        "first chronicles": "1 Chronicles",
        "1st chronicles": "1 Chronicles",
        "2 chronicles": "2 Chronicles",
        "second chronicles": "2 Chronicles",
        "2nd chronicles": "2 Chronicles",
        "chronicles": "Chronicles",
        "ezra": "Ezra",
        "nehemiah": "Nehemiah",
        "esther": "Esther",
        "job": "Job",
        "psalm": "Psalm",
        "psalms": "Psalms",
        "proverbs": "Proverbs",
        "ecclesiastes": "Ecclesiastes",
        "song of solomon": "Song of Solomon",
        "song of songs": "Song of Songs",
        "isaiah": "Isaiah",
        "jeremiah": "Jeremiah",
        "lamentations": "Lamentations",
        "ezekiel": "Ezekiel",
        "daniel": "Daniel",
        "hosea": "Hosea",
        "joel": "Joel",
        "amos": "Amos",
        "obadiah": "Obadiah",
        "jonah": "Jonah",
        "micah": "Micah",
        "nahum": "Nahum",
        "habakkuk": "Habakkuk",
        "zephaniah": "Zephaniah",
        "haggai": "Haggai",
        "zechariah": "Zechariah",
        "malachi": "Malachi",
        # New Testament
        "matthew": "Matthew",
        "mark": "Mark",
        "luke": "Luke",
        "john": "John",
        "acts": "Acts",
        "acts of the apostles": "Acts of the Apostles",
        "romans": "Romans",
        "1 corinthians": "1 Corinthians",
        "first corinthians": "1 Corinthians",
        "1st corinthians": "1 Corinthians",
        "2 corinthians": "2 Corinthians",
        "second corinthians": "2 Corinthians",
        "2nd corinthians": "2 Corinthians",
        "corinthians": "Corinthians",
        "galatians": "Galatians",
        "ephesians": "Ephesians",
        "philippians": "Philippians",
        "colossians": "Colossians",
        "1 thessalonians": "1 Thessalonians",
        "first thessalonians": "1 Thessalonians",
        "1st thessalonians": "1 Thessalonians",
        "2 thessalonians": "2 Thessalonians",
        "second thessalonians": "2 Thessalonians",
        "2nd thessalonians": "2 Thessalonians",
        "thessalonians": "Thessalonians",
        "1 timothy": "1 Timothy",
        "first timothy": "1 Timothy",
        "1st timothy": "1 Timothy",
        "2 timothy": "2 Timothy",
        "second timothy": "2 Timothy",
        "2nd timothy": "2 Timothy",
        "timothy": "Timothy",
        "titus": "Titus",
        "philemon": "Philemon",
        "hebrews": "Hebrews",
        "james": "James",
        "1 peter": "1 Peter",
        "first peter": "1 Peter",
        "1st peter": "1 Peter",
        "2 peter": "2 Peter",
        "second peter": "2 Peter",
        "2nd peter": "2 Peter",
        "peter": "Peter",
        "1 john": "1 John",
        "first john": "1 John",
        "1st john": "1 John",
        "2 john": "2 John",
        "second john": "2 John",
        "2nd john": "2 John",
        "3 john": "3 John",
        "third john": "3 John",
        "3rd john": "3 John",
        "jude": "Jude",
        "revelation": "Revelation",
        "revelations": "Revelation",
    }

    # Book names that are also common English words. These are only capitalized
    # when they appear in a scripture citation (book + chapter/verse), never by
    # the blind dictionary pass — "acts of kindness", "a great job", "the numbers".
    AMBIGUOUS_BOOK_WORDS = {
        "job", "mark", "acts", "numbers", "judges", "kings",
        "revelation", "revelations",
    }

    # Sacred Names, Titles of Deity, and Common Church Phrases
    CHURCH_TERMS: Dict[str, str] = {
        # Multi-Word Sacred Phrases
        "king of kings and lord of lords": "King of Kings and Lord of Lords",
        "king of kings": "King of Kings",
        "lord of lords": "Lord of Lords",
        "lord jesus christ": "Lord Jesus Christ",
        "jesus christ": "Jesus Christ",
        "christ jesus": "Christ Jesus",
        "holy spirit": "Holy Spirit",
        "the holy spirit": "the Holy Spirit",
        "holy ghost": "Holy Ghost",
        "the holy ghost": "the Holy Ghost",
        "heavenly father": "Heavenly Father",
        "almighty god": "Almighty God",
        "son of god": "Son of God",
        "son of man": "Son of Man",
        "lamb of god": "Lamb of God",
        "prince of peace": "Prince of Peace",
        "light of the world": "Light of the World",
        "bread of life": "Bread of Life",
        "good shepherd": "Good Shepherd",
        "alpha and omega": "Alpha and Omega",
        "in jesus name amen": "in Jesus' name, Amen",
        "in jesus name": "in Jesus' name",
        "in jesus' name": "in Jesus' name",
        "in christ alone": "in Christ alone",
        "in christ": "in Christ",
        "body of christ": "Body of Christ",
        "blood of jesus": "Blood of Jesus",
        "blood of christ": "Blood of Christ",
        "cross of christ": "Cross of Christ",
        "kingdom of god": "Kingdom of God",
        "kingdom of heaven": "Kingdom of Heaven",
        "word of god": "Word of God",
        "praise the lord": "Praise the Lord",
        "praise god": "Praise God",
        "glory to god": "Glory to God",
        "glory be to god": "Glory be to God",
        "thank you jesus": "Thank you Jesus",
        "thank you lord": "Thank you Lord",
        "blessed be the lord": "Blessed be the Lord",
        "blessed be the name of the lord": "Blessed be the name of the Lord",
        "amen amen": "Amen, Amen",
        "tithes and offerings": "Tithes and Offerings",
        "ark of the covenant": "Ark of the Covenant",
        "holy of holies": "Holy of Holies",
        "sermon on the mount": "Sermon on the Mount",
        "ten commandments": "Ten Commandments",
        "the lords supper": "the Lord's Supper",
        "the lord's supper": "the Lord's Supper",
        "the lords prayer": "the Lord's Prayer",
        "the lord's prayer": "the Lord's Prayer",
        "apostles creed": "Apostles' Creed",
        "apostles' creed": "Apostles' Creed",
        "old testament": "Old Testament",
        "new testament": "New Testament",
        # Single Word Sacred Names & Church Terms
        # Note: no "gods" -> "God's" mapping — the plural ("no other gods
        # before me") is not a possessive.
        "god": "God",
        "god's": "God's",
        "lord": "Lord",
        "lord's": "Lord's",
        "jesus": "Jesus",
        "jesus'": "Jesus'",
        "christ": "Christ",
        "christ's": "Christ's",
        "messiah": "Messiah",
        "savior": "Savior",
        "saviour": "Saviour",
        "yahweh": "Yahweh",
        "jehovah": "Jehovah",
        "emmanuel": "Emmanuel",
        "immanuel": "Immanuel",
        "amen": "Amen",
        "hallelujah": "Hallelujah",
        "alleluia": "Alleluia",
        "hosanna": "Hosanna",
        "maranatha": "Maranatha",
        "gospel": "Gospel",
        "scripture": "Scripture",
        "scriptures": "Scriptures",
        "bible": "Bible",
        "calvary": "Calvary",
        "golgotha": "Golgotha",
        "gethsemane": "Gethsemane",
        "resurrection": "Resurrection",
        "crucifixion": "Crucifixion",
        "salvation": "Salvation",
        "communion": "Communion",
        "eucharist": "Eucharist",
        "baptism": "Baptism",
        "sanctuary": "Sanctuary",
        "pastor": "Pastor",
        "apostle": "Apostle",
        "apostles": "Apostles",
        "disciple": "Disciple",
        "disciples": "Disciples",
        "deacon": "Deacon",
        "deacons": "Deacons",
        "elder": "Elder",
        "elders": "Elders",
        "tithe": "Tithe",
        "offering": "Offering",
        "covenant": "Covenant",
        "pentecost": "Pentecost",
        "benediction": "Benediction",
        "doxology": "Doxology",
        # ── Biblical Persons ──────────────────────────────────────────────────
        "abraham": "Abraham",
        "moses": "Moses",
        "noah": "Noah",
        "elijah": "Elijah",
        "elisha": "Elisha",
        "david": "David",
        "solomon": "Solomon",
        "joseph": "Joseph",
        "paul": "Paul",
        "peter": "Peter",
        "stephen": "Stephen",
        "barnabas": "Barnabas",
        "timothy": "Timothy",
        "titus": "Titus",
        "lazarus": "Lazarus",
        "mary": "Mary",
        "mary magdalene": "Mary Magdalene",
        "the virgin mary": "the Virgin Mary",
        "the apostle paul": "the Apostle Paul",
        "the apostle peter": "the Apostle Peter",
        # ── Holy Places ───────────────────────────────────────────────────────
        "jerusalem": "Jerusalem",
        "nazareth": "Nazareth",
        "bethlehem": "Bethlehem",
        "galilee": "Galilee",
        "zion": "Zion",
        "mount zion": "Mount Zion",
        "sinai": "Sinai",
        "mount sinai": "Mount Sinai",
        "bethany": "Bethany",
        "jericho": "Jericho",
        "canaan": "Canaan",
        "jordan": "Jordan",
        "river jordan": "River Jordan",
        "mount of olives": "Mount of Olives",
        "garden of eden": "Garden of Eden",
        "eden": "Eden",
        # ── Deity Titles ──────────────────────────────────────────────────────
        "the trinity": "the Trinity",
        "the godhead": "the Godhead",
        "the father": "the Father",
        "the son": "the Son",
        "triune god": "Triune God",
        "the almighty": "the Almighty",
        "the most high": "the Most High",
        "the great i am": "the Great I Am",
        # ── Doctrinal Terms ───────────────────────────────────────────────────
        "atonement": "Atonement",
        "regeneration": "Regeneration",
        "predestination": "Predestination",
        "eschatology": "Eschatology",
        "hermeneutics": "Hermeneutics",
        "exegesis": "Exegesis",
        "soteriology": "Soteriology",
        "dispensationalism": "Dispensationalism",
        "premillennial": "Premillennial",
        "postmillennial": "Postmillennial",
        "rapture": "Rapture",
        "the rapture": "the Rapture",
        "tribulation": "Tribulation",
        "the great tribulation": "the Great Tribulation",
        "millennium": "Millennium",
        "the millennium": "the Millennium",
        "second coming": "Second Coming",
        "the second coming": "the Second Coming",
        "incarnation": "Incarnation",
        "the incarnation": "the Incarnation",
        "trinity": "Trinity",
        # ── Key Phrases ───────────────────────────────────────────────────────
        "born again": "born again",
        "the great commission": "the Great Commission",
        "great commission": "Great Commission",
        "the great commandment": "the Great Commandment",
        "great commandment": "Great Commandment",
        "fear of the lord": "fear of the Lord",
        "grace of god": "grace of God",
        "mercy of god": "mercy of God",
        "love of god": "love of God",
        "peace of god": "peace of God",
        "fruits of the spirit": "fruits of the Spirit",
        "fruit of the spirit": "fruit of the Spirit",
        "gifts of the spirit": "gifts of the Spirit",
        "armor of god": "armor of God",
        "full armor of god": "full armor of God",
    }

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._compiled_church_patterns: List[Tuple[re.Pattern, str]] = []
        self._build_patterns()

    def _build_patterns(self):
        # 1. Church phrases and terms (sorted by phrase length descending).
        # Ambiguous book words are excluded here; they are still formatted by
        # the citation regexes when chapter/verse context is present.
        all_terms = {}
        all_terms.update({k: v for k, v in self.BOOKS_OF_BIBLE.items() if k not in self.AMBIGUOUS_BOOK_WORDS})
        all_terms.update(self.CHURCH_TERMS)

        sorted_terms = sorted(all_terms.items(), key=lambda x: len(x[0]), reverse=True)
        self._compiled_church_patterns = []
        for orig, rep in sorted_terms:
            escaped = re.escape(orig)
            pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
            self._compiled_church_patterns.append((pattern, rep))

    def _words_to_number(self, text: str) -> int:
        """Converts spoken number words (e.g. 'twenty three', 'eight', '16') to integer."""
        text = text.strip().lower()
        if text.isdigit():
            return int(text)

        parts = text.replace("-", " ").split()
        total = 0
        current = 0
        for part in parts:
            if part in self.UNITS:
                current += self.UNITS[part]
            elif part in self.TENS:
                current += self.TENS[part]
            elif part == "hundred":
                current = (current or 1) * 100
            elif part == "and":
                continue  # "one hundred and nineteen"
            elif part.isdigit():
                current += int(part)
        total += current
        return total

    @classmethod
    def _number_phrase_regex(cls) -> str:
        """Regex matching an atomic English number (1-999 or digits).
        Prevents improper splitting of compound numbers like 'twenty three'."""
        units_pat = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"
        tens_pat = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
        unit_digit_pat = r"(?:one|two|three|four|five|six|seven|eight|nine)"
        strict_tens = rf"{tens_pat}(?!\s+{unit_digit_pat}\b)"
        compound_tens_unit = rf"{tens_pat}[\s\-]+{unit_digit_pat}"
        return rf"(?:(?:{unit_digit_pat}\s+hundred(?:\s+and)?\s+)?(?:{compound_tens_unit}|{strict_tens}|{units_pat})|\d+)"


    def format_church_text(self, text: str) -> str:
        """Apply church terminology capitalization and scripture reference formatting."""
        if not self.enabled or not text:
            return text

        # 1. Format Scripture Citations
        text = self._format_scripture_citations(text)

        # 2. Format Church Terms, Sacred Names, and Books of the Bible
        for pattern, replacement in self._compiled_church_patterns:
            text = pattern.sub(replacement, text)

        return text

    def _format_scripture_citations(self, text: str) -> str:
        """
        Detects spoken scripture references and formats them into standard chapter:verse:
        e.g.
        'romans four one to eight' -> 'Romans 4:1-8'
        'romans four one through eight' -> 'Romans 4:1-8'
        'john three sixteen' -> 'John 3:16'
        'first thessalonians five sixteen to eighteen' -> '1 Thessalonians 5:16-18'
        'first corinthians 13 4 through 7' -> '1 Corinthians 13:4-7'
        'Psalm 23' -> 'Psalm 23'
        """
        books_sorted = sorted(self.BOOKS_OF_BIBLE.keys(), key=len, reverse=True)
        books_regex = "|".join([re.escape(b) for b in books_sorted])
        num_phrase = self._number_phrase_regex()

        # 1. Format digit citations: Book + digit + digit (e.g. "John 3 16", "Romans 8 28")
        digit_pattern = re.compile(
            rf"\b(?P<book>{books_regex})\s+(?P<chap>\d+)(?:[:\s]|,\s*verse\s+)(?P<verse>\d+)(?:\s*(?:through|thru|to|-)\s*(?P<end_verse>\d+))?\b",
            re.IGNORECASE,
        )

        def replace_digits(m):
            raw_book = m.group("book").lower()
            canon_book = self.BOOKS_OF_BIBLE.get(raw_book, raw_book.capitalize())
            chap = m.group("chap")
            verse = m.group("verse")
            end_v = m.group("end_verse")
            res = f"{canon_book} {chap}:{verse}"
            if end_v:
                res += f"-{end_v}"
            return res

        text = digit_pattern.sub(replace_digits, text)

        # 2. Spoken Verse Range: e.g. "romans four one to eight", "first thessalonians five sixteen through eighteen"
        spoken_range_pattern = re.compile(
            rf"\b(?P<book>{books_regex})\s+(?:chapter\s+)?(?P<chap>{num_phrase})\s+(?:verse\s+|verses\s+)?(?P<verse>{num_phrase})\s+(?:through|thru|to|-)\s+(?:verse\s+)?(?P<end_verse>{num_phrase})\b",
            re.IGNORECASE,
        )

        def replace_spoken_range(m):
            raw_book = m.group("book").lower()
            canon_book = self.BOOKS_OF_BIBLE.get(raw_book, raw_book.capitalize())
            c_num = self._words_to_number(m.group("chap"))
            v1_num = self._words_to_number(m.group("verse"))
            v2_num = self._words_to_number(m.group("end_verse"))
            if c_num <= 0 or v1_num <= 0 or v2_num <= 0:
                return m.group(0)
            return f"{canon_book} {c_num}:{v1_num}-{v2_num}"

        text = spoken_range_pattern.sub(replace_spoken_range, text)

        # 3. Spoken Single Citation: e.g. "john three sixteen", "romans eight twenty eight", "genesis one one"
        spoken_single_pattern = re.compile(
            rf"\b(?P<book>{books_regex})\s+(?:chapter\s+)?(?P<chap>{num_phrase})\s+(?:verse\s+)?(?P<verse>{num_phrase})\b",
            re.IGNORECASE,
        )

        def replace_spoken_single(m):
            raw_book = m.group("book").lower()
            canon_book = self.BOOKS_OF_BIBLE.get(raw_book, raw_book.capitalize())
            c_num = self._words_to_number(m.group("chap"))
            v_num = self._words_to_number(m.group("verse"))
            if c_num <= 0 or v_num <= 0:
                return m.group(0)
            return f"{canon_book} {c_num}:{v_num}"

        text = spoken_single_pattern.sub(replace_spoken_single, text)

        # 4. Psalm pattern: "Psalm twenty three" -> "Psalm 23", "psalm one hundred nineteen" -> "Psalm 119"
        psalm_pattern = re.compile(
            rf"\b(?P<psalm>psalms?)\s+(?P<num>{num_phrase})\b",
            re.IGNORECASE,
        )

        def replace_psalm(m):
            num_val = self._words_to_number(m.group("num"))
            if 1 <= num_val <= 150:
                return f"Psalm {num_val}"
            return m.group(0)

        text = psalm_pattern.sub(replace_psalm, text)

        return text
