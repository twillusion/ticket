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

## Collect and check

```bash
python -m ticket stubhub collect     # one snapshot into data/prices.sqlite
python -m ticket status              # latest runs, cheapest pair per tier, flags
```

- `collect` exits non-zero on `blocked` / `empty` / `error`, so a scheduler can see failures.
- If StubHub shows a captcha, the run is recorded as **blocked** with a screenshot. Solve it
  once by hand in the Chrome window if you like (the profile in `data/browser-profile` keeps
  cookies), but the tool never tries to get past it itself.
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
