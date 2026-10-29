"""Live, format-specific views of a run, built from what is on disk right now:
the run's config, finished match summaries, and (for multi-table) each
table's live log. Works while a run is still playing and after it ends.

  bracket      rounds of ties: players, seeds, status, winner, chips
  round_robin  every pairing: legs, status, chips, result; points so far
  mtt          every table: players and stacks, hand, blinds, button; busts
  series       aggregate over the runs finished so far
"""

import json
import threading
from itertools import combinations
from pathlib import Path

from arena import bots as botlib
from arena import formats
from arena.events import EVENTS_FILE, SUMMARY_FILE
from arena.formats import bracket, round_robin
from arena.server import status as status_rule
from arena.seeds import derive_seed


def _summary(match_dir: Path):
    f = match_dir / SUMMARY_FILE
    return json.loads(f.read_text()) if f.is_file() else None


def _status(match_dir: Path, active: bool) -> str:
    return status_rule.match_status(match_dir, active)


def _record(summary, **extra):
    return formats.match_record(summary, **extra) if summary and summary.get("results") else None


# Live chips of running matches, read from their event logs. Pages poll this
# every few seconds and logs grow to megabytes, so each log is read
# incrementally: only the lines added since the last read.
_live_cache = {}                    # events path -> state
_live_lock = threading.Lock()


def live_chips(match_dir: Path):
    """Chips after the last finished hand of a match being played:
    {"hand", "hands", "stacks", "delta"}, or None if it hasn't started.
    stacks: chips in front of each player (in cash mode, starting stack +
    net won); delta: chips won so far."""
    f = Path(match_dir) / EVENTS_FILE
    if not f.is_file():
        return None
    with _live_lock:
        st = _live_cache.get(f)
        size = f.stat().st_size
        if st is None or size < st["offset"]:
            st = {"offset": 0, "start": None, "cash": False, "hands": None, "done": 0,
                  "stacks": {}, "delta": {}}
            _live_cache[f] = st
        if size > st["offset"]:
            with open(f, "rb") as fh:
                fh.seek(st["offset"])
                chunk = fh.read(size - st["offset"])
            end = chunk.rfind(b"\n") + 1            # only whole lines
            st["offset"] += end
            for line in chunk[:end].splitlines():
                if st["start"] is None and b'"match_start"' in line:
                    e = json.loads(line)
                    cfg = e["config"]
                    st.update(start=cfg["starting_stack"], cash=cfg["mode"] == "cash",
                              hands=cfg["hands"] if cfg["mode"] != "freezeout" else None)
                    st["stacks"] = {x["name"]: cfg["starting_stack"] for x in e["seats"]}
                    st["delta"] = {x["name"]: 0 for x in e["seats"]}
                elif b'"hand_end"' in line and st["start"] is not None:
                    e = json.loads(line)
                    st["done"] += 1
                    for n, d in e["deltas"].items():
                        st["delta"][n] = st["delta"].get(n, 0) + d
                    if st["cash"]:
                        st["stacks"] = {n: st["start"] + d for n, d in st["delta"].items()}
                    else:
                        st["stacks"].update(e["stacks"])
                        st["delta"] = {n: s - st["start"] for n, s in st["stacks"].items()}
        if st["start"] is None:
            return None
        return {"hand": st["done"], "hands": st["hands"], "stacks": dict(st["stacks"]),
                "delta": dict(st["delta"])}


def run_progress(run_dir: Path, run: dict, run_id: str, active: bool = False):
    """`active`: is this run's job running on the server right now? Only then
    can any of its matches or tables be live (see status.py)."""
    fmt = run.get("format")
    try:
        cfg = formats.RunConfig(**run["config"])
    except Exception:
        return None
    if fmt == "bracket":
        return bracket_progress(run_dir, cfg, run_id, active)
    if fmt == "round_robin":
        return round_robin_progress(run_dir, cfg, run_id, active)
    if fmt == "mtt":
        return mtt_progress(run_dir, run_id, active)
    return None


# ---------------------------------------------------------------------------

def bracket_progress(run_dir: Path, cfg, run_id: str, active: bool = False) -> dict:
    seeds = {name: i + 1 for i, name in enumerate(bracket.seeded_entrants(cfg))}
    # entries are (name or None, decided)
    current = [(n, True) for n in bracket.first_round_slots(cfg)]
    rounds, rnd = [], 1
    while len(current) > 1:
        ties, nxt = [], []
        for i in range(0, len(current), 2):
            (a, da), (b, db) = current[i], current[i + 1]
            mid = bracket.match_id(rnd, i // 2)
            tie = {"match_id": mid, "key": f"{run_id}/matches/{mid}", "round": rnd,
                   "players": [a, b], "seeds": [seeds.get(a), seeds.get(b)],
                   "status": "pending", "winner": None, "stacks": None, "hands": None, "bye": False}
            if da and db and (a is None or b is None):          # a bye (or an empty slot)
                tie.update(bye=True, status="bye", winner=a or b)
                nxt.append((a or b, True))
            elif a and b:
                mdir = run_dir / "matches" / mid
                summary = _summary(mdir)
                tie["status"] = _status(mdir, active)
                rec = _record(summary, players=[a, b])
                if tie["status"] == "running":
                    live = live_chips(mdir)
                    if live:
                        tie.update(stacks=live["stacks"], hands=live["hand"], live=live)
                if rec:
                    w, _, how = formats.two_player_winner(rec, derive_seed(cfg.seed, "tiebreak", mid))
                    tie.update(winner=w, how=how, hands=rec["hands"],
                               stacks={r["bot"]: r["final_stack"] for r in rec["results"]})
                    nxt.append((w, True))
                else:
                    nxt.append((None, False))
            else:
                nxt.append((None, False))
            ties.append(tie)
        rounds.append({"round": rnd, "ties": ties})
        current, rnd = nxt, rnd + 1
    champion = current[0][0] if current and current[0][1] else None
    names = ["Final" if i == len(rounds) - 1 else "Semi-finals" if i == len(rounds) - 2
             else "Quarter-finals" if i == len(rounds) - 3 else f"Round {i + 1}"
             for i in range(len(rounds))]
    for r, name in zip(rounds, names):
        r["name"] = name
    live = {t["key"]: {**t["live"], "players": t["players"]}
            for r in rounds for t in r["ties"] if t.get("live")}
    return {"kind": "bracket", "rounds": rounds, "champion": champion, "live": live}


# ---------------------------------------------------------------------------

def round_robin_progress(run_dir: Path, cfg, run_id: str, active: bool = False) -> dict:
    names = [s.name for s in botlib.resolve_all(cfg.bots, names=cfg.names)]
    pairings, records = [], []
    for a, b in combinations(names, 2):
        pid = round_robin.pairing_id(a, b)
        legs = []
        for leg in ((1, 2) if cfg.duplicate else (1,)):
            mid = f"{pid}_leg{leg}" if cfg.duplicate else pid
            mdir = run_dir / "matches" / mid
            summary = _summary(mdir)
            rec = _record(summary, pairing=pid, leg=leg, players=[a, b])
            if rec:
                records.append(rec)
            status = _status(mdir, active)
            live = live_chips(mdir) if status == "running" else None
            legs.append({"match_id": mid, "key": f"{run_id}/matches/{mid}", "leg": leg,
                         "status": status, "live": live,
                         "delta": {r["bot"]: r["delta"] for r in rec["results"]} if rec else None})
        done = all(l["status"] == "complete" for l in legs)
        # chips won so far: finished legs, plus live legs up to their last finished hand
        so_far = [l["delta"] or (l["live"] or {}).get("delta") or {} for l in legs]
        delta = {a: sum(d.get(a, 0) for d in so_far), b: sum(d.get(b, 0) for d in so_far)}
        result = None
        if done:
            result = "draw" if delta[a] == delta[b] else (a if delta[a] > delta[b] else b)
        pairings.append({"pairing": pid, "players": [a, b], "legs": legs, "delta": delta,
                         "status": "complete" if done else
                                   "running" if any(l["status"] == "running" for l in legs) else
                                   "pending" if all(l["status"] == "pending" for l in legs) else "partial",
                         "winner": result})
    complete = [r for r in records
                if next(p for p in pairings if p["pairing"] == r["pairing"])["status"] == "complete"]
    table = round_robin.standings(cfg, complete) if complete else []
    # every bot's chips won so far, across finished and live matches
    chips = {n: {"bot": n, "delta": 0, "live": 0, "playing": 0} for n in names}
    for p in pairings:
        for leg in p["legs"]:
            for n in p["players"]:
                if leg["delta"]:
                    chips[n]["delta"] += leg["delta"].get(n, 0)
                elif leg["live"]:
                    chips[n]["delta"] += leg["live"]["delta"].get(n, 0)
                    chips[n]["live"] += leg["live"]["delta"].get(n, 0)
                    chips[n]["playing"] += 1
    return {"kind": "round_robin", "bots": names, "pairings": pairings, "standings": table,
            "chips": sorted(chips.values(), key=lambda c: -c["delta"]),
            "live": {l["key"]: {**l["live"], "players": p["players"]}
                     for p in pairings for l in p["legs"] if l["live"]}}


# ---------------------------------------------------------------------------

def mtt_progress(run_dir: Path, run_id: str, active: bool = False) -> dict:
    tables_dir = run_dir / "matches" / "mtt" / "tables"
    tables, eliminations = [], []
    if tables_dir.is_dir():
        for d in sorted(tables_dir.iterdir(), key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else 0):
            f = d / EVENTS_FILE
            if not f.is_file():
                continue
            seats, closed, hand, blinds, level, button, ended = {}, False, None, None, None, None, None
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                t = e["type"]
                if t == "match_start":
                    seats = {s["name"]: {"seat": s["seat"], "stack": e["config"]["starting_stack"]}
                             for s in e["seats"]}
                elif t == "hand_start":
                    hand, blinds, level, button = e["hand"], e["blinds"], e["level"], e["button"]
                    for n, st in e["stacks"].items():
                        if n in seats:
                            seats[n]["stack"] = st
                elif t == "hand_end":
                    for n, st in e["stacks"].items():
                        if n in seats:
                            seats[n]["stack"] = st
                elif t == "seat_change":
                    if e["reason"] == "join":
                        seats[e["bot"]] = {"seat": e["seat"], "stack": e["stack"]}
                    else:
                        seats.pop(e["bot"], None)
                elif t == "elimination":
                    eliminations.append({"bot": e["bot"], "place": e["place"], "hand": e["hand"],
                                         "table": int(d.name[1:])})
                elif t == "table_closed":
                    closed = True
                elif t == "match_end":
                    ended = e.get("stopped")
            tables.append({
                "table": int(d.name[1:]), "key": f"{run_id}/matches/mtt/tables/{d.name}",
                "status": _status(d, active), "closed": closed, "ended": ended, "hand": hand, "level": level, "blinds": blinds,
                "button": button,
                "players": sorted(({"bot": n, **v} for n, v in seats.items()), key=lambda p: p["seat"]),
            })
    eliminations.sort(key=lambda e: e["place"])
    open_tables = [t for t in tables if not t["closed"]]
    left = sum(len(t["players"]) for t in open_tables)
    leader = max((p for t in open_tables for p in t["players"]), key=lambda p: p["stack"], default=None)
    return {"kind": "mtt", "tables": tables, "eliminations": eliminations, "players_left": left,
            "chip_leader": leader["bot"] if leader else None}


# ---------------------------------------------------------------------------

def series_progress(series_dir: Path, series: dict) -> dict:
    from arena.series import aggregate
    done = []
    for d in sorted(series_dir.iterdir()):
        f = d / formats.RUN_FILE
        if d.is_dir() and f.is_file():
            r = json.loads(f.read_text())
            if r.get("status") == "complete":
                done.append(r)
    return {"kind": "series", "runs_done": len(done), "runs": series.get("runs"),
            "aggregate": aggregate(done) if done else [],
            "winners": [{"run_id": r["run_id"], "winner": r["standings"][0]["bot"]} for r in done]}
