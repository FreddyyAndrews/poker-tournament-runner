"""Round-robin: every pair of bots plays heads-up.

By default each pairing is a *duplicate* cash match: the same deals are played
twice with the seats swapped, every hand starting from the starting stack, and
the two legs are added together. A pairing is won by whoever is up on chips
across both legs (equal = draw). Standings: points (win 1, draw 0.5), then
total chips won, then bb/100.
"""

from itertools import combinations

from arena.formats import match_record
from arena.formats.table_fmt import combine

DEFAULTS = {"mode": "cash", "hands": 200, "blinds": "50/100", "duplicate": True}


def pairing_id(a: str, b: str) -> str:
    return f"{a}_vs_{b}"


def play(cfg, ctx) -> dict:
    if len(ctx.specs) < 2:
        raise ValueError("round_robin needs at least 2 bots")
    legs = []
    for a, b in combinations(ctx.specs, 2):
        pid = pairing_id(a.name, b.name)
        seed = ctx.match_seed(pid)
        legs.append((pid, 1, [a, b], seed))
        if cfg.duplicate:
            legs.append((pid, 2, [b, a], seed))

    def play_leg(item):
        pid, leg, specs, seed = item
        mid = f"{pid}_leg{leg}" if cfg.duplicate else pid
        summary = ctx.play(specs, cfg.match_config(seed), mid)
        return match_record(summary, pairing=pid, leg=leg,
                            players=sorted([specs[0].name, specs[1].name],
                                           key=lambda n: [s.name for s in ctx.specs].index(n)))

    records = ctx.parallel(play_leg, legs)
    return {"matches": records, "standings": standings(cfg, records),
            "head_to_head": head_to_head(records)}


def head_to_head(records) -> dict:
    """{bot: {opponent: {"delta": chips won vs them, "result": "W"|"D"|"L"}}}"""
    by_pair = {}
    for rec in records:
        a, b = rec["players"]
        d = by_pair.setdefault((a, b), {a: 0, b: 0})
        for r in rec["results"]:
            d[r["bot"]] += r["delta"]
    out = {}
    for (a, b), d in by_pair.items():
        res_a = "W" if d[a] > d[b] else "L" if d[a] < d[b] else "D"
        res_b = {"W": "L", "L": "W", "D": "D"}[res_a]
        out.setdefault(a, {})[b] = {"delta": d[a], "result": res_a}
        out.setdefault(b, {})[a] = {"delta": d[b], "result": res_b}
    return out


def standings(cfg, records) -> list:
    h2h = head_to_head(records)
    totals = combine(records)
    rows = []
    for bot, t in totals.items():
        res = [v["result"] for v in h2h.get(bot, {}).values()]
        w, d, l = res.count("W"), res.count("D"), res.count("L")
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
    return rows
