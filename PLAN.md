# Plan

## 1. What we're tracking

| | |
|---|---|
| Event | 2026 LoL World Championship **Final** |
| Venue | Barclays Center, Brooklyn, NY |
| Date | Sat 14 Nov 2026 |
| Primary seller | Ticketmaster (Fan First verification; general sale was tiny, ~430 tickets reported) |
| Face value | ~$120–350 |
| Resale right now | reported ~$1,360–3,200+ on SeatGeek / Vivid Seats |

The final is **~7 weeks away** (as of 2026-09-23). The tool is only useful for about 50 days, so
the plan aims for the smallest thing that works rather than a platform.

Out of scope unless you say otherwise: Play-ins (Riot Games Arena, LA), Swiss/QF/SF (Credit
Union of Texas Event Center, Allen TX).

## 2. The "in front of the main stage" filter is the actual hard part

Prices are easy to scrape. Deciding whether "Section 18, Row 7" is in front of the stage is not:

- The stage configuration is set **per event**. Recent Worlds finals have used an end-stage
  layout, but I have **not verified** Barclays' layout for this event, and I'm not going to guess.
- Resale sites label sections inconsistently (`18`, `Sec 18`, `Lower Level 18`, `Floor A`, `GA`).
- 闲鱼 listings are free text ("决赛 内场 前排 连座") and often have no section number at all.

**Approach:** keep a hand-maintained `config/sections.yaml` built once from the real event seat
map (the Ticketmaster / StubHub event page map). Every section gets one of:

```yaml
front:   [...]   # tracked
angled:  [...]   # front corners, tracked only if you opt in (see open questions)
side:    [...]   # ignored
behind:  [...]   # ignored / usually not sold
```

A listing whose section **isn't in the map** doesn't get dropped quietly. It's stored as
`unclassified` and shown in its own banner on the site until someone classifies it. The same
goes for 闲鱼 listings that can't be tied to a section: they're kept and flagged, never treated
as "front" by default.

## 3. Sources: feasibility, honestly

| Source | Access | Structured section/row? | Difficulty | Notes |
|---|---|---|---|---|
| **StubHub** | No public buyer API. Event page loads listings through internal JSON calls | Yes | Medium | Heavy bot protection. Plan: headless Chromium (Playwright), low frequency (every 1–3 h), read the listings JSON the page itself fetches instead of parsing HTML. Against ToS, so expect breakage and possibly blocked accounts |
| **闲鱼 / Goofish** | App-first. Web search needs login; signed `mtop` requests; slider captchas | **No**, free text | High | I'd **not** automate this at first. Start with manual entry (paste a listing URL + price + your reading of the seat). Automate later only if it's worth it |
| SeatGeek | Public platform API exists, but it only returns event-level stats (lowest/avg price), not per section | Event-level only | Low | Could be a cheap "market overall" line, but it can't answer the front-of-stage question |
| Vivid Seats / TickPick / TM resale | Similar to StubHub | Yes | Medium | Add later if StubHub alone is too thin |

Recommendation: **StubHub automated + 闲鱼 manual** for v1.

### Environment constraint (already hit)

This cloud dev container's network policy **blocks** ticketmaster.com, vividseats.com and
similar domains. Collectors can't be developed or run against live sites from here. They
have to run on your own machine (or somewhere with a residential IP, which StubHub's bot
protection prefers anyway). GitHub Actions runners will very likely be blocked too.

## 4. Architecture (small on purpose)

```
collector (Python + Playwright, cron on your machine)
   └─> SQLite  data/prices.sqlite   (append-only snapshots)
          └─> build step: generate static site/index.html
                 └─> open locally, or push to GitHub Pages
```

- **Python.** You both already use it. Playwright for StubHub; no framework.
- **SQLite.** One `listings_snapshot` table:
  `source, listing_id, seen_at, section_raw, section, zone(front/angled/side/behind/unclassified), row, quantity, price_per_ticket, price_includes_fees (bool), currency, url, raw_json`.
  Keep `raw_json` so a later parser fix can be re-run over history.
- **Site.** One static page, no server:
  1. Time series of the min and median **per-ticket, all-in** price for `front` (and `angled` if enabled).
  2. Table of current cheapest front listings with a link out.
  3. Health panel: last successful scrape per source, listings found, count of `unclassified`.
     A scrape that returns 0 listings or fails to parse shows as **red**, never as "no data".
- **Alerts (later):** notify when a front listing drops below a threshold you set.

## 5. Data pitfalls to handle explicitly

- **Fees:** StubHub can show prices with or without fees depending on region and settings.
  Record which one we got. Never mix them on one chart without saying so.
- **Currency:** 闲鱼 is CNY. Store the original currency and convert only at display time, with
  the FX rate and date shown.
- **Quantity/splits:** a "$900" listing might be one ticket out of a pair that won't split.
  Filter by the quantity you actually want to buy.
- **Stale/fake listings:** 闲鱼 especially. Treat as indicative prices, not a market.
- **Ticket delivery:** Ticketmaster mobile transfer. A 闲鱼 seller in China needs a TM account
  that can transfer to a US TM account. That's a buying-risk issue, but it's a reason to label
  闲鱼 prices separately and not blend them with StubHub.

## 6. Milestones

| # | Deliverable | Depends on |
|---|---|---|
| M0 | Repo skeleton + this plan | done |
| M1 | `config/sections.yaml` from the real event seat map + SQLite schema | your answers below + a screenshot of the event seat map |
| M2 | StubHub collector, run manually, writes snapshots | runs on your machine |
| M3 | Static site: chart + cheapest table + health panel | M2 |
| M4 | 闲鱼 manual-entry path (CLI or small form) | M1 |
| M5 | Scheduled runs + price alert | M3 |

M1–M3 is the useful core. Everything after that is optional.

## 7. Open questions (need answers before M1)

1. **Which match(es)?** Final only, or also the semis in Allen, TX?
2. **Floor seats:** do floor sections count as "front"? (They usually are front, but viewing
   quality varies a lot.)
3. **Front corners** (angled but still facing the stage): track them or not?
4. **Quantity:** 1 ticket, or 2+ seated together?
5. **Price ceiling / alert threshold**, if any.
6. **闲鱼:** is manual entry acceptable for v1?
7. **Where will this run?** Your laptop on a cron job is the realistic answer, given bot protection.
8. Can you get a **screenshot of the Barclays seat map for this event** (from StubHub or TM)?
   That's what `sections.yaml` gets built from.
