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
Bots level on points are separated by their head-to-head results against each
other, then bb/100. Bots still level share a place; if that happens at the
cut-off, more than `qualifiers` (4) bots show as qualified and the organizer
settles it with a playoff.

Each row also carries the bot's record against every opponent ("versus") and
against the top and bottom halves of the standings, for the head-to-head
matrix: the scores alone don't show who beats whom.
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
    results = pairing_results(records)
    for p in results:
        for bot in p["players"]:
            i = 1 if p["winner"] is None else 0 if p["winner"] == bot else 2
            wdl.setdefault(bot, [0, 0, 0])[i] += 1
    rows = []
    for bot, t in totals.items():
        w, d, l = wdl[bot]
        rows.append({"bot": bot, "points": w + 0.5 * d, "wins": w, "draws": d, "losses": l,
                     **{k: t[k] for k in ("delta", "hands_played", "bb_per_100", "timeouts",
                                          "errors", "matches")}})
    # Head-to-head: among bots level on points, the points each won in the
    # pairings between just those bots. (Chips can't separate them: in
    # freezeouts a pairing is worth a whole stack or nothing, so equal points
    # means equal chips.)
    level = {}
    for r in rows:
        level.setdefault(r["points"], set()).add(r["bot"])
    h2h = {r["bot"]: 0.0 for r in rows}
    for p in results:
        a, b = p["players"]
        tied = next((g for g in level.values() if a in g and b in g), None)
        if tied and len(tied) > 1:
            for bot in (a, b):
                h2h[bot] += 0.5 if p["winner"] is None else 1.0 if p["winner"] == bot else 0.0
    for r in rows:
        r["head_to_head"] = h2h[r["bot"]] if len(level[r["points"]]) > 1 else None
    rows.sort(key=lambda r: (-r["points"], -h2h[r["bot"]], -r["bb_per_100"], r["bot"]))
    place, prev = 0, None
    for i, r in enumerate(rows):
        key = (r["points"], h2h[r["bot"]], r["bb_per_100"])
        if key != prev:
            place, prev = i + 1, key
        r["place"] = place
        r["qualified"] = place <= QUALIFIERS
    # Who beats whom: each bot's record (wins, draws, losses, in pairings)
    # against every opponent, and against the top and bottom halves of these
    # standings (the top half is the first half of the list, rounded up; a
    # bot's own half doesn't include itself).
    versus = {r["bot"]: {} for r in rows}
    for p in results:
        a, b = p["players"]
        for bot, opp in ((a, b), (b, a)):
            i = 1 if p["winner"] is None else 0 if p["winner"] == bot else 2
            versus.setdefault(bot, {}).setdefault(opp, [0, 0, 0])[i] += 1
    top = {r["bot"] for r in rows[:(len(rows) + 1) // 2]}
    for r in rows:
        r["versus"] = versus[r["bot"]]
        for key, half in (("vs_top", True), ("vs_bottom", False)):
            r[key] = [sum(rec[i] for opp, rec in r["versus"].items() if (opp in top) == half) for i in range(3)]
    return rows
