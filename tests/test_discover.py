from urllib.parse import parse_qs, urlsplit

from ticket.stubhub import sectionmap
from ticket.stubhub.discover import SORT, filter_url

EVENT = "https://www.stubhub.com/x/event/161251368?quantity=2"


def test_filter_url_formats():
    u = filter_url(EVENT, 2, ["276039", "276040"], ["1687", "1687"], SORT)
    q = parse_qs(urlsplit(u).query)
    assert q["sections"] == ["276039,276040"] and q["ticketClasses"] == ["1687"]
    assert q["quantity"] == ["2"] and q["sortBy"] == ["NEWPRICE"]
    assert "ticketClasses" not in parse_qs(urlsplit(filter_url(EVENT, 2, ["1"], None)).query)


def test_ids_from_cards():
    cards = [{"label": "Section 19", "data_attrs": {"data-feature-id": "1687_276040"}},
             {"label": "Section 217", "data_attrs": {"data-feature-id": "19466_276160"}},
             {"label": "Section 5", "data_attrs": {}}]
    assert sectionmap.from_cards(cards) == {"19": ("1687", "276040"), "217": ("19466", "276160")}


SVG_OK = """<svg xmlns="http://www.w3.org/2000/svg">
<g id="s276039" data-name="17"><path d="M0 0"/></g>
<g id="s276040" data-name="19"><path d="M0 0"/></g>
<g id="s276160" data-name="217"><path d="M0 0"/></g>
<g id="s276041" data-name="16"><path d="M0 0"/></g>
<g id="s276099" data-name="115"><text>115</text></g>
</svg>"""


def test_svg_mapping_learned_and_validated():
    sm = sectionmap.build([], [("map.svg", SVG_OK)])
    assert sm.ids["16"] == "276041" and sm.source["16"] == "map"
    assert sm.ids["115"] == "276099"
    assert sm.ids["17"] == "276039" and sm.source["17"] == "card"


def test_svg_that_disagrees_is_not_used():
    bad = SVG_OK.replace('id="s276039"', 'id="s999999"')   # 17 now maps to a different id
    bad = bad.replace('id="s276040"', 'id="s888888"')
    sm = sectionmap.build([], [("map.svg", bad)])
    assert "16" not in sm.ids
    assert any("NOT used" in r for r in sm.report)
