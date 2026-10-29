"""Heads-up matches against GTO Wizard AI, through its benchmark API.

GTO Wizard deals and runs every hand on its own server: we only see our own
hole cards, the board and the action, and its cards once the hand is over. So
our engine can't play these hands. Instead this module plays them through the
API and writes the same event log as a local match (arena/events.py), so the
live view, replays, analysis scripts and bb/100 stats all work unchanged.

Differences from a local match:
  - No seed: GTO Wizard shuffles, so a match can't be replayed or duplicated.
    verify checks chip accounting only (match_start has dealer "gto_wizard").
  - The game is fixed: heads-up, 200bb at 50/100, stacks reset every hand
    (our cash mode). Positions are dealt by GTO Wizard, not by a button.
  - Each hand_end carries GTO Wizard's AIVAT score (luck-adjusted chips won,
    from our side) and its hole cards; AIVAT is far less noisy than chips.

The API: POST /hands starts a hand, POST /hands/<id>/act sends our action, and
every response is the full state with GTO Wizard's replies already applied.
Its action_history uses "f", "k", "c", "bX" (X = total bet on the street) and
"_" (end of a street); we replay it with HandTracker to recover each action.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import eval7

import arena
from arena import structures
from arena.events import DEFAULT_RUNS_DIR, EventLog
from arena.pacing import Cancelled, Pacer
from arena.seats import Seat
from arena.table import MATCH_LOG_MAX_ENTRIES, MatchConfig, bot_result, diagnostics, \
    make_bot_seats, rank, warm_up

API_URL = os.environ.get("GTO_WIZARD_API_URL", "https://researcher.gtowizard.com")
GAME_NAME = "HUNL 200BB"
NAME = "gto_wizard"                 # GTO Wizard's name in our logs
API_NAME = "GTO Wizard"             # ... and in the API's player list
STARTING_STACK, SB, BB = 20_000, 50, 100
DEFAULT_WORKERS = 5                 # hands in play at once (as GTO Wizard's own client)
MAX_WORKERS = 20                    # GTO Wizard's limit

REPO_ENV = Path(__file__).resolve().parent.parent / ".env"
BOARD_LEN = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
STREETS = ("preflop", "flop", "turn", "river")


class GTOWizardError(Exception):
    pass


def api_key() -> str:
    """GTO_WIZARD_API_KEY from the environment, or from the repo's .env."""
    key = os.environ.get("GTO_WIZARD_API_KEY")
    if not key and REPO_ENV.is_file():
        for line in REPO_ENV.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GTO_WIZARD_API_KEY":
                key = value.strip().strip("'\"")
    if not key:
        raise GTOWizardError("GTO_WIZARD_API_KEY is not set (in the environment or .env)")
    return key


class Client:
    """The few API calls we need, with retries on rate limits and server errors."""

    RETRY_STATUS = (429, 500, 502, 503, 504)

    def __init__(self, key: str = None, url: str = API_URL, retries: int = 5, timeout_s: float = 30):
        self.key = key or api_key()
        self.url = url.rstrip("/")
        self.retries = retries
        self.timeout_s = timeout_s

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        # the API rejects urllib's default User-Agent with a 403
        headers = {"X-API-Key": self.key, "Content-Type": "application/json",
                   "User-Agent": f"poker-tournament-runner/{arena.__version__}"}
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code not in self.RETRY_STATUS or attempt == self.retries:
                    raise GTOWizardError(f"{method} {path}: HTTP {e.code} {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == self.retries:
                    raise GTOWizardError(f"{method} {path}: {e}") from None
            time.sleep(min(2 ** attempt, 30))

    def new_hand(self) -> dict:
        return self._request("POST", "/hands", {"game_name": GAME_NAME})

    def act(self, hand_id: int, action: str, amount: int = None) -> dict:
        body = {"action": action} if amount is None else {"action": action, "amount": int(amount)}
        return self._request("POST", f"/hands/{hand_id}/act", body)


def _cards(s: str) -> list:
    return [s[i:i + 2] for i in range(0, len(s or ""), 2)]


class HandTracker:
    """Heads-up betting state, rebuilt from the API's action history.

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
            self.street += 1
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
            raise GTOWizardError(f"unknown action {token!r} in GTO Wizard's history")
        self._log(name, action, amount)
        self.to_act = self.other(name)
        return {"bot": name, "action": action, "amount": amount,
                "pot_after": self.pot, "stack_after": self.stacks[name]}

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
    raises snap up to the minimum and become all-in past the stack. GTO Wizard
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


class GTOWizardSeat(Seat):
    """GTO Wizard's seat in the log. It never gets asked to act: its decisions
    arrive in the API's responses."""

    kind = "gto_wizard"

    def __init__(self):
        super().__init__(NAME, time_limit_ms=0)
        self.decisions = 0

    def summary(self) -> dict:
        return {"decisions": self.decisions, "avg_think_ms": None, "max_think_ms": None,
                "timeouts": 0, "errors": 0, "restarts": 0, "disabled": False}

    def info(self) -> dict:
        return {"name": NAME, "kind": self.kind, "time_limit_ms": None, "api": API_URL,
                "game_name": GAME_NAME}


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


class GTOWizardMatch:
    """Plays hands between one seat and GTO Wizard, logging every event.

    Several hands may be in play at once (play_hand from worker threads): the
    seat is asked for one decision at a time, and what it sees in
    match_action_log is every hand already flushed plus the current one."""

    def __init__(self, seat: Seat, client: Client, log: EventLog, match_id: str, pacer=None):
        self.seat, self.client, self.log, self.match_id, self.pacer = seat, client, log, match_id, pacer
        self.gtow = GTOWizardSeat()
        self.seats = {seat.name: 0, NAME: 1}
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

    def _names(self, resp):
        """Each player in a response, by our name for them."""
        return {(NAME if p["name"] == API_NAME else self.seat.name): p for p in resp["game_state"]["players"]}

    def play_hand(self, hand_num: int, resp: dict = None, live: bool = True) -> dict:
        """Play one hand, from a POST /hands response (or start one). live:
        emit events now; otherwise buffer them for flush(). Returns
        {"deltas", "aivat", "hand"} from our seat's side."""
        h = _Hand(hand_num, self._emit if live else None)
        resp = resp or self.client.new_hand()
        me = self.seat.name
        hand_id = f"{self.match_id}_h{hand_num:04d}"
        api_id = resp["hand_id"]
        players = self._names(resp)
        sb = next(n for n, p in players.items() if p["position"] == "SB")
        t = HandTracker(self.seats, sb=sb, bb=self.gtow.name if sb == me else me,
                        stack=STARTING_STACK, blinds=self.blinds)
        hole = _cards(players[me]["hole_cards"])
        h.emit("hand_start", hand=hand_num, hand_seed=None, level=1, blinds=dict(self.blinds),
               button=self.seats[t.sb], sb=self.seats[t.sb], bb=self.seats[t.bb],
               order=list(self.seats), seats=dict(self.seats),
               stacks={n: STARTING_STACK for n in self.seats}, hole={me: hole},
               gtow_hand_id=api_id)
        for p in t.posts:
            h.emit("post", hand=hand_num, bot=p["bot"], seat=self.seats[p["bot"]],
                   kind=p["kind"], amount=p["amount"])
        # bots see posts in match_action_log just as in action_log
        h.actions.extend({"hand_num": hand_num, **a} for a in t.action_log)
        done = 0                    # history tokens already applied
        pending = None              # our decision, waiting for the API to apply it
        while True:
            gs = resp["game_state"]
            board = _cards(gs["board_cards"])
            for token in gs["action_history"][done:]:
                if token != "_":
                    if t.to_act == me:
                        state, reply = pending
                    else:
                        state, reply = t.state_for(NAME, [], board, hand_id, hand_num), None
                applied = t.apply(token)
                if applied is None:
                    h.emit("deal", hand=hand_num, street=t.street_name,
                           board=board[:BOARD_LEN[t.street_name]])
                    continue
                self._decision(h, state, reply, applied)
            done = len(gs["action_history"])
            if gs["is_hand_over"]:
                break
            if gs["player_to_act"] != "user" or t.to_act != me:
                raise GTOWizardError(f"hand {api_id}: expected our turn, API says "
                                     f"{gs['player_to_act']!r}")
            state = t.state_for(me, hole, board, hand_id, hand_num,
                                legal=gs["legal_actions"], raise_range=gs["raise_range"])
            state["match_action_log"] = (self.match_action_log + h.actions)[-MATCH_LOG_MAX_ENTRIES:]
            with self._seat_lock:
                reply = self.seat.request_action(state)
            code, amount = to_api_action(reply, state, gs["legal_actions"], gs["raise_range"])
            pending = ({k: v for k, v in state.items() if k != "match_action_log"}, reply)
            resp = self.client.act(api_id, code, amount)
        return {**self._finish(h, t, resp), "hand": h}

    def _decision(self, h: _Hand, state, reply, applied):
        name = applied["bot"]
        if reply is None:                       # GTO Wizard: it did what it did
            with self._count_lock:
                self.gtow.decisions += 1
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

    def _finish(self, h: _Hand, t: HandTracker, resp) -> dict:
        me = self.seat.name
        gs = resp["game_state"]
        board = _cards(gs["board_cards"])
        players = self._names(resp)
        hole = {n: _cards(p["hole_cards"]) for n, p in players.items()}
        won = gs["winnings"]                    # chips, our side
        if abs(t.pot - gs["total_pot"]) > 0.5:
            raise GTOWizardError(f"hand {resp['hand_id']}: tracked pot {t.pot} but the API "
                                 f"says {gs['total_pot']}")
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
        delta = {me: int(round(won)), NAME: -int(round(won))}
        if delta[me] == 0 and t.folded is None:   # split pot
            for n in self.seats:
                h.emit("award", hand=h.num, bot=n, amount=t.invested[n], pot="main")
        else:
            winner = me if delta[me] > 0 else NAME
            h.emit("award", hand=h.num, bot=winner,
                   amount=t.invested[winner] + abs(delta[me]), pot="main")
        h.emit("hand_end", hand=h.num, board=board, pot=t.pot,
               stacks={n: STARTING_STACK + delta[n] for n in self.seats}, deltas=delta,
               aivat={me: gs["aivat_score"]}, gtow_hole=hole[NAME],
               gtow_hand_id=resp["hand_id"])
        return {"deltas": delta, "aivat": gs["aivat_score"]}


def check_game(resp: dict):
    game = resp["game"]
    sb, bb = sorted(int(b) for b in game["blinds"])
    if (int(game["starting_stack"]), sb, bb) != (STARTING_STACK, SB, BB) or \
            not game["stack_reset_per_hand"]:
        raise GTOWizardError(f"GTO Wizard's game changed: {game}; arena/gtow.py assumes "
                             f"{STARTING_STACK} stacks at {SB}/{BB}, reset every hand")


def is_gtow(spec) -> bool:
    return isinstance(spec, str) and spec.strip().lower() == NAME


def split_specs(specs: list):
    """For a match spec list naming gto_wizard: (the other player, its index).
    Raises ValueError unless it is exactly one player against GTO Wizard."""
    others = [(s, i) for i, s in enumerate(specs) if not is_gtow(s)]
    if len(specs) != 2 or len(others) != 1:
        raise ValueError("GTO Wizard plays heads-up only: give exactly one other player "
                         "(e.g. `poker match my_bot gto_wizard`)")
    return others[0]


def default_workers(seat: Seat, pace_ms: int = 0) -> int:
    """Hands in play at once: one when someone watches or plays it live
    (humans, LLMs), otherwise DEFAULT_WORKERS."""
    return 1 if pace_ms or seat.kind in ("human", "mcp") else DEFAULT_WORKERS


def config_for(hands: int, time_limit_ms: int = None) -> MatchConfig:
    return MatchConfig(mode="cash", hands=hands, starting_stack=STARTING_STACK,
                       structure=structures.flat(SB, BB), seed=None, time_limit_ms=time_limit_ms)


def play_gtow_match(seat: Seat, config: MatchConfig, match_id: str, log: EventLog,
                    client: Client, pacer=None, workers: int = 1) -> dict:
    """Play a match against GTO Wizard with an already-built seat, `workers`
    hands at a time. Returns {"results", "hands", "stopped", "aivat"}, like
    table.play_match."""
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be 1-{MAX_WORKERS} (GTO Wizard's limit on hands at once)")
    match = GTOWizardMatch(seat, client, log, match_id, pacer)
    names = list(match.seats)
    bankroll = {n: STARTING_STACK for n in names}
    bb_sum = {n: 0.0 for n in names}
    aivat = []
    hand_num, stopped = 0, None
    t0 = time.time()
    seats = [seat, match.gtow]
    pool = ThreadPoolExecutor(workers) if workers > 1 else None
    try:
        warm = warm_up([seat])
        first = client.new_hand()
        check_game(first)
        log.emit("match_start", match_id=match_id, seed=None, config=config.to_dict(),
                 seats=[{**seat.info(), "seat": 0, "warmup": warm[seat.name]},
                        {**match.gtow.info(), "seat": 1, "game": first["game"]}],
                 dealer="gto_wizard", engine_hash=None, arena_version=arena.__version__,
                 workers=workers)
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
            aivat.append(info["aivat"])
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
        stats = aivat_stats(aivat)
        log.emit("match_end", hands=hand_num, duration_s=round(time.time() - t0, 2),
                 results=results, stopped=stopped, aivat={seat.name: stats})
        log.final = {"results": results, "hands": hand_num, "stopped": stopped,
                     "aivat": {seat.name: stats}}
    return log.final


def aivat_stats(scores: list) -> dict:
    """AIVAT bb/100 with a 95% confidence interval."""
    n = len(scores)
    if not n:
        return {"hands": 0, "bb_per_100": None, "ci95": None}
    per_hand = [s / BB for s in scores]
    mean = sum(per_hand) / n
    var = sum((x - mean) ** 2 for x in per_hand) / (n - 1) if n > 1 else 0.0
    return {"hands": n, "bb_per_100": round(mean * 100, 2),
            "ci95": round(1.96 * (var / n) ** 0.5 * 100, 2) if n > 1 else None}


def run_gtow_match(bot_spec, hands: int, match_id: str = None, runs_dir: Path = DEFAULT_RUNS_DIR,
                   use_docker: bool = None, listeners=(), pace_ms: int = 0, cancel=None,
                   client: Client = None, time_limit_ms: int = None, workers: int = None) -> dict:
    """Play `hands` hands of `bot_spec` (a BotSpec or a ready-made Seat) against
    GTO Wizard and write the event log + summary. workers: hands at a time
    (default: see default_workers). Returns the summary."""
    config = config_for(hands, time_limit_ms)
    client = client or Client()
    match_id = match_id or f"gtow_{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}"
    run_dir = Path(runs_dir) / match_id
    # bots are seeded per match; there is no match seed, so use a fresh one
    seat = make_bot_seats([bot_spec], config, use_docker,
                          seed=int.from_bytes(os.urandom(4), "big"))[0]
    workers = workers or default_workers(seat, pace_ms)
    t0 = time.time()
    with EventLog(run_dir) as log:
        log.listeners.extend(listeners)
        outcome, interrupted = None, "error"
        try:
            outcome = play_gtow_match(seat, config, match_id, log, client,
                                      pacer=Pacer(pace_ms, cancel) if (pace_ms or cancel) else None,
                                      workers=workers)
        except Cancelled:
            interrupted = "cancelled"
            raise
        finally:
            final = outcome or getattr(log, "final", {})
            summary = {
                "kind": "match", "dealer": "gto_wizard", "match_id": match_id, "seed": None,
                "config": config.to_dict(),
                "seats": [seat.info(), GTOWizardSeat().info()],
                "results": final.get("results"), "hands": final.get("hands"),
                "aivat": final.get("aivat"),
                "stopped": outcome["stopped"] if outcome else interrupted,
                "duration_s": round(time.time() - t0, 2), "run_dir": str(run_dir),
                "bot_diagnostics": diagnostics([seat]), "workers": workers,
            }
            log.write_summary(summary)
    return summary
