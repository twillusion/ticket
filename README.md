# ticket

Personal tracker for resale ticket prices for the **2026 League of Legends World Championship
Final** (Barclays Center, Brooklyn, 14 Nov 2026). It only tracks seats **in front of the main
stage**: no side sections and nothing behind the stage.

Status: **planning.** Nothing collects data yet. See [PLAN.md](PLAN.md).

## Layout

```
config/     hand-maintained config (section → view classification, sources)
data/       local SQLite price history (gitignored)
src/ticket/ collectors, storage, site generator
```
