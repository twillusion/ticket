"""Load config/*.toml and map sections to tiers."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

TIERS = ("best", "good", "far")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class StubHubConfig:
    event_url: str
    quantity: int
    headless: bool
    browser_channel: str
    profile_dir: Path
    max_load_more: int
    views: tuple[tuple[str, str], ...] = ()   # (name, url); empty = just event_url

    def view_list(self) -> list[tuple[str, str]]:
        return list(self.views) or [("all", self.event_url)]


@dataclass(frozen=True)
class Sources:
    db_path: Path
    raw_dir: Path
    stubhub: StubHubConfig


def _load(name: str) -> dict:
    path = CONFIG_DIR / name
    with path.open("rb") as f:
        return tomllib.load(f)


def load_sources() -> Sources:
    raw = _load("sources.toml")
    st, sh = raw["storage"], raw["stubhub"]
    return Sources(
        db_path=REPO_ROOT / st["db_path"],
        raw_dir=REPO_ROOT / st["raw_dir"],
        stubhub=StubHubConfig(
            event_url=sh["event_url"],
            quantity=int(sh["quantity"]),
            headless=bool(sh["headless"]),
            browser_channel=sh.get("browser_channel", ""),
            profile_dir=REPO_ROOT / sh["profile_dir"],
            max_load_more=int(sh["max_load_more"]),
            views=tuple(_views(sh.get("views", []) + _generated_views())),
        ),
    )


def _generated_views() -> list[dict]:
    """Views written by `python -m ticket stubhub setup-views` (config/stubhub_views.toml)."""
    path = CONFIG_DIR / "stubhub_views.toml"
    if not path.exists():
        return []
    with path.open("rb") as f:
        return tomllib.load(f).get("views", [])


def _views(raw: list[dict]) -> list[tuple[str, str]]:
    out, names = [], set()
    for v in raw:
        name, url = v.get("name"), v.get("url")
        if not name or not url:
            raise ConfigError(f"each [[stubhub.views]] needs a name and a url: {v!r}")
        if name in names:
            raise ConfigError(f"duplicate stubhub view name {name!r}")
        names.add(name)
        out.append((name, url))
    return out


def load_section_tiers(path: Path | None = None) -> dict[str, str]:
    """Return {canonical section: tier}, where tier is best/good/far/excluded.

    Sections not in the result are "unclassified". A section listed twice is a config
    error, not something to resolve by picking one.
    """
    with (path or CONFIG_DIR / "sections.toml").open("rb") as f:
        raw = tomllib.load(f)

    mapping: dict[str, str] = {}

    def add(section: str, tier: str) -> None:
        key = section.strip().upper()
        if key in mapping:
            raise ConfigError(f"section {key!r} listed as both {mapping[key]!r} and {tier!r}")
        mapping[key] = tier

    unknown = set(raw.get("tiers", {})) - set(TIERS)
    if unknown:
        raise ConfigError(f"unknown tier names in sections.toml: {sorted(unknown)}")
    for tier in TIERS:
        for s in raw.get("tiers", {}).get(tier, []):
            add(s, tier)
    for group in raw.get("excluded", {}).values():
        for s in group:
            add(s, "excluded")
    return mapping


def tier_for(section: str | None, mapping: dict[str, str]) -> str:
    if not section:
        return "unclassified"
    return mapping.get(section.upper(), "unclassified")
