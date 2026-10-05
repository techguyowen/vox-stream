"""Benchmark harness for long-running memory consumption and allocation velocity."""

import gc
import os
import tracemalloc
from obs_captioner.bible_engine import BibleEngine
from obs_captioner.history import TranscriptHistory
from obs_captioner.translator import SubtitleTranslator


def benchmark_memory_allocations(iterations: int = 2000):
    gc.collect()
    tracemalloc.start()
    snapshot1 = tracemalloc.take_snapshot()

    # 1. Transcript History continuous appending
    history = TranscriptHistory(max_entries=5000)
    for i in range(iterations):
        history.add_entry(f"Sermon transcript sentence number {i} talking about faith and grace.", 100.0 + i, 103.0 + i)

    # 2. BibleEngine scripture citation checks & contextual parsing
    bible = BibleEngine()
    test_phrases = [
        "Please open your Bible to John 3:16 today.",
        "As we read in Romans chapter 8 verse 28.",
        "And now look at verse 31 in that same chapter.",
        "Now listen church, in 1 Corinthians 13:4-7 love is patient.",
        "Let us consider Psalm 23 the Lord is my shepherd.",
        "Jesus said to love your neighbor as yourself.",
        "Just as the Apostle Paul preached in Ephesians 2:8-9.",
        "We are continuing in verse 10 for we are God's handiwork.",
    ]
    for i in range(iterations):
        phrase = test_phrases[i % len(test_phrases)]
        bible.parse_and_lookup_first(phrase)

    # 3. Translator cache operations
    translator = SubtitleTranslator(disk_cache=False)
    for i in range(iterations):
        key = f"phrase_{i % 250}"
        translator._cache[key] = f"translated_{i % 250}"

    gc.collect()
    snapshot2 = tracemalloc.take_snapshot()
    tracemalloc.stop()

    stats = snapshot2.compare_to(snapshot1, 'lineno')
    total_allocated_kb = sum(stat.size for stat in stats) / 1024.0

    current_mem_kb, peak_mem_kb = tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (0, 0)

    import resource
    usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # on macOS, ru_maxrss is in bytes; on Linux, in KB
    if os.uname().sysname == "Darwin":
        usage_kb = usage_kb / 1024.0

    return {
        "iterations": iterations,
        "net_allocated_kb": round(total_allocated_kb, 1),
        "peak_rss_kb": round(usage_kb, 1),
    }


if __name__ == "__main__":
    import json
    res = benchmark_memory_allocations(1500)
    print(json.dumps(res, indent=2))
