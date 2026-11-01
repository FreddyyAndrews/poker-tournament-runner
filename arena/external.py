"""Heads-up matches against bots that deal on their own server, through an API:
GTO Wizard AI (arena/gtow.py) and Slumbot (arena/slumbot.py).

Both play the same game: heads-up, 200bb at 50/100, stacks reset every hand.
They deal and run every hand themselves: we only see our own hole cards, the
board and the action, and their cards once the hand is over. So our engine
can't play these hands. This module plays them through the API and writes the
same event log as a local match (arena/events.py), so the live view, replays,
analysis scripts and bb/100 stats all work unchanged.

Differences from a local match:
  - No seed: they shuffle, so a match can't be replayed or duplicated.
    verify checks chip accounting only (match_start has a "dealer").
  - Positions are dealt by the opponent's server, not by a button.
  - Each hand_end carries a luck-adjusted score from our side ("score"; GTO
    Wizard's AIVAT, or Slumbot's baseline) and the opponent's hole cards
    ("opp_hole"). The score is far less noisy than chips.

Each opponent is an Opponent with a backend that turns its API responses into
a View (below). Action histories use "f", "k", "c", "bX" (X = total bet on the
street) and "_" (end of a street); HandTracker replays them to recover every
action and build the state a bot sees.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import eval7

import arena
from arena import structures
from arena.events import DEFAULT_RUNS_DIR, EventLog
from arena.pacing import Cancelled, Pacer
from arena.seats import Seat
from arena.table import MATCH_LOG_MAX_ENTRIES, MatchConfig, bot_result, diagnostics, \
    make_bot_seats, rank, warm_up

STARTING_STACK, SB, BB = 20_000, 50, 100
BOARD_LEN = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
STREETS = ("preflop", "flop", "turn", "river")
USAGE_FILE = Path(os.environ.get("POKER_EXTERNAL_USAGE",
                                 Path(__file__).resolve().parent.parent / "data" / "external" / "usage.jsonl"))
_usage_lock = threading.Lock()


class ExternalError(Exception):
    pass


# The outside-bot proxy (arena/proxy.py, `poker proxy`): every call to an
# outside bot goes through it. It holds the API key and Slumbot account (this
# process never reads the repo's secrets file), turns each bot on or off, and
# queues requests so every process shares one limit. A request may wait there
# while others play, hence the long client timeout.
PROXY_URL = os.environ.get("POKER_PROXY_URL", "http://127.0.0.1:8766").rstrip("/")
PROXY_WAIT_S = 1800


def proxy_status() -> dict:
    """The proxy's /status. Raises ExternalError when it isn't running."""
    from arena import proxy
    try:
        return proxy.fetch_status(PROXY_URL)
    except (OSError, ValueError) as e:
        raise ExternalError(f"the outside-bot proxy isn't running at {PROXY_URL} ({e}). Start it "
                            "with `poker proxy`: it holds the API keys and shares Slumbot and GTO "
                            "Wizard fairly between everyone.") from None


def check_proxy(name: str) -> dict:
    """Raise ExternalError unless the proxy will forward to `name` now; returns its status."""
    status = proxy_status()["upstreams"][name]
    if not status["allowed"]:
        raise ExternalError(status["why"])
    return status


def http_error_detail(raw: str) -> str:
    """The message in an error body (the proxy's or the API's error_msg or
    detail) when it is JSON, else the raw text."""
    try:
        body = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(body, dict):
        msg = body.get("error_msg") or body.get("detail")
        if isinstance(msg, str):
            return msg
    return raw


@dataclass
class View:
    """One API response, as the match needs it. Fields an API doesn't give
    (None) are worked out from the action history instead."""
    handle: object                  # what the backend needs to act in this hand
    hand_ref: object                # the opponent's id for the hand (logged)
    we_are_sb: bool
    hole: list                      # our cards
    board: list
    history: list                   # tokens: f k c bX _
    over: bool
    legal: list = None              # our legal actions (f k c b), when it's our turn
    raise_range: dict = None        # {"min", "max"} raise-to totals
    winnings: float = None          # chips won, our side, once over
    total_pot: float = None         # to cross-check our tracking
    score: float = None             # luck-adjusted chips won, our side, once over
    opp_hole: list = field(default_factory=list)


class Opponent:
    """An external bot. Subclasses set the names and make the backend."""
    name = None                     # its name in our logs, and as a match spec
    title = None                    # for people
    score_kind = None               # what its luck-adjusted score is called
    default_workers = 1             # hands in play at once, by default ...
    max_workers = 1                 # ... and at most
    cap_setting = None              # the setting that changes daily_cap_per_bot

    def daily_cap_per_bot(self):
        """Hands one bot may play against it per day (None: no cap)."""
        return None

    def check_configured(self):
        """Raise ExternalError if it can't be played here (e.g. no API key)."""

    def backend(self):
        """An object with new_hand() -> View and act(handle, code, amount) -> View,
        safe to use from several threads."""
        raise NotImplementedError

    def info(self) -> dict:
        return {"name": self.name, "kind": "external", "time_limit_ms": None,
                "title": self.title, "score_kind": self.score_kind}


class HandTracker:
    """Heads-up betting state, rebuilt from the action history.

    seats: {name: table seat}; sb, bb: the names in each blind. Feed it the
    history tokens in order with apply(); it knows whose turn it is, the bets
    and stacks, and builds the state dict a bot sees (as engine/game.py does).
    """

    def __init__(self, seats: dict, sb: str, bb: str, stack: int, blinds: dict):
        self.seats, self.sb, self.bb, self.blinds = seats, sb, bb, blinds
        self.stacks = {n: stack for n in seats}
        self.bets = {n: 0 for n in seats}
        self.invested = {n: 0 for n in seats}
        self.folded = None
        self.street = 0
        self.action_log = []
        self.posts = []
        for name, kind, amount in ((sb, "small_blind", blinds["sb"]), (bb, "big_blind", blinds["bb"])):
            amount = self._put_in(name, amount)
            self.posts.append({"bot": name, "kind": kind[0] + "b", "amount": amount})
            self._log(name, kind, amount)
        self.current_bet = self.bets[bb]
        self.min_raise = blinds["bb"]
        self.to_act = sb

    @property
    def street_name(self):
        return STREETS[self.street]

    @property
    def pot(self):
        return sum(self.invested.values())

    def other(self, name):
        return self.bb if name == self.sb else self.sb

    def _put_in(self, name, amount):
        amount = min(amount, self.stacks[name])
        self.stacks[name] -= amount
        self.bets[name] += amount
        self.invested[name] += amount
        return amount

    def _log(self, name, action, amount):
        self.action_log.append({"seat": self.seats[name], "bot_id": name,
                                "action": action, "amount": amount})

    def apply(self, token: str):
        """Apply one history token. Returns the action as the engine would
        record it ({"bot", "action", "amount", "pot_after", "stack_after"}),
        or None for "_" (the street ended)."""
        if token == "_":
            self.street = min(self.street + 1, 3)
            self.bets = {n: 0 for n in self.bets}
            self.current_bet = 0
            self.min_raise = self.blinds["bb"]
            self.to_act = self.bb
            return None
        name = self.to_act
        if token == "f":
            self.folded = name
            action, amount = "fold", 0
        elif token == "k":
            action, amount = "check", 0
        elif token == "c":
            action, amount = "call", self._put_in(name, self.current_bet - self.bets[name])
        elif token.startswith("b"):
            total = int(float(token[1:]))
            self._put_in(name, total - self.bets[name])
            self.min_raise = max(self.min_raise, total - self.current_bet)
            self.current_bet = max(self.current_bet, total)
            action, amount = ("all_in" if self.stacks[name] == 0 else "raise"), total
        else:
            raise ExternalError(f"unknown action {token!r} in the action history")
        self._log(name, action, amount)
        self.to_act = self.other(name)
        return {"bot": name, "action": action, "amount": amount,
                "pot_after": self.pot, "stack_after": self.stacks[name]}

    def legal(self, name) -> tuple:
        """(legal actions, raise range) for `name`, for APIs that don't say."""
        owed = max(0, self.current_bet - self.bets[name])
        stack = self.stacks[name]
        legal = ["f", "c"] if owed else ["k"]
        if stack > owed and self.stacks[self.other(name)] > 0:
            hi = self.bets[name] + stack
            lo = min(self.current_bet + max(self.min_raise, self.blinds["bb"]), hi)
            return legal + ["b"], {"min": lo, "max": hi}
        return legal, None

    def state_for(self, name, hole, board, hand_id, hand_num, legal=None, raise_range=None) -> dict:
        """What `name` sees when it is their turn, in engine/game.py's format."""
        owed = max(0, self.current_bet - self.bets[name])
        min_raise_to = self.current_bet + self.min_raise
        if raise_range:
            min_raise_to = int(raise_range["min"])
        players = [{"seat": self.seats[n], "bot_id": n, "stack": self.stacks[n],
                    "state": "folded" if self.folded == n else "all_in" if self.stacks[n] == 0
                    else "active",
                    "is_folded": self.folded == n, "is_all_in": self.stacks[n] == 0,
                    "bet_this_street": self.bets[n], "hole_cards": None}
                   for n in sorted(self.seats, key=self.seats.get)]
        state = {
            "type": "action_request", "hand_id": hand_id, "street": self.street_name,
            "seat_to_act": self.seats[name], "your_bot_id": name, "pot": self.pot,
            "community_cards": board[:BOARD_LEN[self.street_name]],
            "current_bet": self.current_bet, "min_raise_to": min_raise_to,
            "amount_owed": owed, "can_check": owed == 0, "your_cards": hole,
            "your_stack": self.stacks[name], "your_bet_this_street": self.bets[name],
            "players": players, "action_log": list(self.action_log), "hand_num": hand_num,
            "blinds": dict(self.blinds), "button_seat": self.seats[self.sb],
            "sb_seat": self.seats[self.sb], "bb_seat": self.seats[self.bb],
        }
        if legal is not None:
            state["legal_actions"] = legal
        return state


def to_api_action(reply: dict, state: dict, legal: list, raise_range) -> tuple:
    """A bot's reply as an API action (code, amount), applying the same rules
    as engine/game.py: unknown actions fold, check/call pick whichever is due,
    raises snap up to the minimum and become all-in past the stack. These APIs
    won't let you fold when you could check, so that becomes a check."""
    act = str(reply.get("action", "fold")).lower().strip()
    try:
        amount = int(reply.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    owed = state["amount_owed"]
    passive = ("k", None) if owed == 0 else ("c", None)
    if act in ("raise", "all_in") and "b" in legal and raise_range:
        hi = int(raise_range["max"])
        if act == "all_in":
            return "b", hi
        return "b", min(max(amount, int(raise_range["min"])), hi)
    if act in ("check", "call", "raise", "all_in"):
        return passive
    return ("f", None) if "f" in legal else passive


class OpponentSeat(Seat):
    """The external bot's seat in the log. It never gets asked to act: its
    decisions arrive in the API's responses."""

    kind = "external"

    def __init__(self, opponent: Opponent):
        super().__init__(opponent.name, time_limit_ms=0)
        self.opponent = opponent
        self.decisions = 0

    def summary(self) -> dict:
        return {"decisions": self.decisions, "avg_think_ms": None, "max_think_ms": None,
                "timeouts": 0, "errors": 0, "restarts": 0, "disabled": False}

    def info(self) -> dict:
        return self.opponent.info()


class _Hand:
    """One hand's events and match-log entries. Emitted as they happen when
    hands play one at a time; buffered when several play at once, then
    flushed in hand order, so the log always reads hand by hand."""

    def __init__(self, num: int, emit=None):
        self.num = num
        self.live_emit = emit
        self.events = []
        self.actions = []          # this hand's match_action_log entries

    def emit(self, event_type, **data):
        if self.live_emit is not None:
            self.live_emit(event_type, **data)
        else:
            self.events.append((event_type, data))


class ExternalMatch:
    """Plays hands between one seat and an external bot, logging every event.

    Several hands may be in play at once (play_hand from worker threads): the
    seat is asked for one decision at a time, and what it sees in
    match_action_log is every hand already flushed plus the current one."""

    def __init__(self, seat: Seat, opponent: Opponent, backend, log: EventLog, match_id: str,
                 pacer=None):
        self.seat, self.backend, self.log, self.match_id, self.pacer = seat, backend, log, match_id, pacer
        self.opp = OpponentSeat(opponent)
        self.name = opponent.name
        self.seats = {seat.name: 0, opponent.name: 1}
        self.blinds = {"sb": SB, "bb": BB, "ante": 0}
        self.match_action_log = []
        self._seat_lock = threading.Lock()     # one decision at a time
        self._count_lock = threading.Lock()

    def _emit(self, event_type, **data):
        event = self.log.emit(event_type, **data)
        if self.pacer is not None:
            self.pacer(event)
        return event

    def flush(self, h: _Hand):
        for event_type, data in h.events:
            self._emit(event_type, **data)
        h.events = []
        self.match_action_log.extend(h.actions)
        del self.match_action_log[:-MATCH_LOG_MAX_ENTRIES]

    def play_hand(self, hand_num: int, view: View = None, live: bool = True) -> dict:
        """Play one hand, from its first View (or start one). live: emit events
        now; otherwise buffer them for flush(). Returns {"deltas", "score",
        "hand"} from our seat's side."""
        h = _Hand(hand_num, self._emit if live else None)
        view = view or self.backend.new_hand()
        me, opp = self.seat.name, self.name
        hand_id = f"{self.match_id}_h{hand_num:04d}"
        sb, bb = (me, opp) if view.we_are_sb else (opp, me)
        t = HandTracker(self.seats, sb=sb, bb=bb, stack=STARTING_STACK, blinds=self.blinds)
        h.emit("hand_start", hand=hand_num, hand_seed=None, level=1, blinds=dict(self.blinds),
               button=self.seats[t.sb], sb=self.seats[t.sb], bb=self.seats[t.bb],
               order=list(self.seats), seats=dict(self.seats),
               stacks={n: STARTING_STACK for n in self.seats}, hole={me: view.hole},
               ext_hand_id=view.hand_ref)
        for p in t.posts:
            h.emit("post", hand=hand_num, bot=p["bot"], seat=self.seats[p["bot"]],
                   kind=p["kind"], amount=p["amount"])
        # bots see posts in match_action_log just as in action_log
        h.actions.extend({"hand_num": hand_num, **a} for a in t.action_log)
        done = 0                    # history tokens already applied
        pending = None              # our decision, waiting for the API to apply it
        while True:
            for token in view.history[done:]:
                if token != "_":
                    if t.to_act == me:
                        state, reply = pending
                    else:
                        state, reply = t.state_for(opp, [], view.board, hand_id, hand_num), None
                applied = t.apply(token)
                if applied is None:
                    h.emit("deal", hand=hand_num, street=t.street_name,
                           board=view.board[:BOARD_LEN[t.street_name]])
                    continue
                self._decision(h, state, reply, applied)
            done = len(view.history)
            if view.over:
                break
            if t.to_act != me:
                raise ExternalError(f"{opp} hand {view.hand_ref}: expected our turn after "
                                    f"{view.history}")
            legal, raise_range = (view.legal, view.raise_range) if view.legal is not None \
                else t.legal(me)
            state = t.state_for(me, view.hole, view.board, hand_id, hand_num,
                                legal=legal, raise_range=raise_range)
            state["match_action_log"] = (self.match_action_log + h.actions)[-MATCH_LOG_MAX_ENTRIES:]
            with self._seat_lock:
                reply = self.seat.request_action(state)
            code, amount = to_api_action(reply, state, legal, raise_range)
            pending = ({k: v for k, v in state.items() if k != "match_action_log"}, reply)
            view = self.backend.act(view.handle, code, amount)
        return {**self._finish(h, t, view), "hand": h}

    def _decision(self, h: _Hand, state, reply, applied):
        name = applied["bot"]
        if reply is None:                       # the external bot: it did what it did
            with self._count_lock:
                self.opp.decisions += 1
            raw, extra = {"action": applied["action"], "amount": applied["amount"]}, {}
        else:
            raw = {"action": reply.get("action")}
            if "amount" in reply:
                raw["amount"] = reply["amount"]
            extra = reply
        h.emit("decision", hand=h.num, bot=name, seat=self.seats[name], street=state["street"],
               state=state, reply=raw,
               applied={k: applied[k] for k in ("action", "amount", "pot_after", "stack_after")},
               think_ms=extra.get("think_ms"), logs=extra.get("logs", ""),
               logs_truncated=extra.get("logs_truncated", False), debug=extra.get("debug"),
               debug_error=extra.get("debug_error"), error=extra.get("error"),
               error_detail=extra.get("error_detail"))
        h.actions.append({"hand_num": h.num, "seat": self.seats[name], "bot_id": name,
                          "action": applied["action"], "amount": applied["amount"]})

    def _finish(self, h: _Hand, t: HandTracker, view: View) -> dict:
        me, opp = self.seat.name, self.name
        board = view.board
        if view.total_pot is not None and abs(t.pot - view.total_pot) > 0.5:
            raise ExternalError(f"{opp} hand {view.hand_ref}: tracked pot {t.pot} but the API "
                                f"says {view.total_pot}")
        hole = {me: view.hole, opp: view.opp_hole}
        if t.folded is None:
            # all-in before the river: the rest of the board is dealt without action
            while t.street < 3 and len(board) > BOARD_LEN[t.street_name]:
                t.street += 1
                h.emit("deal", hand=h.num, street=t.street_name,
                       board=board[:BOARD_LEN[t.street_name]])
            ranks = {}
            for n in self.seats:
                score = eval7.evaluate([eval7.Card(c) for c in hole[n] + board])
                ranks[n] = str(eval7.handtype(score))
            h.emit("showdown", hand=h.num, board=board, reveal=hole, ranks=ranks)
        won = int(round(view.winnings))
        delta = {me: won, opp: -won}
        if won == 0 and t.folded is None:       # split pot
            for n in self.seats:
                h.emit("award", hand=h.num, bot=n, amount=t.invested[n], pot="main")
        else:
            winner = me if won > 0 else opp
            h.emit("award", hand=h.num, bot=winner, amount=t.invested[winner] + abs(won), pot="main")
        h.emit("hand_end", hand=h.num, board=board, pot=t.pot,
               stacks={n: STARTING_STACK + delta[n] for n in self.seats}, deltas=delta,
               score={me: view.score}, opp_hole={opp: view.opp_hole}, ext_hand_id=view.hand_ref)
        return {"deltas": delta, "score": view.score}


# -- usage: hands played against each external bot, for quotas -------------------

def record_usage(opponent: Opponent, bot: str, hands: int, match_id: str):
    if not hands:
        return
    entry = {"date": time.strftime("%Y-%m-%d"), "time": time.strftime("%H:%M:%S"),
             "opponent": opponent.name, "bot": bot, "hands": hands, "match": match_id}
    with _usage_lock:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_FILE, "a") as fh:
            fh.write(json.dumps(entry) + "\n")


def usage(opponent: str, bot: str = None, since: str = None) -> int:
    """Hands played against `opponent` (by `bot`, on or after date `since`)."""
    if not USAGE_FILE.is_file():
        return 0
    total = 0
    for line in USAGE_FILE.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e["opponent"] == opponent and (bot is None or e["bot"] == bot) and \
                (since is None or e["date"] >= since):
            total += e["hands"]
    return total


def check_budget(opponent: Opponent, bot: str, hands: int):
    """Raise ExternalError if `bot` playing `hands` more hands today would go
    over the opponent's daily cap per bot."""
    cap = opponent.daily_cap_per_bot()
    if cap is None:
        return
    today = usage(opponent.name, bot, since=time.strftime("%Y-%m-%d"))
    if today + hands > cap:
        raise ExternalError(
            f"{bot} has played {today} hands against {opponent.title} today; {hands} more would "
            f"pass the daily cap of {cap} per bot (shared quota). Play fewer hands, use slumbot, "
            f"or raise the cap with {opponent.cap_setting}.")


def score_stats(scores: list) -> dict:
    """Luck-adjusted bb/100 with a 95% confidence interval."""
    scores = [s for s in scores if s is not None]
    n = len(scores)
    if not n:
        return {"hands": 0, "bb_per_100": None, "ci95": None}
    per_hand = [s / BB for s in scores]
    mean = sum(per_hand) / n
    var = sum((x - mean) ** 2 for x in per_hand) / (n - 1) if n > 1 else 0.0
    return {"hands": n, "bb_per_100": round(mean * 100, 2),
            "ci95": round(1.96 * (var / n) ** 0.5 * 100, 2) if n > 1 else None}


def config_for(hands: int, time_limit_ms: int = None) -> MatchConfig:
    return MatchConfig(mode="cash", hands=hands, starting_stack=STARTING_STACK,
                       structure=structures.flat(SB, BB), seed=None, time_limit_ms=time_limit_ms)


def default_workers(opponent: Opponent, seat: Seat, pace_ms: int = 0) -> int:
    """Hands in play at once: one when someone watches or plays it live
    (humans, LLMs), otherwise the opponent's default."""
    return 1 if pace_ms or seat.kind in ("human", "mcp") else opponent.default_workers


def play_external_match(seat: Seat, opponent: Opponent, config: MatchConfig, match_id: str,
                        log: EventLog, backend, pacer=None, workers: int = 1) -> dict:
    """Play a match against an external bot with an already-built seat,
    `workers` hands at a time. Returns {"results", "hands", "stopped",
    "score"}, like table.play_match."""
    if not 1 <= workers <= opponent.max_workers:
        raise ValueError(f"workers must be 1-{opponent.max_workers} against {opponent.name}")
    match = ExternalMatch(seat, opponent, backend, log, match_id, pacer)
    names = list(match.seats)
    bankroll = {n: STARTING_STACK for n in names}
    bb_sum = {n: 0.0 for n in names}
    scores = []
    hand_num, stopped = 0, None
    t0 = time.time()
    seats = [seat, match.opp]
    pool = ThreadPoolExecutor(workers) if workers > 1 else None
    try:
        warm = warm_up([seat])
        first = backend.new_hand()
        log.emit("match_start", match_id=match_id, seed=None, config=config.to_dict(),
                 seats=[{**seat.info(), "seat": 0, "warmup": warm[seat.name]},
                        {**match.opp.info(), "seat": 1}],
                 dealer=opponent.name, score_kind=opponent.score_kind, engine_hash=None,
                 arena_version=arena.__version__, workers=workers)
        match._emit("level", hand=0, level=1, sb=SB, bb=BB, ante=0)
        futures, next_start = {}, 0
        while hand_num < config.hands:
            if pool is None:
                info = match.play_hand(hand_num, first if hand_num == 0 else None)
            else:
                while next_start < config.hands and len(futures) < workers:
                    futures[next_start] = pool.submit(match.play_hand, next_start,
                                                      first if next_start == 0 else None, False)
                    next_start += 1
                info = futures.pop(hand_num).result()
            match.flush(info["hand"])
            for n in names:
                bankroll[n] += info["deltas"][n]
                bb_sum[n] += info["deltas"][n] / BB
            scores.append(info["score"])
            hand_num += 1
        stopped = "hand_limit"
    except Cancelled:
        stopped = "cancelled"
        raise
    except BaseException as e:
        stopped = "error: " + repr(e)
        raise
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        seat.stop()
        places = rank(names, key=lambda n: bankroll[n])
        results = [bot_result(s, places[s.name], bankroll[s.name], STARTING_STACK, hand_num,
                              bb_sum[s.name]) for s in seats]
        results.sort(key=lambda r: (r["place"], r["bot"]))
        stats = {seat.name: {**score_stats(scores), "kind": opponent.score_kind}}
        log.emit("match_end", hands=hand_num, duration_s=round(time.time() - t0, 2),
                 results=results, stopped=stopped, score=stats)
        log.final = {"results": results, "hands": hand_num, "stopped": stopped, "score": stats}
    return log.final


def run_external_match(opponent: Opponent, bot_spec, hands: int, match_id: str = None,
                       runs_dir: Path = DEFAULT_RUNS_DIR, use_docker: bool = None, listeners=(),
                       pace_ms: int = 0, cancel=None, backend=None, time_limit_ms: int = None,
                       workers: int = None) -> dict:
    """Play `hands` hands of `bot_spec` (a BotSpec or a ready-made Seat) against
    `opponent` and write the event log + summary. workers: hands at a time
    (default: see default_workers). Returns the summary."""
    config = config_for(hands, time_limit_ms)
    if backend is None:
        opponent.check_configured()
        backend = opponent.backend()
    prefix = {"gto_wizard": "gtow"}.get(opponent.name, opponent.name)
    match_id = match_id or f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}"
    run_dir = Path(runs_dir) / match_id
    # bots are seeded per match; there is no match seed, so use a fresh one
    seat = make_bot_seats([bot_spec], config, use_docker,
                          seed=int.from_bytes(os.urandom(4), "big"))[0]
    workers = workers or default_workers(opponent, seat, pace_ms)
    check_budget(opponent, seat.name, hands)
    t0 = time.time()
    with EventLog(run_dir) as log:
        log.listeners.extend(listeners)
        outcome, interrupted = None, "error"
        try:
            outcome = play_external_match(seat, opponent, config, match_id, log, backend,
                                          pacer=Pacer(pace_ms, cancel) if (pace_ms or cancel) else None,
                                          workers=workers)
        except Cancelled:
            interrupted = "cancelled"
            raise
        finally:
            final = outcome or getattr(log, "final", {})
            record_usage(opponent, seat.name, final.get("hands") or 0, match_id)
            summary = {
                "kind": "match", "dealer": opponent.name, "match_id": match_id, "seed": None,
                "config": config.to_dict(), "seats": [seat.info(), opponent.info()],
                "results": final.get("results"), "hands": final.get("hands"),
                "score": final.get("score"),
                "stopped": outcome["stopped"] if outcome else interrupted,
                "duration_s": round(time.time() - t0, 2), "run_dir": str(run_dir),
                "bot_diagnostics": diagnostics([seat]), "workers": workers,
            }
            log.write_summary(summary)
    return summary


# -- the opponents ------------------------------------------------------------

def opponents() -> dict:
    """Every external bot, by name."""
    from arena import gtow, slumbot
    return {o.name: o for o in (gtow.GTOWizard(), slumbot.Slumbot())}


def find(spec):
    """The Opponent a match spec names (e.g. "slumbot"), or None."""
    if not isinstance(spec, str):
        return None
    return opponents().get(spec.strip().lower())


def split_specs(specs: list):
    """For a match spec list naming an external bot: (opponent, the other
    player's spec, its index). Raises ValueError unless it is exactly one
    player against one external bot."""
    found = [(find(s), i) for i, s in enumerate(specs) if find(s)]
    others = [(s, i) for i, s in enumerate(specs) if not find(s)]
    name = found[0][0].title if found else "This opponent"
    if len(specs) != 2 or len(found) != 1:
        raise ValueError(f"{name} plays heads-up only: give exactly one other player "
                         f"(e.g. `poker match my_bot {found[0][0].name if found else 'slumbot'}`)")
    return found[0][0], others[0][0], others[0][1]
