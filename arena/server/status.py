"""The one rule for the status of matches, runs and series.

Liveness comes only from the server's jobs: something is "running" only while
this server is running it right now. Files on disk never say "live"; they only
say how something ended:

  match   summary.json (or a log ending in match_end)  -> complete / cancelled / error
  run     run.json / series.json with a final status   -> complete / cancelled / error
  either  no final record and no active job            -> incomplete (didn't finish)
  match   inside an active run, not started yet        -> pending

So a server restart, a crash, or a match played by the CLI in its own process
can never leave a badge stuck on Live.
"""

import json
from pathlib import Path

from arena.events import SUMMARY_FILE, has_events, last_event

RUN_FILE = "run.json"
SERIES_FILE = "series.json"
FINAL = ("complete", "cancelled", "error")
LIVE = "running"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def ended_status(stopped) -> str:
    """complete / cancelled / error from a match's `stopped` reason."""
    if stopped == "cancelled":
        return "cancelled"
    if isinstance(stopped, str) and stopped.startswith("error"):
        return "error"
    return "complete"


def match_status(match_dir: Path, active: bool) -> str:
    """Status of one match directory. `active`: is the job that owns it
    (the match itself, or the run it belongs to) running on this server?"""
    match_dir = Path(match_dir)
    summary = _read_json(match_dir / SUMMARY_FILE)
    if summary is not None:
        return ended_status(summary.get("stopped"))
    last = last_event(match_dir)
    if last and last.get("type") == "match_end":
        # logs without a summary (multi-table table logs) end with match_end
        return ended_status(last.get("stopped"))
    if active:
        return LIVE if has_events(match_dir) else "pending"
    return "incomplete"


def run_status(run_dir: Path, active: bool) -> str:
    """Status of a tournament run or series directory. `active`: is the job
    that owns it (the run, or the series it belongs to) running right now?
    A final record wins (a finished run inside a running series is complete)."""
    for name in (SERIES_FILE, RUN_FILE):
        data = _read_json(Path(run_dir) / name)
        if data is not None and data.get("status") in FINAL:
            return data["status"]
    return LIVE if active else "incomplete"


def is_live(status: str) -> bool:
    return status == LIVE
