"""Tables and matches.

A Table has a fixed number of seats. Players keep their seat number for as long
as they sit there (it never shifts when someone else busts); the button moves
to the next occupied seat each hand; blinds follow the structure by hand count;
and every hand is played through the engine with every event logged.

The engine only knows the players dealt into a hand (seats 0..k-1 in table
order). The table translates engine seats to table seats everywhere: in the
state bots see, and in the event log.

Seats may be empty, and players can be seated or removed between hands, which
is how multi-table tournaments move players around (see formats/mtt.py).
"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import arena
from arena import structures
from arena.events import DEFAULT_RUNS_DIR, EventLog
from arena.pacing import Cancelled, Pacer
from arena.seats import BotSeat, Seat
from arena.seeds import derive_seed, new_seed
from engine.game import PokerEngine

MATCH_LOG_MAX_ENTRIES = 200
MAX_STEPS_PER_HAND = 1000

ENGINE_PATH = Path(__file__).resolve().parent.parent / "engine" / "game.py"

MODES = ("fixed", "cash", "freezeout")


def engine_hash() -> str:
    return hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest()[:16]


@dataclass
class MatchConfig:
    # "fixed":     play `hands` hands; stacks carry over (a bot can bust out)
    # "cash":      play `hands` hands; every hand starts from starting_stack
    #              (results are the sum of per-hand wins; nobody busts)
    # "freezeout": play until one bot has every chip; `hands` is a safety cap
    mode: str = "fixed"
    hands: int = 100
    starting_stack: int = 10_000
    structure: structures.Structure = field(default_factory=lambda: structures.flat(50, 100))
    seed: int = None
    time_limit_ms: int = None            # per decision; None = seat default (bots: 2000)

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.hands < 1:
            raise ValueError("hands must be >= 1")
        if self.starting_stack < 1:
            raise ValueError("starting_stack must be >= 1")

    def to_dict(self):
        return {"mode": self.mode, "hands": self.hands, "starting_stack": self.starting_stack,
                "structure": self.structure.to_dict(), "seed": self.seed,
                "time_limit_ms": self.time_limit_ms}

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["structure"] = structures.Structure.from_dict(d["structure"])
        return cls(**d)


def rank(names, key) -> dict:
    """Places for `names`, best first by key (desc); equal keys share a place."""
    ordered = sorted(names, key=lambda n: -key(n))
    places, prev, place = {}, None, 0
    for i, n in enumerate(ordered):
        if key(n) != prev:
            place, prev = i + 1, key(n)
        places[n] = place
    return places


class Table:
    def __init__(self, slots, stacks, structure, seed, log, match_id,
                 table_id=None, reset_stack=None, pacer=None):
        """slots: list of Seat or None (empty seat); index = table seat number.
        reset_stack: if set (cash mode), every hand starts from this stack."""
        self.slots       = list(slots)
        self.stacks      = {s.name: stacks[s.name] for s in self.slots if s}
        self.structure   = structure
        self.seed        = seed
        self.log         = log
        self.match_id    = match_id
        self.table_id    = table_id
        self.reset_stack = reset_stack
        self.pacer       = pacer          # called after every event (pacing / cancel)
        self.button      = None
        self.level_idx   = None
        self.hands_played = 0
        self.match_action_log = []

    # -- seating -------------------------------------------------------------

    @property
    def seat_of(self) -> dict:
        return {s.name: i for i, s in enumerate(self.slots) if s}

    @property
    def by_name(self) -> dict:
        return {s.name: s for s in self.slots if s}

    def players(self) -> list:
        """Names of everyone seated, in seat order."""
        return [s.name for s in self.slots if s]

    def occupied(self) -> list:
        """Names of seated players with chips (or everyone, in cash mode)."""
        if self.reset_stack is not None:
            return self.players()
        return [s.name for s in self.slots if s and self.stacks[s.name] > 0]

    def empty_seats(self) -> list:
        return [i for i, s in enumerate(self.slots) if s is None]

    def seat_player(self, seat, slot: int, stack: int, hand: int = None):
        assert self.slots[slot] is None, f"seat {slot} is taken"
        self.slots[slot] = seat
        self.stacks[seat.name] = stack
        self._emit("seat_change", hand=hand, bot=seat.name, seat=slot, stack=stack,
                   reason="join")

    def remove_player(self, name: str, hand: int = None, reason: str = "leave"):
        """Take a player off the table; returns (seat, stack)."""
        slot = self.seat_of[name]
        seat, stack = self.slots[slot], self.stacks.pop(name)
        self.slots[slot] = None
        self._emit("seat_change", hand=hand, bot=name, seat=slot, stack=stack, reason=reason)
        return seat, stack

    def _emit(self, event_type, **data):
        if self.table_id is not None:
            data = {"table": self.table_id, **data}
        event = self.log.emit(event_type, **data)
        if self.pacer is not None:
            self.pacer(event)
        return event

    def _move_button(self):
        n = len(self.slots)
        live = set(self.occupied())
        if self.button is None:
            start = derive_seed(self.seed, "button") % n
            candidates = [(start + k) % n for k in range(n)]
        else:
            candidates = [(self.button + k) % n for k in range(1, n + 1)]
        for t in candidates:
            if self.slots[t] is not None and self.slots[t].name in live:
                self.button = t
                return

    # -- playing a hand ------------------------------------------------------

    def play_hand(self, hand_num: int, extra_state: dict = None) -> dict:
        """Play one hand. Returns {"busted", "stacks_before", "bb", "order"}.
        extra_state is merged into what bots see (e.g. tournament context)."""
        if self.reset_stack is not None:
            for name in self.players():
                self.stacks[name] = self.reset_stack
        order = self.occupied()
        seat_of = self.seat_of
        e2t = [seat_of[name] for name in order]              # engine seat -> table seat
        self._move_button()
        lvl_idx = self.structure.level_index(hand_num)
        lvl = self.structure.level(hand_num)
        if lvl_idx != self.level_idx:
            self.level_idx = lvl_idx
            self._emit("level", hand=hand_num, level=lvl_idx + 1,
                       sb=lvl.sb, bb=lvl.bb, ante=lvl.ante)

        hand_seed = derive_seed(self.seed, "hand", hand_num)
        stacks_before = {name: self.stacks[name] for name in order}
        engine = PokerEngine(
            hand_id=f"{self.match_id}_h{hand_num:04d}",
            bot_ids=order,
            dealer_seat=order.index(self.slots[self.button].name),
            starting_stacks=stacks_before,
            seed=hand_seed,
            small_blind=lvl.sb, big_blind=lvl.bb, ante=lvl.ante,
            hand_num=hand_num,
        )
        state = engine.start_hand()

        self._emit(
            "hand_start", hand=hand_num, hand_seed=hand_seed, level=lvl_idx + 1,
            blinds={"sb": lvl.sb, "bb": lvl.bb, "ante": lvl.ante},
            button=self.button, sb=e2t[engine._sb_seat()], bb=e2t[engine._bb_seat()],
            order=order, seats={name: seat_of[name] for name in order},
            stacks=stacks_before,
            hole={p.bot_id: [str(c) for c in p.hole_cards] for p in engine.players},
        )
        seen = self._log_engine_events(engine, 0, hand_num, e2t)

        steps = 0
        by_name = self.by_name
        while state["type"] == "action_request":
            seat = state["seat_to_act"]
            name = order[seat]
            seen_state = self._for_bot(state, e2t, extra_state)
            reply = by_name[name].request_action(seen_state)
            raw = {"action": reply.get("action")}
            if "amount" in reply:
                raw["amount"] = reply["amount"]

            state = engine.apply_action(seat, raw)
            applied = next(e for e in engine.events[seen:] if e["type"] == "action")
            self._emit(
                "decision", hand=hand_num, bot=name, seat=e2t[seat], street=seen_state["street"],
                state={k: v for k, v in seen_state.items() if k != "match_action_log"},
                reply=raw,
                applied={"action": applied["action"], "amount": applied["amount"],
                         "pot_after": applied["pot_after"], "stack_after": applied["stack_after"]},
                think_ms=reply.get("think_ms"), logs=reply.get("logs", ""),
                logs_truncated=reply.get("logs_truncated", False),
                debug=reply.get("debug"), debug_error=reply.get("debug_error"),
                error=reply.get("error"), error_detail=reply.get("error_detail"),
            )
            self.match_action_log.append({"hand_num": hand_num, "seat": e2t[seat], "bot_id": name,
                                          "action": applied["action"], "amount": applied["amount"]})
            seen = self._log_engine_events(engine, seen, hand_num, e2t, skip_actions=True)
            steps += 1
            if steps > MAX_STEPS_PER_HAND:
                raise RuntimeError(f"Hand {hand_num} exceeded {MAX_STEPS_PER_HAND} steps")

        result = state
        for name, s in result["final_stacks"].items():
            self.stacks[name] = s
        self._emit(
            "hand_end", hand=hand_num, board=result["community_cards"], pot=result["pot"],
            stacks={name: self.stacks[name] for name in order},
            deltas={name: self.stacks[name] - stacks_before[name] for name in order},
        )
        self.hands_played += 1
        busted = [] if self.reset_stack is not None else \
            [name for name in order if self.stacks[name] == 0]
        return {"busted": busted, "stacks_before": stacks_before, "bb": lvl.bb, "order": order,
                "deltas": {name: self.stacks[name] - stacks_before[name] for name in order}}

    def _for_bot(self, state, e2t, extra_state=None) -> dict:
        """The engine's state with table seat numbers and the match history."""
        s = dict(state)
        for key in ("seat_to_act", "button_seat", "sb_seat", "bb_seat"):
            s[key] = e2t[s[key]]
        s["players"] = [{**p, "seat": e2t[p["seat"]]} for p in state["players"]]
        s["action_log"] = [{**a, "seat": e2t[a["seat"]]} for a in state["action_log"]]
        s["match_action_log"] = self.match_action_log[-MATCH_LOG_MAX_ENTRIES:]
        if extra_state:
            s.update(extra_state)
        return s

    def _log_engine_events(self, engine, start, hand_num, e2t, skip_actions=False) -> int:
        """Translate engine events from index `start` into log events."""
        for e in engine.events[start:]:
            t = e["type"]
            if t == "blind":
                kind = {"ante": "ante", "small_blind": "sb", "big_blind": "bb"}[e["action"]]
                self._emit("post", hand=hand_num, bot=e["bot_id"], seat=e2t[e["seat"]],
                           kind=kind, amount=e["amount"])
                # bots see posts in match_action_log just as in action_log
                self.match_action_log.append({"hand_num": hand_num, "seat": e2t[e["seat"]],
                                              "bot_id": e["bot_id"], "action": e["action"],
                                              "amount": e["amount"]})
            elif t == "street_start" and e["street"] != "preflop":
                self._emit("deal", hand=hand_num, street=e["street"], board=e["community_cards"])
            elif t == "showdown":
                self._emit("showdown", hand=hand_num, board=e["community_cards"],
                           reveal=e["revealed"], ranks=e["hand_strengths"])
                for w in e["winners"]:
                    self._emit("award", hand=hand_num, bot=w["bot_id"], amount=w["amount"],
                               pot=w["pot_type"])
            elif t == "uncontested_win":
                self._emit("award", hand=hand_num, bot=e["bot_id"], amount=e["amount"], pot="main")
            elif t == "action" and not skip_actions:
                raise RuntimeError("unexpected engine action event outside a decision")
        return len(engine.events)


# Per-bot numbers that depend on the live run (timing, process health), not on
# the cards and decisions. verify ignores these.
RUNTIME_STATS = ("decisions", "avg_think_ms", "max_think_ms", "timeouts", "errors",
                 "restarts", "disabled")


def warm_up(seats) -> dict:
    """Start every seat and warm them up in parallel. Returns warmup info by name."""
    for s in seats:
        s.start()
    with ThreadPoolExecutor(max_workers=len(seats)) as pool:
        return dict(zip([s.name for s in seats], pool.map(lambda s: s.warmup(), seats)))


def bot_result(seat, place, final_stack, starting_stack, hands, bb_sum, busted_on=None) -> dict:
    summary = seat.summary()
    return {
        "bot": seat.name, "place": place, "final_stack": final_stack,
        "delta": final_stack - starting_stack,
        "hands_played": hands,
        "bb_per_100": round(bb_sum * 100 / hands, 2) if hands else 0.0,
        "busted_on_hand": busted_on,
        **{k: summary.get(k) for k in RUNTIME_STATS},
    }


def play_match(seats, config: MatchConfig, match_id: str, log, pacer=None) -> dict:
    """Play a single-table match with already-built seats, emitting events to
    `log`. Returns {"results", "hands", "stopped"}. Used by run_match and by
    verify (which replays with recorded decisions instead of live bots)."""
    cash = config.mode == "cash"
    table = Table(seats, {s.name: config.starting_stack for s in seats},
                  config.structure, config.seed, log, match_id,
                  reset_stack=config.starting_stack if cash else None, pacer=pacer)
    bankroll = {s.name: config.starting_stack for s in seats}   # cash mode running total
    places, busted_on = {}, {}
    hands_dealt = {s.name: 0 for s in seats}
    bb_sum = {s.name: 0.0 for s in seats}
    hand_num, stopped = 0, None
    t0 = time.time()
    try:
        warm = warm_up(seats)
        log.emit("match_start", match_id=match_id, seed=config.seed, config=config.to_dict(),
                 seats=[{**s.info(), "seat": i, "warmup": warm[s.name]}
                        for i, s in enumerate(seats)],
                 engine_hash=engine_hash(), arena_version=arena.__version__)

        while True:
            if len(table.occupied()) < 2:
                stopped = "one_player_left"
                break
            if hand_num >= config.hands:
                stopped = "hand_limit"
                break
            info = table.play_hand(hand_num)
            for name in info["order"]:
                hands_dealt[name] += 1
                bb_sum[name] += info["deltas"][name] / info["bb"]
                bankroll[name] += info["deltas"][name]
            if info["busted"]:
                remaining = len(table.occupied())
                ranked = rank(info["busted"], key=lambda n: info["stacks_before"][n])
                seat_of = table.seat_of
                for name in sorted(ranked, key=lambda n: (-ranked[n], seat_of[n])):
                    places[name] = remaining + ranked[name]
                    busted_on[name] = hand_num
                    table._emit("elimination", hand=hand_num, bot=name, place=places[name])
            hand_num += 1
    except Cancelled:
        stopped = "cancelled"
        raise
    except BaseException as e:
        stopped = "error: " + repr(e)
        raise
    finally:
        for s in seats:
            s.stop()
        final = bankroll if cash else table.stacks
        for name, r in rank(table.occupied(), key=lambda n: final[n]).items():
            places[name] = r
        results = [bot_result(s, places.get(s.name), final[s.name], config.starting_stack,
                              hands_dealt[s.name], bb_sum[s.name], busted_on.get(s.name))
                   for s in seats]
        results.sort(key=lambda r: (r["place"] if r["place"] is not None else 99, r["bot"]))
        log.emit("match_end", hands=hand_num, duration_s=round(time.time() - t0, 2),
                 results=results, stopped=stopped)
        # kept on the log so a caller still has the final standings if the
        # match was cancelled or failed (they are also in match_end above)
        log.final = {"results": results, "hands": hand_num, "stopped": stopped}
    return {"results": results, "hands": hand_num, "stopped": stopped}


def make_bot_seats(bot_specs, config, use_docker=None, seed=None):
    """A seat per entry: BotSpecs become sandboxed BotSeats; ready-made Seat
    objects (e.g. remote human/MCP seats) are used as they are."""
    seed = config.seed if seed is None else seed
    seats = []
    for spec in bot_specs:
        if isinstance(spec, Seat):
            seats.append(spec)
        elif getattr(spec, "kind", None) in ("human", "mcp"):
            raise ValueError(f"{spec.name}: {spec.kind} seats need a running server (poker serve)")
        else:
            seats.append(BotSeat(spec, seed=derive_seed(seed, "bot", spec.name),
                                 time_limit_ms=config.time_limit_ms, use_docker=use_docker))
    return seats


def diagnostics(seats) -> dict:
    return {s.name: {k: v for k, v in s.summary().items() if k in ("errors_list", "stderr_tail")}
            for s in seats}


def run_match(bot_specs, config: MatchConfig, match_id: str = None,
              runs_dir: Path = DEFAULT_RUNS_DIR, use_docker: bool = None,
              listeners=(), pace_ms: int = 0, cancel=None) -> dict:
    """Play a match between 2-9 bots and write its event log + summary.
    Returns the summary dict (also saved as summary.json in the run dir)."""
    if not 2 <= len(bot_specs) <= 9:
        raise ValueError(f"A table needs 2-9 players, got {len(bot_specs)}")
    if config.seed is None:
        config.seed = new_seed()
    seed = config.seed
    match_id = match_id or f"match_{time.strftime('%Y%m%d_%H%M%S')}_{seed % 10**6:06d}"
    run_dir = Path(runs_dir) / match_id

    seats = make_bot_seats(bot_specs, config, use_docker)
    t0 = time.time()
    with EventLog(run_dir) as log:
        log.listeners.extend(listeners)
        outcome, interrupted = None, "error"
        try:
            outcome = play_match(seats, config, match_id, log,
                                 pacer=Pacer(pace_ms, cancel) if (pace_ms or cancel) else None)
        except Cancelled:
            interrupted = "cancelled"
            raise
        finally:
            summary = {
                "kind": "match", "match_id": match_id, "seed": seed, "config": config.to_dict(),
                "seats": [s.info() for s in seats],
                "results": (outcome or getattr(log, "final", {})).get("results"),
                "hands": (outcome or getattr(log, "final", {})).get("hands"),
                "stopped": outcome["stopped"] if outcome else interrupted,
                "duration_s": round(time.time() - t0, 2), "run_dir": str(run_dir),
                "bot_diagnostics": diagnostics(seats),
            }
            log.write_summary(summary)
    return summary
