"""Match event logs: one JSON object per line, appended as the match is played
(so a running match can be followed live), plus a summary.json at the end.

Event types (all carry "type"; per-player data is keyed by bot name):
  match_start  match_id, seed, config, seats[], engine_hash, arena_version
  level        level, sb, bb, ante, hand            (whenever blinds change)
  hand_start   hand, hand_seed, level, blinds, button, sb, bb,
               order[] (bot names by engine seat), seats{name: table seat},
               stacks{name}, hole{name: [c, c]}
  post         hand, bot, seat, kind (ante|sb|bb), amount
  decision     hand, bot, seat, street, state (what the bot saw),
               reply{action, amount}  (raw, as the bot returned it),
               applied{action, amount, pot_after, stack_after}  (after rules),
               think_ms, logs, logs_truncated, debug, debug_error, error, error_detail
  deal         hand, street, board[]
  showdown     hand, board[], reveal{name: [c, c]}, ranks{name}
  award        hand, bot, amount, pot (main|side)
  hand_end     hand, board[], pot, stacks{name}, deltas{name}
  elimination  hand, bot, place
  match_end    hands, duration_s, results[], stopped (reason)
"""

import json
import os
from pathlib import Path

DEFAULT_RUNS_DIR = Path(os.environ.get("POKER_RUNS_DIR",
                                       Path(__file__).resolve().parent.parent / "data" / "runs"))

EVENTS_FILE = "events.jsonl"
SUMMARY_FILE = "summary.json"


class EventLog:
    """Writes events for one match. Use as a context manager."""

    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / EVENTS_FILE
        self._fh = open(self.path, "w", encoding="utf-8")
        self.count = 0
        self.listeners = []   # callables(event) for live viewers

    def emit(self, event_type: str, **data):
        event = {"type": event_type, **data}
        self._fh.write(json.dumps(event, default=str) + "\n")
        self._fh.flush()
        self.count += 1
        for fn in self.listeners:
            fn(event)
        return event

    def write_summary(self, summary: dict):
        tmp = self.dir / (SUMMARY_FILE + ".tmp")
        tmp.write_text(json.dumps(summary, indent=2, default=str))
        tmp.replace(self.dir / SUMMARY_FILE)

    def close(self):
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def find_run(ref: str, runs_dir: Path = DEFAULT_RUNS_DIR) -> Path:
    """A run directory from a path, an events.jsonl path, or a match id."""
    p = Path(ref).expanduser()
    if p.is_file() and p.name == EVENTS_FILE:
        return p.parent
    if p.is_dir() and (p / EVENTS_FILE).is_file():
        return p
    candidate = Path(runs_dir) / ref
    if (candidate / EVENTS_FILE).is_file():
        return candidate
    raise FileNotFoundError(f"No match log found for {ref!r} (looked for a run directory "
                            f"or {candidate})")


def read_events(run_dir: Path) -> list:
    with open(Path(run_dir) / EVENTS_FILE, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_summary(run_dir: Path):
    p = Path(run_dir) / SUMMARY_FILE
    return json.loads(p.read_text()) if p.is_file() else None
