# ticket

Personal tracker for resale ticket prices for the **2026 League of Legends World Championship
Final** (Barclays Center, Brooklyn, 14 Nov 2026). It tracks 2 seats together, grouped into
best / good / far section tiers (see [`config/sections.toml`](config/sections.toml)).

Status: **StubHub collector built, not yet run against the real site.** No website yet. See
[PLAN.md](PLAN.md).

## Setup (your computer)

Needs Python 3.11+, Git and Google Chrome. Every command below runs **inside the `ticket`
folder**, with the virtual environment active (your prompt starts with `(.venv)`).

```bash
git clone -b claude/lucid-faraday-dcwuz3 https://github.com/twillusion/ticket.git
cd ticket
python -m venv .venv
# Windows, Command Prompt:
.venv\Scripts\activate.bat
# Windows, PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -e ".[dev]"
python -m playwright install chromium   # only needed if you set browser_channel = "" in sources.toml
```

## First run: probe

StubHub's internal data format isn't documented, so the parser is built from guesses at field
names. Run a probe first. It opens a Chrome window, loads the event page with quantity=2,
saves everything it captured under `data/raw/stubhub/probe-*`, and prints what it found.
Nothing is stored in the database.

```bash
python -m ticket stubhub probe
```

Check the printed sample listings against the StubHub page: price, section, row, and whether
the price includes fees. If they don't match, or it says NOTHING FOUND, send me the output
(and `page.png`). Look through it first; it shouldn't contain anything personal since no login
is used.

## Views (recommended)

StubHub shows only ~10 listings per page load, and blocked the "show more" request in testing.
So instead of paging, the tool loads a few **filtered views**: one per tier, with those sections
selected. StubHub puts the filter in the link (`?sections=276039&ticketClasses=1687`), so the
tool can build these links itself:

```bash
python -m ticket stubhub discover
```

It loads the event once to collect section ids (from listing cards and the seat-map file),
tests with a few page loads whether one link can hold several sections and whether a sort
parameter works, then writes `config/stubhub_views.toml`. Sections whose id isn't known yet
are listed and left out.

Manual fallback, if discover can't work it out:

```bash
python -m ticket stubhub setup-views            # all three tiers
python -m ticket stubhub setup-views --tier best
```

For each tier it opens the event, tells you which sections to click and to sort by lowest
price, and waits for Enter. It then checks the result (URL changed, only that tier's sections
listed, lowest price first) and saves passing tiers to `config/stubhub_views.toml`. Probe a
single saved view with `python -m ticket stubhub probe --view best`.

## Collect and check

```bash
python -m ticket stubhub collect     # one snapshot into data/prices.sqlite
python -m ticket status              # latest runs, cheapest pair per tier, flags
```

- `collect` exits non-zero on `blocked` / `empty` / `error`, so a scheduler can see failures.
- If StubHub shows a bot check, the run is recorded as **blocked** with a screenshot. To get
  past it by hand, run `python -m ticket stubhub probe --wait-for-me` (also works on `collect`).
  The window stays open, you solve the check, press Enter, and the run continues. The cookie is
  kept in `data/browser-profile`, so later runs may pass on their own for a while. The tool
  itself never touches the check.
- Parser fixes don't need a new scrape: `python -m ticket stubhub collect --reparse data/raw/stubhub/<run>`.

## Tests

```bash
pytest
```

The tests use synthetic JSON and a local fake page. They check the parser logic and browser
capture, **not** StubHub's real format.

## Layout

```
config/sources.toml    event URL, quantity, browser settings
config/sections.toml   section -> best/good/far/excluded
src/ticket/stubhub/    fetch (browser capture), parse (JSON -> listings), collect (one run)
src/ticket/db.py       SQLite schema: runs + listings_snapshot
data/                  local database, raw captures, browser profile (gitignored)
docs/                  seat map screenshots the tiers were built from
```
