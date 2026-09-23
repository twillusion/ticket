"""Load the StubHub event page in a real browser and capture every JSON document it receives.

This doesn't call StubHub endpoints directly. It opens the page like a person would, then
records (a) JSON responses the page fetches and (b) JSON embedded in <script> tags. Everything
is written to the run's raw folder before any parsing, so a parser fix can be re-run later.

No captcha solving and no retry loops. If StubHub shows a challenge page, the run stops with
status "blocked" and saves a screenshot.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ticket.config import StubHubConfig
from ticket.jsonscan import json_in_script
from ticket.stubhub.dom import CARDS_JS, CLICK_MORE_JS, COUNT_CARDS_JS, LABELS_JS, SCROLL_LIST_JS

# Challenge detection. Bare vendor names ("perimeterx", "datadome", "captcha-delivery") are NOT
# used: their sensor scripts are included on normal pages too, which caused false "blocked"
# results. Only an actual challenge frame/element, or challenge text visible on the page, counts.
_CHALLENGE_FRAME_MARKERS = ("captcha-delivery.com/captcha", "captcha-delivery.com/interstitial")
_CHALLENGE_HTML_MARKERS = ('id="px-captcha"', "id='px-captcha'")
_CHALLENGE_TEXT_MARKERS = (
    "verify you are a human", "verify you are human", "are you a robot", "access denied",
    "pardon our interruption", "request unsuccessful", "press & hold", "press and hold",
    "slide right to", "unusual activity",
)


class Blocked(Exception):
    pass


@dataclass
class Capture:
    page_url: str
    page_status: int | None
    docs: list[tuple[str, Any]] = field(default_factory=list)   # (url, json)
    json_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # diagnostics, so a probe can show where the data lives without anyone opening files
    title: str = ""
    html_len: int = 0
    body_text: str = ""
    network: list[dict] = field(default_factory=list)   # xhr/fetch requests
    scripts: list[dict] = field(default_factory=list)   # inline scripts summary
    cards: list[dict] = field(default_factory=list)     # listing cards read from the DOM


def with_quantity(url: str, quantity: int) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != "quantity"]
    q.append(("quantity", str(quantity)))
    return urlunsplit(parts._replace(query=urlencode(q)))


def detect_block(status: int | None, html: str, visible_text: str = "",
                 frame_urls: list[str] | tuple[str, ...] = ()) -> str | None:
    for u in frame_urls:
        for m in _CHALLENGE_FRAME_MARKERS:
            if m in u:
                return f"challenge frame on page ({m})"
    lowered_html = html.lower()
    for m in _CHALLENGE_HTML_MARKERS:
        if m in lowered_html:
            return f"challenge element on page ({m})"
    lowered_text = visible_text.lower()
    for m in _CHALLENGE_TEXT_MARKERS:
        if m in lowered_text:
            return f"challenge text on page ({m!r})"
    if status is not None and status >= 400:
        return f"event page returned HTTP {status}"
    return None


def _check_block(page, status: int | None) -> tuple[str, str | None]:
    html = page.content()
    try:
        text = page.inner_text("body", timeout=5_000)
    except Exception:
        text = ""
    return html, detect_block(status, html, text, [f.url for f in page.frames])


_INLINE_SCRIPTS_JS = """
() => Array.from(document.querySelectorAll('script:not([src])'))
  .map(s => ({id: s.id || null, type: s.type || null, text: s.textContent || ''}))
"""
_HINT_WORDS = ("listing", "section", "row", "price", "quantity", "ticket")
_MAX_BODY = 20 * 1024 * 1024


def _harvest_scripts(scripts: list[dict], cap: "Capture") -> None:
    for i, s in enumerate(scripts):
        text, stype = s["text"], (s["type"] or "").lower()
        label = f"script[{i}]#{s['id'] or '?'}[{s['type'] or 'js'}]"
        lowered = text.lower()
        cap.scripts.append({
            "label": label, "length": len(text),
            "hints": {w: lowered.count(w) for w in _HINT_WORDS if w in lowered},
        })
        if not text.strip():
            continue
        if "json" in stype:
            try:
                cap.docs.append((f"embedded:{label}", json.loads(text)))
            except json.JSONDecodeError:
                cap.json_failures.append(f"{label} has a JSON type but is not valid JSON")
        elif len(text) >= 500:
            for j, doc in enumerate(json_in_script(text)):
                cap.docs.append((f"embedded:{label}:json{j}", doc))


def _load_more(page, max_rounds: int, notes: list[str]) -> None:
    """Scroll the listing panel (and click any "show more") until the card count stops growing."""
    counts = [page.evaluate(COUNT_CARDS_JS)]
    clicks = stalls = 0
    for _ in range(max_rounds):
        page.evaluate(SCROLL_LIST_JS)
        if page.evaluate(CLICK_MORE_JS):
            clicks += 1
        page.wait_for_timeout(1800)
        counts.append(page.evaluate(COUNT_CARDS_JS))
        stalls = stalls + 1 if counts[-1] <= counts[-2] else 0
        if stalls >= 2:
            break
    notes.append(f"section labels on page while scrolling: {counts}; show-more clicks: {clicks}")
    if len(counts) - 1 >= max_rounds and stalls < 2:
        notes.append(f"still growing after {max_rounds} rounds; raise max_load_more to get the rest")


def launch_context(p, cfg: StubHubConfig, executable_path: str | None = None, headless: bool | None = None):
    """Chrome with the tool's persistent profile, so cookies from earlier runs are reused."""
    launch: dict[str, Any] = {"headless": cfg.headless if headless is None else headless,
                              "viewport": {"width": 1366, "height": 900}}
    if executable_path:
        launch["executable_path"] = executable_path
    elif cfg.browser_channel:
        launch["channel"] = cfg.browser_channel
    cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(str(cfg.profile_dir), **launch)


def capture_event(cfg: StubHubConfig, raw_dir: Path, *, executable_path: str | None = None,
                  url_override: str | None = None, wait_for_me: bool = False) -> Capture:
    """wait_for_me: if a challenge appears in a visible window, pause so a person can solve it
    by hand, then continue. The tool itself never interacts with the challenge."""
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    raw_dir.mkdir(parents=True, exist_ok=True)
    url = url_override or with_quantity(cfg.event_url, cfg.quantity)
    responses = []

    with sync_playwright() as p:
        ctx = launch_context(p, cfg, executable_path)
        try:
            page = ctx.new_page()
            page.on("response", lambda r: responses.append(r))
            main = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            cap = Capture(page_url=page.url, page_status=main.status if main else None)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                cap.notes.append("networkidle not reached in 15s (normal with analytics; continuing)")

            html, block = _check_block(page, cap.page_status)
            if block and wait_for_me and not cfg.headless:
                page.screenshot(path=str(raw_dir / "blocked-before-human.png"), full_page=True)
                print(f"\n  StubHub is showing a check: {block}\n"
                      "  Solve it in the Chrome window, wait until the ticket listings appear,\n"
                      "  then press Enter here (Ctrl+C to give up).", flush=True)
                input()
                try:
                    page.wait_for_load_state("networkidle", timeout=30_000)
                except PWTimeout:
                    cap.notes.append("networkidle not reached after manual check (continuing)")
                # The first response's status is stale after the check; judge the page as it is now.
                html, block = _check_block(page, None)
                if not block:
                    cap.notes.append("a bot check was solved by hand during this run")
            (raw_dir / "page.html").write_text(html, encoding="utf-8")
            if block:
                page.screenshot(path=str(raw_dir / "blocked.png"), full_page=True)
                raise Blocked(block)

            _load_more(page, cfg.max_load_more, cap.notes)
            page.screenshot(path=str(raw_dir / "page.png"), full_page=False)
            page.screenshot(path=str(raw_dir / "page-full.png"), full_page=True)

            final_html = page.content()
            (raw_dir / "page-final.html").write_text(final_html, encoding="utf-8")
            cap.title, cap.html_len, cap.page_url = page.title(), len(final_html), page.url
            try:
                cap.body_text = page.inner_text("body", timeout=5_000)
            except Exception as e:
                cap.notes.append(f"could not read page text: {type(e).__name__}")
            (raw_dir / "body.txt").write_text(cap.body_text, encoding="utf-8")

            _harvest_scripts(page.evaluate(_INLINE_SCRIPTS_JS), cap)
            cap.cards = page.evaluate(CARDS_JS)
            labels = page.evaluate(LABELS_JS)
            unread = list(labels)
            for c in cap.cards:
                if c["label"] in unread:
                    unread.remove(c["label"])
            if unread:
                cap.notes.append(f"!! {len(labels)} section labels on the page but {len(cap.cards)} cards read; "
                                 f"not read: {unread[:15]}")
            totals = sorted({m.group(0) for m in re.finditer(r"\b\d[\d,]*\s+listings?\b", cap.body_text, re.I)})
            if totals:
                cap.notes.append(f"page says: {totals[:5]} (cards read: {len(cap.cards)})")

            # Every XHR/fetch response from any host, whatever its content type: sites don't
            # always label JSON as JSON. Analytics noise is filtered out later by the parser.
            for r in responses:
                if r.request.resource_type not in ("xhr", "fetch"):
                    continue
                ctype = (r.headers.get("content-type") or "").lower()
                entry = {"method": r.request.method, "status": r.status, "type": ctype[:60], "url": r.url}
                try:
                    body = r.body()
                except Exception as e:
                    entry["body"] = f"unavailable: {type(e).__name__}"
                    cap.network.append(entry)
                    continue
                entry["bytes"] = len(body)
                cap.network.append(entry)
                if r.status >= 400 and (urlsplit(r.url).hostname or "").endswith("stubhub.com"):
                    snippet = body[:300].decode("utf-8", "replace").replace("\n", " ")
                    cap.notes.append(f"!! StubHub refused a request ({r.request.method} {r.status}) "
                                     f"{r.url[:120]} -> {snippet}")
                if not body or len(body) > _MAX_BODY:
                    continue
                try:
                    cap.docs.append((r.url, json.loads(body)))
                except (ValueError, UnicodeDecodeError):
                    if "json" in ctype:
                        cap.json_failures.append(f"{r.url}: labeled JSON but did not parse")
        finally:
            ctx.close()

    (raw_dir / "cards.json").write_text(json.dumps(cap.cards, ensure_ascii=False, indent=1), encoding="utf-8")
    with (raw_dir / "responses.jsonl").open("w", encoding="utf-8") as f:
        for src, doc in cap.docs:
            f.write(json.dumps({"source": src, "json": doc}, ensure_ascii=False) + "\n")
    (raw_dir / "capture_meta.json").write_text(json.dumps({
        "page_url": cap.page_url, "page_status": cap.page_status, "title": cap.title,
        "html_len": cap.html_len, "json_failures": cap.json_failures, "notes": cap.notes,
        "network": cap.network, "scripts": cap.scripts,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return cap


def load_raw(raw_dir: Path) -> list[tuple[str, Any]]:
    """Re-read a run's captured documents (for re-parsing without re-scraping)."""
    docs = []
    with (raw_dir / "responses.jsonl").open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            docs.append((rec["source"], rec["json"]))
    return docs
