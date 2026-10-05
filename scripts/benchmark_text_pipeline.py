"""Benchmark harness for text normalization, formatting, and censorship pipeline."""

import json
import time
from obs_captioner.censor import CensorConfig, ContentFilter
from obs_captioner.formatter import TextFormatter, is_hallucinated_or_leaked_text


def benchmark_pipeline(iterations: int = 5000):
    formatter = TextFormatter()
    censor = ContentFilter(CensorConfig(), church_mode=True)

    sample_lines = [
        "good morning church and welcome to our service today",
        "please open your Bible to John 3 16 as we continue our series",
        "we want to, we want to make sure everyone feels at home",
        "I help our church to continue to be apart. I help our church to continue to be apart.",
        "on November twenty fourth at ten thirty in the morning we will meet",
        "the gates of hell shall not prevail against the church amen!Let us pray",
        "looks like.Guys we are going to start the thirty second psalm",
        "and the Lord said,yes I will provide for you because he is faithful",
        "this is bullshit said nobody in church",
        "holy cow that was an awesome message pastor",
        "spll the word correctly and do not slipp on the floor",
        "we are reading from Romans chapter eight verse twenty eight",
        "areOther people coming to the sanctuary tonight?",
        "Jesus Christ is Lord and Savior of all",
        "['hallucinated', 'list', 'leak']",
    ]

    # Warmup
    for line in sample_lines:
        if not is_hallucinated_or_leaked_text(line):
            fmt = formatter.format_text(line, is_final=True)
            censor.filter_text(fmt)

    # Benchmark loop
    start = time.perf_counter()
    filtered_count = 0
    censored_count = 0

    for i in range(iterations):
        line = sample_lines[i % len(sample_lines)]
        if not is_hallucinated_or_leaked_text(line):
            fmt = formatter.format_text(line, is_final=True)
            res, was_censored = censor.filter_text(fmt)
            filtered_count += 1
            if was_censored:
                censored_count += 1

    elapsed = time.perf_counter() - start
    mean_latency_ms = (elapsed / iterations) * 1000
    throughput = iterations / elapsed

    return {
        "iterations": iterations,
        "elapsed_sec": round(elapsed, 4),
        "mean_latency_ms": round(mean_latency_ms, 4),
        "throughput_lines_sec": round(throughput, 1),
        "filtered_count": filtered_count,
        "censored_count": censored_count,
    }


if __name__ == "__main__":
    result = benchmark_pipeline(5000)
    print(json.dumps(result, indent=2))
