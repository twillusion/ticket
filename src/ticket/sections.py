"""Normalize section labels from resale sites to the ids used in config/sections.toml."""

from __future__ import annotations

import re

_PREFIXES = re.compile(
    r"^(?:"
    r"(?:LOWER|UPPER|MAIN|PLAZA|BALCONY|MEZZANINE)\s+(?:LEVEL|BOWL|TIER)"
    r"|SECTION|SECT\.?|SEC\.?"
    r")\s*",
)


def normalize_section(raw: str | None) -> str | None:
    """'Section 16' / 'Lower Level 16' / 'sec. 16' -> '16'. 'Floor A' -> 'FLOOR'.

    Anything unrecognized is returned upper-cased and whitespace-collapsed, not guessed at.
    Unknown labels then show up as unclassified on the site.
    """
    if raw is None:
        return None
    s = re.sub(r"\s+", " ", str(raw)).strip().upper()
    if not s:
        return None
    prev = None
    while prev != s:
        prev = s
        s = _PREFIXES.sub("", s).strip()
    if s.startswith("FLOOR"):
        return "FLOOR"
    return s
