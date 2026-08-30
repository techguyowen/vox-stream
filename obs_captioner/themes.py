"""Preset visual themes and styling templates for OBS Live Captions."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class ThemePreset:
    id: str
    name: str
    description: str
    font_family: str
    font_size: str
    font_weight: str
    line_height: str
    text_color: str
    interim_color: str
    highlight_color: str
    background_box_color: str
    border_radius: str
    box_padding: str
    text_shadow: str
    text_stroke: str
    animation_style: str


THEME_PRESETS: Dict[str, ThemePreset] = {
    "modern_clean": ThemePreset(
        id="modern_clean",
        name="Modern Clean (Default)",
        description="Balanced modern glassmorphism with high legibility.",
        font_family="Inter, sans-serif",
        font_size="32px",
        font_weight="700",
        line_height="1.35",
        text_color="#FFFFFF",
        interim_color="#90CAF9",
        highlight_color="#FFD166",
        background_box_color="rgba(15, 15, 20, 0.72)",
        border_radius="12px",
        box_padding="14px 26px",
        text_shadow="2px 2px 5px rgba(0, 0, 0, 0.95)",
        text_stroke="2px #000000",
        animation_style="word_pop",
    ),
    "minimal_cinema": ThemePreset(
        id="minimal_cinema",
        name="Minimalist Cinema",
        description="Netflix-style subtitles with zero background box and deep drop shadows.",
        font_family="Roboto, sans-serif",
        font_size="34px",
        font_weight="700",
        line_height="1.3",
        text_color="#FFFFFF",
        interim_color="#E0E7FF",
        highlight_color="#FDE047",
        background_box_color="rgba(0, 0, 0, 0.0)",
        border_radius="0px",
        box_padding="0px",
        text_shadow="2px 2px 4px rgba(0, 0, 0, 1.0), 0 0 10px rgba(0, 0, 0, 0.8)",
        text_stroke="1.5px #000000",
        animation_style="fade",
    ),
    "cyberpunk_neon": ThemePreset(
        id="cyberpunk_neon",
        name="Cyberpunk Neon",
        description="High-energy neon cyan & magenta glow for gaming streams.",
        font_family="'Bebas Neue', sans-serif",
        font_size="40px",
        font_weight="700",
        line_height="1.2",
        text_color="#00F0FF",
        interim_color="#FF007F",
        highlight_color="#FFE600",
        background_box_color="rgba(10, 5, 25, 0.85)",
        border_radius="8px",
        box_padding="12px 28px",
        text_shadow="0 0 12px rgba(0, 240, 255, 0.8)",
        text_stroke="1px #050515",
        animation_style="word_pop",
    ),
    "twitch_purple": ThemePreset(
        id="twitch_purple",
        name="Twitch Purple Glow",
        description="Twitch brand aesthetics with purple accents and crisp typography.",
        font_family="Poppins, sans-serif",
        font_size="32px",
        font_weight="800",
        line_height="1.35",
        text_color="#FFFFFF",
        interim_color="#C084FC",
        highlight_color="#A855F7",
        background_box_color="rgba(24, 18, 38, 0.85)",
        border_radius="16px",
        box_padding="16px 28px",
        text_shadow="2px 2px 4px rgba(0, 0, 0, 0.9)",
        text_stroke="2px #0E0918",
        animation_style="karaoke",
    ),
    "comic_pop": ThemePreset(
        id="comic_pop",
        name="Comic / Gaming Pop",
        description="Vibrant yellow text with thick black outline and punchy animations.",
        font_family="Bangers, cursive",
        font_size="42px",
        font_weight="400",
        line_height="1.2",
        text_color="#FFEA00",
        interim_color="#FFFFFF",
        highlight_color="#FF3366",
        background_box_color="rgba(0, 0, 0, 0.65)",
        border_radius="14px",
        box_padding="12px 24px",
        text_shadow="3px 3px 0px #000000",
        text_stroke="3px #000000",
        animation_style="word_pop",
    ),
    "retro_terminal": ThemePreset(
        id="retro_terminal",
        name="Retro Terminal / Matrix",
        description="Classic green phosphor monochrome terminal vibe.",
        font_family="Consolas, Monaco, monospace",
        font_size="28px",
        font_weight="700",
        line_height="1.4",
        text_color="#00FF66",
        interim_color="#00AA44",
        highlight_color="#FFFFFF",
        background_box_color="rgba(0, 15, 5, 0.90)",
        border_radius="4px",
        box_padding="14px 22px",
        text_shadow="0 0 8px rgba(0, 255, 102, 0.7)",
        text_stroke="1px #002200",
        animation_style="instant",
    ),
    "youtube_cc": ThemePreset(
        id="youtube_cc",
        name="YouTube CC Classic",
        description="High-contrast solid black badge subtitles.",
        font_family="'Open Sans', sans-serif",
        font_size="30px",
        font_weight="700",
        line_height="1.3",
        text_color="#FFFFFF",
        interim_color="#BBDEFB",
        highlight_color="#FFEE58",
        background_box_color="rgba(0, 0, 0, 0.92)",
        border_radius="4px",
        box_padding="8px 18px",
        text_shadow="none",
        text_stroke="none",
        animation_style="fade",
    ),
}


def get_all_presets() -> List[dict]:
    """Return all theme presets as dictionaries for API / UI dropdowns."""
    from dataclasses import asdict
    return [asdict(t) for t in THEME_PRESETS.values()]
