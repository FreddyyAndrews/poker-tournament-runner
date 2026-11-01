"""Offline generator for data/preflop.json: heads-up equity of each of the 169
starting-hand classes against a uniformly random hand (eval7 Monte Carlo).

    $PY my_bots/opus_bot/tools/gen_preflop.py   # writes my_bots/opus_bot/data/preflop.json
"""
import json
import os
import random

import eval7

RANKS = "23456789TJQKA"
SUITS = "shdc"
TRIALS = 20000


def classes():
    for i, hi in enumerate(RANKS):
        for lo in RANKS[: i + 1]:
            if hi == lo:
                yield hi + lo, (hi + "s", lo + "h")
            else:
                yield hi + lo + "s", (hi + "s", lo + "s")
                yield hi + lo + "o", (hi + "s", lo + "h")


def main():
    rng = random.Random(20261012)
    deck = [r + s for r in RANKS for s in SUITS]
    out = {}
    for name, hole in classes():
        mine = [eval7.Card(c) for c in hole]
        rest = [eval7.Card(c) for c in deck if c not in hole]
        score = 0.0
        for _ in range(TRIALS):
            draw = rng.sample(rest, 7)
            board = draw[2:]
            a = eval7.evaluate(mine + board)
            b = eval7.evaluate(draw[:2] + board)
            score += 1.0 if a > b else 0.5 if a == b else 0.0
        out[name] = round(score / TRIALS, 4)
    path = os.path.join(os.path.dirname(__file__), "..", "data", "preflop.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=0, sort_keys=True)
    print(len(out), "classes; AA", out["AA"], "72o", out["72o"])


if __name__ == "__main__":
    main()
