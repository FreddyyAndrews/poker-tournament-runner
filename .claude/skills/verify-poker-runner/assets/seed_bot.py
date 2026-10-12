"""Verification seed bot: a deliberately leaky "calling station".

It never folds to a bet unless the price is huge, and only raises its very
best hands. It prints its reasoning and returns a `debug` dict on every
decision, so the decision inspector, the logs and analyze.py have something
concrete to show, and there is an obvious leak to fix in the improve step
(calling down with weak hands).
"""

BOT_NAME = "Verify seed bot"

RANKS = "23456789TJQKA"


def strength(cards):
    """Very rough preflop score 0..1 from the two hole cards."""
    a, b = sorted((RANKS.index(c[0]) for c in cards), reverse=True)
    score = (a + b) / 24
    if a == b:
        score += 0.35
    if cards[0][1] == cards[1][1]:
        score += 0.05
    return min(score, 1.0)


def decide(state):
    if state.get("type") == "warmup":
        return {}
    s = strength(state["your_cards"])
    owed, pot = state["amount_owed"], state["pot"]
    price = owed / (pot + owed) if owed else 0.0
    print(f"hand {state['hand_num']} {state['street']} cards {state['your_cards']} "
          f"strength {s:.2f} owed {owed} price {price:.2f}")
    debug = {"strength": round(s, 2), "price": round(price, 2)}

    if s > 0.9 and state["your_stack"] > owed:
        return {"action": "raise", "amount": state["min_raise_to"] * 2, "debug": {**debug, "why": "premium"}}
    if owed == 0:
        return {"action": "check", "debug": {**debug, "why": "free"}}
    if price < 0.45:                      # the leak: calls almost any bet with any hand
        return {"action": "call", "debug": {**debug, "why": "cheap enough"}}
    return {"action": "fold", "debug": {**debug, "why": "too expensive"}}
