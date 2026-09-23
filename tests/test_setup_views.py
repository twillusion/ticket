import tomllib

from ticket.config import load_section_tiers
from ticket.stubhub.setup_views import ViewCheck, check_view, query_diff, tier_sections, write_views

BASE = "https://www.stubhub.com/x/event/1?quantity=2"


def card(sec, price):
    return {"label": f"Section {sec}", "lines": [f"Section {sec}", "Row 1", "2 tickets together", price, "incl. fees"],
            "href": None, "data_attrs": {"data-listing-id": f"{sec}{price}"},
            "prices": [{"text": price, "struck": False, "visible": True}]}


def test_query_diff_ignores_quantity():
    assert query_diff(BASE, BASE + "&sections=1,2&sortBy=NEWPRICE") == {"sections": "1,2", "sortBy": "NEWPRICE"}
    assert query_diff(BASE, "https://www.stubhub.com/x/event/1?quantity=4") == {}


def test_check_view_ok_and_failures():
    tiers = load_section_tiers()
    ok = check_view("best", BASE, BASE + "&sections=9", [card("16", "S$3,000"), card("15", "S$3,100")], tiers)
    assert ok.ok, ok.problems

    unchanged = check_view("best", BASE, BASE, [card("16", "S$3,000")], tiers)
    assert any("did not change" in p for p in unchanged.problems)

    mixed = check_view("best", BASE, BASE + "&sections=9", [card("16", "S$3,000"), card("217", "S$1,600")], tiers)
    assert any("outside" in p and "217" in p for p in mixed.problems)

    unsorted = check_view("best", BASE, BASE + "&s=1", [card("16", "S$3,500"), card("15", "S$3,100")], tiers)
    assert any("lowest-first" in p for p in unsorted.problems)


def test_write_views_roundtrip(tmp_path):
    p = tmp_path / "v.toml"
    write_views([ViewCheck("best", 'https://s/?a=1&b="x"', {"a": "1"}, ["16"], [], [3000.0])], p)
    data = tomllib.loads(p.read_text())
    assert data["views"] == [{"name": "best", "url": 'https://s/?a=1&b="x"'}]


def test_tier_sections_match_config():
    t = tier_sections()
    assert t["best"] == ["15", "16", "17"] and "115" in t["good"] and "215" in t["far"]
