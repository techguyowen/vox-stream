"""Preset visual themes and styling templates for OBS Live Captions."""

from dataclasses import dataclass
from typing import Dict, List, Optional


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
        name="Modern Clean (Glassmorphism)",
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
    "broadcast_news": ThemePreset(
        id="broadcast_news",
        name="Broadcast Lower-Third",
        description="Clean network newsroom standard lower-third bar.",
        font_family="'Oswald', 'Inter', sans-serif",
        font_size="34px",
        font_weight="700",
        line_height="1.25",
        text_color="#FFFFFF",
        interim_color="#94A3B8",
        highlight_color="#38BDF8",
        background_box_color="rgba(10, 15, 28, 0.92)",
        border_radius="4px",
        box_padding="12px 24px",
        text_shadow="1px 1px 3px rgba(0, 0, 0, 0.8)",
        text_stroke="none",
        animation_style="instant",
    ),
    "sanctuary_worship": ThemePreset(
        id="sanctuary_worship",
        name="Sanctuary & Worship",
        description="Elegant presentation with warm amber accents for church services.",
        font_family="'Montserrat', sans-serif",
        font_size="36px",
        font_weight="600",
        line_height="1.4",
        text_color="#FFFBEB",
        interim_color="#FEF3C7",
        highlight_color="#F59E0B",
        background_box_color="rgba(20, 20, 26, 0.78)",
        border_radius="14px",
        box_padding="16px 30px",
        text_shadow="2px 2px 6px rgba(0, 0, 0, 0.9)",
        text_stroke="1.5px #0F0F12",
        animation_style="fade",
    ),
    "corporate_keynote": ThemePreset(
        id="corporate_keynote",
        name="Corporate Keynote & Tech",
        description="Minimal executive aesthetic for webinars and presentations.",
        font_family="'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
        font_size="32px",
        font_weight="600",
        line_height="1.35",
        text_color="#F8FAFC",
        interim_color="#94A3B8",
        highlight_color="#60A5FA",
        background_box_color="rgba(24, 28, 36, 0.85)",
        border_radius="10px",
        box_padding="14px 26px",
        text_shadow="0 2px 8px rgba(0, 0, 0, 0.5)",
        text_stroke="none",
        animation_style="word_pop",
    ),
    "minimal_cinema": ThemePreset(
        id="minimal_cinema",
        name="Minimalist Cinema (Netflix / BBC)",
        description="Clean film subtitles with zero background box and deep drop shadows.",
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
    "stage_confidence": ThemePreset(
        id="stage_confidence",
        name="High-Contrast Stage Confidence",
        description="High-visibility text on deep obsidian for 40+ ft stage monitors.",
        font_family="'Inter', sans-serif",
        font_size="44px",
        font_weight="800",
        line_height="1.3",
        text_color="#FDE047",
        interim_color="#FEF08A",
        highlight_color="#FACC15",
        background_box_color="rgba(0, 0, 0, 0.95)",
        border_radius="8px",
        box_padding="18px 36px",
        text_shadow="none",
        text_stroke="none",
        animation_style="instant",
    ),
    "editorial_nordic": ThemePreset(
        id="editorial_nordic",
        name="Editorial & Talk Show",
        description="Warm editorial typography for talk shows and long-form podcasts.",
        font_family="'Lora', Georgia, serif",
        font_size="32px",
        font_weight="600",
        line_height="1.4",
        text_color="#FAFAF9",
        interim_color="#D6D3D1",
        highlight_color="#FBBF24",
        background_box_color="rgba(28, 25, 23, 0.82)",
        border_radius="10px",
        box_padding="14px 28px",
        text_shadow="1px 1px 4px rgba(0, 0, 0, 0.8)",
        text_stroke="1px #1C1917",
        animation_style="fade",
    ),
    "youtube_cc": ThemePreset(
        id="youtube_cc",
        name="Classic Broadcast CEA-708",
        description="Standardized high-contrast solid black badge subtitles.",
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
    "opendyslexic": ThemePreset(
        id="opendyslexic",
        name="📖 OpenDyslexic (Accessibility)",
        description="Specialized bottom-heavy letterforms designed to enhance readability.",
        font_family="'OpenDyslexic', sans-serif",
        font_size="32px",
        font_weight="700",
        line_height="1.5",
        text_color="#FFFFFF",
        interim_color="#7DD3FC",
        highlight_color="#FDE047",
        background_box_color="rgba(15, 23, 42, 0.88)",
        border_radius="12px",
        box_padding="16px 28px",
        text_shadow="2px 2px 4px rgba(0, 0, 0, 0.95)",
        text_stroke="2px #000000",
        animation_style="word_pop",
    ),
    "cyberpunk_neon": ThemePreset(
        id="cyberpunk_neon",
        name="Studio Dark Tech (Cyan & Slate)",
        description="Dark studio aesthetic with subtle electric cyan accents.",
        font_family="'Inter', sans-serif",
        font_size="34px",
        font_weight="700",
        line_height="1.3",
        text_color="#38BDF8",
        interim_color="#94A3B8",
        highlight_color="#0EA5E9",
        background_box_color="rgba(15, 23, 42, 0.90)",
        border_radius="8px",
        box_padding="12px 28px",
        text_shadow="0 0 10px rgba(56, 189, 248, 0.4)",
        text_stroke="1px #0F172A",
        animation_style="word_pop",
    ),
    "twitch_purple": ThemePreset(
        id="twitch_purple",
        name="Studio Creator (Deep Indigo)",
        description="Clean studio broadcast look with deep indigo glassmorphism.",
        font_family="Poppins, sans-serif",
        font_size="32px",
        font_weight="700",
        line_height="1.35",
        text_color="#FFFFFF",
        interim_color="#C084FC",
        highlight_color="#A855F7",
        background_box_color="rgba(24, 20, 42, 0.88)",
        border_radius="12px",
        box_padding="16px 28px",
        text_shadow="2px 2px 4px rgba(0, 0, 0, 0.9)",
        text_stroke="1.5px #0E0918",
        animation_style="karaoke",
    ),
    "comic_pop": ThemePreset(
        id="comic_pop",
        name="High-Contrast Amber Badge",
        description="Bold amber-gold high-contrast badge for active speakers.",
        font_family="'Montserrat', sans-serif",
        font_size="36px",
        font_weight="800",
        line_height="1.25",
        text_color="#FDE047",
        interim_color="#FEF08A",
        highlight_color="#F59E0B",
        background_box_color="rgba(15, 15, 20, 0.85)",
        border_radius="10px",
        box_padding="12px 24px",
        text_shadow="2px 2px 4px rgba(0, 0, 0, 0.9)",
        text_stroke="2px #000000",
        animation_style="word_pop",
    ),
    "retro_terminal": ThemePreset(
        id="retro_terminal",
        name="Console Monospace",
        description="Clean technical developer monospace subtitle layout.",
        font_family="'Consolas', 'Monaco', monospace",
        font_size="28px",
        font_weight="600",
        line_height="1.4",
        text_color="#38BDF8",
        interim_color="#64748B",
        highlight_color="#F1F5F9",
        background_box_color="rgba(15, 23, 42, 0.90)",
        border_radius="6px",
        box_padding="14px 22px",
        text_shadow="none",
        text_stroke="none",
        animation_style="instant",
    ),
}


def get_all_presets(custom_presets: Optional[Dict[str, dict]] = None) -> List[dict]:
    """Return all theme presets including built-in and user-saved custom presets."""
    from dataclasses import asdict
    presets = []
    for t in THEME_PRESETS.values():
        p_dict = asdict(t)
        p_dict["is_custom"] = False
        presets.append(p_dict)

    if custom_presets:
        for p_id, p_val in custom_presets.items():
            if isinstance(p_val, dict):
                p_entry = dict(p_val)
                p_entry["id"] = p_id
                p_entry["is_custom"] = True
                presets.append(p_entry)
    return presets
