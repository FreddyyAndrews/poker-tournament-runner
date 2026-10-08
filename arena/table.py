"""Tables and matches.

A Table seats players at fixed seat numbers for the whole match (seat numbers
never shift when someone busts), moves the button to the next occupied seat
each hand, applies the blind structure by hand count, and plays hands through
the engine, logging every event.

The engine itself only knows about the players dealt into a hand (seats 0..k-1
in table order). The table translates engine seats to table seats everywhere:
in the state bots see, and in the event log.
"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import arena
from arena import structures
from arena.events import DEFAULT_RUNS_DIR, EventLog
from arena.seats import BotSeat
from arena.seeds import derive_seed, new_seed
from engine.game import PokerEngine

MATCH_LOG_MAX_ENTRIES = 200
MAX_STEPS_PER_HAND = 1000

ENGINE_PATH = Path(__file__).resolve().parent.parent / "engine" / "game.py"


def engine_hash() -> str:
    return hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest()[:16]


@dataclass
class MatchConfig:
    mode: str = "fixed"                  # "fixed": play `hands` hands; "freezeout": until one is left
    hands: int = 100                     # fixed: hands to play; freezeout: safety cap
    starting_stack: int = 10_000
    structure: structures.Structure = field(default_factory=lambda: structures.flat(50, 100))
    seed: int = None
    time_limit_ms: int = None            # per decision; None = seat default (bots: 2000)

    def __post_init__(self):
        if self.mode not in ("fixed", "freezeout"):
            raise ValueError(f"mode must be 'fixed' or 'freezeout', got {self.mode!r}")
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


def _rank(names, key) -> dict:
    """Places for `names`, best first by key (desc); equal keys share a place."""
    ordered = sorted(names, key=lambda n: -key(n))
    places, prev, place = {}, None, 0
    for i, n in enumerate(ordered):
        if key(n) != prev:
            place, prev = i + 1, key(n)
        places[n] = place
    return places


class Table:
    def __init__(self, seats, stacks, structure, seed, log, match_id):
        self.seats      = list(seats)                       # index = table seat
        self.by_name    = {s.name: s for s in self.seats}
        self.seat_of    = {s.name: i for i, s in enumerate(self.seats)}
        self.stacks     = dict(stacks)
        self.structure  = structure
        self.seed       = seed
        self.log        = log
        self.match_id   = match_id
        self.button     = None
        self.level_idx  = None
        self.match_action_log = []

    # -- seating -------------------------------------------------------------

    def occupied(self) -> list:
        """Names of players with chips, in table-seat order."""
        return [s.name for s in self.seats if self.stacks[s.name] > 0]

    def _move_button(self):
        n = len(self.seats)
        if self.button is None:
            start = derive_seed(self.seed, "button") % n
            candidates = [(start + k) % n for k in range(n)]
        else:
            candidates = [(self.button + k) % n for k in range(1, n + 1)]
        for t in candidates:
            if self.stacks[self.seats[t].name] > 0:
                self.button = t
                return

    # -- playing a hand ------------------------------------------------------

    def play_hand(self, hand_num: int) -> dict:
        """Play one hand. Returns {"busted": [names], "stacks_before": {...}}."""
        order = self.occupied()
        e2t = [self.seat_of[name] for name in order]         # engine seat -> table seat
        self._move_button()
        lvl_idx = self.structure.level_index(hand_num)
        lvl = self.structure.level(hand_num)
        if lvl_idx != self.level_idx:
            self.level_idx = lvl_idx
            self.log.emit("level", hand=hand_num, level=lvl_idx + 1,
                          sb=lvl.sb, bb=lvl.bb, ante=lvl.ante)

        hand_seed = derive_seed(self.seed, "hand", hand_num)
        stacks_before = {name: self.stacks[name] for name in order}
        engine = PokerEngine(
            hand_id=f"{self.match_id}_h{hand_num:04d}",
            bot_ids=order,
            dealer_seat=order.index(self.seats[self.button].name),
            starting_stacks=stacks_before,
            seed=hand_seed,
            small_blind=lvl.sb, big_blind=lvl.bb, ante=lvl.ante,
            hand_num=hand_num,
        )
        state = engine.start_hand()

        self.log.emit(
            "hand_start", hand=hand_num, hand_seed=hand_seed, level=lvl_idx + 1,
            blinds={"sb": lvl.sb, "bb": lvl.bb, "ante": lvl.ante},
            button=self.button, sb=e2t[engine._sb_seat()], bb=e2t[engine._bb_seat()],
            order=order, seats={name: self.seat_of[name] for name in order},
            stacks=stacks_before,
            hole={p.bot_id: [str(c) for c in p.hole_cards] for p in engine.players},
        )
        seen = self._log_engine_events(engine, 0, hand_num, e2t)

        steps = 0
        while state["type"] == "action_request":
            seat = state["seat_to_act"]
            name = order[seat]
            seen_state = self._for_bot(state, e2t)
            reply = self.by_name[name].request_action(seen_state)
            raw = {"action": reply.get("action")}
            if "amount" in reply:
                raw["amount"] = reply["amount"]

            state = engine.apply_action(seat, raw)
            applied = next(e for e in engine.events[seen:] if e["type"] == "action")
            self.log.emit(
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
        self.log.emit(
            "hand_end", hand=hand_num, board=result["community_cards"], pot=result["pot"],
            stacks={name: self.stacks[name] for name in order},
            deltas={name: self.stacks[name] - stacks_before[name] for name in order},
        )
        busted = [name for name in order if self.stacks[name] == 0]
        return {"busted": busted, "stacks_before": stacks_before, "bb": lvl.bb, "order": order}

    def _for_bot(self, state, e2t) -> dict:
        """The engine's state with table seat numbers and the match history."""
        s = dict(state)
        for key in ("seat_to_act", "button_seat", "sb_seat", "bb_seat"):
            s[key] = e2t[s[key]]
        s["players"] = [{**p, "seat": e2t[p["seat"]]} for p in state["players"]]
        s["action_log"] = [{**a, "seat": e2t[a["seat"]]} for a in state["action_log"]]
        s["match_action_log"] = self.match_action_log[-MATCH_LOG_MAX_ENTRIES:]
        return s

    def _log_engine_events(self, engine, start, hand_num, e2t, skip_actions=False) -> int:
        """Translate engine events from index `start` into log events."""
        for e in engine.events[start:]:
            t = e["type"]
            if t == "blind":
                kind = {"ante": "ante", "small_blind": "sb", "big_blind": "bb"}[e["action"]]
                self.log.emit("post", hand=hand_num, bot=e["bot_id"], seat=e2t[e["seat"]],
                              kind=kind, amount=e["amount"])
            elif t == "street_start" and e["street"] != "preflop":
                self.log.emit("deal", hand=hand_num, street=e["street"],
                              board=e["community_cards"])
            elif t == "showdown":
                self.log.emit("showdown", hand=hand_num, board=e["community_cards"],
                              reveal=e["revealed"], ranks=e["hand_strengths"])
                for w in e["winners"]:
                    self.log.emit("award", hand=hand_num, bot=w["bot_id"], amount=w["amount"],
                                  pot=w["pot_type"])
            elif t == "uncontested_win":
                self.log.emit("award", hand=hand_num, bot=e["bot_id"], amount=e["amount"],
                              pot="main")
            elif t == "action" and not skip_actions:
                raise RuntimeError("unexpected engine action event outside a decision")
        return len(engine.events)


def play_match(seats, config: MatchConfig, match_id: str, log) -> dict:
    """Play a match with already-built seats, emitting events to `log`.
    Returns {"results", "hands", "stopped"}. Used by run_match and by verify
    (which replays with recorded decisions instead of live bots)."""
    seed = config.seed
    table = Table(seats, {s.name: config.starting_stack for s in seats},
                  config.structure, seed, log, match_id)
    places, busted_on = {}, {}
    hands_dealt = {s.name: 0 for s in seats}
    bb_sum = {s.name: 0.0 for s in seats}
    hand_num, stopped = 0, None
    t0 = time.time()
    try:
        for s in seats:
            s.start()
        with ThreadPoolExecutor(max_workers=len(seats)) as pool:
            warm = dict(zip([s.name for s in seats], pool.map(lambda s: s.warmup(), seats)))
        log.emit("match_start", match_id=match_id, seed=seed, config=config.to_dict(),
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
                bb_sum[name] += (table.stacks[name] - info["stacks_before"][name]) / info["bb"]
            if info["busted"]:
                remaining = len(table.occupied())
                ranked = _rank(info["busted"], key=lambda n: info["stacks_before"][n])
                for name in sorted(ranked, key=lambda n: (-ranked[n], table.seat_of[n])):
                    places[name] = remaining + ranked[name]
                    busted_on[name] = hand_num
                    log.emit("elimination", hand=hand_num, bot=name, place=places[name])
            hand_num += 1
    except BaseException as e:
        stopped = "error: " + repr(e)
        raise
    finally:
        for s in seats:
            s.stop()
        for name, r in _rank(table.occupied(), key=lambda n: table.stacks[n]).items():
            places[name] = r
        results = []
        for s in seats:
            n = s.name
            summary = s.summary()
            results.append({
                "bot": n, "place": places.get(n), "final_stack": table.stacks[n],
                "delta": table.stacks[n] - config.starting_stack,
                "hands_played": hands_dealt[n],
                "bb_per_100": round(bb_sum[n] * 100 / hands_dealt[n], 2) if hands_dealt[n] else 0.0,
                "busted_on_hand": busted_on.get(n),
                **{k: summary.get(k) for k in RUNTIME_STATS},
            })
        results.sort(key=lambda r: (r["place"] if r["place"] is not None else 99, r["bot"]))
        log.emit("match_end", hands=hand_num, duration_s=round(time.time() - t0, 2),
                 results=results, stopped=stopped)
    return {"results": results, "hands": hand_num, "stopped": stopped}


# Per-bot numbers that depend on the live run (timing, process health), not on
# the cards and decisions. verify ignores these.
RUNTIME_STATS = ("decisions", "avg_think_ms", "max_think_ms", "timeouts", "errors",
                 "restarts", "disabled")


def run_match(bot_specs, config: MatchConfig, match_id: str = None,
              runs_dir: Path = DEFAULT_RUNS_DIR, use_docker: bool = None,
              listeners=()) -> dict:
    """Play a match between 2-9 bots and write its event log + summary.
    Returns the summary dict (also saved as summary.json in the run dir)."""
    if not 2 <= len(bot_specs) <= 9:
        raise ValueError(f"A table needs 2-9 players, got {len(bot_specs)}")
    if config.seed is None:
        config.seed = new_seed()
    seed = config.seed
    match_id = match_id or f"match_{time.strftime('%Y%m%d_%H%M%S')}_{seed % 10**6:06d}"
    run_dir = Path(runs_dir) / match_id

    seats = [BotSeat(spec, seed=derive_seed(seed, "bot", spec.name),
                     time_limit_ms=config.time_limit_ms, use_docker=use_docker)
             for spec in bot_specs]
    t0 = time.time()
    with EventLog(run_dir) as log:
        log.listeners.extend(listeners)
        outcome = None
        try:
            outcome = play_match(seats, config, match_id, log)
        finally:
            summary = {
                "match_id": match_id, "seed": seed, "config": config.to_dict(),
                "seats": [s.info() for s in seats],
                "results": outcome["results"] if outcome else None,
                "hands": outcome["hands"] if outcome else None,
                "stopped": outcome["stopped"] if outcome else "error",
                "duration_s": round(time.time() - t0, 2), "run_dir": str(run_dir),
                "bot_diagnostics": {s.name: {k: v for k, v in s.summary().items()
                                             if k in ("errors_list", "stderr_tail")}
                                    for s in seats},
            }
            log.write_summary(summary)
    return summary
