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


def _print_capture(cap, raw_dir) -> None:
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

    stubhub_xhr = [n for n in cap.network if "stubhub" in n["url"]]
    print(f"\n[2] XHR/fetch requests: {len(cap.network)} total, {len(stubhub_xhr)} to StubHub (shown)")
    for n in stubhub_xhr[:25]:
        print(f"      {n['method']:4} {n['status']} {n.get('bytes', '?'):>8}B {n['type'][:28]:28} {n['url'][:110]}")

    big = sorted((sc for sc in cap.scripts if sc["length"] >= 2000), key=lambda sc: -sc["length"])
    print(f"\n[3] Inline scripts over 2 KB: {len(big)}")
    for sc in big[:12]:
        print(f"      {sc['length']:>9,} chars  {sc['label'][:50]:50} hints {sc['hints']}")

    print(f"\n[4] JSON documents captured: {len(cap.docs)} (details in responses.jsonl)")

    fee_lines = sorted({ln for ln in lines if "fee" in ln.lower()})[:10]
    print(f"\n[5] Distinct page text mentioning fees: {fee_lines}")

    print(f"\n[6] Listing cards read from the page: {len(cap.cards)}")
    for c in cap.cards[:2]:
        print(f"  - label {c['label']!r}  href {str(c['href'])[:100]}")
        print(f"      lines:  {c['lines'][:14]}")
        print(f"      prices: {[(p['text'], 'STRUCK' if p['struck'] else 'live', 'shown' if p['visible'] else 'hidden') for p in c['prices']]}")


def cmd_probe(args) -> int:
    from ticket.stubhub.collect import extract_all
    from ticket.stubhub.fetch import Blocked, capture_event, with_quantity

    sources = load_sources()
    cfg = sources.stubhub
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    views = cfg.view_list()
    if args.view:
        views = [v for v in views if v[0] == args.view]
        if not views:
            print(f"No view named {args.view!r}. Configured: {[n for n, _ in cfg.view_list()]}", file=sys.stderr)
            return 1

    docs, cards = [], []
    for name, url in views:
        raw_dir = sources.raw_dir / "stubhub" / f"probe-{stamp}" / name
        print(f"\n==================== view {name!r} ====================")
        try:
            cap = capture_event(cfg, raw_dir, url_override=with_quantity(url, cfg.quantity),
                                wait_for_me=args.wait_for_me)
        except Blocked as e:
            print(f"BLOCKED: {e}\nScreenshot and page HTML saved in {raw_dir}", file=sys.stderr)
            if not args.wait_for_me:
                print("Try: python -m ticket stubhub probe --wait-for-me  (solve the check by hand)", file=sys.stderr)
            return 2
        _print_capture(cap, raw_dir)
        docs.extend(cap.docs)
        for c in cap.cards:
            c["view"] = name
        cards.extend(cap.cards)

    res, method = extract_all(docs, cards, cfg.quantity, cfg.event_url)
    tiers = load_section_tiers()
    print(f"\n==================== all views ====================")
    print(f"Listings taken from: {method}   parsed: {len(res.listings)}   duplicates across views: "
          f"{res.duplicates}   issues: {len(res.errors)}")
    for l in sorted(res.listings, key=lambda l: (tier_for(l.section, tiers), l.price_per_ticket or 0)):
        fees = {True: "incl. fees", False: "excl. fees", None: "fees ?"}[l.price_includes_fees]
        print(f"  {tier_for(l.section, tiers):12} sec {l.section:>5} row {str(l.row):>4} qty {l.quantity}"
              f"  {l.price_per_ticket:>9,.0f} {l.currency} ({fees})  pair {pair_status(l.quantity, None if l.allowed_splits is None else json.dumps(l.allowed_splits), l.requested_quantity):11}"
              f"  id {l.listing_id}  [{l.price_candidates.get('view')}]")
    for e in res.errors[:20]:
        print(f"  ! {e}")
    if not res.listings:
        print("\nNOTHING FOUND in the captured data. Paste this whole output to Claude; also look at\n"
              "page.png in the probe folder: are ticket listings visible, or is something in the way?")
        return 2
    print("\nCompare 2-3 listings above with the StubHub page (price, section, row).")
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
    if any(r["price_field"] != "dom:unstruck-price" for r in rows):
        print("  (for JSON-sourced listings, fee inclusion is inferred from the field name)")
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
    pr.add_argument("--view", help="only probe this configured view")
    sh.add_parser("discover", help="find section ids, test StubHub's link filters, write the per-tier views")
    sv = sh.add_parser("setup-views", help="guided: filter StubHub per tier in the window, check it, save the links")
    sv.add_argument("--tier", action="append", choices=TIERS, help="only (re)do this tier; repeatable")
    c = sh.add_parser("collect", help="capture and store one snapshot")
    c.add_argument("--reparse", metavar="RAW_DIR", help="re-parse a saved raw folder instead of scraping")
    c.add_argument("--wait-for-me", action="store_true",
                   help="if StubHub shows a bot check, pause so you can solve it (not for scheduled runs)")

    sub.add_parser("status", help="summarize the latest runs and prices")

    args = p.parse_args(argv)
    if args.cmd == "stubhub":
        if args.action == "discover":
            from ticket.stubhub.discover import run_discover
            return run_discover(load_sources())
        if args.action == "setup-views":
            from ticket.stubhub.setup_views import run_wizard
            return run_wizard(load_sources(), only=args.tier)
        return cmd_probe(args) if args.action == "probe" else cmd_collect(args)
    return cmd_status(args)
