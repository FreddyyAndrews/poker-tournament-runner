"""Verify a match log.

Two independent checks:

1. Replay. Rebuild the match from its seed and config with "replay seats" that
   answer every decision with the reply recorded in the log, run it through the
   same table and engine code, and require the regenerated event stream to
   match the log exactly: cards, posts, applied actions, what each bot was
   shown, awards, stacks, eliminations and final places. Anything edited in the
   log (a card, an amount, a winner) breaks the match.

2. Accounting. Straight from the log, without the engine: chips are conserved
   every hand, each hand starts with the stacks the previous one ended with,
   awards add up, and the final results agree with the last stacks.

Fields that depend on the live run rather than the cards and decisions (think
time, bot logs/debug, errors, warmup, process stats) are not compared.
"""

import json
from collections import defaultdict, deque
from pathlib import Path

from arena.events import DEFAULT_RUNS_DIR, find_run, has_events, read_events
from arena.seats import Seat
from arena.table import RUNTIME_STATS, MatchConfig, engine_hash, play_match

# Decision fields that come from the live bot process, not the game.
_LIVE_DECISION_FIELDS = ("think_ms", "logs", "logs_truncated", "debug", "debug_error",
                         "error", "error_detail")


class VerifyError(Exception):
    pass


class ReplaySeat(Seat):
    kind = "replay"

    def __init__(self, name, replies):
        super().__init__(name)
        self.replies = replies

    def request_action(self, state):
        if not self.replies:
            raise VerifyError(f"{self.name} was asked to act in hand {state.get('hand_num')} "
                              "but the log has no more decisions for it")
        return dict(self.replies.popleft())


class MemoryLog:
    def __init__(self):
        self.events = []

    def emit(self, event_type, **data):
        event = {"type": event_type, **data}
        self.events.append(event)
        return event


def _normalise(event: dict) -> dict:
    e = dict(event)
    t = e["type"]
    if t == "decision":
        for k in _LIVE_DECISION_FIELDS:
            e.pop(k, None)
    elif t == "match_start":
        e = {"type": t, "match_id": e["match_id"], "seed": e["seed"], "config": e["config"],
             "seats": [s["name"] for s in e["seats"]], "tables": e.get("tables")}
    elif t == "match_end":
        e.pop("duration_s", None)
        e["results"] = [{k: v for k, v in r.items() if k not in RUNTIME_STATS}
                        for r in e["results"]]
    # JSON round-trip differences (tuples vs lists) don't matter.
    return _jsonish(e)


def _jsonish(x):
    if isinstance(x, (list, tuple)):
        return [_jsonish(v) for v in x]
    if isinstance(x, dict):
        return {str(k): _jsonish(v) for k, v in x.items()}
    return x


def _first_difference(a, b, path="") -> str:
    if type(a) != type(b):
        return f"{path or 'value'}: log has {a!r}, replay has {b!r}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: present in {'replay' if k not in a else 'log'} only"
            d = _first_difference(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return ""
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: log has {len(a)} items, replay has {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return ""
    return "" if a == b else f"{path}: log has {a!r}, replay has {b!r}"


def _replay(events) -> list:
    start = events[0]
    if start["type"] != "match_start":
        raise VerifyError("log does not begin with match_start")
    config = MatchConfig.from_dict(start["config"])
    replies = defaultdict(deque)
    for e in events:
        if e["type"] == "decision":
            replies[e["bot"]].append(e["reply"])
    seats = [ReplaySeat(s["name"], replies[s["name"]]) for s in start["seats"]]
    log = MemoryLog()
    if start.get("format") == "mtt":
        from arena.formats.mtt import play_mtt
        play_mtt(seats, config, start["table_size"], start["match_id"], log)
    else:
        play_match(seats, config, start["match_id"], log)
    leftover = {name: len(q) for name, q in replies.items() if q}
    if leftover:
        raise VerifyError(f"log has decisions the replay never asked for: {leftover}")
    return log.events


def _check_accounting(events) -> list:
    problems = []
    start = events[0]
    cfg = start["config"]
    cash = cfg["mode"] == "cash"
    multi_table = start.get("format") == "mtt"
    stacks = {s["name"]: cfg["starting_stack"] for s in start["seats"]}
    bankroll = dict(stacks)                     # cash mode: running total
    total = sum(stacks.values())
    awards = 0
    for e in events:
        t = e["type"]
        where = f"hand {e.get('hand')}" + (f" table {e['table']}" if "table" in e else "")
        if t == "hand_start":
            awards = 0
            for name, s in e["stacks"].items():
                expected = cfg["starting_stack"] if cash else stacks.get(name)
                if s != expected:
                    problems.append(f"{where}: {name} starts with {s}, expected {expected}")
            if not multi_table:
                dealt_out = [n for n, s in stacks.items() if s > 0 and n not in e["stacks"]]
                if dealt_out:
                    problems.append(f"{where}: players with chips not dealt in: {dealt_out}")
        elif t == "award":
            awards += e["amount"]
        elif t == "hand_end":
            if sum(e["deltas"].values()) != 0:
                problems.append(f"{where}: deltas sum to {sum(e['deltas'].values())}, not 0")
            if awards != e["pot"]:
                problems.append(f"{where}: awards total {awards} but pot was {e['pot']}")
            if cash:
                for name, d in e["deltas"].items():
                    bankroll[name] += d
            else:
                stacks.update(e["stacks"])
                if sum(stacks.values()) != total:
                    problems.append(f"{where}: {sum(stacks.values())} chips in play, expected {total}")
        elif t == "seat_change" and e["reason"] in ("join", "leave", "table_break", "balance", "bust"):
            if e["stack"] != stacks.get(e["bot"]):
                problems.append(f"{where}: {e['bot']} moved with {e['stack']} chips, "
                                f"had {stacks.get(e['bot'])}")
        elif t == "match_end":
            final = bankroll if cash else stacks
            for r in e["results"]:
                if r["final_stack"] != final.get(r["bot"]):
                    problems.append(f"result for {r['bot']}: final stack {r['final_stack']} "
                                    f"but the log adds up to {final.get(r['bot'])}")
            places = [r["place"] for r in e["results"]]
            if multi_table or cfg["mode"] == "freezeout":
                if sorted(p for p in places if p) != list(range(1, len(places) + 1)) and \
                        e.get("stopped") == "one_player_left":
                    problems.append(f"places are not 1..{len(places)}: {sorted(places)}")
    if events[-1]["type"] != "match_end":
        problems.append("log has no match_end (match incomplete or log truncated)")
    return problems


def verify_events(events) -> dict:
    report = {"ok": False, "events": len(events), "hands": 0, "decisions": 0,
              "problems": [], "warnings": []}
    if not events:
        report["problems"].append("log is empty")
        return report
    report["hands"] = sum(1 for e in events if e["type"] == "hand_start")
    report["decisions"] = sum(1 for e in events if e["type"] == "decision")
    if events[0].get("dealer"):
        # an external bot (GTO Wizard, Slumbot) dealt these hands on its server,
        # so there is no seed to replay
        dealer = next((s.get("title") for s in events[0]["seats"] if s["name"] == events[0]["dealer"]),
                      None) or events[0]["dealer"]
        report["warnings"].append(f"dealt by {dealer}: the cards can't be replayed, "
                                  "only the chip accounting is checked")
        report["problems"].extend(_check_accounting(events))
        report["ok"] = not report["problems"]
        return report
    if events[0].get("engine_hash") != engine_hash():
        report["warnings"].append("engine/game.py has changed since this match was played; "
                                  "a replay mismatch may come from that change")

    report["problems"].extend(_check_accounting(events))

    try:
        replayed = _replay(events)
    except VerifyError as e:
        report["problems"].append("replay: " + str(e))
    except Exception as e:   # engine refused the logged actions/cards
        report["problems"].append(f"replay failed: {e!r}")
    else:
        logged = [_normalise(e) for e in events]
        fresh = [_normalise(e) for e in replayed]
        for i, (a, b) in enumerate(zip(logged, fresh)):
            if a != b:
                where = f"event {i + 1} ({a['type']}" + (f", hand {a['hand']}" if "hand" in a else "") + ")"
                report["problems"].append(f"replay mismatch at {where}: "
                                          + _first_difference(a, b))
                break
        else:
            if len(logged) != len(fresh):
                report["problems"].append(f"replay produced {len(fresh)} events, log has {len(logged)}")

    report["ok"] = not report["problems"]
    return report


def verify_match_dir(run_dir) -> dict:
    report = verify_events(read_events(run_dir))
    report["run_dir"] = str(run_dir)
    return report


def _record_from_log(record: dict, events: list) -> dict:
    """The run.json match record, with results taken from the match log."""
    end = events[-1] if events and events[-1]["type"] == "match_end" else None
    if end is None:
        raise VerifyError(f"match {record['match_id']}: log has no match_end")
    rec = dict(record)
    rec["results"] = [{k: r.get(k) for k in record["results"][0]} for r in end["results"]]
    rec["hands"], rec["stopped"] = end["hands"], end["stopped"]
    if "players" in rec and sorted(rec["players"]) != sorted(r["bot"] for r in end["results"]):
        raise VerifyError(f"match {record['match_id']}: run.json says {rec['players']} played, "
                          f"the log says {[r['bot'] for r in end['results']]}")
    return rec


def verify_run_dir(run_dir) -> dict:
    """Verify every match log in a tournament run, then recompute the
    standings from the logs and compare them with run.json."""
    from arena import formats
    run_dir = Path(run_dir)
    run = json.loads((run_dir / formats.RUN_FILE).read_text())
    report = {"ok": False, "kind": "run", "run_dir": str(run_dir), "format": run.get("format"),
              "matches": [], "problems": [], "warnings": []}
    if run.get("status") != "complete":
        report["problems"].append(f"run status is {run.get('status')!r}, not complete")
        return report
    records, logged_ids = [], set()
    for record in run["matches"]:
        mdir = run_dir / "matches" / record["match_id"]
        logged_ids.add(record["match_id"])
        if not has_events(mdir):
            report["problems"].append(f"match {record['match_id']}: log missing")
            continue
        events = read_events(mdir)
        m = verify_events(events)
        report["matches"].append({"match_id": record["match_id"], "ok": m["ok"],
                                  "hands": m["hands"], "problems": m["problems"]})
        report["problems"].extend(f"match {record['match_id']}: {p}" for p in m["problems"])
        report["warnings"].extend(w for w in m["warnings"] if w not in report["warnings"])
        try:
            records.append(_record_from_log(record, events))
        except VerifyError as e:
            report["problems"].append(str(e))
    extra = sorted(d.name for d in (run_dir / "matches").iterdir()
                   if d.is_dir() and d.name not in logged_ids) if (run_dir / "matches").is_dir() else []
    if extra:
        report["problems"].append(f"match logs not listed in run.json: {extra}")
    if not report["problems"]:
        try:
            cfg = formats.RunConfig(**run["config"])
            recomputed = _jsonish(formats.standings(cfg, records))
            d = _first_difference(_jsonish(run["standings"]), recomputed, "standings")
            if d:
                report["problems"].append("standings don't match the match logs: " + d)
        except Exception as e:
            report["problems"].append(f"could not recompute standings: {e!r}")
    report["ok"] = not report["problems"]
    return report


def verify_series_dir(series_dir) -> dict:
    from arena.series import SERIES_FILE, aggregate
    series_dir = Path(series_dir)
    series = json.loads((series_dir / SERIES_FILE).read_text())
    report = {"ok": False, "kind": "series", "run_dir": str(series_dir), "runs": [],
              "problems": [], "warnings": []}
    runs = []
    for rid in series["run_ids"]:
        r = verify_run_dir(series_dir / rid)
        report["runs"].append({"run_id": rid, "ok": r["ok"]})
        report["problems"].extend(f"{rid}: {p}" for p in r["problems"])
        report["warnings"].extend(w for w in r["warnings"] if w not in report["warnings"])
        runs.append(json.loads((series_dir / rid / "run.json").read_text()))
    if not report["problems"]:
        d = _first_difference(_jsonish(series["aggregate"]), _jsonish(aggregate(runs)), "aggregate")
        if d:
            report["problems"].append("series totals don't match the runs: " + d)
    report["ok"] = not report["problems"]
    return report


def verify_run(ref) -> dict:
    """Verify a match, a tournament run, or a series, by directory or id."""
    from arena import formats
    from arena.series import SERIES_FILE
    p = Path(ref).expanduser()
    if not p.exists():
        p = DEFAULT_RUNS_DIR / ref
    if (p / SERIES_FILE).is_file():
        return verify_series_dir(p)
    if (p / formats.RUN_FILE).is_file():
        return verify_run_dir(p)
    return verify_match_dir(find_run(ref))
