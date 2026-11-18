"""Qualifier: repeated round robins for as long as a time budget allows.

Each *cycle* is a full round robin: every pair of bots plays a duplicate pair
of matches (the same cards twice, seats swapped), with seeds of its own, so
every cycle deals different cards. Cycles run back to back, each playing as
many matches at once as `workers` allows.

A cycle that has started always finishes. A new one starts only if the time
budget leaves room for it, judged by how long cycles have taken so far, so
the round ends close to the budget and every bot has played every opponent
the same number of times. Only complete cycles count.

Standings, over all cycles: each pairing in each cycle is won by whoever is
up on chips across its two legs (equal = draw). Points: win 1, draw 0.5.
Ties are broken by chips won, then bb/100, then name. The top `qualifiers`
bots (4 by default) go through.
"""

import json
import time
from itertools import combinations

from arena.formats import match_record
from arena.formats.table_fmt import combine

DEFAULTS = {"mode": "freezeout", "hands": 2000, "blinds": "standard", "duplicate": True}
PROGRESS_FILE = "qualifier.json"
QUALIFIERS = 4


def match_id(cycle: int, a: str, b: str, leg: int) -> str:
    return f"c{cycle:02d}_{a}_vs_{b}_leg{leg}"


def _legs(ctx, cycle: int):
    legs = []
    for a, b in combinations(ctx.specs, 2):
        seed = ctx.match_seed("cycle", cycle, a.name, b.name)
        legs.append((cycle, a, b, 1, [a, b], seed))
        legs.append((cycle, a, b, 2, [b, a], seed))
    return legs


def play(cfg, ctx) -> dict:
    if len(ctx.specs) < 2:
        raise ValueError("a qualifier needs at least 2 bots")
    budget_s = (cfg.time_budget_min or 0) * 60
    order = [s.name for s in ctx.specs]
    t0 = time.time()
    records, cycle_times = [], []

    def save_progress(running_cycle):
        ctx.run_dir.mkdir(parents=True, exist_ok=True)
        (ctx.run_dir / PROGRESS_FILE).write_text(json.dumps({
            "started_at": t0, "time_budget_min": cfg.time_budget_min, "max_cycles": cfg.max_cycles,
            "cycles_complete": len(cycle_times), "cycle_seconds": [round(t, 1) for t in cycle_times],
            "running_cycle": running_cycle, "matches_per_cycle": len(ctx.specs) * (len(ctx.specs) - 1),
        }, indent=2))

    def play_leg(item):
        cycle, a, b, leg, specs, seed = item
        summary = ctx.play(specs, cfg.match_config(seed), match_id(cycle, a.name, b.name, leg))
        return match_record(summary, cycle=cycle, pairing=f"{a.name}_vs_{b.name}", leg=leg,
                            players=sorted([a.name, b.name], key=order.index))

    cycle = 1
    while True:
        save_progress(cycle)
        start = time.time()
        records += ctx.parallel(play_leg, _legs(ctx, cycle))
        cycle_times.append(time.time() - start)
        elapsed = time.time() - t0
        typical = sum(cycle_times) / len(cycle_times)
        if cfg.max_cycles and cycle >= cfg.max_cycles:
            break
        if elapsed + typical > budget_s:
            break
        cycle += 1
    save_progress(None)
    return {"matches": records, "standings": standings(cfg, records), "cycles": len(cycle_times),
            "cycle_seconds": [round(t, 1) for t in cycle_times], "qualifiers": QUALIFIERS,
            "elapsed_s": round(time.time() - t0, 1)}


def pairing_results(records) -> list:
    """One result per pairing per cycle: {"cycle", "players", "delta", "winner"}."""
    by = {}
    for rec in records:
        a, b = rec["players"]
        key = (rec.get("cycle", 1), a, b)
        d = by.setdefault(key, {a: 0, b: 0, "legs": 0})
        d["legs"] += 1
        for r in rec["results"]:
            d[r["bot"]] += r["delta"]
    out = []
    for (cycle, a, b), d in by.items():
        winner = None if d[a] == d[b] else (a if d[a] > d[b] else b)
        out.append({"cycle": cycle, "players": [a, b], "delta": {a: d[a], b: d[b]},
                    "legs": d["legs"], "winner": winner})
    return out


def standings(cfg, records) -> list:
    totals = combine(records)
    wdl = {bot: [0, 0, 0] for bot in totals}
    for p in pairing_results(records):
        for bot in p["players"]:
            i = 1 if p["winner"] is None else 0 if p["winner"] == bot else 2
            wdl.setdefault(bot, [0, 0, 0])[i] += 1
    rows = []
    for bot, t in totals.items():
        w, d, l = wdl[bot]
        rows.append({"bot": bot, "points": w + 0.5 * d, "wins": w, "draws": d, "losses": l,
                     **{k: t[k] for k in ("delta", "hands_played", "bb_per_100", "timeouts",
                                          "errors", "matches")}})
    rows.sort(key=lambda r: (-r["points"], -r["delta"], -r["bb_per_100"], r["bot"]))
    place, prev = 0, None
    for i, r in enumerate(rows):
        key = (r["points"], r["delta"], r["bb_per_100"])
        if key != prev:
            place, prev = i + 1, key
        r["place"] = place
        r["qualified"] = place <= QUALIFIERS
    return rows
