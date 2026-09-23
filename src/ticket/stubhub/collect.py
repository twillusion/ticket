"""One StubHub run: capture -> save raw -> parse -> store, with an honest run status."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ticket import db
from ticket.config import Sources
from ticket.stubhub.dom import extract_cards, load_cards
from ticket.stubhub.fetch import Blocked, capture_event, load_raw
from ticket.stubhub.parse import SOURCE, ExtractResult, extract


def extract_all(docs, cards, quantity: int, event_url: str) -> tuple[ExtractResult, str]:
    """JSON listings if the page delivered any; otherwise the rendered listing cards."""
    from_json = extract(docs, quantity, event_url)
    if from_json.listings:
        return from_json, "json"
    from_dom = extract_cards(cards, quantity, event_url)
    from_dom.candidates = from_json.candidates
    return from_dom, "dom"


@dataclass
class RunOutcome:
    run_id: str
    status: str
    message: str
    result: ExtractResult | None
    raw_dir: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _status_for(result: ExtractResult, method: str) -> tuple[str, str]:
    n, errs = len(result.listings), len(result.errors)
    if n == 0:
        return "empty", (f"0 listings parsed ({errs} errors). StubHub's page may have changed, or "
                         "listings didn't load. Run `probe`.")
    if errs:
        return "partial", f"{n} listings stored via {method}, {errs} parse issues"
    return "ok", f"{n} listings stored via {method}"


def run(sources: Sources, *, reparse_dir: Path | None = None, **capture_kwargs) -> RunOutcome:
    started = _now()
    run_id = f"{SOURCE}-{started.replace(':', '').replace('-', '')}"
    raw_dir = reparse_dir or (sources.raw_dir / SOURCE / run_id)
    conn = db.connect(sources.db_path)
    db.start_run(conn, run_id, SOURCE, started, str(raw_dir))
    try:
        if reparse_dir:
            docs, cards = load_raw(reparse_dir), load_cards(reparse_dir)
        else:
            cap = capture_event(sources.stubhub, raw_dir, **capture_kwargs)
            docs, cards = cap.docs, cap.cards
        result, method = extract_all(docs, cards, sources.stubhub.quantity, sources.stubhub.event_url)
        status, message = _status_for(result, method)
        db.insert_listings(conn, run_id, started, result.listings)
        db.finish_run(conn, run_id, _now(), status, len(result.listings), len(result.errors), message)
        return RunOutcome(run_id, status, message, result, raw_dir)
    except Blocked as e:
        msg = f"blocked: {e}. Screenshot in {raw_dir}"
        db.finish_run(conn, run_id, _now(), "blocked", 0, 0, msg)
        return RunOutcome(run_id, "blocked", msg, None, raw_dir)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        db.finish_run(conn, run_id, _now(), "error", 0, 0, msg)
        print(f"[stubhub] run {run_id} failed: {msg}", file=sys.stderr)
        raise
    finally:
        conn.close()
