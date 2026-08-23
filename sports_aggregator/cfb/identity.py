"""Visual identity for conferences, and safe use of team colors.

Team colors come from CFBD and are chosen for helmets, not for text. Some are
near-white, some are near-black, and a few would be illegible against the page.
Rather than trusting them blindly this module derives a contrast-checked pair for
each team: an accent that is always visible on the page background, and a
readable foreground for when the color is used as a fill.

Conferences have no color in the CFBD data at all. The palette here is an
editorial choice, kept in one place, and deliberately distinguishable rather than
an attempt to reproduce official league branding.
"""

from __future__ import annotations

from typing import Any


#: Editorial conference palette. Values are chosen for separation on a cream
#: page, not as official league marks.
CONFERENCE_COLORS: dict[str, str] = {
    "SEC": "#12457b",
    "Big Ten": "#0d2f5f",
    "ACC": "#8a1538",
    "Big 12": "#a3261f",
    "Pac-12": "#134e37",
    "American Athletic": "#1b6a8c",
    "Mountain West": "#6b3f8c",
    "Sun Belt": "#b3651a",
    "Mid-American": "#2f6b3a",
    "Conference USA": "#1f5f78",
    "FBS Independents": "#5a4a3a",
}

DEFAULT_CONFERENCE_COLOR = "#6b625a"

#: Page background these colors are checked against.
PAGE_BACKGROUND = (245, 240, 231)


def conference_color(conference: str | None) -> str:
    return CONFERENCE_COLORS.get(conference or "", DEFAULT_CONFERENCE_COLOR)


def _channels(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    candidate = value.strip().lstrip("#")
    if len(candidate) == 3:
        candidate = "".join(char * 2 for char in candidate)
    if len(candidate) != 6:
        return None
    try:
        return (int(candidate[0:2], 16), int(candidate[2:4], 16), int(candidate[4:6], 16))
    except ValueError:
        return None


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance, used for both contrast checks below."""
    channels = []
    for raw in rgb:
        value = raw / 255
        channels.append(value / 12.92 if value <= 0.04045
                        else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    lighter = max(_relative_luminance(first), _relative_luminance(second))
    darker = min(_relative_luminance(first), _relative_luminance(second))
    return (lighter + 0.05) / (darker + 0.05)


def _darken(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, int(channel * factor)) for channel in rgb)  # type: ignore[return-value]


def readable_accent(color: str | None, *, minimum: float = 3.0) -> str | None:
    """A team color darkened only as far as it needs to be to stay visible.

    A white or pale gold accent disappears against the page. Darkening in steps
    keeps the hue recognisable instead of falling back to a generic color.
    """
    rgb = _channels(color)
    if rgb is None:
        return None
    for factor in (1.0, 0.85, 0.7, 0.55, 0.45, 0.35):
        candidate = _darken(rgb, factor)
        if contrast_ratio(candidate, PAGE_BACKGROUND) >= minimum:
            return "#%02x%02x%02x" % candidate
    return "#%02x%02x%02x" % _darken(rgb, 0.3)


def foreground_for(color: str | None) -> str:
    """Black or white text, whichever is readable on this fill."""
    rgb = _channels(color)
    if rgb is None:
        return "#ffffff"
    return "#17130f" if contrast_ratio(rgb, (255, 255, 255)) < 3.2 else "#ffffff"


def team_identity(brand: dict[str, Any] | None) -> dict[str, Any]:
    """Everything a template needs to paint one team consistently."""
    brand = brand or {}
    accent = readable_accent(brand.get("color")) or DEFAULT_CONFERENCE_COLOR
    return {
        **brand,
        "accent": accent,
        "fill": brand.get("color") or accent,
        "on_fill": foreground_for(brand.get("color")),
        "conference_color": conference_color(brand.get("conference")),
    }
