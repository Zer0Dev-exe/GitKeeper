"""Identidad visual de la TUI: tema de colores y logotipo."""

from __future__ import annotations

from rich.text import Text
from textual.theme import Theme

BRAND_ORANGE = "#F97316"  # git
BRAND_VIOLET = "#A855F7"  # IA

THEME = Theme(
    name="gitkeeper",
    primary=BRAND_ORANGE,
    secondary=BRAND_VIOLET,
    accent="#22D3EE",
    success="#22C55E",
    warning="#F59E0B",
    error="#EF4444",
    foreground="#E5E7EB",
    background="#0B0F17",
    surface="#111827",
    panel="#1A2233",
    dark=True,
)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def gradient(text: str, start: str = BRAND_ORANGE, end: str = BRAND_VIOLET, bold: bool = True) -> Text:
    """Texto con degradado de color letra a letra."""
    r1, g1, b1 = _hex_to_rgb(start)
    r2, g2, b2 = _hex_to_rgb(end)
    result = Text()
    steps = max(len(text) - 1, 1)
    for i, char in enumerate(text):
        t = i / steps
        color = f"#{round(r1 + (r2 - r1) * t):02x}{round(g1 + (g2 - g1) * t):02x}{round(b1 + (b2 - b1) * t):02x}"
        result.append(char, style=f"{'bold ' if bold else ''}{color}")
    return result


def logo() -> Text:
    """Logotipo: pastilla naranja con el nombre, seguida del lema en degradado suave."""
    text = Text()
    text.append(" ◆ GitKeeper ", style=f"bold #0B0F17 on {BRAND_ORANGE}")
    text.append("  ")
    text.append_text(gradient("tus repositorios, con descripciones hechas por IA", "#FDBA74", "#C4B5FD", bold=False))
    return text
