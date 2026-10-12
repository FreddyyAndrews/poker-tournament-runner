"""Multi-table freezeout.

Players are seated at random across ceil(n / table_size) tables. Play advances
in *ticks*: every table with 2+ players plays one hand per tick (in parallel),
so blind levels (by tick) stay in step across tables. After each tick:

  1. busted players are removed and placed: everyone busting in the same tick
     is ranked by the stack they started that hand with (bigger = better place);
  2. a table is broken whenever the remaining players fit on one fewer table,
     its players moving to the emptiest tables;
  3. tables are balanced so no table has 2+ more players than another.

That ends with a final table and, eventually, one winner.

All tables write to one event log (each event tagged with its table). Each
table's events are buffered during a tick and appended in table order, so the
log is the same no matter how threads are scheduled; that is what lets
`poker verify` replay it.
"""

import math
import random
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import arena
from arena.events import EventLog
from arena.formats import match_record
from arena.pacing import Cancelled, Pacer
from arena.seeds import derive_seed
from arena.table import (Table, bot_result, diagnostics, engine_hash, make_bot_seats,
                         rank, warm_up)

DEFAULTS = {"mode": "freezeout", "hands": 5000, "blinds": "standard", "duplicate": False}

MATCH_ID = "mtt"


class _Buffer:
    """Collects a table's events for the main log (flushed in table order),
    and optionally writes them straight away to the table's own live log."""

    def __init__(self, live=None):
        self.events = []
        self.live = live

    def emit(self, event_type, **data):
        event = {"type": event_type, **data}
        self.events.append(event)
        if self.live is not None:
            self.live.emit(event_type, **data)
        return event

    def end(self, **data):
        if self.live is not None:
            self.live.emit("match_end", **data)
            self.live.close()
            self.live = None


def _flush(tables, log):
    for tid in sorted(tables):
        buf = tables[tid].log
        for e in buf.events:
            data = {k: v for k, v in e.items() if k != "type"}
            log.emit(e["type"], **data)
        buf.events.clear()


def play_mtt(seats, match_cfg, table_size: int, match_id: str, log, workers: int = 1,
             pacer=None, table_logs_dir=None) -> dict:
    """Play a multi-table freezeout with already-built seats. Returns
    {"results", "hands", "stopped"} like play_match."""
    seed = match_cfg.seed
    n = len(seats)
    if n < 2:
        raise ValueError("mtt needs at least 2 players")
    order = list(seats)
    random.Random(derive_seed(seed, "seating")).shuffle(order)
    n_tables = math.ceil(n / table_size)
    slots = {tid: [None] * table_size for tid in range(1, n_tables + 1)}
    for k, s in enumerate(order):
        tid = k % n_tables + 1
        slots[tid][k // n_tables] = s
    def live_log(tid, sl):
        """Per-table live log (for watching one table as it plays); None in replays."""
        if table_logs_dir is None:
            return None
        live = EventLog(Path(table_logs_dir) / f"t{tid}")
        live.emit("match_start", match_id=f"{match_id}_t{tid}", seed=seed, config=match_cfg.to_dict(),
                  format="mtt_table", table=tid, table_size=table_size,
                  seats=[{**s.info(), "seat": i} for i, s in enumerate(sl) if s])
        return live

    tables = {tid: Table(sl, {s.name: match_cfg.starting_stack for s in sl if s},
                         match_cfg.structure, derive_seed(seed, "table", tid), _Buffer(live_log(tid, sl)),
                         f"{match_id}_t{tid}", table_id=tid, pacer=pacer)
              for tid, sl in slots.items()}
    by_name = {s.name: s for s in seats}
    final_stack = {}                              # busted players: 0
    places, busted_on = {}, {}
    hands_dealt = {s.name: 0 for s in seats}
    bb_sum = {s.name: 0.0 for s in seats}
    tick, stopped = 0, None
    t0 = time.time()

    def alive():
        return [name for t in tables.values() for name in t.occupied()]

    try:
        warm = warm_up(seats)
        log.emit("match_start", match_id=match_id, seed=seed, config=match_cfg.to_dict(),
                 format="mtt", table_size=table_size,
                 seats=[{**s.info(), "warmup": warm[s.name]} for s in seats],
                 tables={tid: [s.name if s else None for s in t.slots] for tid, t in tables.items()},
                 engine_hash=engine_hash(), arena_version=arena.__version__)

        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        try:
            while True:
                players_left = len(alive())
                if players_left < 2:
                    stopped = "one_player_left"
                    break
                if tick >= match_cfg.hands:
                    stopped = "hand_limit"
                    break
                lvl = match_cfg.structure.level(tick)
                context = {"tournament": {
                    "format": "mtt", "level": match_cfg.structure.level_index(tick) + 1,
                    "players_left": players_left, "entrants": n, "tables": len(tables),
                    "blinds": {"sb": lvl.sb, "bb": lvl.bb, "ante": lvl.ante}}}
                playing = [t for _, t in sorted(tables.items()) if len(t.occupied()) >= 2]
                infos = list(pool.map(lambda t: t.play_hand(tick, context), playing))
                _flush(tables, log)

                busted = {}
                for t, info in zip(playing, infos):
                    for name in info["order"]:
                        hands_dealt[name] += 1
                        bb_sum[name] += info["deltas"][name] / info["bb"]
                    for name in info["busted"]:
                        busted[name] = (info["stacks_before"][name], t)
                if busted:
                    remaining = players_left - len(busted)
                    ranked = rank(list(busted), key=lambda b: busted[b][0])
                    for name in sorted(busted, key=lambda b: (-ranked[b], busted[b][1].table_id,
                                                              busted[b][1].seat_of[b])):
                        t = busted[name][1]
                        places[name] = remaining + ranked[name]
                        busted_on[name] = tick
                        final_stack[name] = 0
                        t.remove_player(name, hand=tick, reason="bust")
                        t._emit("elimination", hand=tick, bot=name, place=places[name])
                    _rebalance(tables, table_size, seed, tick, log)
                _flush(tables, log)
                tick += 1
        finally:
            pool.shutdown(wait=True)
    except Cancelled:
        stopped = "cancelled"
        raise
    except BaseException as e:
        stopped = "error: " + repr(e)
        raise
    finally:
        for s in seats:
            s.stop()
        stacks = {name: st for t in tables.values() for name, st in t.stacks.items()}
        final_stack.update(stacks)
        live = [nm for nm in stacks if stacks[nm] > 0]
        for name, r in rank(live, key=lambda nm: stacks[nm]).items():
            places[name] = r
        results = [bot_result(by_name[nm], places.get(nm), final_stack.get(nm, 0),
                              match_cfg.starting_stack, hands_dealt[nm], bb_sum[nm],
                              busted_on.get(nm))
                   for nm in by_name]
        results.sort(key=lambda r: (r["place"] if r["place"] is not None else 10**6, r["bot"]))
        for t in tables.values():
            t.log.end(hands=tick, results=[], stopped=stopped)
        log.emit("match_end", hands=tick, duration_s=round(time.time() - t0, 2),
                 results=results, stopped=stopped)
        # kept on the log so a caller still has the final standings if the
        # match was cancelled or failed (they are also in match_end above)
        log.final = {"results": results, "hands": tick, "stopped": stopped}
    return {"results": results, "hands": tick, "stopped": stopped}


def _rebalance(tables, table_size, seed, tick, log):
    """Break tables when possible, then even out table sizes."""
    def counts():
        return {tid: len(t.players()) for tid, t in tables.items()}

    def move(name, src, dst, why):
        rng = random.Random(derive_seed(seed, "move", tick, name))
        seat, stack = src.remove_player(name, hand=tick, reason=why)
        dst.seat_player(seat, rng.choice(dst.empty_seats()), stack, hand=tick)

    # Break the smallest table while everyone fits on one fewer table.
    while len(tables) > 1 and sum(counts().values()) <= (len(tables) - 1) * table_size:
        c = counts()
        tid = max(tables, key=lambda t: (-c[t], t))        # fewest players, highest id
        breaking = tables[tid]
        for name in list(breaking.players()):
            c = counts()
            dst = min((t for t in tables if t != tid and tables[t].empty_seats()),
                      key=lambda t: (c[t], t))
            move(name, breaking, tables[dst], "table_break")
        breaking._emit("table_closed", hand=tick)
        _flush({tid: breaking}, log)
        breaking.log.end(hands=tick, results=[], stopped="table_closed")
        del tables[tid]

    # Balance: move from the fullest table to the emptiest until within one.
    while True:
        c = counts()
        big = max(tables, key=lambda t: (c[t], -t))
        small = min(tables, key=lambda t: (c[t], t))
        if c[big] - c[small] <= 1:
            break
        players = tables[big].players()
        rng = random.Random(derive_seed(seed, "balance", tick, big, c[big]))
        move(rng.choice(players), tables[big], tables[small], "balance")


def play(cfg, ctx) -> dict:
    match_cfg = cfg.match_config(ctx.match_seed(MATCH_ID))
    if match_cfg.mode != "freezeout":
        raise ValueError("mtt is always a freezeout (mode: freezeout)")
    seats = make_bot_seats(ctx.specs, match_cfg, ctx.use_docker)
    run_dir = ctx.run_dir / "matches" / MATCH_ID
    t0 = time.time()
    with EventLog(run_dir) as log:
        outcome, interrupted = None, "error"
        try:
            pacer = Pacer(ctx.pace_ms, ctx.cancel) if (ctx.pace_ms or ctx.cancel) else None
            outcome = play_mtt(seats, match_cfg, cfg.table_size, MATCH_ID, log,
                               workers=max(ctx.workers, len(seats)) if ctx.pace_ms else ctx.workers,
                               pacer=pacer, table_logs_dir=run_dir / "tables")
        except Cancelled:
            interrupted = "cancelled"
            raise
        finally:
            summary = {
                "kind": "match", "match_id": MATCH_ID, "seed": match_cfg.seed,
                "config": match_cfg.to_dict(), "format": "mtt", "table_size": cfg.table_size,
                "seats": [s.info() for s in seats],
                "results": (outcome or getattr(log, "final", {})).get("results"),
                "hands": (outcome or getattr(log, "final", {})).get("hands"),
                "stopped": outcome["stopped"] if outcome else interrupted,
                "duration_s": round(time.time() - t0, 2), "run_dir": str(run_dir),
                "bot_diagnostics": diagnostics(seats),
            }
            log.write_summary(summary)
    record = match_record(summary)
    return {"matches": [record], "standings": standings(cfg, [record])}


def standings(cfg, records) -> list:
    (rec,) = records
    return sorted((dict(r) for r in rec["results"]), key=lambda r: (r["place"] or 10**6, r["bot"]))
