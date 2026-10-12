"""opus_bot: an exploitative equity/EV bot.

Every decision:
  1. Replays the match history (match_action_log) to keep per-opponent stats:
     how often they fold to a bet of each size (per pot-ratio bucket), and how
     often they open, bet and raise.
  2. Narrows each opponent's range from what they did this hand, using those
     frequencies (a player who raises 10% of the time is on the top 10%; one who
     raises 70% of the time is treated as nearly random).
  3. Runs an eval7 Monte Carlo of our equity against those ranges.
  4. Scores fold / check / call and a handful of bet sizes with a one-street EV
     model (fold equity from the learned fold rates, equity when called against
     the tightened calling range) and takes the best one.
"""
import bisect
import json
import math
import os
import random
import time

import eval7

BOT_NAME = "opus_bot"
BOT_AVATAR = "robot_1"

RANKS = "23456789TJQKA"
SUITS = "shdc"
DECK = [r + s for r in RANKS for s in SUITS]
CARD = {c: eval7.Card(c) for c in DECK}

DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
with open(os.path.join(DATA_DIR, "preflop.json")) as _fh:
    PRE_EQ = json.load(_fh)          # hand class -> heads-up equity vs a random hand

# The runner seeds the global RNG before every decision, so use it directly.
rng = random


def hand_class(c1, c2):
    a, b = c1[0], c2[0]
    if RANKS.index(a) < RANKS.index(b):
        a, b = b, a
    if a == b:
        return a + b
    return a + b + ("s" if c1[1] == c2[1] else "o")


def _build_combos():
    combos = []
    for i in range(52):
        for j in range(i + 1, 52):
            c1, c2 = DECK[i], DECK[j]
            combos.append((PRE_EQ[hand_class(c1, c2)], c1, c2))
    combos.sort(key=lambda x: -x[0])
    n = len(combos)
    return [(c1, c2, (k + 0.5) / n) for k, (_, c1, c2) in enumerate(combos)]


COMBOS = _build_combos()             # all 1326 combos, best preflop first, with percentile

# ── opponent model ────────────────────────────────────────────────────────────
BUCKET_EDGES = [0.26, 0.34, 0.45, 0.55]          # r = owed / pot when facing a bet
# "_rr": facing our raise after they already raised this street (their range is strong)
FOLD_PRIOR = {"pre": [0.30, 0.40, 0.45, 0.45, 0.45],
              "post": [0.25, 0.30, 0.35, 0.40, 0.40],
              "pre_rr": [0.15, 0.20, 0.25, 0.25, 0.25],
              "post_rr": [0.15, 0.20, 0.25, 0.25, 0.25]}
FOLD_W = 2.0
RATE_PRIOR = {"open": (0.25, 4.0), "bet": (0.35, 4.0), "rvb": (0.12, 4.0)}


def bucket(r):
    return bisect.bisect_right(BUCKET_EDGES, r)


class Opp:
    def __init__(self):
        self.fold = {k: [[0, 0] for _ in range(5)] for k in FOLD_PRIOR}
        self.rate = {k: [0, 0] for k in RATE_PRIOR}   # [aggressive actions, opportunities]

    def fold_est(self, st, r):
        k = bucket(r)
        tab = self.fold[st]
        f, n = tab[k]
        num, den = FOLD_PRIOR[st][k] * FOLD_W + f, FOLD_W + n
        for j in (k - 1, k + 1):
            if 0 <= j < 5 and tab[j][1]:
                w = min(tab[j][1], 4) * 0.25 / (1 + n / 5.0)
                num += w * tab[j][0] / tab[j][1]
                den += w
        return num / den

    def q(self, key):
        p, w = RATE_PRIOR[key]
        r, n = self.rate[key]
        return (r + p * w) / (n + w)


OPPS = {}
STATE = {"last_hand": -1}


def opp(bid):
    if bid not in OPPS:
        OPPS[bid] = Opp()
    return OPPS[bid]


def replay(entries, participants):
    """Walk one hand's action log, tracking streets and the pot.
    Returns [(bot, action, amount, street, owed, pot_before, raises_this_street,
    raised_before)]; blinds don't count as raises, raised_before means this bot
    already raised this street."""
    bets = {p: 0 for p in participants}
    street, cur, pot, nraise = 0, 0, 0, 0
    folded, allin, acted, raisers = set(), set(), set(), set()
    out = []
    for e in entries:
        b, a, amt = e["bot_id"], e["action"], e.get("amount") or 0
        if b not in bets:
            bets[b] = 0
        if a == "ante":
            pot += amt
            continue
        if a in ("small_blind", "big_blind"):
            bets[b] += amt
            pot += amt
            cur = max(cur, bets[b])
            continue
        owed = max(0, cur - bets[b])
        out.append((b, a, amt, street, owed, pot, nraise, b in raisers))
        if a == "fold":
            folded.add(b)
        elif a == "call":
            bets[b] += amt
            pot += amt
            if amt < owed:
                allin.add(b)
        elif a in ("raise", "all_in"):
            pot += max(0, amt - bets[b])
            bets[b] = amt
            if amt > cur:
                cur = amt
                nraise += 1
                acted = set()
                raisers.add(b)
            if a == "all_in":
                allin.add(b)
        acted.add(b)
        live = [p for p in bets if p not in folded]
        if len(live) <= 1:
            break
        can_act = [p for p in live if p not in allin]
        if all(p in acted and bets[p] == cur for p in can_act):
            street += 1
            bets = {p: 0 for p in bets}
            cur, nraise, acted, raisers = 0, 0, set(), set()
    return out


def learn(events, me):
    for b, a, amt, street, owed, pot, nraise, rr in events:
        if b == me:
            continue
        o = opp(b)
        aggressive = a in ("raise", "all_in")
        if street == 0 and nraise == 0:
            o.rate["open"][1] += 1          # preflop with only the blinds in front
            o.rate["open"][0] += aggressive
        elif owed > 0:
            st = ("pre" if street == 0 else "post") + ("_rr" if rr else "")
            f = o.fold[st][bucket(owed / max(pot, 1))]
            f[1] += 1
            f[0] += a == "fold"
            o.rate["rvb"][1] += 1
            o.rate["rvb"][0] += aggressive
        else:
            o.rate["bet"][1] += 1
            o.rate["bet"][0] += aggressive


def update_history(gs, me):
    hand = gs.get("hand_num", 0)
    by_hand = {}
    for e in gs.get("match_action_log") or []:
        h = e.get("hand_num")
        if h is None or h <= STATE["last_hand"] or h >= hand:
            continue
        by_hand.setdefault(h, []).append(e)
    for h in sorted(by_hand):
        entries = by_hand[h]
        if entries[0]["action"] not in ("ante", "small_blind", "big_blind"):
            continue                      # start of the hand fell out of the window
        participants = []
        for e in entries:
            if e["bot_id"] not in participants:
                participants.append(e["bot_id"])
        learn(replay(entries, participants), me)
    STATE["last_hand"] = max(STATE["last_hand"], hand - 1)


def soften(q):
    """Fraction of range kept after an aggressive action taken with frequency q.
    Frequent aggressors are assumed to be partly random rather than value-heavy."""
    w = min(1.0, max(0.0, (q - 0.2) / 0.4))
    return q + (1 - q) * w


def ranges_this_hand(events, me):
    """(pre_frac, post_frac) per opponent from their actions this hand."""
    fr = {}
    for b, a, amt, street, owed, pot, nraise, rr in events:
        if b == me:
            continue
        o = opp(b)
        pre, post = fr.get(b, (1.0, 1.0))
        if a in ("raise", "all_in"):
            if street == 0:
                pre *= soften(o.q("open") if nraise == 0 else o.q("rvb"))
            else:
                post *= soften(o.q("bet") if owed == 0 else o.q("rvb"))
        elif a == "call" and owed > 0:
            st = ("pre" if street == 0 else "post") + ("_rr" if rr else "")
            cont = 1 - o.fold_est(st, owed / max(pot, 1))
            if street == 0:
                pre *= max(cont, 0.15) if nraise > 0 else 0.85
            else:
                post *= max(cont, 0.2) ** 0.7
        fr[b] = (max(pre, 0.04), max(post, 0.08))
    return fr


# ── equity ────────────────────────────────────────────────────────────────────

def build_pool(dead, board, pre_frac, post_frac):
    """Opponent hands: the top pre_frac preflop, then the top post_frac by made
    hand on this board."""
    pool = [(c1, c2) for c1, c2, pct in COMBOS
            if pct <= pre_frac and c1 not in dead and c2 not in dead]
    if len(pool) < 30:
        pool = [(c1, c2) for c1, c2, _ in COMBOS if c1 not in dead and c2 not in dead][:30]
    if board and post_frac < 0.999:
        bc = [CARD[c] for c in board]
        scored = [(eval7.evaluate([CARD[c1], CARD[c2]] + bc), rng.random(), (c1, c2)) for c1, c2 in pool]
        scored.sort(reverse=True)
        keep = max(20, int(math.ceil(post_frac * len(scored))))
        pool = [h for _, _, h in scored[:keep]]
    return pool


def monte_carlo(my, board, pools, p_in, trials, detail=False):
    """Equity of `my` vs opponents drawn from `pools` (each present with prob
    p_in). With detail, also returns [(pool rank fraction, result)] for the
    first opponent so equity vs the top part of its range can be read off."""
    dead = set(my) | set(board)
    rest = [c for c in DECK if c not in dead]
    k = 5 - len(board)
    mine_c = [CARD[c] for c in my]
    board_c = [CARD[c] for c in board]
    tot, n, det = 0.0, 0, []
    for _ in range(trials):
        used = set()
        holes = []
        first_rank = None
        for idx, (pool, p) in enumerate(zip(pools, p_in)):
            if p < 1 and rng.random() > p:
                continue
            for _t in range(8):
                i = rng.randrange(len(pool))
                h = pool[i]
                if h[0] not in used and h[1] not in used:
                    break
            else:
                continue
            used.add(h[0])
            used.add(h[1])
            holes.append(h)
            if idx == 0:
                first_rank = i / len(pool)
        if k:
            draw = [c for c in rng.sample(rest, k + 2 * len(holes)) if c not in used][:k]
            full = board_c + [CARD[c] for c in draw]
        else:
            full = board_c
        mv = eval7.evaluate(mine_c + full)
        res, ties = 1.0, 0
        for h in holes:
            ov = eval7.evaluate([CARD[h[0]], CARD[h[1]]] + full)
            if ov > mv:
                res = 0.0
                break
            if ov == mv:
                ties += 1
        if res and ties:
            res = 1.0 / (ties + 1)
        tot += res
        n += 1
        if detail and first_rank is not None:
            det.append((first_rank, res))
    eq = tot / n if n else 0.5
    return (eq, det) if detail else eq


def eq_top(det, frac, fallback):
    """Equity against the best `frac` of the first opponent's range."""
    xs = [r for rank, r in det if rank < frac]
    if len(xs) < 15:
        return fallback * (0.6 + 0.4 * frac)
    return sum(xs) / len(xs)


# ── decision ──────────────────────────────────────────────────────────────────

def in_position(gs, me_seat, live_seats):
    n = len(gs["players"])
    if n == 2:
        return me_seat == gs["button_seat"]
    order = [(gs["button_seat"] + i) % n for i in range(1, n + 1)]   # button acts last
    live = [s for s in order if s in live_seats]
    return bool(live) and live[-1] == me_seat


def decide(gs):
    t0 = time.time()
    me = gs["your_bot_id"]
    try:
        update_history(gs, me)
    except Exception as exc:          # model updates must never cost a hand
        print("history error", exc)

    street = gs["street"]
    board = list(gs["community_cards"])
    my = list(gs["your_cards"])
    pot = gs["pot"]
    owed = gs["amount_owed"]
    my_bet = gs["your_bet_this_street"]
    stack = gs["your_stack"]
    cur = gs["current_bet"]
    bb = gs["blinds"]["bb"]
    max_to = stack + my_bet
    players = gs["players"]
    me_seat = gs["seat_to_act"]
    st = "pre" if street == "preflop" else "post"

    participants = [p["bot_id"] for p in players if p.get("state") != "busted"]
    events = replay(gs.get("action_log") or [], participants)
    fr = ranges_this_hand(events, me)
    voluntary = {e[0] for e in events}
    raised_now = set()                # opponents who raised on this street
    for e in events:
        if e[1] in ("raise", "all_in") and e[3] == ("preflop", "flop", "turn", "river").index(street):
            raised_now.add(e[0])

    live = [p for p in players if p["bot_id"] != me and p.get("state") in ("active", "all_in")]
    ip = in_position(gs, me_seat, {p["seat"] for p in live} | {me_seat})

    dead = set(my) | set(board)
    rows = []                         # (player, pool, p_in)
    for p in live:
        bid = p["bot_id"]
        pre_f, post_f = fr.get(bid, (1.0, 1.0))
        p_in = 1.0
        if street == "preflop" and bid not in voluntary and p["state"] == "active":
            # hasn't acted yet: in the pot only if they continue
            o_owed = max(0, cur - p["bet_this_street"])
            if o_owed > 0:
                p_in = max(0.05, 1 - opp(bid).fold_est("pre", o_owed / max(pot + owed, 1)))
        rows.append((p, build_pool(dead, board, pre_f, post_f), p_in))
    # the opponent with the most in front first, so its detail drives eq_top
    rows.sort(key=lambda r: (-r[0]["bet_this_street"], -r[2]))
    pools = [r[1] for r in rows]
    p_ins = [r[2] for r in rows]

    heads_up = len(rows) == 1
    eq, det = monte_carlo(my, board, pools, p_ins, 1500 if heads_up else 900, detail=True)

    realize = {"preflop": 0.92 if ip else 0.82, "flop": 0.97 if ip else 0.92,
               "turn": 0.98 if ip else 0.95, "river": 1.0}[street]

    evs = {}
    if owed == 0:
        evs["check"] = eq * realize * pot
    else:
        evs["fold"] = 0.0
        call_amt = min(owed, stack)
        evs["call"] = eq * realize * (pot + call_amt) - call_amt

    # ── raise candidates ──
    cands = set()
    if max_to > cur and stack > owed:
        if street == "preflop" and cur <= bb:
            limpers = sum(1 for p in players if p["bot_id"] != me and p["bot_id"] in voluntary
                          and p["bet_this_street"] == bb)
            for m in (2.0, 2.5, 3.0, 4.0):
                cands.add(int((m + limpers) * bb))
        elif owed == 0:
            for f in (0.33, 0.5, 0.75, 1.0, 1.5):
                cands.add(my_bet + int(f * pot))
        else:
            for k in (0.5, 0.75, 1.0, 1.5):
                cands.add(cur + int(k * (pot + owed)))
            if street == "preflop":
                cands.add(int(cur * 3))
        cands.add(gs["min_raise_to"])
        cands.add(max_to)
    raises = sorted({min(max(c, gs["min_raise_to"]), max_to) for c in cands})

    best_raise = None
    for R in raises:
        X = R - my_bet
        new_pot = pot + X
        f_all = 1.0
        conts = {}
        for i, (p, _, _) in enumerate(rows):
            if p.get("state") != "active":
                continue
            Y = min(max(0, R - p["bet_this_street"]), p["stack"])
            if Y <= 0:
                continue
            key = st + ("_rr" if p["bot_id"] in raised_now else "")
            fo = opp(p["bot_id"]).fold_est(key, Y / max(new_pot, 1))
            f_all *= fo
            conts[i] = (1 - fo, Y)
        if not conts:
            continue                  # everyone left is all in: raising is just calling
        i_main = max(conts, key=lambda i: conts[i][0])
        c_main, Y_main = conts[i_main]
        if heads_up:
            eq_c = eq_top(det, max(c_main, 0.03), eq)
        else:
            tight, sub_p = [], []
            for i, (p, pool, pin) in enumerate(rows):
                if i in conts:
                    c = max(conts[i][0], 0.05)
                    pre_f, post_f = fr.get(p["bot_id"], (1.0, 1.0))
                    if street == "preflop":
                        tight.append(build_pool(dead, board, pre_f * c, post_f))
                    else:
                        tight.append(build_pool(dead, board, pre_f, post_f * c))
                    sub_p.append(min(1.0, conts[i][0] / max(1 - f_all, 1e-6)))
                else:
                    tight.append(pool)
                    sub_p.append(pin)
            eq_c = monte_carlo(my, board, tight, sub_p, 300)
        rz = 1.0 if R >= max_to or street == "river" else realize
        ev = f_all * pot + (1 - f_all) * (eq_c * rz * (new_pot + Y_main) - X)
        evs["raise_%d" % R] = ev
        if best_raise is None or ev > best_raise[0]:
            best_raise = (ev, R, f_all, eq_c)

    passive = "check" if owed == 0 else ("call" if evs["call"] > 0 else "fold")
    action = passive
    if best_raise and best_raise[0] > max(evs.get(passive, 0.0), 0.0) + 0.02 * pot:
        action = "raise"

    if action == "raise":
        R = best_raise[1]
        out = {"action": "all_in"} if R >= max_to else {"action": "raise", "amount": int(R)}
    elif action == "call" and owed >= stack:
        out = {"action": "all_in"}
    else:
        out = {"action": action}

    top = sorted(evs.items(), key=lambda kv: -kv[1])[:4]
    out["debug"] = {
        "eq": round(eq, 3), "ip": ip, "opps": len(rows),
        "ranges": {r[0]["bot_id"]: [round(x, 2) for x in fr.get(r[0]["bot_id"], (1, 1))] for r in rows},
        "ev_bb": {k: round(v / bb, 2) for k, v in top},
        "raise": None if not best_raise else
        {"to": best_raise[1], "f": round(best_raise[2], 2), "eq_c": round(best_raise[3], 3)},
        "ms": int((time.time() - t0) * 1000),
    }
    return out
