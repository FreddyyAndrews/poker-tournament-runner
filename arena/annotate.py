"""Spectator annotations: facts about a match a viewer (or a commentator)
wants that the log doesn't state outright.

  equity   on a bet, raise or all-in after the flop: the bettor's equity
           against a random hand, P(win) + P(tie)/2, from what the bettor knew
           at that moment (its two cards and the board). The timeline marks a
           hand as a bluff when this is below BLUFF_EQUITY.

Annotations are added only when events are sent to spectators, never to the
logs, and only to events that have already happened, so a live match or a
broadcast gives nothing away. Players' own views (`as=<player>`) never get
them: the equity of an opponent's bet would leak its cards.
"""

import hashlib
import itertools
import random
from functools import lru_cache

import eval7

BLUFF_EQUITY = 0.40               # below this, a postflop bet or raise counts as a bluff
AGGRESSIVE = ("raise", "all_in")
_SAMPLES = 1500                   # Monte Carlo deals on the flop and turn (the river is exact)
_DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]


@lru_cache(maxsize=100_000)
def equity_vs_random(cards: tuple, board: tuple) -> float:
    """Equity of `cards` against one random hand on `board` (3-5 cards).
    Deterministic: the same cards and board always give the same number."""
    mine = [eval7.Card(c) for c in cards]
    known = [eval7.Card(c) for c in board]
    rest = [eval7.Card(c) for c in _DECK if c not in cards and c not in board]
    missing = 5 - len(known)
    win = tie = total = 0

    def score(opp, runout):
        nonlocal win, tie, total
        full = known + list(runout)
        a, b = eval7.evaluate(mine + full), eval7.evaluate(list(opp) + full)
        win += a > b
        tie += a == b
        total += 1

    if missing == 0:                                      # river: every opponent hand
        for opp in itertools.combinations(rest, 2):
            score(opp, ())
    else:
        # str hashes change from process to process; this seed doesn't
        rng = random.Random(int(hashlib.sha256(' '.join(cards + board).encode()).hexdigest()[:12], 16))
        for _ in range(_SAMPLES):
            draw = rng.sample(rest, 2 + missing)
            score(draw[:2], draw[2:])
    return (win + tie / 2) / total


def annotate(event: dict) -> dict:
    """The event, with `equity` added to postflop bets, raises and all-ins."""
    if event.get("type") != "decision" or event.get("street") == "preflop":
        return event
    applied = event.get("applied") or {}
    if applied.get("action") not in AGGRESSIVE:
        return event
    state = event.get("state") or {}
    cards, board = state.get("your_cards"), state.get("community_cards")
    if not cards or len(cards) != 2 or not board or len(board) < 3:
        return event
    try:
        eq = equity_vs_random(tuple(cards), tuple(board))
    except Exception:               # a malformed card in an old log: no annotation
        return event
    return {**event, "equity": round(eq, 3)}


def annotate_line(line: str) -> str:
    """A raw log line, annotated if it is a decision that may need it (cheap
    check first: most lines are passed through without parsing)."""
    import json
    if '"decision"' not in line or ('"raise"' not in line and '"all_in"' not in line):
        return line
    event = json.loads(line)
    out = annotate(event)
    return line if out is event else json.dumps(out)
