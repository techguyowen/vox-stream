"""Comprehensive Speech Recognition Model Benchmark & Ranking Suite for VoxStream."""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from obs_captioner.config import AppConfig
from obs_captioner.engines import (
    create_engine,
    GoogleWebEngine,
    VoskEngine,
    MoonshineEngine,
    LocalWhisperEngine,
)
from obs_captioner.engines.base import BaseSTTEngine, TranscriptEvent


# --- Standard Evaluation Corpus ----------------------------------------------
BENCHMARK_SAMPLES = [
    {
        "id": "conversational",
        "name": "General Conversational Speech",
        "text": "Good morning everyone, welcome to today's live stream. We are testing speech recognition performance and latency across multiple AI models.",
    },
    {
        "id": "church_scripture",
        "name": "Church & Biblical Scripture",
        "text": "Please turn in your Bibles to 1 Thessalonians chapter 5 verse 16 through 18, where the Apostle Paul encourages the believers in Christ Jesus.",
    },
    {
        "id": "tech_broadcast",
        "name": "OBS Broadcast & Tech Jargon",
        "text": "Configure the OBS browser source overlay at 192.168.1.145 on port 8765, enable screen wake lock API, and verify the WebSocket reconnect backoff.",
    },
    {
        "id": "fast_phonetics",
        "name": "Fast Speech & Phonetics",
        "text": "The quick brown fox jumps over the lazy dog while analyzing complex acoustic parameters and variable length neural transformers.",
    },
]


def generate_audio_clip(text: str, output_wav_path: str) -> float:
    """Generate 16kHz mono 16-bit linear PCM audio clip using macOS TTS or synthetic speech."""
    temp_aiff = output_wav_path + ".aiff"
    try:
        # macOS native voice synthesis
        subprocess.run(["say", "-o", temp_aiff, text], check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", temp_aiff, output_wav_path], check=True, capture_output=True)
        if os.path.exists(temp_aiff):
            os.remove(temp_aiff)

        # Append 600ms trailing silence to allow VAD to finalize
        with wave.open(output_wav_path, "rb") as wf:
            params = wf.getparams()
            raw_frames = wf.readframes(wf.getnframes())

        with wave.open(output_wav_path, "wb") as wf:
            wf.setparams(params)
            wf.writeframes(raw_frames)
            wf.writeframes(b"\x00\x00" * int(16000 * 0.6))
    except Exception:
        # Fallback: create empty/simulated wav
        with wave.open(output_wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 32000)

    with wave.open(output_wav_path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def compute_wer(reference: str, hypothesis: str) -> float:
    """Calculate Word Error Rate (WER) using Levenshtein distance on words."""
    ref_words = [w.strip().lower() for w in re.findall(r"\w+", reference) if w.strip()]
    hyp_words = [w.strip().lower() for w in re.findall(r"\w+", hypothesis) if w.strip()]

    if not ref_words:
        return 0.0 if not hyp_words else 100.0

    d = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        d[i][0] = i
    for j in range(len(hyp_words) + 1):
        d[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                substitution = d[i - 1][j - 1] + 1
                insertion = d[i][j - 1] + 1
                deletion = d[i - 1][j] + 1
                d[i][j] = min(substitution, insertion, deletion)

    wer = (d[len(ref_words)][len(hyp_words)] / len(ref_words)) * 100.0
    return min(100.0, round(wer, 1))


@dataclass
class ModelBenchmarkResult:
    model_id: str
    engine_type: str
    display_name: str
    category: str
    size_mb: int
    is_offline: bool
    avg_wer: float
    avg_accuracy: float
    avg_latency_ms: float
    avg_rtf: float  # Real-Time Factor: processing_time / audio_duration (< 1.0 is faster than real-time)
    church_scripture_score: str
    sample_transcripts: Dict[str, str]
    overall_score: float  # 0 to 100
    rank: int = 0


async def benchmark_engine_on_audio(
    engine: BaseSTTEngine,
    wav_path: str,
    chunk_size_bytes: int = 3200,  # 100ms chunks at 16kHz 16-bit
) -> Tuple[str, float, float]:
    """Feed audio stream into engine, measuring total processing time, latency, and transcribed output."""
    with wave.open(wav_path, "rb") as wf:
        raw_pcm = wf.readframes(wf.getnframes())
        duration = wf.getnframes() / wf.getframerate()

    transcripts: List[str] = []

    async def _on_caption(evt: TranscriptEvent):
        if evt.text and evt.text.strip():
            if evt.is_final:
                transcripts.append(evt.text.strip())

    async def _audio_gen():
        for i in range(0, len(raw_pcm), chunk_size_bytes):
            chunk = raw_pcm[i:i + chunk_size_bytes]
            yield chunk
            # Yield control to event loop to simulate real-time ingestion
            await asyncio.sleep(0.001)

    start_time = time.perf_counter()
    try:
        # Run streaming with timeout
        await asyncio.wait_for(
            engine.start_streaming(_audio_gen(), _on_caption),
            timeout=duration + 10.0,
        )
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        pass
    finally:
        await engine.stop()

    proc_time = max(0.05, time.perf_counter() - start_time)
    final_text = " ".join(transcripts).strip()
    rtf = proc_time / duration if duration > 0 else 1.0

    return final_text, proc_time, rtf


async def evaluate_single_model(
    model_id: str,
    cfg_factory,
    name: str,
    category: str,
    size_mb: int,
    is_offline: bool,
    audio_files: List[Tuple[dict, str, float]],
) -> Optional[ModelBenchmarkResult]:
    """Run standardized audio test suite across one speech recognition engine."""
    print(f"\n⚡ Benchmarking: {name} ({'Offline' if is_offline else 'Cloud API'})...")

    cfg = cfg_factory()
    try:
        engine = create_engine(cfg)
        init_ok = await engine.initialize()
        if not init_ok:
            print(f"❌ Failed to initialize {name}")
            return None
    except Exception as e:
        print(f"❌ Initialization error for {name}: {e}")
        return None

    sample_results = {}
    wer_list = []
    rtf_list = []
    latencies = []

    for sample_meta, wav_path, duration in audio_files:
        # Create fresh engine instance per clip to ensure clean state
        eng = create_engine(cfg)
        await eng.initialize()

        t_start = time.perf_counter()
        hyp_text, proc_time, rtf = await benchmark_engine_on_audio(eng, wav_path)
        latency_ms = (proc_time - (duration * 0.5)) * 1000.0  # approximate delta from audio midpoint
        latency_ms = max(25.0, latency_ms)

        wer = compute_wer(sample_meta["text"], hyp_text)
        accuracy = max(0.0, 100.0 - wer)

        wer_list.append(wer)
        rtf_list.append(rtf)
        latencies.append(latency_ms)
        sample_results[sample_meta["id"]] = hyp_text

        print(f"   • [{sample_meta['name'][:22]:<22}] WER: {wer:5.1f}% | Acc: {accuracy:5.1f}% | RTF: {rtf:.2f}x | Output: \"{hyp_text[:50]}...\"")

    avg_wer = round(sum(wer_list) / len(wer_list), 1) if wer_list else 100.0
    avg_acc = max(0.0, round(100.0 - avg_wer, 1))
    avg_rtf = round(sum(rtf_list) / len(rtf_list), 2) if rtf_list else 1.0
    avg_lat = round(sum(latencies) / len(latencies), 1) if latencies else 200.0

    # Church scripture rating based on church sample
    church_hyp = sample_results.get("church_scripture", "")
    has_thess = "thessalonians" in church_hyp.lower()
    has_verse = "5" in church_hyp or "16" in church_hyp
    has_jesus = "jesus" in church_hyp.lower() or "christ" in church_hyp.lower()

    if has_thess and has_verse and has_jesus:
        scripture_rating = "⭐⭐⭐⭐⭐ Excellent (Full Citations & Sacred Names)"
    elif has_thess or has_jesus:
        scripture_rating = "⭐⭐⭐⭐ Good (Key Scripture Names Captured)"
    else:
        scripture_rating = "⭐⭐⭐ Moderate (Phonetic Approximation)"

    # Compute Composite Score (Weighted: 50% Accuracy, 25% Speed/RTF, 15% Latency, 10% Offline Reliability)
    acc_score = avg_acc * 0.50
    speed_score = max(0.0, min(25.0, (1.5 - min(1.5, avg_rtf)) / 1.5 * 25.0))
    lat_score = max(0.0, min(15.0, (500.0 - min(500.0, avg_lat)) / 500.0 * 15.0))
    offline_score = 10.0 if is_offline else 6.0
    overall = round(acc_score + speed_score + lat_score + offline_score, 1)

    return ModelBenchmarkResult(
        model_id=model_id,
        engine_type=cfg.general.engine,
        display_name=name,
        category=category,
        size_mb=size_mb,
        is_offline=is_offline,
        avg_wer=avg_wer,
        avg_accuracy=avg_acc,
        avg_latency_ms=avg_lat,
        avg_rtf=avg_rtf,
        church_scripture_score=scripture_rating,
        sample_transcripts=sample_results,
        overall_score=overall,
    )


async def run_full_benchmark():
    """Main benchmark orchestration routine."""
    print("=" * 80)
    print("🎙️  VOXSTREAM AI SPEECH-TO-TEXT MODEL BENCHMARK & RANKING SUITE")
    print("=" * 80)

    temp_dir = tempfile.mkdtemp(prefix="voxstream_bench_")
    print(f"📁 Synthesizing standardized 16kHz speech evaluation audio clips...")

    audio_clips = []
    for s in BENCHMARK_SAMPLES:
        wav_file = os.path.join(temp_dir, f"{s['id']}.wav")
        dur = generate_audio_clip(s["text"], wav_file)
        audio_clips.append((s, wav_file, dur))
        print(f"   • Generated '{s['name']}' ({dur:.2f}s, 16kHz mono linear PCM)")

    from obs_captioner.config import load_config

    def make_cfg(eng: str, **kwargs):
        c = load_config()
        c.general.engine = eng
        if eng == "vosk":
            c.vosk.model_name = kwargs.get("model_name", "small")
        elif eng == "moonshine":
            c.moonshine.model_name = kwargs.get("model_name", "moonshine/base")
        elif eng == "local_whisper":
            c.local_whisper.model_size = kwargs.get("model_size", "base.en")
        return c

    # Define Model Configurations
    models_to_test = [
        (
            "vosk_small",
            lambda: make_cfg("vosk", model_name="small"),
            "Vosk Small (40 MB)",
            "Offline Acoustic (Kaldi)",
            40,
            True,
        ),
        (
            "moonshine_tiny",
            lambda: make_cfg("moonshine", model_name="moonshine/tiny"),
            "Moonshine Tiny (60 MB)",
            "Neural Transformer (ONNX/Torch)",
            60,
            True,
        ),
        (
            "moonshine_base",
            lambda: make_cfg("moonshine", model_name="moonshine/base"),
            "Moonshine Base (180 MB)",
            "Neural Transformer (ONNX/Torch)",
            180,
            True,
        ),
        (
            "whisper_tiny",
            lambda: make_cfg("local_whisper", model_size="tiny.en"),
            "Faster-Whisper Tiny.en (75 MB)",
            "CTranslate2 Whisper",
            75,
            True,
        ),
        (
            "whisper_base",
            lambda: make_cfg("local_whisper", model_size="base.en"),
            "Faster-Whisper Base.en (140 MB)",
            "CTranslate2 Whisper",
            140,
            True,
        ),
        (
            "whisper_small",
            lambda: make_cfg("local_whisper", model_size="small.en"),
            "Faster-Whisper Small.en (460 MB)",
            "CTranslate2 Whisper",
            460,
            True,
        ),
        (
            "google_web",
            lambda: make_cfg("google_web"),
            "Google Speech (Free / Zero-Setup)",
            "Cloud Real-Time API",
            0,
            False,
        ),
    ]

    results: List[ModelBenchmarkResult] = []

    for m_id, cfg_fn, m_name, cat, size_mb, is_off in models_to_test:
        res = await evaluate_single_model(m_id, cfg_fn, m_name, cat, size_mb, is_off, audio_clips)
        if res:
            results.append(res)

    # Sort results by overall score descending
    results.sort(key=lambda r: r.overall_score, reverse=True)
    for idx, r in enumerate(results, start=1):
        r.rank = idx

    # Clean up temp audio files
    shutil.rmtree(temp_dir, ignore_errors=True)

    # Print Formatted Leaderboard
    print("\n" + "=" * 95)
    print("🏆  VOXSTREAM SPEECH-TO-TEXT MODEL LEADERBOARD & FINAL RANKINGS")
    print("=" * 95)
    print(f"{'Rank':<5} | {'Model Name':<32} | {'Accuracy':<9} | {'WER':<7} | {'Speed (RTF)':<12} | {'Score':<7} | {'Type'}")
    print("-" * 95)
    for r in results:
        off_label = "🔒 Offline" if r.is_offline else "☁️ Cloud"
        print(f" #{r.rank:<4} | {r.display_name:<32} | {r.avg_accuracy:>6.1f}%   | {r.avg_wer:>5.1f}% | {r.avg_rtf:>5.2f}x Real | {r.overall_score:>5.1f} | {off_label}")
    print("=" * 95 + "\n")

    return results


if __name__ == "__main__":
    asyncio.run(run_full_benchmark())
