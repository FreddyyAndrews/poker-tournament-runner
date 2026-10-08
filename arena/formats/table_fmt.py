"""One table of 2-9 bots. Heads-up is a table of 2; with `duplicate`, a
heads-up match is played twice with the same cards and the seats swapped, and
results are combined (this cancels most of the luck of the deal)."""

from arena.formats import match_record
from arena.table import rank

DEFAULTS = {"mode": "fixed", "hands": 100, "blinds": "50/100", "duplicate": False}


def play(cfg, ctx) -> dict:
    specs = ctx.specs
    if not 2 <= len(specs) <= 9:
        raise ValueError(f"table format needs 2-9 bots, got {len(specs)}")
    if cfg.duplicate and len(specs) != 2:
        raise ValueError("duplicate is only supported for heads-up (2 bots)")
    seed = ctx.match_seed("table")
    legs = [specs, list(reversed(specs))] if cfg.duplicate else [specs]
    records = []
    for i, leg in enumerate(legs, 1):
        summary = ctx.play(leg, cfg.match_config(seed), f"table_leg{i}" if cfg.duplicate else "table")
        records.append(match_record(summary, leg=i))
    return {"matches": records, "standings": standings(cfg, records)}


def combine(records) -> dict:
    """Per-bot totals across matches: delta, hands, bb/100 (hand-weighted),
    timeouts, errors."""
    totals = {}
    for rec in records:
        for r in rec["results"]:
            t = totals.setdefault(r["bot"], {"bot": r["bot"], "delta": 0, "hands_played": 0,
                                             "_bb": 0.0, "timeouts": 0, "errors": 0, "matches": 0})
            t["delta"] += r["delta"]
            t["hands_played"] += r["hands_played"]
            t["_bb"] += r["bb_per_100"] * r["hands_played"]
            t["timeouts"] += r["timeouts"] or 0
            t["errors"] += r["errors"] or 0
            t["matches"] += 1
    for t in totals.values():
        t["bb_per_100"] = round(t.pop("_bb") / t["hands_played"], 2) if t["hands_played"] else 0.0
    return totals


def standings(cfg, records) -> list:
    if len(records) == 1:
        rows = [dict(r) for r in records[0]["results"]]
    else:
        totals = combine(records)
        places = rank(list(totals), key=lambda b: totals[b]["delta"])
        rows = [{**t, "place": places[b]} for b, t in totals.items()]
    return sorted(rows, key=lambda r: (r["place"] or 99, r["bot"]))
