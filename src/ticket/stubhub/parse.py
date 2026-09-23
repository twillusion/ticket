"""Find and map listing objects in the JSON that StubHub's event page loads.

StubHub has no public buyer API. Its internal response shape is undocumented, and it
couldn't be inspected from the environment this was written in. So this module:

1. searches every captured JSON document for arrays of listing-like objects, and
2. maps fields using lists of candidate key names.

Every mapping decision is recorded on the listing (`price_field`, `price_candidates`), so it
can be audited later. `python -m ticket stubhub probe` prints what was found, so the key
lists below can be corrected against the real data.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from ticket.db import Listing
from ticket.sections import normalize_section

SOURCE = "stubhub"

ID_KEYS = ("listingId", "ListingId", "listing_id", "id", "Id")
SECTION_KEYS = ("section", "sectionName", "SectionName", "Section", "sectionMapName", "section_name")
ROW_KEYS = ("row", "rowName", "Row", "RowName", "row_name")
QTY_KEYS = ("availableTickets", "availableQuantity", "quantity", "Quantity", "ticketCount",
            "numberOfTickets", "availableTicketCount")
SPLITS_KEYS = ("splits", "availableSplits", "quantityOptions", "ticketSplits", "splitOptions",
               "availableQuantities")
URL_KEYS = ("listingUrl", "url", "deepLink", "href")
NOTE_KEYS = ("listingNotes", "notes", "features", "disclosures", "ticketNotes",
             "listingFeatures", "seatFeatures", "listingNotesText")
CURRENCY_KEYS = ("currencyCode", "currency", "Currency", "CurrencyCode")

# Price fields in preference order. The bool is whether fees are included, INFERRED FROM THE
# FIELD NAME ONLY (None = can't tell). Check one listing against the website to confirm.
PRICE_KEYS: tuple[tuple[str, bool | None], ...] = (
    ("priceWithFees", True),
    ("rawPriceWithFees", True),
    ("priceIncludingFees", True),
    ("allInPrice", True),
    ("priceWithoutFees", False),
    ("rawPrice", None),
    ("price", None),
    ("Price", None),
    ("formattedPrice", None),
)

_SYMBOLS = {
    "S$": "SGD", "SGD": "SGD", "US$": "USD", "USD": "USD", "CA$": "CAD", "C$": "CAD",
    "A$": "AUD", "AU$": "AUD", "HK$": "HKD", "NZ$": "NZD", "£": "GBP", "€": "EUR",
    "GBP": "GBP", "EUR": "EUR", "CAD": "CAD", "AUD": "AUD", "HKD": "HKD", "CNY": "CNY",
    "RMB": "CNY",
}
# "$" and "¥" are ambiguous on their own and are never resolved without an explicit code.
_MONEY_RE = re.compile(
    r"^\s*(?P<pre>[A-Z]{1,3}\$|[A-Z]{3}|\$|£|€|¥)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<post>[A-Z]{3})?\s*$"
)


class ListingParseError(Exception):
    pass


@dataclass
class Candidate:
    """An array in a captured JSON document that looks like a list of listings."""
    source_url: str
    path: str
    count: int
    sample_keys: list[str]
    sample: dict


@dataclass
class ExtractResult:
    listings: list[Listing] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    duplicates: int = 0


def _first_key(obj: dict, keys: tuple[str, ...]) -> tuple[str | None, Any]:
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return k, obj[k]
    return None, None


def _has_any(obj: dict, keys) -> bool:
    return any(k in obj and obj[k] not in (None, "") for k in keys)


def _looks_like_listing(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and _has_any(obj, SECTION_KEYS)
        and _has_any(obj, [k for k, _ in PRICE_KEYS])
        # Section-level price summaries (the pins on the seat map) have section + price but
        # no row or quantity. Requiring one of those keeps them out.
        and (_has_any(obj, ROW_KEYS) or _has_any(obj, QTY_KEYS))
    )


def find_listing_arrays(doc: Any, path: str = "$") -> Iterator[tuple[str, list]]:
    if isinstance(doc, list):
        dicts = [x for x in doc if isinstance(x, dict)]
        if dicts and sum(_looks_like_listing(x) for x in dicts) >= 0.8 * len(dicts):
            yield path, dicts
            return
        for i, x in enumerate(doc):
            yield from find_listing_arrays(x, f"{path}[{i}]")
    elif isinstance(doc, dict):
        for k, v in doc.items():
            yield from find_listing_arrays(v, f"{path}.{k}")


def parse_money(value: Any, default_currency: str | None) -> tuple[float, str | None]:
    """Return (amount, ISO currency or None). Raises ListingParseError if not a price."""
    if isinstance(value, bool):
        raise ListingParseError(f"boolean is not a price: {value!r}")
    if isinstance(value, (int, float)):
        return float(value), default_currency
    if isinstance(value, dict):
        _, amount = _first_key(value, ("amount", "value", "amt", "raw", "rawValue"))
        _, cur = _first_key(value, ("currency", "currencyCode", "code"))
        if amount is None:
            _, formatted = _first_key(value, ("formatted", "display", "text"))
            if formatted is None:
                raise ListingParseError(f"price object without an amount: {value!r}")
            return parse_money(formatted, cur or default_currency)
        amt, parsed_cur = parse_money(amount, None)
        return amt, (cur.upper() if isinstance(cur, str) else None) or parsed_cur or default_currency
    if isinstance(value, str):
        m = _MONEY_RE.match(value.replace(" ", " "))
        if not m:
            raise ListingParseError(f"unrecognized price string: {value!r}")
        amount = float(m["num"].replace(",", ""))
        code = m["post"] or m["pre"]
        if code in (None, "$", "¥"):
            return amount, default_currency
        if code not in _SYMBOLS:
            raise ListingParseError(f"unknown currency marker {code!r} in {value!r}")
        return amount, _SYMBOLS[code]
    raise ListingParseError(f"unsupported price type {type(value).__name__}: {value!r}")


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _parse_splits(v: Any) -> list[int] | None:
    if isinstance(v, str):
        v = [p for p in re.split(r"[,\s]+", v) if p]
    if not isinstance(v, list):
        return None
    out = []
    for item in v:
        if isinstance(item, dict):
            _, item = _first_key(item, ("quantity", "value", "count"))
        n = _as_int(item)
        if n is None:
            return None
        out.append(n)
    return sorted(set(out))


def _strings(v: Any, limit: int = 50) -> list[str]:
    out: list[str] = []

    def walk(x: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(v)
    return out


def _price_candidates(obj: dict) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if "price" in k.lower() and not isinstance(v, (list,))}


def map_listing(obj: dict, requested_quantity: int | None, event_url: str) -> tuple[Listing, list[str]]:
    """Map one listing object. Returns (listing, non-fatal issues)."""
    issues: list[str] = []

    _, section_raw = _first_key(obj, SECTION_KEYS)
    if isinstance(section_raw, dict):
        _, section_raw = _first_key(section_raw, ("name", "label", "displayName"))
    if section_raw is None:
        raise ListingParseError("no section")
    section_raw = str(section_raw)

    _, cur = _first_key(obj, CURRENCY_KEYS)
    listing_currency = cur.upper() if isinstance(cur, str) and len(cur) == 3 else None

    price_field = price = currency = None
    includes_fees: bool | None = None
    price_errors = []
    for key, fees in PRICE_KEYS:
        if key in obj and obj[key] not in (None, ""):
            try:
                price, currency = parse_money(obj[key], listing_currency)
            except ListingParseError as e:
                price_errors.append(f"{key}: {e}")
                continue
            price_field, includes_fees = key, fees
            break
    if price_field is None:
        raise ListingParseError("no usable price (" + "; ".join(price_errors or ["no price field"]) + ")")
    if currency is None:
        issues.append(f"currency unknown for price field {price_field!r}")
    if price is not None and price <= 0:
        raise ListingParseError(f"non-positive price {price!r} in {price_field!r}")

    id_key, listing_id = _first_key(obj, ID_KEYS)
    if listing_id is None:
        digest = hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]
        listing_id = f"h:{digest}"
        issues.append("no listing id; used content hash")

    _, row = _first_key(obj, ROW_KEYS)
    _, qty = _first_key(obj, QTY_KEYS)
    splits_key, splits_raw = _first_key(obj, SPLITS_KEYS)
    splits = _parse_splits(splits_raw) if splits_key else None
    if splits_key and splits is None:
        issues.append(f"could not parse splits from {splits_key!r}: {splits_raw!r}")

    notes: list[str] = []
    for k in NOTE_KEYS:
        if k in obj:
            notes.extend(_strings(obj[k]))

    _, url = _first_key(obj, URL_KEYS)
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.stubhub.com" + url
    if not isinstance(url, str):
        url = event_url

    listing = Listing(
        source=SOURCE,
        listing_id=str(listing_id),
        section_raw=section_raw,
        section=normalize_section(section_raw),
        row=None if row is None else str(row),
        quantity=_as_int(qty),
        allowed_splits=splits,
        requested_quantity=requested_quantity,
        price_per_ticket=price,
        currency=currency,
        price_field=price_field,
        price_includes_fees=includes_fees,
        price_candidates=_price_candidates(obj),
        view_notes=notes,
        url=url,
        raw=obj,
    )
    return listing, issues


def extract(docs: list[tuple[str, Any]], requested_quantity: int | None, event_url: str) -> ExtractResult:
    """docs: (source url, parsed JSON) for every JSON document captured from the page."""
    result = ExtractResult()
    seen: set[str] = set()
    for source_url, doc in docs:
        for path, items in find_listing_arrays(doc):
            result.candidates.append(Candidate(
                source_url=source_url, path=path, count=len(items),
                sample_keys=sorted(items[0].keys()), sample=items[0],
            ))
            for obj in items:
                try:
                    listing, issues = map_listing(obj, requested_quantity, event_url)
                except ListingParseError as e:
                    result.errors.append(f"{source_url} {path}: {e}")
                    continue
                if listing.listing_id in seen:
                    result.duplicates += 1
                    continue
                seen.add(listing.listing_id)
                result.listings.append(listing)
                result.errors.extend(f"{listing.listing_id}: {i}" for i in issues)
    return result
