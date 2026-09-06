"""Music Detection and Caption Suppression for VoxStream.

Dual-layer detection:
1. Text/Token Guard: Recognizes musical note characters (♪, ♫), bracketed music tags ([music], [singing]),
   flanked song lyrics, and repetitive autoregressive hallucination loops.
2. Acoustic Guard: Sliding-window spectral flatness, harmonic concentration, and zero-crossing rate
   to identify sustained church organ, piano chords, and worship pads in the absence of speech formants.
"""

import collections
import logging
import math
import re
from typing import Deque, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger("obs_captioner.music")

# Musical symbols emitted by Whisper, Moonshine, and other ASR models
MUSIC_SYMBOLS = set("♪♫♩♬♭♯")

# Regex patterns for music tags and musical descriptions
MUSIC_TAG_PATTERNS = [
    re.compile(
        r"\[[^\]]*(?:music|singing|chords|instrumental|choir|applause|cheering|organ|piano|guitar|hymn)[^\]]*\]",
        re.IGNORECASE,
    ),
    re.compile(
        r"\([^)]*(?:music|singing|chords|instrumental|choir|applause|cheering|organ|piano|guitar|hymn)[^)]*\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*[♪♫♩♬♭♯].*[♪♫♩♬♭♯]\s*$",
        re.IGNORECASE,
    ),
]

# Symbols pattern
MUSIC_CHAR_PATTERN = re.compile(r"[♪♫♩♬♭♯]")


def is_repetition_loop(text: str) -> bool:
    """Detect autoregressive repetition loops typical of Whisper during music or sustained tones.

    Examples:
      'Thank you. Thank you. Thank you. Thank you.' -> True
      'you you you you you' -> True
      'Hallelujah Amen Hallelujah Amen Hallelujah Amen' -> True
    """
    if not text:
        return False

    words = re.findall(r"\b\w+\b", text.lower())
    total_words = len(words)
    if total_words < 3:
        return False

    # Check 1-gram, 2-gram, 3-gram, 4-gram repetitions
    max_gram = min(4, total_words // 3)
    for n in range(1, max_gram + 1):
        for i in range(total_words - n * 3 + 1):
            ngram = words[i : i + n]
            count = 1
            j = i + n
            while j + n <= total_words and words[j : j + n] == ngram:
                count += 1
                j += n
            # If repeated at least 3 times and covers at least 50% of the utterance
            if count >= 3 and (count * n) >= (total_words * 0.5):
                return True

    return False


def is_music_text(text: str) -> bool:
    """Return True if the transcript is identified as music, singing, or a music hallucination."""
    if not text:
        return False

    stripped = text.strip()
    if not stripped:
        return False

    # 1. Direct musical note symbols (if note is present at start/end or dominates)
    if any(c in MUSIC_SYMBOLS for c in stripped):
        # Clean out notes and punctuation: if nothing left, it's 100% music symbol
        no_notes = re.sub(r"[♪♫♩♬♭♯\s.,!?:;\"'()\[\]\-_]", "", stripped)
        if not no_notes:
            return True
        # If starts with or ends with a music symbol (typical of transcribed lyrics: '♪ Amazing grace ♪')
        if stripped[0] in MUSIC_SYMBOLS or stripped[-1] in MUSIC_SYMBOLS:
            return True
        # If text contains musical notes anywhere
        return True

    # 2. Bracketed or parenthesized music/sound tags
    for pat in MUSIC_TAG_PATTERNS:
        if pat.search(stripped):
            return True

    # 3. Autoregressive hallucination loop check
    if is_repetition_loop(stripped):
        return True

    return False


class AcousticMusicDetector:
    """Sliding-window acoustic music and tonality detector.

    Analyzes spectral flatness and zero-crossing rates over 1.0–1.5s sliding windows.
    Sustained musical chords (organ, worship synth pads, piano decay) exhibit near-zero
    spectral flatness and harmonic stability without the rapid consonant transients of speech.
    """

    def __init__(self, window_frames: int = 12):
        self.window_frames = window_frames
        # Each entry: (rms_db, spectral_flatness, zcr)
        self._history: Deque[Tuple[float, float, float]] = collections.deque(maxlen=window_frames)
        self.music_detected: bool = False

    def reset(self) -> None:
        """Clear analysis history."""
        self._history.clear()
        self.music_detected = False

    def process_chunk(
        self,
        audio_chunk_bytes: bytes,
        sample_rate: int = 16000,
        noise_gate_db: float = -45.0,
    ) -> bool:
        """Process one 100ms PCM chunk and return True if sustained acoustic music is detected."""
        if np is None or not audio_chunk_bytes:
            return False

        try:
            even_len = len(audio_chunk_bytes) & ~1
            if even_len == 0:
                return False

            samples = np.frombuffer(audio_chunk_bytes[:even_len], dtype=np.int16).astype(np.float64)
            if len(samples) < 128:
                return False

            # 1. RMS Energy
            rms = np.sqrt(np.mean(samples ** 2))
            if rms <= 0:
                self._history.append((-100.0, 1.0, 0.0))
                self.music_detected = False
                return False

            rms_db = 20.0 * math.log10(rms / 32767.0)
            if rms_db < noise_gate_db:
                # Below noise gate -> silence/inactive
                self._history.append((rms_db, 1.0, 0.0))
                self.music_detected = False
                return False

            # 2. Zero-Crossing Rate (ZCR)
            zero_crossings = np.sum(np.abs(np.diff(samples > 0)))
            zcr = float(zero_crossings) / len(samples)

            # 3. Spectral Flatness (Wiener entropy)
            # Apply Hanning window to reduce spectral leakage
            windowed = samples * np.hanning(len(samples))
            spectrum = np.abs(np.fft.rfft(windowed)) ** 2
            spectrum = spectrum + 1e-10  # Prevent log(0)

            # Focus on 100 Hz - 4000 Hz where musical harmonics and speech reside
            freq_bins = len(spectrum)
            min_bin = max(1, int(100.0 * freq_bins / (sample_rate / 2)))
            max_bin = min(freq_bins, int(4000.0 * freq_bins / (sample_rate / 2)))
            sub_spectrum = spectrum[min_bin:max_bin]

            geo_mean = float(np.exp(np.mean(np.log(sub_spectrum))))
            arith_mean = float(np.mean(sub_spectrum))
            spectral_flatness = geo_mean / arith_mean if arith_mean > 0 else 1.0

            self._history.append((rms_db, spectral_flatness, zcr))

            # Need at least 6 frames (~600ms) of history to evaluate stability
            if len(self._history) < 6:
                return False

            # Filter for active frames (above noise gate)
            active_frames = [f for f in self._history if f[0] >= noise_gate_db]
            if len(active_frames) < 5:
                self.music_detected = False
                return False

            flatness_values = [f[1] for f in active_frames]
            zcr_values = [f[2] for f in active_frames]

            mean_flatness = float(np.mean(flatness_values))
            std_flatness = float(np.std(flatness_values))
            mean_zcr = float(np.mean(zcr_values))
            max_zcr = float(np.max(zcr_values))

            # Acoustic music signature:
            # - Very low spectral flatness (< 0.045) indicating discrete harmonic musical pitches
            # - Low variance in flatness (< 0.025) indicating sustained chords/pads without consonant transients
            # - Low ZCR without high-frequency unvoiced consonant bursts (mean_zcr < 0.16, max_zcr < 0.25)
            is_tonal_music = (
                mean_flatness < 0.045
                and std_flatness < 0.025
                and mean_zcr < 0.16
                and max_zcr < 0.25
            )

            self.music_detected = is_tonal_music
            return self.music_detected

        except Exception as e:
            logger.debug(f"Acoustic music analysis error: {e}")
            return False
