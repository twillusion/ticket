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

_BLOCK_MARKERS = (
    "px-captcha", "captcha-delivery", "access denied", "please verify you are a human",
    "are you a robot", "pardon our interruption", "request unsuccessful",
)
# Not "perimeterx" or "datadome": their sensor scripts load on normal pages too.


class Blocked(Exception):
    pass


@dataclass
class Capture:
    page_url: str
    page_status: int | None
    docs: list[tuple[str, Any]] = field(default_factory=list)   # (url, json)
    json_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def with_quantity(url: str, quantity: int) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != "quantity"]
    q.append(("quantity", str(quantity)))
    return urlunsplit(parts._replace(query=urlencode(q)))


def detect_block(status: int | None, html: str) -> str | None:
    lowered = html.lower()
    for marker in _BLOCK_MARKERS:
        if marker in lowered:
            return f"challenge page detected (marker: {marker!r})"
    if status is not None and status >= 400:
        return f"event page returned HTTP {status}"
    return None


_EMBEDDED_JS = """
() => Array.from(document.querySelectorAll('script')).filter(s => {
  const t = (s.type || '').toLowerCase();
  return t.includes('json') || s.id === '__NEXT_DATA__';
}).map(s => ({id: s.id || null, type: s.type || null, text: s.textContent || ''}))
"""


def _load_more(page, max_rounds: int, notes: list[str]) -> None:
    button = page.get_by_role("button", name=re.compile(r"show more|load more|see more", re.I))
    clicks = 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(1200)
        try:
            if button.first.is_visible(timeout=500):
                button.first.click(timeout=3000)
                clicks += 1
                page.wait_for_timeout(1500)
        except Exception as e:  # a missing/detached button is normal; record it and move on
            notes.append(f"load-more: {type(e).__name__}")
            break
    notes.append(f"load-more clicks: {clicks}")


def capture_event(cfg: StubHubConfig, raw_dir: Path, *, executable_path: str | None = None,
                  url_override: str | None = None, allowed_host_suffix: str = "stubhub.com") -> Capture:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    raw_dir.mkdir(parents=True, exist_ok=True)
    url = url_override or with_quantity(cfg.event_url, cfg.quantity)
    responses = []

    with sync_playwright() as p:
        launch: dict[str, Any] = {"headless": cfg.headless, "viewport": {"width": 1366, "height": 900}}
        if executable_path:
            launch["executable_path"] = executable_path
        elif cfg.browser_channel:
            launch["channel"] = cfg.browser_channel
        cfg.profile_dir.mkdir(parents=True, exist_ok=True)
        ctx = p.chromium.launch_persistent_context(str(cfg.profile_dir), **launch)
        try:
            page = ctx.new_page()
            page.on("response", lambda r: responses.append(r))
            main = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            cap = Capture(page_url=page.url, page_status=main.status if main else None)
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PWTimeout:
                cap.notes.append("networkidle not reached in 30s (continuing)")

            html = page.content()
            (raw_dir / "page.html").write_text(html, encoding="utf-8")
            block = detect_block(cap.page_status, html)
            if block:
                page.screenshot(path=str(raw_dir / "blocked.png"), full_page=True)
                raise Blocked(block)

            _load_more(page, cfg.max_load_more, cap.notes)
            page.screenshot(path=str(raw_dir / "page.png"), full_page=False)

            for s in page.evaluate(_EMBEDDED_JS):
                try:
                    cap.docs.append((f"embedded:script#{s['id'] or '?'}[{s['type']}]", json.loads(s["text"])))
                except json.JSONDecodeError:
                    cap.json_failures.append(f"embedded script {s['id']!r} is not valid JSON")

            for r in responses:
                host = urlsplit(r.url).hostname or ""
                if not host.endswith(allowed_host_suffix):
                    continue
                if "json" not in (r.headers.get("content-type") or "").lower():
                    continue
                try:
                    cap.docs.append((r.url, r.json()))
                except Exception as e:
                    cap.json_failures.append(f"{r.url}: {type(e).__name__}: {e}")
        finally:
            ctx.close()

    with (raw_dir / "responses.jsonl").open("w", encoding="utf-8") as f:
        for src, doc in cap.docs:
            f.write(json.dumps({"source": src, "json": doc}, ensure_ascii=False) + "\n")
    (raw_dir / "capture_meta.json").write_text(json.dumps({
        "page_url": cap.page_url, "page_status": cap.page_status,
        "json_failures": cap.json_failures, "notes": cap.notes,
    }, indent=2), encoding="utf-8")
    return cap


def load_raw(raw_dir: Path) -> list[tuple[str, Any]]:
    """Re-read a run's captured documents (for re-parsing without re-scraping)."""
    docs = []
    with (raw_dir / "responses.jsonl").open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            docs.append((rec["source"], rec["json"]))
    return docs
