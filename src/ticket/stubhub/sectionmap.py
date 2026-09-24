"""Map StubHub section names ("17") to its internal section ids (276039).

Filtered links look like `?sections=276039&ticketClasses=1687` (seen 2026-09-24). Two sources:

1. Listing cards carry `data-feature-id="<ticketClass>_<sectionId>"`, e.g. Section 19 ->
   "1687_276040". Reliable, but only for sections that happen to have a card on the page.
2. The seat-map SVG the page downloads. Its structure is unknown, so the parser tries every
   (id attribute, name source) combination and keeps one only if it agrees with every
   id known from cards. Anything else is reported, not guessed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field

from ticket.sections import normalize_section

# Observed in real runs on 2026-09-23/24 (probe output and setup-views URLs).
SEED: dict[str, tuple[str, str]] = {
    "17": ("1687", "276039"),
    "19": ("1687", "276040"),
    "106": ("19465", "276051"),
    "211": ("19466", "276076"),
    "217": ("19466", "276160"),
}

_FEATURE = re.compile(r"^(\d+)_(\d+)$")
_ID = re.compile(r"(?<!\d)(\d{5,7})(?!\d)")


@dataclass
class SectionMap:
    ids: dict[str, str] = field(default_factory=dict)          # section name -> section id
    classes: dict[str, str] = field(default_factory=dict)      # section name -> ticketClass id
    source: dict[str, str] = field(default_factory=dict)       # section name -> where it came from
    report: list[str] = field(default_factory=list)


def from_cards(cards: list[dict]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for c in cards:
        m = _FEATURE.match(str(c.get("data_attrs", {}).get("data-feature-id", "")))
        name = normalize_section(c.get("label"))
        if m and name:
            out[name] = (m[1], m[2])
    return out


def _svg_strategies(svg_text: str) -> dict[tuple[str, str], dict[str, str]]:
    """(id attribute, name source) -> {section id: section name}."""
    root = ET.fromstring(svg_text)
    strategies: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for el in root.iter():
        id_hits = [(a, m[1]) for a, v in el.attrib.items() for m in _ID.finditer(v)]
        if not id_hits:
            continue
        sources = {f"@{a.split('}')[-1]}": v for a, v in el.attrib.items()}
        text = " ".join(t.strip() for t in el.itertext() if t.strip())
        if text:
            sources["text"] = text
        for id_attr, sid in id_hits:
            for src, val in sources.items():
                if src == f"@{id_attr.split('}')[-1]}" or len(val) > 30:
                    continue
                name = normalize_section(val)
                if name and re.fullmatch(r"[A-Z]?\d{1,3}[A-Z]?", name):
                    strategies[(id_attr.split('}')[-1], src)][sid] = name
    return strategies


def build(cards: list[dict], svgs: list[tuple[str, str]]) -> SectionMap:
    known = dict(SEED)
    known.update(from_cards(cards))
    sm = SectionMap()
    for name, (cls, sid) in known.items():
        sm.ids[name], sm.classes[name], sm.source[name] = sid, cls, "card"
    sm.report.append(f"ids known from listing cards (incl. earlier runs): {len(known)}")

    for url, text in svgs:
        try:
            strategies = _svg_strategies(text)
        except ET.ParseError as e:
            sm.report.append(f"!! map {url[:80]} is not parseable SVG: {e}")
            continue
        best = None
        for key, mapping in strategies.items():
            by_name = {n: s for s, n in mapping.items()}
            hits = sum(1 for n, (_, sid) in known.items() if by_name.get(n) == sid)
            misses = sum(1 for n, (_, sid) in known.items() if n in by_name and by_name[n] != sid)
            if hits >= 2 and misses == 0 and (best is None or (hits, len(mapping)) > best[0]):
                best = ((hits, len(mapping)), key, by_name)
        if best is None:
            sm.report.append(f"!! map {url[:80]}: no attribute pattern agrees with the known ids; "
                             "section ids from the map NOT used")
            for n, (_, sid) in list(known.items())[:3]:
                i = text.find(sid)
                sm.report.append(f"     context for {n}={sid}: "
                                 f"{text[max(0, i - 150):i + 150]!r}" if i >= 0 else f"     {sid} not in map file")
            continue
        (hits, size), key, by_name = best
        sm.report.append(f"map {url[:80]}: pattern {key} matches {hits}/{len(known)} known ids; "
                         f"{size} sections")
        for name, sid in by_name.items():
            if name not in sm.ids:
                sm.ids[name], sm.source[name] = sid, "map"
    return sm
