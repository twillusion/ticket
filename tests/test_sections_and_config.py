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
