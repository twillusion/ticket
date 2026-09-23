"""Command line entry point: python -m ticket <command>."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ticket import db
from ticket.config import TIERS, load_section_tiers, load_sources, tier_for
from ticket.pairs import FILTER_ONLY, NO, pair_status

VIEW_WORDS = ("obstruct", "limited", "side view", "behind", "partial view", "restricted")
PRICE_LIKE = re.compile(r"(?:[A-Z]{0,3}\$|£|€|¥)\s?\d[\d,]*")
SECTION_LIKE = re.compile(r"\b(?:section|sec\.?)\s*\d+", re.I)


def _fees_label(v) -> str:
    return {1: "incl. fees", 0: "excl. fees"}.get(v, "fees UNKNOWN")


def cmd_probe(args) -> int:
    from ticket.stubhub.fetch import Blocked, capture_event
    from ticket.stubhub.parse import extract

    sources = load_sources()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_dir = sources.raw_dir / "stubhub" / f"probe-{stamp}"
    try:
        cap = capture_event(sources.stubhub, raw_dir, wait_for_me=args.wait_for_me)
    except Blocked as e:
        print(f"BLOCKED: {e}\nScreenshot and page HTML saved in {raw_dir}", file=sys.stderr)
        if not args.wait_for_me:
            print("Try: python -m ticket stubhub probe --wait-for-me  (solve the check by hand)", file=sys.stderr)
        return 2

    print(f"Page: {cap.page_url}  HTTP {cap.page_status}")
    print(f"Title: {cap.title!r}   HTML size: {cap.html_len:,} chars")
    print(f"Raw captures saved in: {raw_dir}")
    for n in cap.notes:
        print(f"  note: {n}")

    lines = [ln.strip() for ln in cap.body_text.splitlines() if ln.strip()]
    pricey = [ln for ln in lines if PRICE_LIKE.search(ln) or SECTION_LIKE.search(ln)]
    print(f"\n[1] Visible page text: {len(lines)} lines, {len(pricey)} look like prices/sections")
    for ln in pricey[:25]:
        print(f"      | {ln[:120]}")

    print(f"\n[2] XHR/fetch requests: {len(cap.network)}")
    for n in cap.network[:45]:
        print(f"      {n['method']:4} {n['status']} {n.get('bytes', '?'):>8}B {n['type'][:28]:28} {n['url'][:110]}")

    big = sorted((sc for sc in cap.scripts if sc["length"] >= 2000), key=lambda sc: -sc["length"])
    print(f"\n[3] Inline scripts over 2 KB: {len(big)}")
    for sc in big[:12]:
        print(f"      {sc['length']:>9,} chars  {sc['label'][:50]:50} hints {sc['hints']}")

    print(f"\n[4] JSON documents captured: {len(cap.docs)}")
    for src, doc in cap.docs[:30]:
        top = list(doc.keys())[:10] if isinstance(doc, dict) else f"list[{len(doc)}]"
        print(f"  - {src[:120]}\n      top-level: {top}")
    if len(cap.docs) > 30:
        print(f"  ... {len(cap.docs) - 30} more (see responses.jsonl)")
    for f in cap.json_failures:
        print(f"  ! {f}")

    res = extract(cap.docs, sources.stubhub.quantity, sources.stubhub.event_url)
    print(f"\n[5] Listing-like arrays found: {len(res.candidates)}")
    for c in res.candidates:
        print(f"  - {c.count} items at {c.path}\n      from {c.source_url[:120]}\n      keys: {c.sample_keys}")
        print("      sample: " + json.dumps(c.sample, ensure_ascii=False)[:1500])
    print(f"\nParsed listings: {len(res.listings)}   duplicates skipped: {res.duplicates}   issues: {len(res.errors)}")
    for l in res.listings[:8]:
        print(f"  {l.listing_id:>14}  sec {l.section_raw!r} -> {l.section!r}  row {l.row!r}  qty {l.quantity}"
              f"  splits {l.allowed_splits}  {l.price_per_ticket} {l.currency}"
              f"  via {l.price_field!r} ({_fees_label(None if l.price_includes_fees is None else int(l.price_includes_fees))})")
        print(f"      price fields seen: {json.dumps(l.price_candidates, ensure_ascii=False)[:300]}")
    for e in res.errors[:20]:
        print(f"  ! {e}")
    if not res.candidates:
        print("\nNOTHING FOUND in the captured data. Paste this whole output to Claude; also look at\n"
              f"page.png in {raw_dir}: are ticket listings visible, or is something (a pop-up,\n"
              "a quantity picker, a queue page) in the way?")
        return 2
    print("\nCompare 2-3 listings above with what the StubHub page shows (price, fees, section).")
    return 0


def cmd_collect(args) -> int:
    from ticket.stubhub.collect import run

    sources = load_sources()
    out = run(sources, reparse_dir=Path(args.reparse) if args.reparse else None,
              **({"wait_for_me": True} if args.wait_for_me and not args.reparse else {}))
    print(f"[stubhub] {out.run_id}: {out.status.upper()} - {out.message}")
    if out.result:
        for e in out.result.errors[:10]:
            print(f"  ! {e}")
        if len(out.result.errors) > 10:
            print(f"  ! ... {len(out.result.errors) - 10} more issues")
    return 0 if out.status in ("ok", "partial") else 2


def cmd_status(args) -> int:
    sources = load_sources()
    if not sources.db_path.exists():
        print(f"No database yet at {sources.db_path}. Run: python -m ticket stubhub collect")
        return 1
    conn = db.connect(sources.db_path)
    tiers = load_section_tiers()

    print("Recent runs:")
    runs = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 8").fetchall()
    for r in runs:
        flag = "  " if r["status"] == "ok" else "!!"
        print(f" {flag} {r['started_at']}  {r['source']:8} {r['status']:8} {r['message'] or ''}")
    last_ok = conn.execute(
        "SELECT * FROM runs WHERE status IN ('ok','partial') ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not last_ok:
        print("\nNo successful run yet.")
        return 1
    if runs and runs[0]["run_id"] != last_ok["run_id"]:
        print(f"\n!! The latest run did NOT succeed. Figures below are from {last_ok['started_at']}.")

    rows = conn.execute("SELECT * FROM listings_snapshot WHERE run_id = ?", (last_ok["run_id"],)).fetchall()
    print(f"\nLatest good run {last_ok['run_id']}: {len(rows)} listings")

    by_tier: dict[str, list] = {t: [] for t in (*TIERS, "excluded", "unclassified")}
    for r in rows:
        by_tier[tier_for(r["section"], tiers)].append(r)

    currencies = Counter(r["currency"] for r in rows)
    fee_flags = Counter(_fees_label(r["price_includes_fees"]) for r in rows)
    price_fields = Counter(r["price_field"] for r in rows)
    print(f"  currency: {dict(currencies)}   price field: {dict(price_fields)}   {dict(fee_flags)}")
    print("  (fee inclusion is inferred from the field name; confirm once against the StubHub page)")
    if None in currencies:
        print("  !! some listings have an UNKNOWN currency. Their prices are not comparable.")

    print("\nCheapest pair (2 x per-ticket price) per tier:")
    for t in TIERS:
        usable = [r for r in by_tier[t] if pair_status(r["quantity"], r["allowed_splits"], r["requested_quantity"]) != NO
                  and r["price_per_ticket"] is not None]
        if not usable:
            print(f"  {t:5}: no buyable pairs")
            continue
        best = min(usable, key=lambda r: r["price_per_ticket"])
        ps = pair_status(best["quantity"], best["allowed_splits"], best["requested_quantity"])
        caveat = "  (split rules unknown; relies on StubHub's quantity filter)" if ps == FILTER_ONLY else ""
        print(f"  {t:5}: {2 * best['price_per_ticket']:,.0f} {best['currency'] or '???'} "
              f"({_fees_label(best['price_includes_fees'])})  sec {best['section']} row {best['row']}"
              f"  [{len(usable)} buyable listings]{caveat}")

    unc = Counter(r["section_raw"] for r in by_tier["unclassified"])
    if unc:
        print(f"\n!! {sum(unc.values())} listings in sections not in config/sections.toml:")
        for s, n in unc.most_common(20):
            print(f"     {s!r}: {n}")

    flagged = [(r, n) for t in TIERS for r in by_tier[t] for n in json.loads(r["view_notes"] or "[]")
               if any(w in n.lower() for w in VIEW_WORDS)]
    if flagged:
        print("\nView disclosures in tracked sections (stage-layout clues):")
        for r, n in flagged[:20]:
            print(f"   sec {r['section']} row {r['row']}: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ticket", description="LoL Worlds 2026 Final resale tracker")
    sub = p.add_subparsers(dest="cmd", required=True)

    sh = sub.add_parser("stubhub", help="StubHub collector").add_subparsers(dest="action", required=True)
    pr = sh.add_parser("probe", help="load the page, save raw captures, print what was found; stores nothing")
    pr.add_argument("--wait-for-me", action="store_true",
                    help="if StubHub shows a bot check, pause so you can solve it in the window")
    c = sh.add_parser("collect", help="capture and store one snapshot")
    c.add_argument("--reparse", metavar="RAW_DIR", help="re-parse a saved raw folder instead of scraping")
    c.add_argument("--wait-for-me", action="store_true",
                   help="if StubHub shows a bot check, pause so you can solve it (not for scheduled runs)")

    sub.add_parser("status", help="summarize the latest runs and prices")

    args = p.parse_args(argv)
    if args.cmd == "stubhub":
        return cmd_probe(args) if args.action == "probe" else cmd_collect(args)
    return cmd_status(args)
