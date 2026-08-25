"""Visual identity for conferences, and safe use of team colors.

Team colors come from CFBD and are chosen for helmets, not for text. Some are
near-white, some are near-black, and a few would be illegible against the page.
Rather than trusting them blindly this module derives a contrast-checked pair for
each team: an accent that is always visible on the page background, and a
readable foreground for when the color is used as a fill.

Conferences have no color in the CFBD data at all. The palette here is an
editorial choice, kept in one place, and deliberately distinguishable rather than
an attempt to reproduce official league branding.

Every accent is derived twice, once against the light page and once against the
dark one, because the correction runs in opposite directions. A pale gold has to
be darkened to survive on cream; a navy has to be lightened to survive on
charcoal. Darkening a navy for a dark page — which is what a single light-page
accent would do — makes it invisible. Both values are sent to the page so the
theme can switch without another request, and each one moves only as far as it
must so the team's hue stays recognisable.
"""

from __future__ import annotations

import re
from typing import Any


#: Editorial conference palette. Values are chosen for separation on a cool-gray
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

DEFAULT_CONFERENCE_COLOR = "#41556d"

#: Conference abbreviations, as CFBD publishes them.
#:
#: CFBD has no conference *logo* — the /conferences payload carries only id,
#: name, shortName, abbreviation, classification and memberCount — so a
#: conference is represented by its own short form set in its palette color
#: rather than by a mark we would have to source elsewhere. The values are
#: CFBD's, pinned here because conferences are derived from team rows and there
#: is no conference table to read them from; a missing one falls back to the
#: name itself, which is always correct if longer.
CONFERENCE_ABBREVIATIONS: dict[str, str] = {
    "ACC": "ACC",
    "American Athletic": "AAC",
    "Big 12": "B12",
    "Big Ten": "B1G",
    "Conference USA": "CUSA",
    "FBS Independents": "Ind",
    "Mid-American": "MAC",
    "Mountain West": "MWC",
    "Pac-12": "PAC",
    "SEC": "SEC",
    "Sun Belt": "SBC",
}

#: Page backgrounds these colors are checked against, light and dark.
PAGE_BACKGROUND = (242, 245, 249)
DARK_PAGE_BACKGROUND = (16, 21, 28)


def conference_color(conference: str | None) -> str:
    return CONFERENCE_COLORS.get(conference or "", DEFAULT_CONFERENCE_COLOR)


def conference_color_dark(conference: str | None) -> str:
    """The editorial palette lifted onto the dark page.

    Derived from the light palette rather than hand-authored as a second list,
    so the two can never drift apart when a conference is added or realigned.
    """
    return dark_accent(conference_color(conference)) or DEFAULT_CONFERENCE_COLOR


def conference_slug(value: str) -> str:
    """A conference name as it appears in a URL."""
    return re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")


def conference_abbreviation(conference: str | None) -> str:
    """The short form for a conference, or its name when none is published."""
    name = (conference or "").strip()
    if not name:
        return ""
    return CONFERENCE_ABBREVIATIONS.get(name, name)


def conference_identity(conference: str | None) -> dict[str, Any]:
    """Everything a template needs to paint one conference consistently.

    The counterpart to :func:`team_identity`. A conference has no logo to show,
    so its mark is its abbreviation set in its palette color, derived for both
    page backgrounds like every other accent here.
    """
    name = (conference or "").strip()
    return {
        "conference": name,
        "abbreviation": conference_abbreviation(name),
        "slug": conference_slug(name),
        "color": conference_color(name),
        "color_dark": conference_color_dark(name),
        # A short mark can sit inside a filled chip; the fill needs a readable
        # foreground the same way a team fill does.
        "on_fill": foreground_for(conference_color(name)),
    }


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


def _lighten(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Mix `factor` of white into the color, preserving hue.

    The mirror of :func:`_darken`. Blending toward white rather than raising
    saturation keeps a team's navy recognisably that team's navy.
    """
    return tuple(  # type: ignore[return-value]
        min(255, int(channel + (255 - channel) * factor)) for channel in rgb)


#: How far each step moves. Both ladders start at "unchanged" so a color that is
#: already legible is returned exactly as the school specified it.
_DARKEN_STEPS = (1.0, 0.85, 0.7, 0.55, 0.45, 0.35)
_LIGHTEN_STEPS = (0.0, 0.15, 0.3, 0.45, 0.58, 0.7)


def readable_accent(color: str | None, *, minimum: float = 3.0,
                    background: tuple[int, int, int] = PAGE_BACKGROUND) -> str | None:
    """A team color moved only as far as it must be to stay visible.

    On a light page a pale color is darkened; on a dark page a deep color is
    lightened. Stepping keeps the hue recognisable instead of falling back to a
    generic color.
    """
    rgb = _channels(color)
    if rgb is None:
        return None
    on_dark = _relative_luminance(background) < 0.18
    steps = _LIGHTEN_STEPS if on_dark else _DARKEN_STEPS
    adjust = _lighten if on_dark else _darken
    for factor in steps:
        candidate = adjust(rgb, factor)
        if contrast_ratio(candidate, background) >= minimum:
            return "#%02x%02x%02x" % candidate
    # Nothing on the ladder cleared the bar: go one step past its end rather
    # than returning a color known to be unreadable.
    return "#%02x%02x%02x" % adjust(rgb, 0.8 if on_dark else 0.3)


def dark_accent(color: str | None, *, minimum: float = 3.0) -> str | None:
    """The same team color prepared for the dark page background."""
    return readable_accent(color, minimum=minimum, background=DARK_PAGE_BACKGROUND)


def foreground_for(color: str | None) -> str:
    """Black or white text, whichever is readable on this fill.

    Independent of page theme: a fill covers the background it sits on, so only
    the fill's own luminance matters.
    """
    rgb = _channels(color)
    if rgb is None:
        return "#ffffff"
    return "#0b1728" if contrast_ratio(rgb, (255, 255, 255)) < 3.2 else "#ffffff"


def team_identity(brand: dict[str, Any] | None) -> dict[str, Any]:
    """Everything a template needs to paint one team consistently."""
    brand = brand or {}
    accent = readable_accent(brand.get("color")) or DEFAULT_CONFERENCE_COLOR
    accent_dark = (dark_accent(brand.get("color"))
                   or conference_color_dark(brand.get("conference")))
    return {
        **brand,
        "accent": accent,
        "accent_dark": accent_dark,
        "fill": brand.get("color") or accent,
        "on_fill": foreground_for(brand.get("color")),
        "conference_color": conference_color(brand.get("conference")),
        "conference_color_dark": conference_color_dark(brand.get("conference")),
        # The conference's own mark, for surfaces that show both identities.
        "conference_identity": (conference_identity(brand["conference"])
                                if brand.get("conference") else None),
    }
