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

from collections import defaultdict, deque

from arena.events import find_run, read_events
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
             "seats": [s["name"] for s in e["seats"]]}
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
    play_match(seats, config, start["match_id"], log)
    leftover = {name: len(q) for name, q in replies.items() if q}
    if leftover:
        raise VerifyError(f"log has decisions the replay never asked for: {leftover}")
    return log.events


def _check_accounting(events) -> list:
    problems = []
    start = events[0]
    stacks = {s["name"]: start["config"]["starting_stack"] for s in start["seats"]}
    total = sum(stacks.values())
    hand = None
    awards = 0
    for e in events:
        t = e["type"]
        if t == "hand_start":
            hand, awards = e["hand"], 0
            for name, s in e["stacks"].items():
                if stacks.get(name) != s:
                    problems.append(f"hand {hand}: {name} starts with {s}, "
                                    f"previous hand ended with {stacks.get(name)}")
            dealt_out = [n for n, s in stacks.items() if s > 0 and n not in e["stacks"]]
            if dealt_out:
                problems.append(f"hand {hand}: players with chips not dealt in: {dealt_out}")
        elif t == "award":
            awards += e["amount"]
        elif t == "hand_end":
            if sum(e["deltas"].values()) != 0:
                problems.append(f"hand {hand}: deltas sum to {sum(e['deltas'].values())}, not 0")
            if awards != e["pot"]:
                problems.append(f"hand {hand}: awards total {awards} but pot was {e['pot']}")
            stacks.update(e["stacks"])
            if sum(stacks.values()) != total:
                problems.append(f"hand {hand}: {sum(stacks.values())} chips in play, expected {total}")
        elif t == "match_end":
            for r in e["results"]:
                if r["final_stack"] != stacks.get(r["bot"]):
                    problems.append(f"result for {r['bot']}: final stack {r['final_stack']} "
                                    f"but the last hand ended with {stacks.get(r['bot'])}")
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


def verify_run(ref) -> dict:
    run_dir = find_run(ref)
    report = verify_events(read_events(run_dir))
    report["run_dir"] = str(run_dir)
    return report
