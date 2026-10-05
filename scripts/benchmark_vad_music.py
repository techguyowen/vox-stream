"""Benchmark harness for AcousticMusicDetector and VoiceActivityDetector audio processing."""

import json
import math
import time
import tracemalloc
import numpy as np

from obs_captioner.music import AcousticMusicDetector
from obs_captioner.vad import VoiceActivityDetector


def generate_audio_chunks(sample_rate: int = 16000, duration: float = 0.1):
    """Generate representative 100ms PCM chunks for benchmarking."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    
    # 1. Sustained 3-note worship chord (C major: 261.6Hz, 329.6Hz, 392.0Hz)
    chord = (
        0.25 * np.sin(2 * np.pi * 261.6 * t)
        + 0.25 * np.sin(2 * np.pi * 329.6 * t)
        + 0.25 * np.sin(2 * np.pi * 392.0 * t)
    )
    chord_bytes = (chord * 32767).astype(np.int16).tobytes()

    # 2. White / turbulent noise (speech-like consonant burst)
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.2, len(t))
    noise_bytes = (np.clip(noise, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

    # 3. Ambient low-energy background (-55 dBFS)
    quiet = rng.normal(0, 0.002, len(t))
    quiet_bytes = (quiet * 32767).astype(np.int16).tobytes()

    # 4. Pure single tone (organ pipe: 440 Hz)
    tone = 0.5 * np.sin(2 * np.pi * 440.0 * t)
    tone_bytes = (tone * 32767).astype(np.int16).tobytes()

    # Generate blocks of consecutive frames (10 frames each) to simulate realistic speech and music intervals
    bursts = []
    for _ in range(10):
        bursts.append(("chord", chord_bytes))
    for _ in range(10):
        bursts.append(("noise", noise_bytes))
    for _ in range(10):
        bursts.append(("quiet", quiet_bytes))
    for _ in range(10):
        bursts.append(("tone", tone_bytes))

    return bursts


def benchmark_vad_and_music(iterations: int = 4000):
    chunks = generate_audio_chunks()
    detector = AcousticMusicDetector(window_frames=12)
    vad = VoiceActivityDetector(sample_rate=16000, noise_gate_db=-45.0, enable_silero=False, suppress_music=True)

    # Warmup
    for _, chunk in chunks:
        detector.process_chunk(chunk, sample_rate=16000, noise_gate_db=-45.0)
        vad.calculate_rms_db(chunk)
        vad.is_speech(chunk)

    detector.reset()

    # Benchmark: Acoustic Music Detector processing
    tracemalloc.start()
    start_music = time.perf_counter()
    music_hits = 0

    for i in range(iterations):
        name, chunk = chunks[i % len(chunks)]
        is_music = detector.process_chunk(chunk, sample_rate=16000, noise_gate_db=-45.0)
        if is_music:
            music_hits += 1

    music_elapsed = time.perf_counter() - start_music
    music_current_mem, music_peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    music_latency_us = (music_elapsed / iterations) * 1_000_000
    music_throughput = iterations / music_elapsed

    # Benchmark: VAD RMS calculation & is_speech
    tracemalloc.start()
    start_vad = time.perf_counter()
    speech_hits = 0

    for i in range(iterations):
        name, chunk = chunks[i % len(chunks)]
        is_sp = vad.is_speech(chunk)
        if is_sp:
            speech_hits += 1

    vad_elapsed = time.perf_counter() - start_vad
    vad_current_mem, vad_peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    vad_latency_us = (vad_elapsed / iterations) * 1_000_000
    vad_throughput = iterations / vad_elapsed

    total_elapsed = music_elapsed + vad_elapsed
    total_latency_us = ((music_elapsed + vad_elapsed) / iterations) * 1_000_000

    results = {
        "iterations": iterations,
        "music_detector": {
            "elapsed_sec": round(music_elapsed, 4),
            "latency_us": round(music_latency_us, 2),
            "throughput_fps": round(music_throughput, 1),
            "peak_memory_kb": round(music_peak_mem / 1024, 2),
            "music_hits": music_hits,
        },
        "vad_engine": {
            "elapsed_sec": round(vad_elapsed, 4),
            "latency_us": round(vad_latency_us, 2),
            "throughput_fps": round(vad_throughput, 1),
            "peak_memory_kb": round(vad_peak_mem / 1024, 2),
            "speech_hits": speech_hits,
        },
        "combined": {
            "total_elapsed_sec": round(total_elapsed, 4),
            "combined_latency_us": round(total_latency_us, 2),
        },
    }

    print("\n=== VAD & Acoustic Music Detector Benchmark ===")
    print(f"Iterations:             {iterations}")
    print(f"Music Detector Latency: {music_latency_us:.2f} μs / chunk ({music_throughput:.1f} frames/sec)")
    print(f"Music Peak Alloc:       {music_peak_mem / 1024:.2f} KB")
    print(f"VAD Engine Latency:     {vad_latency_us:.2f} μs / chunk ({vad_throughput:.1f} frames/sec)")
    print(f"VAD Peak Alloc:         {vad_peak_mem / 1024:.2f} KB")
    print(f"Combined Pipeline:      {total_latency_us:.2f} μs / chunk")
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    benchmark_vad_and_music()
