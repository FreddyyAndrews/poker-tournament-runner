"""
Bot template. Copy this folder to my_bots/<your_bot>/ and write decide().

  - decide() is called once per decision and has 2 seconds to return, or your
    bot folds. Crashes and invalid actions also fold; your bot stays in the game.
  - You may use the Python 3.10 standard library, eval7, numpy, scipy, treys
    and scikit-learn. Nothing else is installed in the sandbox.
  - No network, no file writes, no subprocesses, no eval/exec. You may read
    your own data/ folder, at import time only.
  - Check your bot before submitting:  make validate BOT=my_bots/<your_bot>

The full list of what decide() receives is in the README ("What your bot
sees"); the most useful keys are summarised below.

RETURN FORMAT:
  {"action": "fold"}
  {"action": "check"}                       # only when can_check is True
  {"action": "call"}
  {"action": "raise", "amount": 1200}       # amount = your TOTAL bet this street
  {"action": "all_in"}

CARDS are strings like "As" (ace of spades) or "Td" (ten of diamonds).
"""

import random  # noqa: F401  (seeded per match, so a seeded match replays exactly)


def decide(game_state: dict) -> dict:
    """
    game_state (the most useful keys):
      your_cards        ["Ah", "Kh"]
      community_cards   [] preflop, then 3, 4, 5 cards
      street            "preflop" | "flop" | "turn" | "river"
      pot, current_bet, min_raise_to, amount_owed, can_check
      your_stack, your_bet_this_street, your_bot_id
      blinds            {"sb", "bb", "ante"} for this hand (they rise every 20 hands)
      hand_num          hand number in this match, from 0
      button_seat, sb_seat, bb_seat, seat_to_act
      players           public info per seat: seat, bot_id, stack, state, bet_this_street, ...
      action_log        every action this hand: {"seat", "bot_id", "action", "amount"}
      match_action_log  the last 200 actions across the match, with "hand_num"

    Debugging: anything you print() is saved with the decision, and you can add
    a JSON-serialisable "debug" value to what you return, e.g.
    {"action": "call", "debug": {"equity": 0.4}}. Run with: python3 play.py --show
    """
    if game_state.get("type") == "warmup":     # one call before hand 1; load things here if you like
        return {"action": "check"}

    # ── Your strategy goes here. This example is deliberately simple. ──────────
    cards = game_state["your_cards"]
    pot = game_state["pot"]
    owed = game_state["amount_owed"]

    ranks = [c[0] for c in cards]
    if ranks[0] == ranks[1] and ranks[0] in "AKQJ":           # big pocket pair: raise
        raise_to = max(game_state["min_raise_to"], game_state["current_bet"] + pot)
        return {"action": "raise", "amount": raise_to}
    if game_state["can_check"]:
        return {"action": "check"}
    if owed <= pot * 0.25:                                     # cheap to continue: call
        return {"action": "call"}
    return {"action": "fold"}
