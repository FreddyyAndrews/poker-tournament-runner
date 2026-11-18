"""Broadcast: show a finished match or run to an audience as if it were live.

  POST /api/broadcast {"source": "<run or match id>", "name": "Final", "pace_ms": 900}

The tournament's final is played first, at full speed and out of sight, so
nothing can go wrong in front of the room. Then it is *broadcast*: every
match is replayed from its log, with each bot's recorded decisions fed back
through the same table code (as `poker verify` does), at a watching pace,
as a new run that is live while it plays. The replay produces exactly the
same hands as the original (it is checked against it as it goes).

To everyone watching it is a live tournament: matches show as Live with
"Hand 12 of 12+", the bracket fills in one result at a time, and standings
appear only when the last match ends. The browser is only ever sent what has
already happened, so nothing on screen can give the result away.

Keep the original run off the projector: its page and the Recent list show
how it ended.
"""

import json
import time
from collections import defaultdict, deque
from pathlib import Path

import arena
from arena.events import EventLog, read_events
from arena.pacing import Pacer
from arena.table import MatchConfig, play_match
from arena.verify import ReplaySeat, _normalise

RUN_FILE = "run.json"


class _BroadcastSeat(ReplaySeat):
    """A replay seat that presents itself exactly as the original seat did."""

    def __init__(self, info: dict, replies):
        super().__init__(info["name"], replies)
        self._info = {k: v for k, v in info.items() if k not in ("seat", "warmup")}
        self.kind = info.get("kind", "bot")

    def info(self) -> dict:
        return dict(self._info)

    def warmup(self) -> dict:
        return {"ok": True}


class _CheckedLog(EventLog):
    """Writes the replayed events, checking each against the original."""

    def __init__(self, run_dir: Path, original: list):
        super().__init__(run_dir)
        self.original = original
        self.n = 0

    def emit(self, event_type, **data):
        event = super().emit(event_type, **data)
        if event_type not in ("match_start", "match_end") and self.n < len(self.original):
            want = _normalise({k: v for k, v in self.original[self.n].items() if k not in ("i", "t")})
            got = _normalise({k: v for k, v in event.items() if k not in ("i", "t")})
            if want != got:
                raise RuntimeError(f"broadcast diverged from the original at event {self.n + 1} "
                                   f"({event_type}); stopped")
        self.n += 1
        return event


def _matches_in_order(source_dir: Path, data: dict) -> list:
    if data.get("kind") == "match":
        return [(source_dir, data["match_id"])]
    return [(source_dir / "matches" / m["match_id"], m["match_id"]) for m in data.get("matches", [])]


def broadcast(source: str, dest_id: str, runs_dir: Path, name: str = None, pace_ms: int = 900,
              cancel=None, progress=()) -> dict:
    src = next((d for d in (Path(source).expanduser(), Path(runs_dir) / source)
                if d.is_dir() and ((d / RUN_FILE).is_file() or (d / "summary.json").is_file())), None)
    if src is None:
        raise ValueError(f"{source}: no finished match or run by that name")
    if (src / RUN_FILE).is_file():
        data = json.loads((src / RUN_FILE).read_text())
    elif (src / "summary.json").is_file():
        data = json.loads((src / "summary.json").read_text())
    else:
        raise ValueError(f"{source}: no finished match or run there")
    if data.get("status") not in (None, "complete") or (data.get("kind") == "match" and not data.get("results")):
        raise ValueError(f"{source} hasn't finished; only finished matches and runs can be broadcast")
    if data.get("format") == "mtt":
        raise ValueError("multi-table runs can't be broadcast")

    dest = Path(runs_dir) / dest_id
    single = data.get("kind") == "match"
    base = {"kind": "run", "run_id": dest_id, "format": data.get("format", "table"), "name": name,
            "seed": data.get("seed"), "config": data.get("config"), "bots": data.get("bots"),
            "run_dir": str(dest), "broadcast": True}
    if single:
        base["config"] = {"format": "table", "bots": [s["name"] for s in data.get("seats", [])]}
    dest.mkdir(parents=True, exist_ok=False)
    (dest / RUN_FILE).write_text(json.dumps({**base, "status": "running"}, indent=2))

    for match_dir, mid in _matches_in_order(src, data):
        events = read_events(match_dir)
        start = events[0]
        replies = defaultdict(deque)
        for e in events:
            if e["type"] == "decision":
                replies[e["bot"]].append(e["reply"])
        seats = [_BroadcastSeat(s, replies[s["name"]]) for s in start["seats"]]
        config = MatchConfig.from_dict(start["config"])
        for fn in progress:
            fn({"event": "match_start", "match_id": mid})
        out_dir = dest / "matches" / mid
        with _CheckedLog(out_dir, events) as log:
            play_match(seats, config, mid, log, pacer=Pacer(pace_ms, cancel))
            summary = json.loads((match_dir / "summary.json").read_text())
            summary.update(run_dir=str(out_dir), broadcast_of=str(match_dir.relative_to(runs_dir)))
            log.write_summary(summary)
        for fn in progress:
            fn({"event": "match_done", "match_id": mid})
        time.sleep(min(5.0, pace_ms * 6 / 1000))         # a pause between matches

    final = {**base, "status": "complete", "standings": data.get("standings"),
             "matches": data.get("matches"), "seeding": data.get("seeding"),
             "arena_version": arena.__version__}
    if single:
        final["standings"] = [{"bot": r["bot"], "place": r["place"], "delta": r["delta"],
                               "hands_played": r["hands_played"], "bb_per_100": r["bb_per_100"],
                               "timeouts": r.get("timeouts"), "errors": r.get("errors")}
                              for r in data["results"]]
        final["matches"] = [{"match_id": data["match_id"]}]
    (dest / RUN_FILE).write_text(json.dumps(final, indent=2, default=str))
    return final
