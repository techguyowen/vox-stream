import urllib.request
import json
import sqlite3
import os
from pathlib import Path

data_dir = Path("obs_captioner/data")
data_dir.mkdir(parents=True, exist_ok=True)
db_path = data_dir / "bible.db"

BOOK_NAMES = [
    # Old Testament (1-39)
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
    "Joshua", "Judges", "Ruth", "1 Samuel", "2 Samuel",
    "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles", "Ezra",
    "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
    "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah", "Lamentations",
    "Ezekiel", "Daniel", "Hosea", "Joel", "Amos",
    "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk",
    "Zephaniah", "Haggai", "Zechariah", "Malachi",
    # New Testament (40-66)
    "Matthew", "Mark", "Luke", "John", "Acts",
    "Romans", "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians",
    "Philippians", "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy",
    "2 Timothy", "Titus", "Philemon", "Hebrews", "James",
    "1 Peter", "2 Peter", "1 John", "2 John", "3 John",
    "Jude", "Revelation"
]

def build_database():
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verses (
            translation TEXT NOT NULL,
            book_num INTEGER NOT NULL,
            book_name TEXT NOT NULL,
            chapter INTEGER NOT NULL,
            verse INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (translation, book_name, chapter, verse)
        )
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lookup ON verses(translation, book_name, chapter, verse);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_book_lookup ON verses(translation, book_num, chapter, verse);")
    
    translations = [
        ("bsb", "https://bolls.life/static/translations/BSB.json"),
        ("web", "https://bolls.life/static/translations/WEB.json"),
        ("kjv", "https://bolls.life/static/translations/KJV.json"),
    ]
    
    for code, url in translations:
        print(f"Downloading and indexing {code.upper()}...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
            
        print(f"Inserting {len(data)} verses for {code.upper()} into SQLite...")
        rows = []
        for v in data:
            b_num = v.get("book", 1)
            b_name = BOOK_NAMES[b_num - 1] if 1 <= b_num <= len(BOOK_NAMES) else f"Book {b_num}"
            ch = v.get("chapter", 1)
            vs = v.get("verse", 1)
            txt = v.get("text", "").strip()
            # Clean any HTML formatting tags like <i> or <pb/> if present
            txt = txt.replace("<i>", "").replace("</i>", "").replace("<pb/>", "").replace("<p>", "").replace("</p>", "")
            rows.append((code, b_num, b_name, ch, vs, txt))
            
        cur.executemany("INSERT OR REPLACE INTO verses VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        print(f"✅ {code.upper()} complete ({len(rows)} verses indexed).")
        
    conn.close()
    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f"🎉 Complete offline Bible database created at {db_path} ({size_mb:.2f} MB)!")

if __name__ == "__main__":
    build_database()
