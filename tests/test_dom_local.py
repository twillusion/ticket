"""DOM listing-card extraction against a local fake page (not StubHub).

Modeled on what the first real probe showed: listings rendered as HTML cards ("Section 19",
then prices, sometimes a crossed-out old price next to the current one), in a scrollable
panel that loads more cards on scroll. Class names are random on purpose, since the extractor
must not rely on them.
"""

import http.server
import threading

import pytest

from test_capture_local import _chromium
from ticket.stubhub.collect import run
from ticket.stubhub.dom import map_card
from ticket.stubhub.parse import ListingParseError

PAGE = """<!doctype html><html><head><style>
 .x9 { height: 300px; overflow-y: auto; border: 1px solid #ccc }
 .k2 { display: block; height: 120px; } .old { text-decoration: line-through }
</style></head><body>
<h1>League of Legends World Championship - Final</h1>
<div>Prices include estimated fees</div>
<div class="x9" id="panel"></div>
<script>
const rows = [
  ["217", "A", 2, null, "S$1,643", "data-listing-id=\\"9001\\""],
  ["19", "5", 2, "S$4,688", "S$3,290", "data-listing-id=\\"9002\\""],
  ["108", "C", 4, null, "S$3,255", ""],
  ["25", "1", 2, null, "S$6,389", "data-listing-id=\\"9004\\""],
  ["16", "7", 2, null, "S$3,917", "data-listing-id=\\"9005\\""],
  ["115", "2", 2, null, "S$3,309", "data-listing-id=\\"9006\\""],
];
let shown = 0;
function add(n) {
  const panel = document.getElementById('panel');
  for (const [sec, row, qty, old, price, attr] of rows.slice(shown, shown + n)) {
    const href = attr ? '' : ' href="/x/event/1?listingId=9003&quantity=2"';
    panel.insertAdjacentHTML('beforeend',
      `<a class="k2"${href}><div ${attr}><div><span>Section ${sec}</span></div><div>Row ${row}</div>` +
      `<div>${qty} tickets together</div><div>Clear view</div>` +
      (old ? `<span class="old">${old}</span>` : '') + `<span><b>${price}</b></span><div>incl. fees</div></div></a>`);
  }
  shown += n;
}
add(3);
document.getElementById('panel').addEventListener('scroll', e => {
  const p = e.target;
  if (p.scrollTop + p.clientHeight >= p.scrollHeight - 5 && shown < rows.length) setTimeout(() => add(3), 200);
});
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.skipif(_chromium() is None, reason="no Chromium binary available")
def test_cards_scroll_struck_price_and_ids(server, tmp_path):
    from ticket.config import Sources, StubHubConfig

    sources = Sources(tmp_path / "db.sqlite", tmp_path / "raw",
                      StubHubConfig(server + "/event", 2, True, "", tmp_path / "profile", 6))
    out = run(sources, executable_path=_chromium(), url_override=server + "/event?quantity=2")
    assert out.status == "ok", (out.message, out.result.errors)
    by_sec = {l.section: l for l in out.result.listings}
    assert set(by_sec) == {"217", "19", "108", "25", "16", "115"}, "scrolling should load all 6"

    s19 = by_sec["19"]
    assert s19.price_per_ticket == 3290.0 and s19.currency == "SGD", "struck S$4,688 must be ignored"
    assert (s19.listing_id, s19.row, s19.quantity) == ("9002", "5", 2)
    assert by_sec["108"].listing_id == "9003"          # from the href query
    assert by_sec["108"].quantity == 4
    assert "Clear view" in by_sec["16"].view_notes
    assert all(l.price_field == "dom:unstruck-price" and l.price_includes_fees is True for l in by_sec.values())
    assert s19.allowed_splits == [2]                     # "2 tickets together" under quantity=2
    assert by_sec["108"].allowed_splits is None           # 4 together: can't assume it splits to 2

    again = run(sources, reparse_dir=out.raw_dir)       # cards.json re-parses without a browser
    assert {l.section for l in again.result.listings} == set(by_sec)


def test_two_live_prices_is_an_error_not_a_guess():
    card = {"label": "Section 16", "lines": ["Section 16", "S$3,000", "S$3,400"], "href": None, "data_attrs": {},
            "prices": [{"text": "S$3,000", "struck": False, "visible": True},
                       {"text": "S$3,400", "struck": False, "visible": True}]}
    with pytest.raises(ListingParseError, match="different un-struck prices"):
        map_card(card, 2, "https://www.stubhub.com/x")


def test_data_price_disagreement_is_an_error():
    card = {"label": "Section 16", "lines": ["Section 16", "S$3,000", "incl. fees"], "href": None,
            "data_attrs": {"data-price": "S$3,400"},
            "prices": [{"text": "S$3,000", "struck": False, "visible": True}]}
    with pytest.raises(ListingParseError, match="data-price"):
        map_card(card, 2, "https://www.stubhub.com/x")


def test_same_price_twice_is_fine():
    card = {"label": "Section 16", "lines": ["Section 16", "Row 3", "S$3,000"], "href": None, "data_attrs": {},
            "prices": [{"text": "S$3,000", "struck": False, "visible": True},
                       {"text": "S$3,000", "struck": False, "visible": False}]}
    listing, issues = map_card(card, 2, "https://www.stubhub.com/x")
    assert listing.price_per_ticket == 3000.0 and listing.row == "3"
    assert any("content hash" in i for i in issues)
