import asyncio
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from obs_captioner.engines.nllb_translator import NLLBTranslator
from obs_captioner.translation_cache import TranslationDiskCache
from obs_captioner.translator import SubtitleTranslator, TranslationConfig


def test_get_does_not_modify_disk_until_flush():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        cache = TranslationDiskCache(db_path=db_path, flush_threshold=50)

        cache.set("key1", "hello", "hola")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()
        assert row[0] == 1

        val1 = cache.get("key1")
        val2 = cache.get("key1")
        assert val1 == "hola"
        assert val2 == "hola"

        row_before = conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()
        assert row_before[0] == 1

        cache.flush()

        row_after = conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()
        assert row_after[0] == 3

        conn.close()
        cache.close()


def test_flush_threshold_auto_flushes():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        cache = TranslationDiskCache(db_path=db_path, flush_threshold=3)

        cache.set("key1", "hello", "hola")

        conn = sqlite3.connect(str(db_path))
        assert conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()[0] == 1

        cache.get("key1")
        cache.get("key1")
        assert conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()[0] == 1

        cache.get("key1")
        assert conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()[0] == 4

        conn.close()
        cache.close()


def test_close_flushes_pending_increments():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        cache = TranslationDiskCache(db_path=db_path, flush_threshold=50)

        cache.set("key1", "hello", "hola")
        val = cache.get("key1")
        assert val == "hola"

        conn = sqlite3.connect(str(db_path))
        assert conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()[0] == 1

        cache.close()

        row = conn.execute("SELECT hit_count FROM translations WHERE cache_key = 'key1'").fetchone()
        assert row[0] == 2
        conn.close()


def test_idx_translations_lru_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        cache = TranslationDiskCache(db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        indexes = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()]
        assert "idx_translations_lru" in indexes

        info = conn.execute("PRAGMA index_info('idx_translations_lru')").fetchall()
        col_names = [col[2] for col in info]
        assert col_names == ["hit_count", "created_at"]

        conn.close()
        cache.close()


def test_prune_enforces_max_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        cache = TranslationDiskCache(db_path=db_path, max_entries=10, prune_interval=5)

        for i in range(25):
            cache.set(f"k{i}", f"src{i}", f"tr{i}")

        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
        assert count <= 10
        conn.close()
        cache.close()


def test_translator_uses_async_disk_cache():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            disk_cache = TranslationDiskCache(db_path=db_path)
            mock_nllb = MagicMock(spec=NLLBTranslator)
            mock_nllb.translate = AsyncMock(return_value="Hola async")

            cfg = TranslationConfig(enabled=True, provider="nllb", target_language="es")
            translator = SubtitleTranslator(cfg, nllb_translator=mock_nllb, disk_cache=disk_cache)

            res1 = await translator.translate_to_language("Hello async", target_lang="es")
            assert res1 == "Hola async"

            translator._cache.clear()

            res2 = await translator.translate_to_language("Hello async", target_lang="es")
            assert res2 == "Hola async"
            assert mock_nllb.translate.await_count == 1

            disk_cache.close()

    asyncio.run(_run())
