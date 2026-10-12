"""Claude bot: equity vs modelled ranges, EV-based sizing, per-opponent exploitation."""
import random

import eval7

BOT_NAME = "Claude"

RANKS = "23456789TJQKA"
SUITS = "shdc"
DECK = [eval7.Card(r + s) for r in RANKS for s in SUITS]
CARD = {str(c): c for c in DECK}

# ── Preflop ranking: every 1326 combo ordered by equity vs a random hand ──────
_ALL = [(DECK[i], DECK[j]) for i in range(52) for j in range(i + 1, 52)]


def _cls(c1, c2):
    r1, r2 = sorted((str(c1)[0], str(c2)[0]), key=RANKS.index, reverse=True)
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if str(c1)[1] == str(c2)[1] else "o")


# Equity of each starting-hand class vs one random hand (400k-sample Monte Carlo).
PF_EQ = {
    "AA": 0.852, "KK": 0.8241, "QQ": 0.7992, "JJ": 0.7745, "TT": 0.7504, "99": 0.721,
    "88": 0.6917, "AKs": 0.6724, "77": 0.6627, "AQs": 0.66, "AJs": 0.6543,
    "AKo": 0.6539, "ATs": 0.6463, "AQo": 0.6461, "AJo": 0.636, "KQs": 0.6333,
    "66": 0.633, "A9s": 0.6291, "ATo": 0.6271, "KJs": 0.6267, "A8s": 0.6187,
    "KTs": 0.6181, "KQo": 0.6155, "A7s": 0.6095, "A9o": 0.6071, "KJo": 0.6058,
    "55": 0.6027, "QJs": 0.6025, "K9s": 0.6001, "A5s": 0.6, "A8o": 0.5986,
    "A6s": 0.5984, "KTo": 0.5969, "QTs": 0.5952, "A4s": 0.5907, "A7o": 0.5886,
    "K8s": 0.5827, "A3s": 0.5824, "QJo": 0.5808, "K9o": 0.5784, "Q9s": 0.5765,
    "A6o": 0.5764, "A5o": 0.576, "JTs": 0.5754, "K7s": 0.5741, "A2s": 0.5737,
    "QTo": 0.573, "44": 0.571, "A4o": 0.5682, "K6s": 0.5668, "Q8s": 0.5624,
    "K8o": 0.5608, "J9s": 0.5577, "K5s": 0.5573, "A3o": 0.5571, "Q9o": 0.553,
    "JTo": 0.552, "K7o": 0.5518, "A2o": 0.5491, "K4s": 0.549, "Q7s": 0.5439,
    "K6o": 0.5422, "K3s": 0.5411, "J8s": 0.5404, "T9s": 0.5403, "33": 0.5375,
    "Q8o": 0.5369, "Q6s": 0.5354, "J9o": 0.5329, "K2s": 0.5327, "K5o": 0.5326,
    "Q5s": 0.5276, "T8s": 0.5239, "J7s": 0.5235, "K4o": 0.5233, "Q4s": 0.5174,
    "Q7o": 0.5173, "J8o": 0.5155, "K3o": 0.5148, "T9o": 0.5142, "Q6o": 0.511,
    "Q3s": 0.5105, "98s": 0.5093, "T7s": 0.5073, "J6s": 0.5061, "K2o": 0.5052,
    "22": 0.5038, "Q2s": 0.5027, "Q5o": 0.5007, "J5s": 0.4998, "T8o": 0.4983,
    "J7o": 0.4956, "Q4o": 0.4912, "J4s": 0.4908, "97s": 0.4902, "T6s": 0.489,
    "J3s": 0.4828, "Q3o": 0.482, "98o": 0.481, "87s": 0.4804, "T7o": 0.4796,
    "J6o": 0.4794, "J2s": 0.4739, "96s": 0.4737, "Q2o": 0.4731, "T5s": 0.4721,
    "J5o": 0.4715, "T4s": 0.466, "97o": 0.4629, "86s": 0.4625, "J4o": 0.4622,
    "T6o": 0.4591, "95s": 0.458, "T3s": 0.4572, "76s": 0.4532, "J3o": 0.4521,
    "87o": 0.4491, "T2s": 0.4467, "85s": 0.4463, "96o": 0.4445, "J2o": 0.4442,
    "T5o": 0.4422, "94s": 0.4403, "75s": 0.4367, "T4o": 0.4352, "93s": 0.4333,
    "86o": 0.4328, "65s": 0.4311, "84s": 0.4285, "T3o": 0.4274, "95o": 0.4265,
    "92s": 0.424, "76o": 0.423, "74s": 0.4188, "T2o": 0.4175, "54s": 0.4146,
    "64s": 0.4143, "85o": 0.4142, "83s": 0.4083, "94o": 0.4067, "75o": 0.4062,
    "82s": 0.4022, "73s": 0.4021, "93o": 0.4003, "65o": 0.3998, "53s": 0.397,
    "63s": 0.3959, "84o": 0.3953, "92o": 0.3908, "43s": 0.386, "74o": 0.3854,
    "72s": 0.3817, "54o": 0.3805, "64o": 0.3803, "52s": 0.3781, "62s": 0.3764,
    "83o": 0.3748, "82o": 0.368, "73o": 0.3677, "42s": 0.3676, "63o": 0.3625,
    "53o": 0.3622, "32s": 0.3608, "43o": 0.3506, "72o": 0.3462, "52o": 0.343,
    "62o": 0.3407, "42o": 0.332, "32o": 0.3238,
}
_ALL.sort(key=lambda h: (-PF_EQ[_cls(*h)], str(h[0]), str(h[1])))
PCT = {}  # class -> percentile (0 = best)
for _i, _h in enumerate(_ALL):
    PCT.setdefault(_cls(*_h), (_i + 0.5) / len(_ALL))


def top_range(frac, dead):
    n = max(12, int(len(_ALL) * min(1.0, frac)))
    return [h for h in _ALL[:n] if h[0] not in dead and h[1] not in dead]


# ── Opponent model (module state persists for the whole match) ────────────────
class Opp:
    def __init__(self):
        self.hands = 0
        self.vpip = 0
        self.pfr = 0
        self.acts = {"fold": 0, "check": 0, "call": 0, "raise": 0}
        # responses to my bets by size bucket: (folds, calls, raises)
        self.vs_bet = {}
        # postflop: [checks, bets] when not facing a bet; [folds, calls, raises] facing one
        self.unopened = [0, 0]
        self.facing = [0, 0, 0]

    def bet_freq(self):
        c, b = self.unopened
        return (b + 0.35 * 4) / (c + b + 4)

    def post_rates(self):
        f, c, r = self.facing
        n = f + c + r + 4
        return (f + 0.35 * 4) / n, (c + 0.5 * 4) / n, (r + 0.15 * 4) / n

    def card_corr(self):
        """How much this player's actions depend on their cards (0 = not at all).
        Players who never fold postflop (maniacs, stations) tell us little by betting."""
        fold, _, _ = self.post_rates()
        # nobody raises most of the time on value alone
        by_aggr = (0.65 - self.aggression()) / 0.3
        return max(0.0, min(1.0, fold / 0.25, by_aggr))

    def rate(self, k, prior, n0=6):
        tot = sum(self.acts.values())
        return (self.acts[k] + prior * n0) / (tot + n0)

    def vpip_rate(self):
        return (self.vpip + 0.5 * 5) / (self.hands + 5)

    def pfr_rate(self):
        return (self.pfr + 0.2 * 5) / (self.hands + 5)

    def aggression(self):
        a = self.acts
        return (a["raise"] + 1) / (a["raise"] + a["call"] + a["check"] + 3)

    def fold_to_bet(self, frac, raise_=False, preflop=False):
        """P(fold) when I bet `frac` of the pot (or raise, if `raise_`)."""
        a = self.acts
        base = (a["fold"] + 1) / (a["fold"] + a["call"] + a["raise"] + 4)
        b = _bucket(frac) + (10 if raise_ else 0) + (20 if preflop else 0)
        if raise_:
            base *= 0.6
        f, c, r = self._near(b)
        n0 = 3
        return (f + base * n0) / (f + c + r + n0)

    def reraise(self, frac, raise_=False, preflop=False):
        """P(they raise) when I bet `frac` of the pot."""
        b = _bucket(frac) + (10 if raise_ else 0) + (20 if preflop else 0)
        f, c, r = self._near(b)
        a = self.acts
        base = (a["raise"] + 0.5) / (sum(a.values()) + 5)
        n0 = 4
        return (r + base * n0) / (f + c + r + n0)

    def _near(self, b):
        f, c, r = self.vs_bet.get(b, (0, 0, 0))
        # neighbouring buckets inform each other a little
        for nb in (b - 1, b + 1):
            nf, nc, nr = self.vs_bet.get(nb, (0, 0, 0))
            f += 0.3 * nf
            c += 0.3 * nc
            r += 0.3 * nr
        return f, c, r

    def evidence(self, raise_=False):
        """How many of my bets (or raises) this opponent has responded to."""
        return sum(f + c + r for k, (f, c, r) in self.vs_bet.items()
                   if k < 20 and (k >= 10) == raise_)


def _bucket(frac):
    for i, edge in enumerate((0.4, 0.6, 0.85, 1.2, 2.0)):
        if frac <= edge:
            return i
    return 5


OPPS = {}
SEEN_HANDS = set()      # hand_nums whose actions have been folded into stats
MY_BETS = {}            # hand_num -> list of (index in action_log, frac)
STATE = {"me": None, "cur_hand": None}


def opp(name):
    if name not in OPPS:
        OPPS[name] = Opp()
    return OPPS[name]


def _absorb_hand(hand_num, actions, me):
    """Fold one finished hand's actions into opponent stats."""
    first = set()
    for a in actions:
        n, act = a["bot_id"], a["action"]
        if n == me or act in POSTS:
            continue
        o = opp(n)
        if act == "all_in":
            act = "raise"
        if act in o.acts:
            o.acts[act] += 1
        if n not in first:
            first.add(n)
            o.hands += 1
            if act in ("call", "raise"):
                o.vpip += 1
            if act == "raise":
                o.pfr += 1
    # postflop tendencies, from the hand split into streets (needs the blind posts)
    posts = HAND_POSTS.pop(hand_num, None)
    if posts is not None:
        for street in split_streets(posts + actions)[1:]:
            for a, facing in street:
                if a["bot_id"] == me:
                    continue
                o, act = opp(a["bot_id"]), a["action"]
                if not facing:
                    if act == "check":
                        o.unopened[0] += 1
                    elif act in ("raise", "all_in"):
                        o.unopened[1] += 1
                elif act == "fold":
                    o.facing[0] += 1
                elif act == "call":
                    o.facing[1] += 1
                elif act in ("raise", "all_in"):
                    o.facing[2] += 1
    # responses to my bets
    for idx, key in MY_BETS.get(hand_num, []):
        responded = set()
        for a in actions[idx + 1:]:
            n, act = a["bot_id"], a["action"]
            if n == me:
                break
            if n in responded:
                continue
            responded.add(n)
            o = opp(n)
            f, c, r = o.vs_bet.get(key, (0, 0, 0))
            if act == "fold":
                f += 1
            elif act in ("raise", "all_in"):
                r += 1
            else:
                c += 1
            o.vs_bet[key] = (f, c, r)


POSTS = ("small_blind", "big_blind", "ante")
HAND_POSTS = {}   # hand_num -> blind/ante posts (match_action_log leaves them out)


def split_streets(actions, all_in_at=()):
    """Split one hand's actions (posts included) into betting rounds.
    Returns [[(action, facing_a_bet), ...] per street]. Raise/all_in amounts are
    street totals, call amounts are chips added, antes are dead money.
    `all_in_at`: indices of actions after which that player is all-in."""
    streets = [[]]
    bets = {}
    folded, allin, acted = set(), set(), set()
    players = []
    cur = 0
    for i, a in enumerate(actions):
        n, act, amt = a["bot_id"], a["action"], a.get("amount") or 0
        if n not in players:
            players.append(n)
        if act == "ante":
            continue
        if act in ("small_blind", "big_blind"):
            bets[n] = amt
            cur = max(cur, amt)
            continue
        facing = cur > bets.get(n, 0)
        streets[-1].append((a, facing))
        if act == "fold":
            folded.add(n)
        elif act == "call":
            bets[n] = bets.get(n, 0) + amt
        elif act in ("raise", "all_in"):
            bets[n] = amt
            if amt > cur:
                cur = amt
                acted = set()
        if act == "all_in" or i in all_in_at:
            allin.add(n)
        acted.add(n)
        live = [p for p in players if p not in folded and p not in allin]
        if len([p for p in players if p not in folded]) <= 1:
            break
        if all(p in acted and bets.get(p, 0) == cur for p in live) and (
                len(live) > 1 or all(bets.get(p, 0) >= cur for p in live)):
            streets.append([])
            bets, acted, cur = {}, set(), 0
    return streets


def _update_model(state):
    me = state["your_bot_id"]
    hn = state["hand_num"]
    log = state.get("match_action_log") or []
    by_hand = {}
    for a in log:
        if a["hand_num"] < hn and a["hand_num"] not in SEEN_HANDS:
            by_hand.setdefault(a["hand_num"], []).append(a)
    for h in sorted(by_hand):
        # the oldest hand in a full rolling window may be cut off: skip it
        if not (len(log) >= 200 and h == log[0]["hand_num"]):
            _absorb_hand(h, by_hand[h], me)
        SEEN_HANDS.add(h)
        MY_BETS.pop(h, None)


# ── Equity ────────────────────────────────────────────────────────────────────
def opp_range(name, streets, dead, board):
    o = opp(name)
    pre = [a["action"] for a, _ in streets[0] if a["bot_id"] == name]
    if not pre:
        frac = 1.0
    elif pre[0] in ("raise", "all_in"):
        frac = min(1.0, o.pfr_rate() * 1.3)
    elif pre[0] == "call":
        frac = min(1.0, o.vpip_rate() * 1.1)
    else:
        frac = 1.0
    # preflop re-raises narrow according to how often they are aggressive
    for _ in range(sum(1 for a in pre[1:] if a in ("raise", "all_in"))):
        frac *= min(1.0, 0.35 + 1.2 * o.aggression())
    rng = top_range(max(frac, 0.03), dead)
    # postflop: keep the part of the range that plays this way on this board
    c = o.card_corr()
    for k, street in enumerate(streets[1:4]):
        b = board[:3 + k]
        if len(b) < 3:
            break
        for a, facing in street:
            if a["bot_id"] != name:
                continue
            act = a["action"]
            if act in ("raise", "all_in"):
                f = o.post_rates()[2] if facing else o.bet_freq()
                rng = narrow(rng, b, 1 - c * (1 - f ** 0.7))
            elif act == "call":
                rng = narrow(rng, b, 1 - c * min(0.5, o.post_rates()[0]))
    return rng


def _draw(hole, board):
    """Flush draw or open-ended straight draw that uses a hole card."""
    cards = hole + board
    suits = [str(x)[1] for x in cards]
    for h in hole:
        if suits.count(str(h)[1]) == 4:
            return True
    ranks = {RANKS.index(str(x)[0]) for x in cards}
    if 12 in ranks:
        ranks.add(-1)
    hr = {RANKS.index(str(h)[0]) for h in hole}
    for lo in range(-1, 9):
        win = set(range(lo, lo + 4))
        if win <= ranks and win & hr:
            return True
    return False


def narrow(rng, board, q):
    """Top `q` of the range by made-hand strength on `board` (draws count as fair)."""
    if q >= 0.999 or len(rng) <= 8:
        return rng
    scored = [(eval7.evaluate(list(h) + board), h) for h in rng]
    if len(board) < 5:
        vals = sorted(v for v, _ in scored)
        fair = vals[int(len(vals) * 0.6)]
        scored = [(max(v, fair) if _draw(list(h), board) else v, h) for v, h in scored]
    scored.sort(key=lambda x: -x[0])
    keep = max(8, int(round(len(scored) * q)))
    return [h for _, h in scored[:keep]]


def equity(hole, board, ranges, iters=None):
    """P(win) vs one hand drawn from each range. Exact heads-up on the turn and
    river; otherwise Monte Carlo on `random` (seeded per decision, so replays match)."""
    if len(ranges) == 1:
        r = ranges[0]
        if len(board) == 5:
            return eval7.py_hand_vs_range_exact(hole, [(h, 1.0) for h in r], board)
        if len(board) == 4:
            dead = set(hole) | set(board)
            tot = 0.0
            n = 0
            for c in DECK:
                if c in dead:
                    continue
                rr = [(h, 1.0) for h in r if h[0] != c and h[1] != c]
                if rr:
                    tot += eval7.py_hand_vs_range_exact(hole, rr, board + [c]) * len(rr)
                    n += len(rr)
            return tot / max(n, 1)
    iters = iters or (1500 if len(ranges) == 1 else 700)
    dead = set(hole) | set(board)
    rest = [c for c in DECK if c not in dead]
    win = 0.0
    done = 0
    need = 5 - len(board)
    for _ in range(iters):
        used = set()
        hands = []
        ok = True
        for r in ranges:
            for _try in range(10):
                h = r[random.randrange(len(r))]
                if h[0] not in used and h[1] not in used:
                    break
            else:
                ok = False
                break
            used.add(h[0]); used.add(h[1])
            hands.append(h)
        if not ok:
            continue
        pool = [c for c in rest if c not in used]
        extra = random.sample(pool, need)
        full = board + extra
        mine = eval7.evaluate(hole + full)
        best = max(eval7.evaluate(list(h) + full) for h in hands)
        if mine > best:
            win += 1
        elif mine == best:
            win += 0.5
        done += 1
    return win / max(done, 1)


# ── Helpers ───────────────────────────────────────────────────────────────────
def act_raise(state, total):
    cap = state["your_stack"] + state["your_bet_this_street"]
    total = int(max(total, state["min_raise_to"]))
    if total >= cap * 0.9:
        return {"action": "all_in"}
    return {"action": "raise", "amount": total}


def order_after(state, start_seat):
    seats = sorted(p["seat"] for p in state["players"])
    i = 0
    while i < len(seats) and seats[i] <= start_seat:
        i += 1
    return seats[i:] + seats[:i]


def players_behind(state):
    """Non-folded players who still act after me on this street."""
    me = state["seat_to_act"]
    start = state["bb_seat"] if state["street"] == "preflop" else state["button_seat"]
    order = order_after(state, start)
    by_seat = {p["seat"]: p for p in state["players"]}
    idx = order.index(me)
    return [by_seat[s] for s in order[idx + 1:]
            if by_seat[s]["state"] == "active" and not by_seat[s]["is_folded"]]


def record_bet(state, total):
    pot_before = state["pot"] + state["amount_owed"]
    added = total - state["your_bet_this_street"] - state["amount_owed"]
    frac = added / max(pot_before, 1)
    facing = state["current_bet"] > (state["blinds"]["bb"] if state["street"] == "preflop" else 0)
    key = _bucket(frac) + (10 if facing else 0) + (20 if state["street"] == "preflop" else 0)
    # index among decisions only: match_action_log has no blind/ante posts
    idx = sum(1 for a in state["action_log"] if a["action"] not in POSTS)
    MY_BETS.setdefault(state["hand_num"], []).append((idx, key))


# ── Decision ──────────────────────────────────────────────────────────────────
# Committing a big share of the stack on a thin edge is a poor trade in freezeouts:
# every chip put in costs a penalty growing with the square of the total at risk.
RISK = 0.6
HAND_START = {}   # hand_num -> my chips at the start of the hand


def risk_cost(add, state):
    start = HAND_START.get(state["hand_num"]) or (state["your_stack"] + state["your_bet_this_street"])
    sunk = start - state["your_stack"]
    add = min(add, state["your_stack"])
    return RISK * ((sunk + add) ** 2 - sunk ** 2) / max(start, 1)


def decide(state):
    if state.get("type") == "warmup":
        return {"action": "check"}
    _update_model(state)
    HAND_START.setdefault(state["hand_num"], state["your_stack"] + state["your_bet_this_street"])
    me = state["your_bot_id"]
    hole = [CARD[c] for c in state["your_cards"]]
    board = [CARD[c] for c in state["community_cards"]]
    dead = set(hole) | set(board)
    actions = state["action_log"]
    HAND_POSTS.setdefault(state["hand_num"], [a for a in actions if a["action"] in POSTS])
    live = [p for p in state["players"] if p["bot_id"] != me and not p["is_folded"]
            and p["state"] in ("active", "all_in")]
    streets = split_streets(actions, _all_in_points(state))
    ranges = [opp_range(p["bot_id"], streets, dead, board) for p in live]
    eq = equity(hole, board, ranges)

    bb = state["blinds"]["bb"]
    stack = state["your_stack"]
    eff = min(stack, max((p["stack"] for p in live), default=0)) / bb  # in bb

    if state["street"] == "preflop":
        res = preflop(state, hole, eq, live, eff)
    else:
        res = postflop(state, eq, live)
    if res["action"] == "raise":
        record_bet(state, res["amount"])
    elif res["action"] == "all_in":
        record_bet(state, stack + state["your_bet_this_street"])
    res.setdefault("debug", {}).update(
        eq=round(eq, 3), eff_bb=round(eff, 1),
        opps={p["bot_id"]: _opp_debug(opp(p["bot_id"])) for p in live})
    return res


def _opp_debug(o):
    return {"vpip": round(o.vpip_rate(), 2), "aggr": round(o.aggression(), 2),
            "f_bet": [round(o.fold_to_bet(f), 2) for f in (0.5, 0.75, 1.5)],
            "f_raise": round(o.fold_to_bet(0.75, True), 2),
            "f_open": round(o.fold_to_bet(1.5, preflop=True), 2)}


def _all_in_points(state):
    """Indices of the actions that put currently all-in players all-in."""
    out = set()
    for p in state["players"]:
        if p["state"] == "all_in":
            idx = [i for i, a in enumerate(state["action_log"]) if a["bot_id"] == p["bot_id"]]
            if idx:
                out.add(idx[-1])
    return out


def call_or_fold(state, eq, extra_ok=0.0):
    """Call when the risk-adjusted EV of calling beats folding."""
    owed = state["amount_owed"]
    pot = state["pot"]
    ev = eq * (pot + owed) - owed - risk_cost(owed, state) + extra_ok * owed
    if ev > 0:
        return {"action": "call", "debug": {"call_ev": round(ev)}}
    return {"action": "fold", "debug": {"call_ev": round(ev)}}


def preflop(state, hole, eq, live, eff):
    bb = state["blinds"]["bb"]
    owed = state["amount_owed"]
    pot = state["pot"]
    stack = state["your_stack"]
    pct = PCT[_cls(*hole)]
    raised = state["current_bet"] > bb
    behind = players_behind(state)

    # Short stacks: push or fold
    if eff <= 12:
        if not raised:
            # fold equity: chance everyone behind folds
            pf = 1.0
            for p in behind:
                pf *= opp(p["bot_id"]).fold_to_bet(eff * bb / max(pot, 1), preflop=True)
            thresh = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.3}.get(len(behind), 0.22)
            thresh = min(1.0, thresh * (eff <= 6 and 1.4 or 1.0) + 0.3 * pf)
            if pct <= thresh:
                return {"action": "all_in"}
            return {"action": "check"} if state["can_check"] else {"action": "fold"}
        if owed >= stack or eq < 0.5:
            return call_or_fold(state, eq)
        res = call_or_fold(state, eq)
        return {"action": "all_in"} if res["action"] == "call" else res

    if not raised:
        n = len(behind)
        open_frac = {0: 0.6, 1: 0.8, 2: 0.45, 3: 0.33, 4: 0.26}.get(n, 0.2)
        # loosen when the players behind fold a lot
        fold_all = 1.0
        for p in behind:
            fold_all *= opp(p["bot_id"]).fold_to_bet(1.5, preflop=True)
        open_frac = min(1.0, open_frac + 0.5 * fold_all)
        limpers = sum(1 for a in state["action_log"] if a["action"] == "call")
        if pct <= open_frac:
            return act_raise(state, bb * (2.5 + limpers))
        if state["can_check"]:
            return {"action": "check"}
        if owed <= bb / 2 and pct <= 0.6 and n <= 1:
            return {"action": "call"}
        return {"action": "fold"}

    # Facing a raise: re-raise for value with a real edge, else call by pot odds
    # (a little looser deep, where position and implied odds help).
    if eq > 0.62 or pct < 0.03:
        total = state["current_bet"] * 3 + (pot - state["current_bet"])
        add = total - state["your_bet_this_street"]
        if eq * (pot + 2 * add - owed) - add - risk_cost(add, state) > 0 or pct < 0.03:
            return act_raise(state, total)
    return call_or_fold(state, eq, extra_ok=0.08 if eff > 40 else 0.0)




SIZES = (0.33, 0.5, 0.75, 1.0, 1.5)


def postflop(state, eq, live):
    pot = state["pot"]
    owed = state["amount_owed"]
    stack = state["your_stack"]
    my_bet = state["your_bet_this_street"]
    n = len(live)

    def bet_ev(frac):
        """Risk-adjusted EV of putting in owed + frac*(pot+owed)."""
        b = frac * (pot + owed)
        pf = 1.0
        no_rr = 1.0
        aggr = 0.0
        for p in live:
            o = opp(p["bot_id"])
            pf *= o.fold_to_bet(frac, raise_=owed > 0)
            no_rr *= 1 - o.reraise(frac, raise_=owed > 0)
            aggr = max(aggr, o.aggression())
        p_rr = min(1 - pf, 1 - no_rr)
        p_call = max(0.0, 1 - pf - p_rr)
        # when called, equity drops: callers hold the stronger part of their range
        eq_c = eq * (1 - 0.35 * pf) if n == 1 else eq * (1 - 0.25 * pf)
        called_pot = pot + owed + b + b * (1 + (n - 1) * 0.5)
        add = owed + b
        ev = pf * pot + p_call * (eq_c * called_pot - add - risk_cost(add, state))
        if p_rr > 0:
            # re-raised: assume it's then all-in or fold for us. Re-raises from
            # passive players are strong; from maniacs they mean little.
            S = min(stack, max(p["stack"] + p["bet_this_street"] for p in live) - my_bet)
            eq_rr = eq * min(1.0, 0.45 + aggr)
            shove = eq_rr * (pot + 2 * S - owed) - S - risk_cost(S, state)
            ev += p_rr * max(-add, shove)
        return ev

    best_frac, best_ev = 0.0, -1e18
    for f in SIZES:
        if f * (pot + owed) + owed >= stack:
            f = (stack - owed) / max(pot + owed, 1)
        if f <= 0:
            continue
        ev = bet_ev(f)
        if ev > best_ev:
            best_frac, best_ev = f, ev
    dbg = {"bet_ev": round(best_ev), "frac": round(best_frac, 2)}

    if owed == 0:
        check_ev = eq * pot * (0.85 if n == 1 else 0.7)
        dbg["check_ev"] = round(check_ev)
        if best_ev > check_ev and best_frac > 0:
            return dict(act_raise(state, my_bet + best_frac * pot), debug=dbg)
        return {"action": "check", "debug": dbg}

    call_ev = eq * (pot + owed) - owed - risk_cost(owed, state)
    dbg["call_ev"] = round(call_ev)
    # Raising a bet without a strong hand needs evidence that this opponent folds.
    proven = all(opp(p["bot_id"]).evidence(True) >= 6 for p in live)
    if best_frac > 0 and best_ev > max(call_ev, 0) and (eq > 0.55 or proven and eq > 0.25):
        total = state["current_bet"] + best_frac * (pot + owed)
        return dict(act_raise(state, total), debug=dbg)
    if call_ev > 0:
        return {"action": "call", "debug": dbg}
    return {"action": "fold", "debug": dbg}
