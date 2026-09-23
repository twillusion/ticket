"""SQLite storage: one row per run, one row per listing seen in a run (append-only)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,      -- ok | partial | empty | blocked | error
    listings_found INTEGER NOT NULL DEFAULT 0,
    parse_errors   INTEGER NOT NULL DEFAULT 0,
    message        TEXT,
    raw_dir        TEXT
);

CREATE TABLE IF NOT EXISTS listings_snapshot (
    run_id              TEXT NOT NULL REFERENCES runs(run_id),
    source              TEXT NOT NULL,
    listing_id          TEXT NOT NULL,
    seen_at             TEXT NOT NULL,
    section_raw         TEXT,
    section             TEXT,
    row                 TEXT,
    quantity            INTEGER,
    allowed_splits      TEXT,           -- JSON list of ints, NULL if the source didn't say
    requested_quantity  INTEGER,        -- quantity filter the page was loaded with
    price_per_ticket    REAL,
    currency            TEXT,
    price_field         TEXT,           -- which source field the price came from
    price_includes_fees INTEGER,        -- 1 / 0 / NULL = unknown
    price_candidates    TEXT,           -- JSON of every price-like field seen
    view_notes          TEXT,           -- JSON list of strings
    url                 TEXT,
    raw_json            TEXT NOT NULL,
    PRIMARY KEY (run_id, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_seen ON listings_snapshot(source, seen_at);
"""


@dataclass
class Listing:
    source: str
    listing_id: str
    section_raw: str | None
    section: str | None
    row: str | None
    quantity: int | None
    allowed_splits: list[int] | None
    requested_quantity: int | None
    price_per_ticket: float | None
    currency: str | None
    price_field: str | None
    price_includes_fees: bool | None
    price_candidates: dict = field(default_factory=dict)
    view_notes: list[str] = field(default_factory=list)
    url: str | None = None
    raw: dict = field(default_factory=dict)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def start_run(conn: sqlite3.Connection, run_id: str, source: str, started_at: str, raw_dir: str) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, source, started_at, status, raw_dir) VALUES (?, ?, ?, 'error', ?)",
        (run_id, source, started_at, raw_dir),
    )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    finished_at: str,
    status: str,
    listings_found: int,
    parse_errors: int,
    message: str | None,
) -> None:
    conn.execute(
        """UPDATE runs SET finished_at = ?, status = ?, listings_found = ?, parse_errors = ?, message = ?
           WHERE run_id = ?""",
        (finished_at, status, listings_found, parse_errors, message, run_id),
    )
    conn.commit()


def insert_listings(conn: sqlite3.Connection, run_id: str, seen_at: str, listings: list[Listing]) -> None:
    def fees(v: bool | None) -> int | None:
        return None if v is None else int(v)

    conn.executemany(
        """INSERT INTO listings_snapshot (
               run_id, source, listing_id, seen_at, section_raw, section, row, quantity,
               allowed_splits, requested_quantity, price_per_ticket, currency, price_field,
               price_includes_fees, price_candidates, view_notes, url, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                run_id, l.source, l.listing_id, seen_at, l.section_raw, l.section, l.row, l.quantity,
                None if l.allowed_splits is None else json.dumps(l.allowed_splits),
                l.requested_quantity, l.price_per_ticket, l.currency, l.price_field,
                fees(l.price_includes_fees), json.dumps(l.price_candidates, ensure_ascii=False),
                json.dumps(l.view_notes, ensure_ascii=False), l.url,
                json.dumps(l.raw, ensure_ascii=False),
            )
            for l in listings
        ],
    )
    conn.commit()
