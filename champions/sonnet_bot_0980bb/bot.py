"""sonnet_bot: equity-driven NLHE bot.

Monte Carlo equity (eval7) vs random hands, shrunk when facing aggression,
then pot-odds calls and value bets sized by equity. See README.md.
"""
import random
import time

import eval7

BOT_NAME = "sonnet_bot"
BOT_AVATAR = "robot_1"

BUDGET = 0.6  # seconds of Monte Carlo per decision

_PREFLOP_CACHE = {}


def _equity(hole, board, n_opp, iters):
    h = [eval7.Card(c) for c in hole]
    b = [eval7.Card(c) for c in board]
    seen = set(h + b)
    deck = [c for c in eval7.Deck().cards if c not in seen]
    need = 5 - len(b)
    wins = 0.0
    deadline = time.time() + BUDGET
    done = 0
    for i in range(iters):
        if i % 20 == 0 and time.time() > deadline:
            break
        random.shuffle(deck)
        k = need + 2 * n_opp
        draw = deck[:k]
        full = b + draw[:need]
        mine = eval7.evaluate(h + full)
        best = 0
        tie = 1
        for o in range(n_opp):
            opp = draw[need + 2 * o: need + 2 * o + 2]
            s = eval7.evaluate(opp + full)
            if s > mine:
                best = 1
                break
            if s == mine:
                tie += 1
        if not best:
            wins += 1.0 / tie
        done += 1
    return wins / max(done, 1)


def _agg(state):
    """Fraction of non-blind opponent actions this hand that were raises."""
    me = state["your_bot_id"]
    acts = [a for a in state["action_log"]
            if a["bot_id"] != me and a["action"] in ("call", "raise", "all_in", "check")]
    if not acts:
        return 0.0
    r = sum(1 for a in acts if a["action"] in ("raise", "all_in"))
    return r / len(acts)


def _raise_freq(state):
    """Opponents' raise share over the recent match history (0.25 if unknown)."""
    me = state["your_bot_id"]
    acts = [a for a in state.get("match_action_log", [])
            if a["bot_id"] != me and a["action"] in ("call", "raise", "all_in", "check")]
    if len(acts) < 30:
        return 0.25
    return sum(1 for a in acts if a["action"] in ("raise", "all_in")) / len(acts)


def decide(state):
    street = state["street"]
    hole = state["your_cards"]
    board = state["community_cards"]
    owed = state["amount_owed"]
    pot = state["pot"]
    stack = state["your_stack"]
    mybet = state["your_bet_this_street"]
    min_raise = state["min_raise_to"]
    max_to = stack + mybet
    can_check = state["can_check"]
    n_opp = max(1, sum(1 for p in state["players"]
                       if p["bot_id"] != state["your_bot_id"]
                       and p["state"] in ("active", "all_in")))

    key = None
    if street == "preflop":
        ranks = sorted((c[0] for c in hole))
        key = (tuple(ranks), hole[0][1] == hole[1][1], n_opp)
        eq = _PREFLOP_CACHE.get(key)
        if eq is None:
            eq = _equity(hole, board, n_opp, 1500)
            _PREFLOP_CACHE[key] = eq
    else:
        eq = _equity(hole, board, n_opp, 1500)

    # Facing aggression, opponents are stronger than random: shrink equity.
    agg = _agg(state)
    scale = max(0.4, min(1.2, 1.25 - _raise_freq(state)))
    facing = owed > 0
    adj = eq
    if facing:
        bet_frac = owed / max(pot - owed, 1)
        adj = eq - min(0.18, 0.06 + 0.10 * bet_frac) * (0.6 + agg) * scale
    # Re-raised after we already bet this street: range is strong.
    if facing and street != "preflop" and any(
            a["bot_id"] == state["your_bot_id"] and a["action"] in ("raise", "all_in")
            for a in state["action_log"][-12:]):
        adj -= 0.10 * scale
    # Random-hand equity is inflated multiway; fair share is 1/(n_opp+1).
    fair = 1.0 / (n_opp + 1)
    edge = adj - fair

    debug = {"eq": round(eq, 3), "adj": round(adj, 3), "fair": round(fair, 3), "n": n_opp, "sc": round(scale, 2)}

    def raise_to(frac):
        target = mybet + owed + int(frac * (pot + owed))
        target = max(target, min_raise)
        if target >= max_to * 0.7:
            return {"action": "all_in", "debug": debug}
        return {"action": "raise", "amount": target, "debug": debug}

    def act(a):
        a["debug"] = debug
        return a

    if street == "preflop":
        bb = state["blinds"]["bb"]
        if facing:
            need = owed / (pot + owed)
            if edge > 0.22 and owed < 0.15 * max_to:
                return raise_to(1.0)
            if adj > need + 0.05 and adj > fair + 0.03:
                return act({"action": "call"})
            if can_check:
                return act({"action": "check"})
            return act({"action": "fold"})
        # Unopened / limped: raise good hands
        if edge > 0.06:
            return raise_to(0.9)
        if can_check:
            return act({"action": "check"})
        if edge > 0.0 and owed <= bb:
            return act({"action": "call"})
        return act({"action": "fold"})

    # Postflop
    if facing:
        need = owed / (pot + owed)  # pot odds
        if adj > 0.80 and owed < stack:
            return raise_to(0.75)
        if adj > need + 0.04 and adj > fair + 0.02:
            return act({"action": "call"})
        return act({"action": "fold"})

    # Checked to us
    if edge > 0.30:
        return raise_to(0.75)
    if edge > 0.15:
        return raise_to(0.5)
    if edge > 0.05 and random.random() < 0.35:
        return raise_to(0.4)
    if n_opp == 1 and street != "river" and random.random() < 0.15:
        return raise_to(0.5)  # light bluff
    return act({"action": "check"})
