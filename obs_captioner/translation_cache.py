"""Persistent SQLite Disk-Backed Translation Cache for VoxStream."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("obs_captioner.translation_cache")


def get_default_cache_db_path() -> Path:
    """Resolve the default persistent translation database path."""
    cache_dir = Path.home() / ".cache" / "voxstream"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "translation_cache.db"


class TranslationDiskCache:
    """High-performance thread-safe SQLite L2 translation cache with auto-pruning."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        max_entries: int = 25000,
    ):
        self.db_path = db_path or get_default_cache_db_path()
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or initialize thread-safe SQLite connection with WAL enabled."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        return self._conn

    def _init_db(self) -> None:
        """Create database tables and indices if not already present."""
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS translations (
                            cache_key TEXT PRIMARY KEY,
                            source_text TEXT NOT NULL,
                            translated_text TEXT NOT NULL,
                            provider TEXT NOT NULL,
                            source_lang TEXT NOT NULL,
                            target_lang TEXT NOT NULL,
                            created_at REAL NOT NULL,
                            hit_count INTEGER DEFAULT 1
                        );
                        """
                    )
                    conn.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_translations_provider
                        ON translations (provider, target_lang);
                        """
                    )
            logger.debug(f"Translation disk cache initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize translation disk cache: {e}", exc_info=True)

    def get(self, cache_key: str) -> Optional[str]:
        """Retrieve a cached translation by key. Increments hit count on hit."""
        if not cache_key:
            return None

        try:
            with self._lock:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT translated_text, hit_count FROM translations WHERE cache_key = ?",
                    (cache_key,),
                )
                row = cursor.fetchone()
                if row:
                    translated, hits = row
                    # Increment hit counter
                    with conn:
                        conn.execute(
                            "UPDATE translations SET hit_count = ? WHERE cache_key = ?",
                            (hits + 1, cache_key),
                        )
                    return translated
        except Exception as e:
            logger.debug(f"Error reading from translation disk cache: {e}")

        return None

    def set(
        self,
        cache_key: str,
        source_text: str,
        translated_text: str,
        provider: str = "nllb",
        source_lang: str = "en",
        target_lang: str = "es",
    ) -> None:
        """Store or update a translation pair in SQLite."""
        if not cache_key or not translated_text:
            return

        try:
            now = time.time()
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO translations (
                            cache_key, source_text, translated_text, provider, source_lang, target_lang, created_at, hit_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            translated_text = excluded.translated_text,
                            hit_count = hit_count + 1;
                        """,
                        (cache_key, source_text, translated_text, provider, source_lang, target_lang, now),
                    )

                # Prune if exceeded max entries
                self._maybe_prune(conn)
        except Exception as e:
            logger.debug(f"Error saving to translation disk cache: {e}")

    def _maybe_prune(self, conn: sqlite3.Connection) -> None:
        """Auto-prune oldest/least-used entries if table exceeds max_entries."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM translations")
            count = cursor.fetchone()[0]
            if count > self.max_entries:
                batch = 500 if self.max_entries >= 1000 else 0
                prune_amount = min(count, max(1, count - self.max_entries + batch))
                logger.info(f"Pruning {prune_amount} entries from translation disk cache...")
                with conn:
                    conn.execute(
                        """
                        DELETE FROM translations WHERE cache_key IN (
                            SELECT cache_key FROM translations
                            ORDER BY hit_count ASC, created_at ASC
                            LIMIT ?
                        )
                        """,
                        (prune_amount,),
                    )
        except Exception as e:
            logger.debug(f"Error pruning translation disk cache: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Return diagnostic metrics about the disk cache."""
        try:
            with self._lock:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), COALESCE(SUM(hit_count), 0) FROM translations")
                count, hits = cursor.fetchone()
                size_kb = round(self.db_path.stat().st_size / 1024, 1) if self.db_path.exists() else 0
                return {
                    "total_entries": count,
                    "total_hits": hits,
                    "db_size_kb": size_kb,
                    "db_path": str(self.db_path),
                }
        except Exception as e:
            return {"error": str(e), "total_entries": 0, "total_hits": 0, "db_size_kb": 0}

    def clear(self) -> None:
        """Clear all entries from the disk cache."""
        try:
            with self._lock:
                conn = self._get_connection()
                with conn:
                    conn.execute("DELETE FROM translations")
            logger.info("Translation disk cache cleared.")
        except Exception as e:
            logger.error(f"Failed to clear translation disk cache: {e}")

    def close(self) -> None:
        """Safely close the SQLite database connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
