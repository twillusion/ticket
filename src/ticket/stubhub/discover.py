"""`python -m ticket stubhub discover`: build the per-tier filtered links automatically.

1. Load the event page once, collect section ids from listing cards and the seat-map SVG.
2. Test, with a few page loads, how StubHub's link filters behave:
   - does `sections=<id>` work without `ticketClasses`?
   - can one link hold several sections (`sections=a,b`)?
   - does a sort parameter work (`sortBy=NEWPRICE&sortDirection=0`)?
3. Write config/stubhub_views.toml from what actually worked, and list any tier sections
   whose id is unknown (they're left out, loudly).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ticket.config import TIERS
from ticket.stubhub import sectionmap
from ticket.stubhub.dom import extract_cards
from ticket.stubhub.fetch import Blocked, capture_event, with_quantity
from ticket.stubhub.setup_views import VIEWS_FILE, tier_sections

SORT = {"sortBy": "NEWPRICE", "sortDirection": "0"}


def filter_url(event_url: str, quantity: int, section_ids: list[str], class_ids: list[str] | None,
               extra: dict[str, str] | None = None) -> str:
    parts = urlsplit(event_url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("quantity", "sections", "ticketClasses", "rows")]
    q += [("quantity", str(quantity)), ("sections", ",".join(section_ids))]
    if class_ids is not None:
        q.append(("ticketClasses", ",".join(dict.fromkeys(class_ids))))
    q += list((extra or {}).items())
    return urlunsplit(parts._replace(query=urlencode(q, safe=",")))


@dataclass
class Trial:
    name: str
    url: str
    sections: list[str]
    prices: list[float]
    error: str | None = None

    @property
    def seen(self) -> set[str]:
        return set(self.sections)


def _trial(cfg, raw, name: str, url: str) -> Trial:
    try:
        cap = capture_event(cfg, raw / name, url_override=url, wait_for_me=True)
    except Blocked as e:
        return Trial(name, url, [], [], f"blocked: {e}")
    res = extract_cards(cap.cards, cfg.quantity, url)
    return Trial(name, url, [l.section or "?" for l in res.listings],
                 [l.price_per_ticket for l in res.listings if l.price_per_ticket is not None])


def _show(t: Trial) -> None:
    print(f"  [{t.name}] {t.url}")
    if t.error:
        print(f"      !! {t.error}")
    else:
        print(f"      {len(t.sections)} listings, sections {sorted(t.seen)}, prices {t.prices}")


def run_discover(sources) -> int:
    cfg = sources.stubhub
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = sources.raw_dir / "stubhub" / f"discover-{stamp}"
    wanted = tier_sections()

    print("Step 1: loading the event page to collect section ids ...")
    try:
        cap = capture_event(cfg, raw / "0-event", url_override=with_quantity(cfg.event_url, cfg.quantity),
                            wait_for_me=True)
    except Blocked as e:
        print(f"!! BLOCKED on the first page load: {e}")
        return 2
    sm = sectionmap.build(cap.cards, cap.svgs)
    for line in sm.report:
        print(f"  {line}")
    (raw / "section_ids.json").write_text(json.dumps(
        {"ids": sm.ids, "classes": sm.classes, "source": sm.source}, indent=1), encoding="utf-8")
    missing = {t: [s for s in wanted[t] if s not in sm.ids] for t in TIERS}
    for t in TIERS:
        have = [s for s in wanted[t] if s in sm.ids]
        print(f"  {t:5}: ids known for {have}" + (f"; !! UNKNOWN for {missing[t]}" if missing[t] else ""))

    print("\nStep 2: testing how StubHub's link filters behave (a few page loads) ...")
    a, b = "17", "19"       # both seen with listings and ids in real runs
    ida, idb = sm.ids[a], sm.ids[b]
    cls = [sm.classes[a], sm.classes[b]]
    t_single = _trial(cfg, raw, "1-single-no-class", filter_url(cfg.event_url, cfg.quantity, [ida], None))
    _show(t_single)
    t_multi = _trial(cfg, raw, "2-multi-with-class", filter_url(cfg.event_url, cfg.quantity, [ida, idb], cls))
    _show(t_multi)
    t_multi_nc = _trial(cfg, raw, "3-multi-no-class", filter_url(cfg.event_url, cfg.quantity, [ida, idb], None))
    _show(t_multi_nc)
    t_sort = _trial(cfg, raw, "4-sort", filter_url(cfg.event_url, cfg.quantity, [ida], [sm.classes[a]], SORT))
    _show(t_sort)

    def only(t: Trial, allowed: set[str]) -> bool:
        return not t.error and bool(t.sections) and t.seen <= allowed

    single_ok = only(t_single, {a})
    multi_ok = only(t_multi, {a, b}) and t_multi.seen == {a, b}
    multi_nc_ok = only(t_multi_nc, {a, b}) and t_multi_nc.seen == {a, b}
    sort_ok = only(t_sort, {a}) and len(t_sort.prices) > 1 and t_sort.prices == sorted(t_sort.prices)

    print("\n  Findings:")
    print(f"    sections= alone (no ticketClasses) filters correctly: {single_ok}")
    print(f"    several sections in one link (with ticketClasses):  {multi_ok}")
    print(f"    several sections in one link (no ticketClasses):    {multi_nc_ok}")
    print(f"    sort parameter gives lowest-first:                  {sort_ok}"
          + ("" if len(t_sort.prices) > 1 else "  (inconclusive: fewer than 2 listings)"))
    for t in (t_single, t_multi, t_multi_nc, t_sort):
        if t.error:
            print(f"    !! trial {t.name} did not complete, so its finding is unreliable")

    # Decide how to build links. Without ticketClasses is preferred: class ids are only known
    # for sections seen on a card, while section ids may also come from the map.
    use_classes = not (single_ok or multi_nc_ok)
    multi = multi_nc_ok or (multi_ok and use_classes)
    extra = SORT if sort_ok else None
    if not (single_ok or multi_ok or multi_nc_ok):
        print("\n!! None of the filtered links behaved as expected, so nothing was saved. "
              "Paste this output to Claude.")
        return 2

    views: list[tuple[str, str, str]] = []
    left_out: list[str] = []
    for t in TIERS:
        secs = [s for s in wanted[t] if s in sm.ids and (not use_classes or s in sm.classes)]
        left_out += [f"{t}:{s}" for s in wanted[t] if s not in secs]
        if not secs:
            continue
        groups = [secs] if multi else [[s] for s in secs]
        for g in groups:
            name = t if multi else f"{t}-{g[0]}"
            url = filter_url(cfg.event_url, cfg.quantity, [sm.ids[s] for s in g],
                             [sm.classes[s] for s in g] if use_classes else None, extra)
            views.append((name, url, f"sections {g}"))

    lines = [f"# Generated by `python -m ticket stubhub discover` on {stamp}.",
             f"# Link format: sections=<ids>{'&ticketClasses=<ids>' if use_classes else ''}"
             f"{' (several per link)' if multi else ' (one section per link)'}"
             f"{'; sorted lowest-first' if extra else '; unsorted'}.",
             "# Safe to edit or delete; re-run discover to regenerate.", ""]
    for name, url, note in views:
        lines += ["[[views]]", f"name = {json.dumps(name)}", f"url = {json.dumps(url)}", f"# {note}", ""]
    VIEWS_FILE.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nSaved {len(views)} view(s) to {VIEWS_FILE}: {[v[0] for v in views]}")
    print(f"  -> each `collect` run will load {len(views)} page(s).")
    if left_out:
        print(f"!! Left out because their StubHub id is unknown: {left_out}")
        print("   They'll be picked up once a listing card for them appears; re-run discover later.")
    if not multi and len(views) > 10:
        print("!! That's a lot of page loads per run. Consider trimming the 'far' tier.")
    return 0
