import pytest

from ticket.config import ConfigError, load_section_tiers, tier_for
from ticket.pairs import FILTER_ONLY, NO, YES, pair_status
from ticket.sections import normalize_section


@pytest.mark.parametrize("raw,expected", [
    ("16", "16"), ("Section 16", "16"), ("sec. 16", "16"), ("Lower Level 115", "115"),
    ("  upper   level  215 ", "215"), ("Floor A", "FLOOR"), ("A40", "A40"), ("", None), (None, None),
    ("Baseline Club", "BASELINE CLUB"),
])
def test_normalize_section(raw, expected):
    assert normalize_section(raw) == expected


def test_real_sections_config_loads_and_tiers():
    tiers = load_section_tiers()
    assert tier_for("16", tiers) == "best"
    assert tier_for("115", tiers) == "good"
    assert tier_for("215", tiers) == "far"
    assert tier_for("8", tiers) == "excluded"
    assert tier_for("999", tiers) == "unclassified"
    assert tier_for(None, tiers) == "unclassified"


def test_duplicate_section_is_config_error(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[tiers]\nbest=["16"]\ngood=["16"]\n')
    with pytest.raises(ConfigError):
        load_section_tiers(p)


def test_pair_status():
    assert pair_status(2, "[2]", 2) == YES
    assert pair_status(4, "[1, 3, 4]", 2) == NO
    assert pair_status(1, None, 2) == NO
    assert pair_status(2, None, 2) == FILTER_ONLY
    assert pair_status(None, None, None) == NO


def test_views_config_validation():
    from ticket.config import StubHubConfig, _views
    assert _views([{"name": "best", "url": "u1"}, {"name": "good", "url": "u2"}]) == [("best", "u1"), ("good", "u2")]
    with pytest.raises(ConfigError):
        _views([{"name": "best", "url": "u1"}, {"name": "best", "url": "u2"}])
    with pytest.raises(ConfigError):
        _views([{"name": "best"}])
    cfg = StubHubConfig("https://e", 2, False, "", None, 1)
    assert cfg.view_list() == [("all", "https://e")]


def test_launch_modes(tmp_path):
    from ticket.config import StubHubConfig
    from ticket.stubhub.fetch import launch_context

    seen = {}

    class Chromium:
        def launch_persistent_context(self, profile, **kw):
            seen.clear(); seen.update(kw)

    class P:
        chromium = Chromium()

    cfg = StubHubConfig("https://e", 2, False, "chrome", tmp_path / "prof", 1, offscreen=True)
    launch_context(P(), cfg)
    assert seen["headless"] is False and seen["args"] == ["--window-position=-32000,-32000"]
    launch_context(P(), cfg, offscreen=False)            # e.g. --wait-for-me: must be visible
    assert "args" not in seen
    launch_context(P(), cfg, headless=True)              # headless ignores offscreen
    assert seen["headless"] is True and "args" not in seen
