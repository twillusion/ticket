"""End-to-end check of the browser capture against a local fake page (not StubHub).

Verifies the mechanics: the page is loaded, JSON fetched by page scripts and JSON embedded in
<script> tags are both captured, raw files are written, the run is stored, and challenge pages
are reported as blocked. Skipped if no Chromium is available.
"""

import http.server
import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from ticket.config import Sources, StubHubConfig
from ticket.stubhub.collect import run

PAGE = """<!doctype html><html><body><div id=grid>loading</div>
<script type="application/json" id="index-data">{"grid":{"items":[
 {"listingId": 1, "section": "16", "row": "5", "availableTickets": 2, "splits": [2], "price": "S$3,917"}]}}</script>
<script>
fetch('/api/listings').then(r => r.json()).then(d => {
  document.getElementById('grid').textContent = d.items.length + ' listings';
});
</script></body></html>"""
API = {"items": [
    {"listingId": 2, "section": "Section 15", "row": "9", "availableTickets": 4, "splits": [2, 4], "price": "S$4,222"},
    {"listingId": 3, "section": "8", "row": "1", "availableTickets": 2, "price": "S$4,659"},
]}
BLOCKED = "<html><body><div id='px-captcha'></div>Please verify you are a human</body></html>"


def _chromium() -> str | None:
    for c in (os.environ.get("CHROMIUM_PATH"), "/opt/pw-browsers/chromium",
              shutil.which("chromium"), shutil.which("chromium-browser")):
        if c and Path(c).exists():
            return c
    return None


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/listings"):
            body, ctype = json.dumps(API).encode(), "application/json"
        elif self.path.startswith("/blocked"):
            body, ctype = BLOCKED.encode(), "text/html"
        else:
            body, ctype = PAGE.encode(), "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def sources(tmp_path):
    return Sources(
        db_path=tmp_path / "db.sqlite",
        raw_dir=tmp_path / "raw",
        stubhub=StubHubConfig(event_url="http://example.invalid/event", quantity=2, headless=True,
                              browser_channel="", profile_dir=tmp_path / "profile", max_load_more=1),
    )


@pytest.mark.skipif(_chromium() is None, reason="no Chromium binary available")
def test_capture_and_store(server, sources):
    out = run(sources, executable_path=_chromium(), url_override=server + "/event?quantity=2",
              allowed_host_suffix="127.0.0.1")
    assert out.status == "ok", out.message
    assert {l.listing_id for l in out.result.listings} == {"1", "2", "3"}
    assert (out.raw_dir / "responses.jsonl").exists() and (out.raw_dir / "page.html").exists()

    conn = sqlite3.connect(sources.db_path)
    assert conn.execute("SELECT status, listings_found FROM runs").fetchone() == ("ok", 3)
    assert conn.execute("SELECT COUNT(*) FROM listings_snapshot").fetchone()[0] == 3

    # re-parsing the saved raw folder reproduces the same listings without a browser
    again = run(sources, reparse_dir=out.raw_dir)
    assert {l.listing_id for l in again.result.listings} == {"1", "2", "3"}


@pytest.mark.skipif(_chromium() is None, reason="no Chromium binary available")
def test_challenge_page_is_blocked(server, sources):
    out = run(sources, executable_path=_chromium(), url_override=server + "/blocked",
              allowed_host_suffix="127.0.0.1")
    assert out.status == "blocked"
    assert (out.raw_dir / "blocked.png").exists()
    conn = sqlite3.connect(sources.db_path)
    assert conn.execute("SELECT status FROM runs").fetchone() == ("blocked",)
