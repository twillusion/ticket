"""Read listing cards from the rendered StubHub page.

The first real probe (2026-09-23) showed StubHub renders listings straight into the HTML:
no listing JSON in XHR responses or inline scripts. So we read the DOM, without depending on
CSS class names (they're generated and change often):

- a card is found from its "Section N" text label, by climbing to the largest ancestor that
  still contains exactly one section label and at least one price;
- prices are read per text node, and crossed-out ones (line-through, <s>, <del>) are marked,
  so a "was S$4,688, now S$3,290" card isn't misread.

Mapping text to fields is conservative: an ambiguous card is a parse error, not a guess.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from ticket.db import Listing
from ticket.sections import normalize_section
from ticket.stubhub.parse import SOURCE, ListingParseError, parse_money

CARDS_JS = r"""
() => {
  const PRICE = /(?:[A-Z]{0,3}\$|£|€|¥)\s?\d[\d,]*(?:\.\d+)?/;
  const PRICE_G = /(?:[A-Z]{0,3}\$|£|€|¥)\s?\d[\d,]*(?:\.\d+)?/g;
  const LABEL = /^\s*(?:section|sec\.?)\s+\S+/i;
  const COUNT = /\b(?:section|sec\.?)\s+\S+/gi;
  const nSections = el => ((el.innerText || '').match(COUNT) || []).length;

  const labels = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const t = (w.currentNode.nodeValue || '').trim();
    if (t.length < 40 && LABEL.test(t) && w.currentNode.parentElement) labels.push(w.currentNode.parentElement);
  }

  const seen = new Set();
  const cards = [];
  for (const label of labels) {
    let el = label, card = null;
    while (el && el !== document.body) {
      if (nSections(el) > 1) break;
      if (PRICE.test(el.innerText || '')) card = el;
      el = el.parentElement;
    }
    if (!card || seen.has(card)) continue;
    seen.add(card);

    const prices = [];
    const tw = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    while (tw.nextNode()) {
      const node = tw.currentNode, txt = (node.nodeValue || '').trim();
      const found = txt.match(PRICE_G);
      if (!found) continue;
      let struck = false;
      for (let e = node.parentElement; e && e !== card.parentElement; e = e.parentElement) {
        const st = getComputedStyle(e);
        if ((st.textDecorationLine || '').includes('line-through') || e.tagName === 'S' || e.tagName === 'DEL') {
          struck = true; break;
        }
      }
      const pe = node.parentElement;
      const visible = !!(pe && (pe.offsetWidth || pe.offsetHeight || pe.getClientRects().length));
      for (const f of found) prices.push({text: f, context: txt.slice(0, 80), struck, visible});
    }

    const attrs = {};
    for (const e of [card, ...card.querySelectorAll('*')].slice(0, 400)) {
      for (const a of e.attributes) {
        if (a.name.startsWith('data-') && Object.keys(attrs).length < 60 && !(a.name in attrs)) attrs[a.name] = a.value.slice(0, 200);
      }
    }
    const link = card.closest('a') || card.querySelector('a[href]');
    cards.push({
      label: label.innerText.trim(),
      lines: (card.innerText || '').split('\n').map(s => s.trim()).filter(Boolean).slice(0, 40),
      prices,
      data_attrs: attrs,
      id_attr: card.id || null,
      href: link ? link.getAttribute('href') : null,
      html: card.outerHTML.slice(0, 6000),
    });
  }
  return cards;
}
"""

COUNT_CARDS_JS = r"""
() => {
  const LABEL = /^\s*(?:section|sec\.?)\s+\S+/i;
  let n = 0;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const t = (w.currentNode.nodeValue || '').trim(); if (t.length < 40 && LABEL.test(t)) n++; }
  return n;
}
"""

# Scroll the nearest scrollable ancestor of the last section label (the listing panel), plus
# the window. Returns true if something scrolled.
SCROLL_LIST_JS = r"""
() => {
  const LABEL = /^\s*(?:section|sec\.?)\s+\S+/i;
  let last = null;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const t = (w.currentNode.nodeValue || '').trim(); if (t.length < 40 && LABEL.test(t)) last = w.currentNode.parentElement; }
  let moved = false;
  for (let e = last; e && e !== document.documentElement; e = e.parentElement) {
    const st = getComputedStyle(e);
    if (/(auto|scroll)/.test(st.overflowY) && e.scrollHeight > e.clientHeight + 10) {
      const before = e.scrollTop; e.scrollTop = e.scrollHeight; moved = moved || e.scrollTop !== before; break;
    }
  }
  const y = window.scrollY; window.scrollTo(0, document.body.scrollHeight); moved = moved || window.scrollY !== y;
  return moved;
}
"""

CLICK_MORE_JS = r"""
() => {
  const re = /^(show|load|see) more/i;
  for (const b of document.querySelectorAll('button, a[role=button], [role=button]')) {
    if (re.test((b.innerText || '').trim()) && (b.offsetWidth || b.offsetHeight)) { b.click(); return true; }
  }
  return false;
}
"""

_ROW = re.compile(r"\brow\s+([A-Za-z0-9]{1,6})\b", re.I)
_QTY = re.compile(r"\b(\d{1,2})\s*(?:tickets?|tix)\b", re.I)
_ID_ATTR = re.compile(r"listing[-_]?id|^data-id$|^data-listing$", re.I)


def _listing_id(card: dict) -> tuple[str | None, str]:
    for k, v in card.get("data_attrs", {}).items():
        if _ID_ATTR.search(k) and v:
            return str(v), f"attr {k}"
    href = card.get("href")
    if href:
        q = parse_qs(urlsplit(href).query)
        for k in ("listingId", "listing_id", "ListingId", "listingid"):
            if q.get(k):
                return q[k][0], f"href {k}"
    return None, ""


def map_card(card: dict, requested_quantity: int | None, event_url: str) -> tuple[Listing, list[str]]:
    issues: list[str] = []
    section_raw = card.get("label")
    if not section_raw:
        raise ListingParseError("card without a section label")

    live = [p for p in card.get("prices", []) if not p.get("struck") and p.get("visible", True)]
    parsed = []
    for p in live:
        try:
            parsed.append(parse_money(p["text"], None))
        except ListingParseError as e:
            issues.append(f"price text {p['text']!r}: {e}")
    distinct = sorted(set(parsed))
    if not distinct:
        raise ListingParseError(f"{section_raw}: no un-struck price in card (prices seen: {card.get('prices')})")
    if len(distinct) > 1:
        raise ListingParseError(f"{section_raw}: {len(distinct)} different un-struck prices {distinct}; "
                                f"can't tell which is the ticket price. Card lines: {card.get('lines')}")
    price, currency = distinct[0]
    if currency is None:
        issues.append("currency unknown (bare '$' or '¥')")

    lines = card.get("lines", [])
    text = "\n".join(lines)
    row_m = _ROW.search(text)
    qty_m = _QTY.search(text)

    listing_id, id_source = _listing_id(card)
    if listing_id is None:
        basis = json.dumps([section_raw, row_m and row_m[1], price, lines], sort_keys=True)
        listing_id = "h:" + hashlib.sha1(basis.encode()).hexdigest()[:16]
        issues.append("no listing id in card; used content hash")

    consumed = re.compile(r"^(?:section|sec\.?)\s|\brow\s|(?:[A-Z]{0,3}\$|£|€|¥)\s?\d|\btickets?\b", re.I)
    notes = [ln for ln in lines if not consumed.search(ln)][:15]

    href = card.get("href")
    url = urljoin("https://www.stubhub.com/", href) if href else event_url

    listing = Listing(
        source=SOURCE,
        listing_id=str(listing_id),
        section_raw=section_raw,
        section=normalize_section(section_raw),
        row=row_m[1] if row_m else None,
        quantity=int(qty_m[1]) if qty_m else None,
        allowed_splits=None,
        requested_quantity=requested_quantity,
        price_per_ticket=price,
        currency=currency,
        price_field="dom:unstruck-price",
        price_includes_fees=None,
        price_candidates={"dom_prices": card.get("prices", []), "id_source": id_source},
        view_notes=notes,
        url=url,
        raw={k: v for k, v in card.items() if k != "html"} | {"html_head": card.get("html", "")[:2000]},
    )
    return listing, issues


def extract_cards(cards: list[dict], requested_quantity: int | None, event_url: str):
    from ticket.stubhub.parse import ExtractResult

    result = ExtractResult()
    seen: set[str] = set()
    for card in cards:
        try:
            listing, issues = map_card(card, requested_quantity, event_url)
        except ListingParseError as e:
            result.errors.append(f"dom card: {e}")
            continue
        if listing.listing_id in seen:
            result.duplicates += 1
            continue
        seen.add(listing.listing_id)
        result.listings.append(listing)
        result.errors.extend(f"{listing.listing_id}: {i}" for i in issues)
    return result


def load_cards(raw_dir) -> list[dict[str, Any]]:
    path = raw_dir / "cards.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
