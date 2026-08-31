"""Configuration management for OBS Real-Time Live Captioner."""

import json
import os
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from .censor import CensorConfig
from .translator import TranslationConfig
from .twitch_bot import TwitchConfig
from .vocabulary import VocabularyConfig
from .themes import THEME_PRESETS

logger = logging.getLogger("obs_captioner.config")


@dataclass
class GeneralConfig:
    engine: str = "vosk"  # "vosk", "moonshine", "google_web", "gemini_live", "google_stt", "local_whisper"
    language: str = "en-US"
    log_level: str = "INFO"
    auto_capitalization: bool = True
    auto_punctuation: bool = True
    church_mode: bool = True


@dataclass
class AudioConfig:
    device_name_filter: str = "default"
    device_index: Optional[int] = None
    sample_rate: int = 16000
    chunk_duration_ms: int = 100
    enable_vad: bool = True
    vad_threshold: float = 0.5
    noise_gate_db: float = -45.0


@dataclass
class GoogleSTTConfig:
    credentials_path: str = ""
    model: str = "latest_long"
    enable_automatic_punctuation: bool = True
    enable_word_time_offsets: bool = True
    profanity_filter: bool = False
    speech_contexts: List[str] = field(default_factory=lambda: ["OBS", "Twitch", "Discord", "YouTube"])


@dataclass
class GeminiLiveConfig:
    api_key: str = ""
    model: str = "gemini-3.5-transcribe-live"
    custom_vocabulary: List[str] = field(default_factory=lambda: ["OBS Studio", "Twitch", "Discord", "YouTube", "Jesus Christ"])
    smart_transcription: bool = True
    system_instruction: str = (
        "You are Gemini 3.5 Transcribe, a real-time speech transcriber. Transcribe the incoming audio accurately into text verbatim. "
        "Output only the transcribed text without commentary, pleasantries, or conversation."
    )


@dataclass
class LocalWhisperConfig:
    model_size: str = "base.en"
    device: str = "auto"
    compute_type: str = "auto"
    language: str = "en"
    beam_size: int = 1


@dataclass
class VoskConfig:
    model_name: str = "small"  # "small" (vosk-model-small-en-us-0.15) or "accurate" (vosk-model-en-us-0.22)
    model_path: str = ""  # Optional custom local folder path
    sample_rate: int = 16000


@dataclass
class BandwidthConfig:
    api_key: str = ""

@dataclass
class MoonshineConfig:
    model_name: str = "moonshine/tiny"  # "moonshine/tiny" or "moonshine/base"
    sample_rate: int = 16000


@dataclass
class OBSConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""
    auto_start_on_stream: bool = True
    auto_start_on_record: bool = True
    update_text_source: bool = True
    text_source_name: str = "Live Captions"
    send_cea608_captions: bool = True
    auto_open_projector: bool = False
    projector_type: str = "preview"
    projector_monitor_index: int = 1
    projector_source_name: str = "Captions Overlay"


@dataclass
class OverlayConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8765
    theme_id: str = "modern_clean"
    max_width: str = "90%"
    max_lines: int = 2
    text_align: str = "center"  # "left", "center", "right"
    vertical_align: str = "bottom"  # "top", "bottom"
    animation_style: str = "word_pop"  # "word_pop", "fade", "karaoke", "scroll", "instant"
    google_font: str = ""  # e.g., "Montserrat", "Poppins", "Bebas Neue", "Oswald"
    font_family: str = "Inter, sans-serif"
    font_size: str = "32px"
    font_weight: str = "700"
    line_height: str = "1.35"
    text_color: str = "#FFFFFF"
    interim_color: str = "#90CAF9"
    highlight_color: str = "#FFD166"
    background_box_color: str = "rgba(15, 15, 20, 0.72)"
    border_radius: str = "12px"
    box_padding: str = "14px 26px"
    text_shadow: str = "2px 2px 5px rgba(0, 0, 0, 0.95)"
    text_stroke: str = "2px #000000"
    auto_hide_seconds: float = 4.0

    def apply_theme(self, theme_id: str, custom_presets: Optional[Dict[str, dict]] = None) -> bool:
        """Apply attributes from a known theme preset or custom user preset."""
        preset = THEME_PRESETS.get(theme_id)
        if preset:
            self.theme_id = preset.id
            self.font_family = preset.font_family
            self.font_size = preset.font_size
            self.font_weight = preset.font_weight
            self.line_height = preset.line_height
            self.text_color = preset.text_color
            self.interim_color = preset.interim_color
            self.highlight_color = preset.highlight_color
            self.background_box_color = preset.background_box_color
            self.border_radius = preset.border_radius
            self.box_padding = preset.box_padding
            self.text_shadow = preset.text_shadow
            self.text_stroke = preset.text_stroke
            self.animation_style = preset.animation_style
            return True
        elif custom_presets and theme_id in custom_presets:
            cp = custom_presets[theme_id]
            self.theme_id = theme_id
            self.font_family = cp.get("font_family", self.font_family)
            self.font_size = cp.get("font_size", self.font_size)
            self.font_weight = cp.get("font_weight", self.font_weight)
            self.line_height = cp.get("line_height", self.line_height)
            self.text_color = cp.get("text_color", self.text_color)
            self.interim_color = cp.get("interim_color", self.interim_color)
            self.highlight_color = cp.get("highlight_color", self.highlight_color)
            self.background_box_color = cp.get("background_box_color", self.background_box_color)
            self.border_radius = cp.get("border_radius", self.border_radius)
            self.box_padding = cp.get("box_padding", self.box_padding)
            self.text_shadow = cp.get("text_shadow", self.text_shadow)
            self.text_stroke = cp.get("text_stroke", self.text_stroke)
            self.animation_style = cp.get("animation_style", self.animation_style)
            return True
        return False


@dataclass
class APIConfig:
    enabled: bool = True
    api_key: str = ""


@dataclass
class AppConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
    custom_presets: Dict[str, dict] = field(default_factory=dict)
    censor: CensorConfig = field(default_factory=CensorConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    twitch: TwitchConfig = field(default_factory=TwitchConfig)
    google_stt: GoogleSTTConfig = field(default_factory=GoogleSTTConfig)
    gemini_live: GeminiLiveConfig = field(default_factory=GeminiLiveConfig)
    local_whisper: LocalWhisperConfig = field(default_factory=LocalWhisperConfig)
    vosk: VoskConfig = field(default_factory=VoskConfig)
    moonshine: MoonshineConfig = field(default_factory=MoonshineConfig)
    bandwidth: BandwidthConfig = field(default_factory=BandwidthConfig)
    obs: OBSConfig = field(default_factory=OBSConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    api: APIConfig = field(default_factory=APIConfig)


_current_config_path: Optional[str] = None


def get_config_path() -> str:
    """Return the active configuration file path."""
    global _current_config_path
    if _current_config_path:
        return _current_config_path

    default_paths = [
        Path("config.json"),
        Path(__file__).parent.parent / "config.json",
        Path("config.json.example"),
    ]
    for p in default_paths:
        if p.is_file():
            _current_config_path = str(p)
            return _current_config_path

    _current_config_path = "config.json"
    return _current_config_path


def _safe_dataclass_load(cls, data: Optional[dict]):
    """Instantiate a dataclass, ignoring any unknown legacy/future fields."""
    if not isinstance(data, dict):
        return cls()
    field_names = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in field_names}
    return cls(**filtered)

def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from JSON file with environment variable fallback overrides."""
    global _current_config_path
    if config_path:
        _current_config_path = config_path
    else:
        config_path = get_config_path()

    data = {}
    if config_path and Path(config_path).is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"Loaded configuration from: {config_path}")
        except Exception as e:
            logger.warning(f"Failed to read {config_path}: {e}. Using defaults.")

    cfg = AppConfig(
        general=_safe_dataclass_load(GeneralConfig, data.get("general")),
        audio=_safe_dataclass_load(AudioConfig, data.get("audio")),
        vocabulary=_safe_dataclass_load(VocabularyConfig, data.get("vocabulary")),
        custom_presets=data.get("custom_presets", {}) or {},
        censor=_safe_dataclass_load(CensorConfig, data.get("censor")),
        translation=_safe_dataclass_load(TranslationConfig, data.get("translation")),
        twitch=_safe_dataclass_load(TwitchConfig, data.get("twitch")),
        google_stt=_safe_dataclass_load(GoogleSTTConfig, data.get("google_stt")),
        gemini_live=_safe_dataclass_load(GeminiLiveConfig, data.get("gemini_live")),
        local_whisper=_safe_dataclass_load(LocalWhisperConfig, data.get("local_whisper")),
        vosk=_safe_dataclass_load(VoskConfig, data.get("vosk")),
        moonshine=_safe_dataclass_load(MoonshineConfig, data.get("moonshine")),
        bandwidth=_safe_dataclass_load(BandwidthConfig, data.get("bandwidth")),
        obs=_safe_dataclass_load(OBSConfig, data.get("obs")),
        overlay=_safe_dataclass_load(OverlayConfig, data.get("overlay")),
        api=_safe_dataclass_load(APIConfig, data.get("api")),

    )

    # Environment variable overrides
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and not cfg.google_stt.credentials_path:
        cfg.google_stt.credentials_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

    if os.environ.get("GEMINI_API_KEY") and not cfg.gemini_live.api_key:
        cfg.gemini_live.api_key = os.environ["GEMINI_API_KEY"]

    if os.environ.get("BANDWIDTH_API_KEY") and not cfg.bandwidth.api_key:
        cfg.bandwidth.api_key = os.environ["BANDWIDTH_API_KEY"]

    if os.environ.get("OBS_WS_PASSWORD"):
        cfg.obs.password = os.environ["OBS_WS_PASSWORD"]

    if os.environ.get("CAPTIONER_API_KEY"):
        cfg.api.api_key = os.environ["CAPTIONER_API_KEY"]

    return cfg


def save_config(cfg: AppConfig, config_path: Optional[str] = None) -> bool:
    """Serialize and atomically save configuration back to config.json."""
    target_path = Path(config_path or get_config_path())
    if target_path.name == "config.json.example":
        global _current_config_path
        target_path = target_path.parent / "config.json"
        _current_config_path = str(target_path)

    tmp_path = target_path.with_suffix(".tmp")
    try:
        data = asdict(cfg)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(target_path)
        logger.info(f"Saved configuration atomically to: {target_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save configuration to {target_path}: {e}")
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False
