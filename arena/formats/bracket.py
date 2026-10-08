"""Single-elimination heads-up bracket.

Seeds are placed in standard bracket order (1 v 16, 8 v 9, ... so the top
seeds can only meet late). When the field isn't a power of two, the top seeds
get byes. Each tie is one heads-up match (freezeout by default); a tie on chips
is settled by a seeded coin flip. Losers in the same round share a place:
final -> 2nd, semi-finals -> 3rd, quarter-finals -> 5th, and so on.
"""

import random

from arena import bots as botlib
from arena.formats import match_record, two_player_winner
from arena.formats.table_fmt import combine
from arena.seeds import derive_seed

DEFAULTS = {"mode": "freezeout", "hands": 2000, "blinds": "standard", "duplicate": False}


def bracket_order(size: int) -> list:
    """Seed numbers in bracket position order, e.g. 8 -> [1, 8, 4, 5, 2, 7, 3, 6]."""
    order = [1]
    while len(order) < size:
        m = len(order) * 2
        order = [x for s in order for x in (s, m + 1 - s)]
    return order


def seeded_entrants(cfg) -> list:
    """Bot names in seed order (seed 1 first)."""
    names = [s.name for s in botlib.resolve_all(cfg.bots, names=cfg.names)]
    if cfg.seeding == "random":
        random.Random(derive_seed(cfg.seed, "seeding")).shuffle(names)
    return names


def first_round_slots(cfg) -> list:
    names = seeded_entrants(cfg)
    size = 1
    while size < len(names):
        size *= 2
    return [names[s - 1] if s <= len(names) else None for s in bracket_order(size)]


def match_id(rnd: int, idx: int) -> str:
    return f"r{rnd}_m{idx + 1}"


def play(cfg, ctx) -> dict:
    if len(ctx.specs) < 2:
        raise ValueError("bracket needs at least 2 bots")
    if cfg.duplicate:
        raise ValueError("duplicate is not supported for brackets")
    by_name = {s.name: s for s in ctx.specs}
    current = first_round_slots(cfg)
    records, rnd = [], 1
    while len(current) > 1:
        pairs = [(current[i], current[i + 1]) for i in range(0, len(current), 2)]
        to_play = [(i, a, b) for i, (a, b) in enumerate(pairs) if a and b]

        def play_pair(item, rnd=rnd):
            i, a, b = item
            mid = match_id(rnd, i)
            summary = ctx.play([by_name[a], by_name[b]], cfg.match_config(ctx.match_seed(mid)), mid)
            return match_record(summary, round=rnd, slot=i, players=[a, b])

        played = {r["slot"]: r for r in ctx.parallel(play_pair, to_play)}
        records.extend(played[i] for i, _, _ in to_play)
        nxt = []
        for i, (a, b) in enumerate(pairs):
            if a and b:
                nxt.append(two_player_winner(played[i], derive_seed(cfg.seed, "tiebreak", match_id(rnd, i)))[0])
            else:
                nxt.append(a or b)
        current = nxt
        rnd += 1
    return {"matches": records, "standings": standings(cfg, records),
            "seeding": seeded_entrants(cfg)}


def standings(cfg, records) -> list:
    """Replay the bracket from the seeding and the match records. Raises
    ValueError if the records don't form this bracket."""
    by_key = {(r["round"], tuple(r["players"])): r for r in records}
    current = first_round_slots(cfg)
    places, eliminated, wins, losses = {}, {}, {}, {}
    used, rnd = set(), 1
    while len(current) > 1:
        nxt = []
        for i in range(0, len(current), 2):
            a, b = current[i], current[i + 1]
            if a and b:
                rec = by_key.get((rnd, (a, b)))
                if rec is None:
                    raise ValueError(f"bracket: no round {rnd} match between {a} and {b}")
                used.add(rec["match_id"])
                w, l, _ = two_player_winner(rec, derive_seed(cfg.seed, "tiebreak", match_id(rnd, i // 2)))
                places[l] = len(current) // 2 + 1
                eliminated[l] = rnd
                wins[w] = wins.get(w, 0) + 1
                losses[l] = losses.get(l, 0) + 1
                nxt.append(w)
            else:
                nxt.append(a or b)
        current = nxt
        rnd += 1
    places[current[0]] = 1
    extra = [r["match_id"] for r in records if r["match_id"] not in used]
    if extra:
        raise ValueError(f"bracket: matches that don't belong to the bracket: {extra}")
    totals = combine(records)
    rows = []
    for name in places:
        t = totals.get(name, {"delta": 0, "hands_played": 0, "bb_per_100": 0.0,
                              "timeouts": 0, "errors": 0, "matches": 0})
        rows.append({"bot": name, "place": places[name], "wins": wins.get(name, 0),
                     "losses": losses.get(name, 0), "eliminated_in_round": eliminated.get(name),
                     **{k: t[k] for k in ("delta", "hands_played", "bb_per_100", "timeouts",
                                          "errors", "matches")}})
    return sorted(rows, key=lambda r: (r["place"], r["bot"]))
