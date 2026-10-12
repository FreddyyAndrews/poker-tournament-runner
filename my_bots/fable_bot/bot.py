"""fable_bot: equity-driven, size-aware exploitative No-Limit Hold'em bot.

Every decision is an expected-value comparison between fold / check / call and a
menu of bet or raise sizes. Equity comes from a seeded Monte Carlo (eval7)
against each opponent's estimated range. The EV of a bet uses a per-opponent
model, learned from the match action log, of how often that opponent folds to
a bet of a given price (amount owed / pot). Opponents that fold to big bets get
bluffed with the cheapest size that works; opponents that never fold get value
bet thin and never bluffed.
"""
import random
import time

import eval7

BOT_NAME = "fable_bot"

# ── Preflop equity of every starting hand vs one random hand (precomputed) ────
PRE = {"22": 0.5039, "32s": 0.3581, "32o": 0.3208, "42s": 0.3656, "42o": 0.3318, "52s": 0.3807, "52o": 0.3436, "62s": 0.3769, "62o": 0.3372, "72s": 0.3785, "72o": 0.3438, "82s": 0.4027, "82o": 0.3677, "92s": 0.4265, "92o": 0.3838, "T2s": 0.446, "T2o": 0.416, "J2s": 0.4699, "J2o": 0.4498, "Q2s": 0.4985, "Q2o": 0.4723, "K2s": 0.5246, "K2o": 0.5018, "A2s": 0.5755, "A2o": 0.5524, "33": 0.5373, "43s": 0.3855, "43o": 0.3573, "53s": 0.394, "53o": 0.3586, "63s": 0.3942, "63o": 0.3678, "73s": 0.3982, "73o": 0.3679, "83s": 0.4076, "83o": 0.382, "93s": 0.427, "93o": 0.3958, "T3s": 0.4555, "T3o": 0.4262, "J3s": 0.4814, "J3o": 0.4532, "Q3s": 0.5076, "Q3o": 0.4762, "K3s": 0.5378, "K3o": 0.51, "A3s": 0.5838, "A3o": 0.5571, "44": 0.5692, "54s": 0.4156, "54o": 0.3838, "64s": 0.4119, "64o": 0.3825, "74s": 0.419, "74o": 0.3797, "84s": 0.4272, "84o": 0.3922, "94s": 0.4324, "94o": 0.4024, "T4s": 0.4639, "T4o": 0.437, "J4s": 0.4908, "J4o": 0.4552, "Q4s": 0.5217, "Q4o": 0.4853, "K4s": 0.5404, "K4o": 0.5189, "A4s": 0.5897, "A4o": 0.5639, "55": 0.5965, "65s": 0.4286, "65o": 0.3969, "75s": 0.4378, "75o": 0.4038, "85s": 0.4386, "85o": 0.4185, "95s": 0.4527, "95o": 0.43, "T5s": 0.4688, "T5o": 0.445, "J5s": 0.4965, "J5o": 0.469, "Q5s": 0.5209, "Q5o": 0.5003, "K5s": 0.5576, "K5o": 0.5317, "A5s": 0.6, "A5o": 0.5735, "66": 0.6356, "76s": 0.4548, "76o": 0.4217, "86s": 0.4637, "86o": 0.4312, "96s": 0.4703, "96o": 0.4392, "T6s": 0.4852, "T6o": 0.4578, "J6s": 0.5049, "J6o": 0.4749, "Q6s": 0.5348, "Q6o": 0.5031, "K6s": 0.5587, "K6o": 0.5369, "A6s": 0.5985, "A6o": 0.5753, "77": 0.6549, "87s": 0.4757, "87o": 0.4493, "97s": 0.487, "97o": 0.4653, "T7s": 0.5004, "T7o": 0.4769, "J7s": 0.516, "J7o": 0.497, "Q7s": 0.5309, "Q7o": 0.5133, "K7s": 0.569, "K7o": 0.5532, "A7s": 0.6064, "A7o": 0.5852, "88": 0.6907, "98s": 0.5067, "98o": 0.4832, "T8s": 0.5172, "T8o": 0.4988, "J8s": 0.538, "J8o": 0.5087, "Q8s": 0.5608, "Q8o": 0.5312, "K8s": 0.5803, "K8o": 0.5539, "A8s": 0.6144, "A8o": 0.5982, "99": 0.7177, "T9s": 0.5392, "T9o": 0.5153, "J9s": 0.5595, "J9o": 0.5285, "Q9s": 0.5728, "Q9o": 0.549, "K9s": 0.5951, "K9o": 0.5756, "A9s": 0.6255, "A9o": 0.6058, "TT": 0.7534, "JTs": 0.5689, "JTo": 0.5503, "QTs": 0.5885, "QTo": 0.5693, "KTs": 0.6064, "KTo": 0.5952, "ATs": 0.6387, "ATo": 0.6216, "JJ": 0.7756, "QJs": 0.5966, "QJo": 0.5758, "KJs": 0.6254, "KJo": 0.6044, "AJs": 0.6625, "AJo": 0.6343, "QQ": 0.8009, "KQs": 0.6309, "KQo": 0.6109, "AQs": 0.6598, "AQo": 0.6427, "KK": 0.8224, "AKs": 0.6727, "AKo": 0.6588, "AA": 0.8491}

RANKS = "23456789TJQKA"
SUITS = "shdc"
DECK = [eval7.Card(r + s) for r in RANKS for s in SUITS]
CARD = {str(c): c for c in DECK}


def hand_class(c1, c2):
    r1, r2 = c1[0], c2[0]
    if RANKS.index(r1) < RANKS.index(r2):
        r1, r2 = r2, r1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if c1[1] == c2[1] else "o")


def _ordered_combos():
    combos = []
    for i in range(52):
        for j in range(i + 1, 52):
            a, b = DECK[i], DECK[j]
            combos.append((PRE[hand_class(str(a), str(b))], a, b))
    combos.sort(key=lambda t: -t[0])
    return [(a, b) for _, a, b in combos]


COMBOS = _ordered_combos()          # all 1326 combos, strongest first
N_COMBOS = len(COMBOS)

# ── Opponent model ───────────────────────────────────────────────────────────
# Fold-to-bet is bucketed by the price the opponent faces: owed / pot (pot
# including the bet). Menu sizes below were chosen so common thresholds
# (a third, a half, two thirds of the pot) fall into different buckets.
EDGES = [0.15, 0.22, 0.30, 0.35, 0.42, 0.50, 0.60]
NB = len(EDGES) + 1
PRIOR_W = 2.0
PRIOR = {
    # (preflop, opponent already bet/raised this street) -> fold prob per price bucket
    (True, False): [0.30, 0.40, 0.48, 0.55, 0.60, 0.65, 0.75, 0.90],
    (True, True): [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50],
    (False, False): [0.20, 0.28, 0.34, 0.40, 0.45, 0.50, 0.60, 0.75],
    (False, True): [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.42, 0.55],
}


def bucket(price):
    for k, e in enumerate(EDGES):
        if price < e:
            return k
    return NB - 1


class Opp:
    def __init__(self):
        self.ftb = {k: [[0, 0] for _ in range(NB)] for k in PRIOR}   # [folds, faced]
        self.raise_vs_bet = [0, 0]                     # [raises, faced bets postflop]
        self.bet_vs_check = [0, 0]                     # [bets, chances postflop]
        self.hands = 0
        self.vpip = 0
        self.pfr = 0

    def fold_prob(self, price, preflop, aggr=False):
        table = self.ftb[(preflop, aggr)]
        prior = PRIOR[(preflop, aggr)]
        est = []
        for k in range(NB):
            f, n = table[k]
            est.append((PRIOR_W * prior[k] + f) / (PRIOR_W + n))
        # Fold probability is assumed non-decreasing in price.
        run = 0.0
        for k in range(NB):
            run = max(run, est[k])
            est[k] = run
        return est[bucket(price)]

    def vpip_rate(self, prior=0.45):
        return (4 * prior + self.vpip) / (4 + self.hands)

    def pfr_rate(self, prior=0.25):
        return (4 * prior + self.pfr) / (4 + self.hands)

    def raise_rate(self):
        r, n = self.raise_vs_bet
        return (2 * 0.12 + r) / (2 + n)

    def summary(self):
        return {
            "hands": self.hands,
            "vpip": round(self.vpip_rate(), 2),
            "pfr": round(self.pfr_rate(), 2),
            "ftb": {"%s%s" % ("pre" if k[0] else "post", "_aggr" if k[1] else ""):
                    [round(self.fold_prob(p, *k), 2) for p in (0.1, 0.2, 0.26, 0.32, 0.38, 0.45, 0.55, 0.7)]
                    for k in PRIOR},
            "n": {"%s%s" % ("pre" if k[0] else "post", "_aggr" if k[1] else ""): [n for _, n in self.ftb[k]]
                  for k in PRIOR},
        }


OPPS = {}
PROCESSED = {}          # hand_num -> number of log entries already replayed
ME = {"id": None}


def opp(bot_id):
    o = OPPS.get(bot_id)
    if o is None:
        o = OPPS[bot_id] = Opp()
    return o


class Replay:
    """Replays one hand's action log to learn what each opponent faced."""

    def __init__(self):
        self.bets = {}
        self.pot = 0
        self.current_bet = 0
        self.folded = set()
        self.allin = set()
        self.acted = set()
        self.street = 0
        self.seen = set()
        self.pre_counted = set()
        self.street_aggr = set()
        self.pre_raised = set()
        self.pre_called = set()

    def players(self):
        return [p for p in self.bets if p not in self.folded and p not in self.allin]

    def _street_done(self):
        live = [p for p in self.bets if p not in self.folded]
        if len(live) <= 1:
            return True
        active = [p for p in live if p not in self.allin]
        return all(p in self.acted and self.bets[p] == self.current_bet for p in active)

    def _next_street(self):
        self.street += 1
        for p in self.bets:
            self.bets[p] = 0
        self.current_bet = 0
        self.acted = set()
        self.street_aggr = set()

    def apply(self, e):
        bot, act, amt = e["bot_id"], e["action"], e.get("amount") or 0
        self.bets.setdefault(bot, 0)
        learn = bot != ME["id"]
        o = opp(bot) if learn else None
        if act == "ante":
            self.pot += amt
            return
        if act in ("small_blind", "big_blind"):
            self.bets[bot] = amt
            self.pot += amt
            self.current_bet = max(self.current_bet, amt)
            return
        if learn and bot not in self.seen:
            self.seen.add(bot)
            o.hands += 1
        owed = max(0, self.current_bet - self.bets[bot])
        price = owed / self.pot if self.pot > 0 else 0.0
        pre = self.street == 0
        t = o.ftb[(pre, bot in self.street_aggr)] if learn else None
        if act == "fold":
            if learn and owed > 0:
                t[bucket(price)][0] += 1
                t[bucket(price)][1] += 1
            self.folded.add(bot)
            self.acted.add(bot)
        elif act == "check":
            if learn and not pre:
                o.bet_vs_check[1] += 1
            self.acted.add(bot)
        elif act == "call":
            if pre:
                self.pre_called.add(bot)
            if learn:
                if owed > 0:
                    t[bucket(price)][1] += 1
                if pre and bot not in self.pre_counted:
                    self.pre_counted.add(bot)
                    o.vpip += 1
            self.bets[bot] += amt
            self.pot += amt
            self.acted.add(bot)
        elif act in ("raise", "all_in"):
            if pre:
                self.pre_raised.add(bot)
            if learn:
                if owed > 0:
                    t[bucket(price)][1] += 1
                    if not pre:
                        o.raise_vs_bet[1] += 1
                        if amt > self.current_bet:
                            o.raise_vs_bet[0] += 1
                elif not pre:
                    o.bet_vs_check[1] += 1
                    o.bet_vs_check[0] += 1
                if pre and bot not in self.pre_counted:
                    self.pre_counted.add(bot)
                    o.vpip += 1
                    if amt > self.current_bet:
                        o.pfr += 1
            add = max(0, amt - self.bets[bot])
            self.pot += add
            self.bets[bot] = max(self.bets[bot], amt)
            if amt > self.current_bet:
                self.current_bet = amt
                self.acted = {bot}
            else:
                self.acted.add(bot)
            self.street_aggr.add(bot)
            if act == "all_in":
                self.allin.add(bot)
        else:
            return
        if self._street_done():
            self._next_street()


REPLAYS = {}


def update_model(gs):
    """Replay every hand in match_action_log that has new entries."""
    log = gs.get("match_action_log") or []
    by_hand = {}
    for e in log:
        by_hand.setdefault(e.get("hand_num", -1), []).append(e)
    for hn, entries in by_hand.items():
        done = PROCESSED.get(hn, 0)
        if done == 0:
            # A hand whose beginning fell off the front of the window can't be
            # replayed reliably.
            if entries[0]["action"] not in ("ante", "small_blind", "big_blind"):
                PROCESSED[hn] = len(entries)
                continue
            REPLAYS[hn] = Replay()
        rp = REPLAYS.get(hn)
        if rp is None:
            PROCESSED[hn] = len(entries)
            continue
        for e in entries[done:]:
            rp.apply(e)
        PROCESSED[hn] = len(entries)
    # forget old replays
    if len(REPLAYS) > 8:
        for hn in sorted(REPLAYS)[:-8]:
            REPLAYS.pop(hn, None)


# ── Equity ───────────────────────────────────────────────────────────────────
def _sample_hand(q, deck, used, rng):
    if q is None or q >= 0.98:
        for _t in range(40):
            a, b = rng.sample(deck, 2)
            if a not in used and b not in used:
                return a, b
        return None
    top = max(6, int(q * N_COMBOS))
    for _t in range(40):
        a, b = COMBOS[rng.randrange(top)]
        if a not in used and b not in used:
            return a, b
    return None


def equity(my, board, specs, iters, rng):
    """Monte Carlo equity of my (2 Cards) on board vs opponents.

    specs: one (q, f) per opponent. q is the fraction of starting hands the
    opponent holds (None = random), f the probability they fold to the action
    being considered: whoever continues holds the top (1 - f) of their range.
    Heads-up and postflop that filter is by made-hand strength on the board;
    otherwise it is by preflop rank."""
    known = set(my) | set(board)
    deck = [c for c in DECK if c not in known]
    need = 5 - len(board)
    if len(specs) == 1:
        q, f = specs[0]
        if not board:
            q = max(0.04, (1.0 if q is None else q) * (1 - f)) if f > 0 else q
            f = 0.0
        cands = []
        for _ in range(iters):
            h = _sample_hand(q, deck, known, rng)
            if h:
                cands.append(h)
        if board and f > 0.02:
            scored = [(eval7.evaluate([a, b] + board), (a, b)) for a, b in cands]
            scored.sort(key=lambda t: -t[0])
            cands = [h for _, h in scored[:max(8, int(len(scored) * (1 - f)))]]
        total = 0.0
        for a, b in cands:
            if need:
                rest = [c for c in deck if c is not a and c is not b]
                run = board + rng.sample(rest, need)
            else:
                run = board
            mine = eval7.evaluate(my + run)
            o = eval7.evaluate([a, b] + run)
            total += 1.0 if mine > o else (0.5 if mine == o else 0.0)
        return total / max(1, len(cands))
    # multiway
    qs = [max(0.04, (1.0 if q is None else q) * (1 - f)) if f > 0 else q for q, f in specs]
    total = 0.0
    n = 0
    for _ in range(iters):
        used = set(known)
        opps = []
        for q in qs:
            h = _sample_hand(q, deck, used, rng)
            if h is None:
                break
            used.add(h[0])
            used.add(h[1])
            opps.append(list(h))
        if len(opps) < len(qs):
            continue
        if need:
            rest = [c for c in deck if c not in used]
            run = board + rng.sample(rest, need)
        else:
            run = board
        mine = eval7.evaluate(my + run)
        best, ties = 0, 0
        for o in opps:
            v = eval7.evaluate(o + run)
            if v > best:
                best, ties = v, 1
            elif v == best:
                ties += 1
        n += 1
        if mine > best:
            total += 1.0
        elif mine == best:
            total += 1.0 / (1 + ties)
    return total / max(1, n)


# ── Decision ─────────────────────────────────────────────────────────────────
BET_FRACS = [0.15, 0.25, 0.35, 0.47, 0.6, 0.8, 1.3]
RAISE_MULTS = [2.2, 2.8, 3.6]
OPEN_BB = [2.0, 2.5, 3.0, 4.0]
RISK = 0.03            # EV margin per chip risked on a bet/raise (doubled beyond the pot)
BET_MARGIN = 0.02      # bet must beat check by this fraction of the pot


def opp_range(o, raised_pre, called_pre, n_players):
    """Fraction of starting hands this opponent is assumed to hold."""
    if o is None:
        return None
    if raised_pre:
        return max(0.03, min(1.0, o.pfr_rate(0.25 if n_players == 2 else 0.15) * 1.1))
    if called_pre:
        return max(0.08, min(1.0, o.vpip_rate(0.45 if n_players == 2 else 0.3) * 1.15))
    return None


def _decide(gs, t0):
    ME["id"] = gs["your_bot_id"]
    update_model(gs)
    street = gs["street"]
    pre = street == "preflop"
    pot = gs["pot"]
    owed = gs["amount_owed"]
    stack = gs["your_stack"]
    my_bet = gs["your_bet_this_street"]
    cur = gs["current_bet"]
    min_raise_to = gs["min_raise_to"]
    bb = gs["blinds"]["bb"]
    my_max = stack + my_bet
    me = gs["your_bot_id"]
    board = [CARD[c] for c in gs["community_cards"]]
    my = [CARD[c] for c in gs["your_cards"]]
    seed = hash((gs.get("hand_id"), street, len(gs.get("action_log") or [])))
    rng = random.Random(seed)

    # Opponents still contesting the pot.
    live = [p for p in gs["players"]
            if p["bot_id"] != me and p["state"] in ("active", "all_in")]
    n_players = 1 + len(live)
    if not live:
        return {"action": "check"} if gs["can_check"] else {"action": "call"}

    # Replay this hand so far: who raised/called preflop, who bet this street.
    rp = Replay()
    for ev_ in gs.get("action_log") or []:
        rp.apply(ev_)
    pre_raised, pre_called, aggr_now = rp.pre_raised, rp.pre_called, rp.street_aggr

    ranges = []
    for p in live:
        o = OPPS.get(p["bot_id"])
        ranges.append(opp_range(o, p["bot_id"] in pre_raised, p["bot_id"] in pre_called, n_players))

    iters = 600 if len(live) == 1 else max(150, 500 // len(live))
    eq_cache = {}

    def eq_vs(specs):
        key = tuple((None if q is None else round(q, 2), round(f, 2)) for q, f in specs)
        if key not in eq_cache:
            if pre and len(specs) == 1 and key[0] == (None, 0.0):
                eq_cache[key] = PRE[hand_class(*gs["your_cards"])]
            else:
                eq_cache[key] = equity(my, board, list(specs), iters, rng)
        return eq_cache[key]

    e = eq_vs([(q, 0.0) for q in ranges])

    # In position = nobody live acts after me this street.
    seat = gs["seat_to_act"]
    n_seats = len(gs["players"])
    btn = gs["button_seat"]
    def dist(s):
        return (s - btn) % n_seats
    in_pos = all(dist(seat) > dist(p["seat"]) for p in live if p["state"] == "active")
    realize = 1.0 if (street == "river" or in_pos) else (0.88 if pre else 0.92)

    evs = {}
    if owed > 0:
        evs["fold"] = 0.0
        call_amt = min(owed, stack)
        ec = e if call_amt >= stack else e * realize
        evs["call"] = ec * pot - (1 - ec) * call_amt
    else:
        evs["check"] = e * realize * pot

    # Candidate raise totals.
    cands = set()
    if stack > 0:
        if pre:
            if cur <= bb:        # nobody raised yet: open sizes
                for m in OPEN_BB:
                    cands.add(int(m * bb))
            else:
                for m in RAISE_MULTS:
                    cands.add(int(m * cur))
        elif owed == 0:
            for fr in BET_FRACS:
                cands.add(int(pot * fr))
        else:
            for m in RAISE_MULTS:
                cands.add(int(m * cur))
        cands.add(my_max)
    options = []
    for r in cands:
        r = max(r, min_raise_to)
        if r > 0.62 * my_max:
            r = my_max
        if r <= cur and r < my_max:
            continue
        options.append(r)
    options = sorted(set(options))

    aggressors = [p for p in live if p["state"] == "active"]

    best_r = None
    for r in options:
        add = r - my_bet
        if add <= 0:
            continue
        pot_after = pot + add
        F = 1.0
        max_owed = 0
        cont = []
        for p, q in zip(live, ranges):
            o = OPPS.get(p["bot_id"])
            opp_owed = min(r, p["bet_this_street"] + p["stack"]) - p["bet_this_street"]
            if p["state"] != "active" or opp_owed <= 0:
                cont.append((q, 0.0))
                continue
            price = opp_owed / pot_after
            ag = p["bot_id"] in aggr_now
            f = o.fold_prob(price, pre, ag) if o else PRIOR[(pre, ag)][bucket(price)]
            F *= f
            max_owed = max(max_owed, opp_owed)
            cont.append((q, f))
        e_call = eq_vs(cont) if F < 1 else e
        ev = F * pot + (1 - F) * (e_call * (pot + max_owed) - (1 - e_call) * add)
        ev -= RISK * add + RISK * max(0, add - pot)
        # Opponents who raise a lot make medium-strength bets costly.
        ragg = max((OPPS[p["bot_id"]].raise_rate() for p in aggressors if p["bot_id"] in OPPS), default=0.12)
        if e_call < 0.6 and not pre:
            ev -= (1 - F) * ragg * add * (0.6 - e_call)
        key = "all_in" if r >= my_max else "raise_%d" % r
        evs[key] = ev
        if best_r is None or ev > evs[best_r[0]]:
            best_r = (key, r)

    passive = "call" if owed > 0 else "check"
    base = max(evs.get("fold", -1e9), evs[passive])
    choice = "fold" if (owed > 0 and evs["fold"] >= evs["call"]) else passive
    if best_r and evs[best_r[0]] > base + BET_MARGIN * max(pot, bb):
        choice = best_r[0]

    debug = {"e": round(e, 3), "n_opp": len(live), "ranges": ranges, "in_pos": in_pos,
             "e_call": {str(k): round(v, 3) for k, v in eq_cache.items()},
             "ev": {k: round(v) for k, v in evs.items()},
             "opp": {p["bot_id"]: OPPS[p["bot_id"]].summary() for p in live if p["bot_id"] in OPPS},
             "ms": round((time.time() - t0) * 1000, 1)}
    if choice == "all_in":
        return {"action": "all_in", "debug": debug}
    if choice.startswith("raise_"):
        return {"action": "raise", "amount": best_r[1], "debug": debug}
    return {"action": choice, "debug": debug}


def decide(game_state: dict) -> dict:
    t0 = time.time()
    try:
        return _decide(game_state, t0)
    except Exception as ex:          # never crash: fall back to the cheap action
        print("fable_bot error:", repr(ex))
        if game_state.get("can_check"):
            return {"action": "check"}
        if game_state.get("amount_owed", 0) <= game_state.get("pot", 0) * 0.25:
            return {"action": "call"}
        return {"action": "fold"}
