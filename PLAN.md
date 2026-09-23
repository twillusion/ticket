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

**Approach:** keep a hand-maintained [`config/sections.toml`](config/sections.toml) built from
the event seat map ([`docs/seatmap-stubhub-2026-09-23.png`](docs/seatmap-stubhub-2026-09-23.png)).
Tracked sections go into `best` / `good` / `far`. Known but unwanted sections go into `excluded`.

**Stage orientation is still unverified.** StubHub's drawing puts the stage at the 15/16/17
end, which would make the chosen sections the *behind-stage* end. The listing pattern (no
floor listings and none in 1/31/3/4/203/204/228/229) suggests that drawing is a generic
template and the real stage is at the Modelo Bridge end. The config header records this. It
needs checking against Riot's or Ticketmaster's own map.

**Update 2026-09-23: two maps, two layouts.** A second map
([`docs/seatmap-centerstage-2026-09-23.png`](docs/seatmap-centerstage-2026-09-23.png)) shows a
**center stage** with no floor seating. It comes from the **Ticketmaster event page** (static
"Seat Map" panel) and carries a "general layout, may vary without notice" disclaimer.
StubHub shows an end stage with a runway. Ticketmaster is the primary seller, so its map
counts for more, but the disclaimer means it isn't confirmation. Both agree that
**no floor seats are sold.** If the stage really is in the center, the long sides
(7/8/9, 23/24/25) are closest and 15/16/17 is an end. Which side counts as "front" would then
depend on which way the player booths face, which is also unknown.

Research on 2026-09-23 found **no published stage layout** from Riot, Barclays or any press
coverage. This dev container is also blocked from reaching Ticketmaster, StubHub, SeatGeek,
Vivid Seats, TickPick, Barclays and lolesports, so none of their event maps have been checked.
Ways to verify, cheapest first:

1. **Ticketmaster event map** (primary seller). Its map comes from the venue's manifest for
   this event, so it's more likely to be real than a reseller template.
2. **Compare reseller maps** (StubHub / SeatGeek / Vivid / TickPick). If they put the stage in
   different places, they're templates.
3. **View disclosures in listings.** Sellers and Ticketmaster add notes like "limited view",
   "side view" or "obstructed". The collector keeps these per listing (`view_notes`). If they
   pile up in 15/16/17, that end is behind the stage.
4. **Inventory pattern over time.** Sections that never show listings were probably not sold.
   This is weak evidence on its own.
5. Ask Barclays box office / Riot Player Support directly.

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
  `source, listing_id, seen_at, section_raw, section, row, quantity, allowed_splits, price_per_ticket, price_includes_fees (bool), currency, view_notes, url, raw_json`.
  Keep `raw_json` so a later parser fix can be re-run over history.
  **Tiers are not stored.** They're computed from `config/sections.toml` when the site is
  built. The collector records **every** section, so if the stage layout turns out different,
  editing the config re-tiers the whole history. Nothing has to be re-scraped.
- **Site.** One static page, no server:
  1. Time series of the min and median **all-in price for 2 seats together**, one line per tier (best / good / far).
  2. Table of current cheapest buyable pairs per tier with a link out.
  3. Health panel: last successful scrape per source, listings found, count of `unclassified`.
     A scrape that returns 0 listings or fails to parse shows as **red**, never as "no data".
- **Event markers on the chart:** semifinal results (who made the final) will move prices more
  than anything else. Mark them on the time series so price jumps can be explained.
- **Alerts (later):** notify when a pair in a tracked tier drops below a threshold you set.

## 5. Data pitfalls to handle explicitly

- **Fees:** StubHub can show prices with or without fees depending on region and settings.
  Record which one we got. Never mix them on one chart without saying so.
- **Currency:** your StubHub session shows **SGD**; 闲鱼 is CNY. Store the original currency and convert only at display time, with
  the FX rate and date shown.
- **Quantity/splits: buying 2, seated together.** A listing counts only if you can buy
  exactly 2 from it: `quantity >= 2` and the seller's split rules allow 2. Listings of 3 that
  won't split to 2 are out. Charts show the **all-in total for the pair**, not the headline
  per-ticket price. A listing with unknown split rules is kept but flagged, not assumed OK.
- **Stale/fake listings:** 闲鱼 especially. Treat as indicative prices, not a market.
- **Buyer protection:** Ticketmaster has no primary tickets on sale, so resale is the only
  route. Buy only through a platform that guarantees delivery and refunds a no-show (StubHub,
  SeatGeek, Vivid Seats, TM resale). 闲鱼 gives no real protection for a cross-border TM
  transfer. Its prices are a reference, not a place to buy.
- **Late primary releases:** venues often release held-back tickets once the stage is
  finalised, sometimes days before the event, and at face value. Worth a manual check of the
  TM event page every few days. Automating it is optional (same bot-protection issue as StubHub).
- **Ticket delivery:** Ticketmaster mobile transfer. A 闲鱼 seller in China needs a TM account
  that can transfer to a US TM account. That's a buying-risk issue, but it's a reason to label
  闲鱼 prices separately and not blend them with StubHub.

## 6. Milestones

| # | Deliverable | Depends on |
|---|---|---|
| M0 | Repo skeleton + this plan | done |
| M1 | `config/sections.toml` (draft done) + SQLite schema | stage orientation confirmed |
| M2 | StubHub collector, run manually, writes snapshots | runs on your machine |
| M3 | Static site: chart + cheapest table + health panel | M2 |
| M4 | 闲鱼 manual-entry path (CLI or small form) | M1 |
| M5 | Scheduled runs + price alert | M3 |

M1–M3 is the useful core. Everything after that is optional.

## 7. Open questions

Answered: Final only. 2 tickets together. Seat map provided. Floor excluded (no listings anyway). Sections split
into best / good / far.

1. **Stage orientation:** still unconfirmed (see §2). Decision: **favor 15/16/17 anyway**.
   It's a straight-on view under both a center stage (TM map) and a Modelo-end stage. It's
   only bad under StubHub's layout, which the TM map contradicts.
2. **Loge boxes (A18–A48) and Baseline Club** inside the circled area: currently excluded. Track them?
3. **Price ceiling / alert threshold**, if any (in SGD?).
4. **闲鱼:** is manual entry acceptable for v1?
5. **Where will this run?** Your laptop on a cron job is the realistic answer, given bot protection.
