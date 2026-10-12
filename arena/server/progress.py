"""Live, format-specific views of a run, built from what is on disk right now:
the run's config, finished match summaries, and (for multi-table) each
table's live log. Works while a run is still playing and after it ends.

  bracket      rounds of ties: players, seeds, status, winner, chips
  round_robin  every pairing: legs, status, chips, result; points so far
  mtt          every table: players and stacks, hand, blinds, button; busts
  series       aggregate over the runs finished so far
"""

import json
from itertools import combinations
from pathlib import Path

from arena import bots as botlib
from arena import formats
from arena.events import EVENTS_FILE, SUMMARY_FILE
from arena.formats import bracket, round_robin
from arena.seeds import derive_seed


def _summary(match_dir: Path):
    f = match_dir / SUMMARY_FILE
    return json.loads(f.read_text()) if f.is_file() else None


def _status(match_dir: Path, summary) -> str:
    if summary is not None:
        return "complete" if summary.get("stopped") not in ("cancelled", "error") else summary["stopped"]
    return "running" if (match_dir / EVENTS_FILE).is_file() else "pending"


def _record(summary, **extra):
    return formats.match_record(summary, **extra) if summary and summary.get("results") else None


def run_progress(run_dir: Path, run: dict, run_id: str):
    fmt = run.get("format")
    try:
        cfg = formats.RunConfig(**run["config"])
    except Exception:
        return None
    if fmt == "bracket":
        return bracket_progress(run_dir, cfg, run_id)
    if fmt == "round_robin":
        return round_robin_progress(run_dir, cfg, run_id)
    if fmt == "mtt":
        return mtt_progress(run_dir, run_id)
    return None


# ---------------------------------------------------------------------------

def bracket_progress(run_dir: Path, cfg, run_id: str) -> dict:
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
                tie["status"] = _status(mdir, summary)
                rec = _record(summary, players=[a, b])
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
    return {"kind": "bracket", "rounds": rounds, "champion": champion}


# ---------------------------------------------------------------------------

def round_robin_progress(run_dir: Path, cfg, run_id: str) -> dict:
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
            legs.append({"match_id": mid, "key": f"{run_id}/matches/{mid}", "leg": leg,
                         "status": _status(mdir, summary),
                         "delta": {r["bot"]: r["delta"] for r in rec["results"]} if rec else None})
        done = all(l["status"] == "complete" for l in legs)
        delta = {a: sum((l["delta"] or {}).get(a, 0) for l in legs),
                 b: sum((l["delta"] or {}).get(b, 0) for l in legs)}
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
    return {"kind": "round_robin", "bots": names, "pairings": pairings, "standings": table}


# ---------------------------------------------------------------------------

def mtt_progress(run_dir: Path, run_id: str) -> dict:
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
                "closed": closed, "ended": ended, "hand": hand, "level": level, "blinds": blinds,
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
