"""Editorial Chaos design language: Paper / Ink / Studio presets.

Colours are semantic and never the sole carrier of meaning (status always has an
icon or text too).  ``contrast`` implements the WCAG 2.x ratio so tests can assert
that every text/background pair meets AA (4.5:1 body, 3:1 large/UI).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    bg: str          # desk surface
    surface: str     # raised paper
    ink: str         # primary text
    muted: str       # metadata text
    rule: str        # fine dividers
    accent: str      # oxblood / vermilion: focus, primary actions
    on_accent: str
    olive: str       # settled / completed
    warn: str        # attention (never alone: always with icon/text)
    texture: bool = False


PAPER = Palette("paper", False, "#F3ECDD", "#FBF7EE", "#221F1B", "#625A4E", "#D6CBB6", "#7A1F2B", "#FFFFFF", "#4F5B2D", "#8A4B00")
INK = Palette("ink", True, "#171615", "#201E1C", "#ECE5D8", "#B0A898", "#3B3733", "#E58A93", "#2A0F13", "#B5C58A", "#E3B068")
STUDIO = Palette("studio", False, "#EEE2CE", "#F8F0E1", "#1A1A1A", "#574F41", "#C9B998", "#B8301B", "#FFFFFF", "#445E22", "#7C4300", True)

PRESETS: Dict[str, Palette] = {"paper": PAPER, "ink": INK, "studio": STUDIO}

DISPLAY = "Display"
DISPLAY_BOLD = "DisplayBold"
DISPLAY_ITALIC = "DisplayItalic"
SANS = "Sans"
SANS_BOLD = "SansBold"

FONTS = {
    DISPLAY: "fonts/CrimsonPro-Regular.ttf",
    DISPLAY_BOLD: "fonts/CrimsonPro-Bold.ttf",
    DISPLAY_ITALIC: "fonts/CrimsonPro-Italic.ttf",
    SANS: "fonts/InstrumentSans-Regular.ttf",
    SANS_BOLD: "fonts/InstrumentSans-Bold.ttf",
}


def _lin(c: int) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_: str) -> float:
    h = hex_.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def get(name: str) -> Palette:
    return PRESETS.get(name, PAPER)
