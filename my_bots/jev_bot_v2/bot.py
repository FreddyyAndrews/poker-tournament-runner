"""jev_bot_v2: jev_bot with a mixed strategy.

Same request as jev_bot, but instead of always playing Jev's top choice the
action is sampled from Jev's probability distribution: if Jev gives raise 0.6
and call 0.4, the bot raises 60% of the time and calls 40% of the time in that
spot. The raise size is sampled the same way from the size distribution.

Every decision is one HTTP request to Jev. The raw game state is the request's
`state`, a short strategy guide is the "system prompt" (the instructions of a
Choice question), and the legal actions are the Choice options. A second,
speculative Choice picks a raise size from a few pot-relative amounts that are
computed in code, since Jev is not a calculator. There is no context
management, no equity calculation and no opponent modelling on purpose: this
version measures how far Jev's judgment alone gets.

Needs network access, so it only runs locally (never in the Docker sandbox).
API key: JEV_API_KEY in the environment or in the repo root .env.

Env knobs:
  JEV_MODEL              model id or alias (default jev-latest)
  JEV_SAFETY_MS          time left unused under the decision deadline (default 400)
  JEV_INCLUDE_MATCH_LOG  1 (default) sends match_action_log, 0 drops it
"""
import json
import os
import random
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

BOT_NAME = "jev_bot_v2"
API_URL = "https://api.typesafe.ai/v1/systemone"


# ── Config ───────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parents[2] / ".env")   # repo root, never overrides
API_KEY = os.environ.get("JEV_API_KEY")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
SAFETY_MS = int(os.environ.get("JEV_SAFETY_MS", "400"))
INCLUDE_MATCH_LOG = os.environ.get("JEV_INCLUDE_MATCH_LOG", "1") != "0"

_client = httpx.Client(headers={"Authorization": f"Bearer {API_KEY}",
                                "Content-Type": "application/json"})


# ── The "system prompt" ──────────────────────────────────────────────────────
SYSTEM_PROMPT = {
    "role": "You are a strong, disciplined No-Limit Texas Hold'em player choosing "
            "one action for the player whose id is `game.your_bot_id`.",
    "how_to_read_game": [
        "`game.your_cards` are your private hole cards; `game.community_cards` is the board.",
        "`game.pot` is the pot, `game.amount_owed` is what a call costs you now, "
        "`game.your_stack` is what you have left.",
        "`game.action_log` is every action this hand in order; `game.players` shows "
        "each seat's stack, bet and whether they folded or are all-in.",
        "Cards are rank+suit: ranks 2-9, T, J, Q, K, A; suits s, h, d, c.",
    ],
    "strategy": [
        "Preflop: raise strong hands (big pairs, big aces, suited broadways), call "
        "cheaply with playable hands, fold weak offsuit hands to raises.",
        "Postflop: bet and raise for value with strong made hands (two pair or better, "
        "top pair with a good kicker), check or call with medium hands and good draws, "
        "fold weak hands to big bets.",
        "Compare the call price to the pot: a small price can be called with a draw "
        "or a medium hand, a large price needs a strong hand.",
        "Never fold when checking is free. Take free cards with weak hands.",
        "Be more aggressive when few players are in the hand and when opponents have "
        "shown weakness (checked or called passively).",
        "Go all-in only with very strong hands or when the stack is short compared "
        "with the pot.",
    ],
}


# ── Options built in code ────────────────────────────────────────────────────
def _max_total(gs):
    return gs["your_stack"] + gs.get("your_bet_this_street", 0)


def _action_options(gs):
    """Legal actions, in a fixed order, with concrete chip amounts in the text."""
    owed, pot, stack = gs["amount_owed"], gs["pot"], gs["your_stack"]
    opts = {}
    if not gs["can_check"]:
        opts["fold"] = "Give up the hand and lose what you already put in."
    if gs["can_check"]:
        opts["check"] = "Bet nothing and stay in the hand for free."
    if owed > 0:
        price = f"{owed} chips" + (f" ({owed / pot:.0%} of the {pot} pot)" if pot else "")
        if owed >= stack:
            opts["call"] = f"Call for {price}, which is all of your remaining stack."
        else:
            opts["call"] = f"Call for {price} to stay in the hand."
    if _max_total(gs) > gs["min_raise_to"]:
        verb = "Raise" if gs["current_bet"] > 0 else "Bet"
        opts["raise"] = (f"{verb}: put in more chips than needed (at least a total of "
                         f"{gs['min_raise_to']} this street) to build the pot or make "
                         f"opponents fold.")
    opts["all_in"] = f"Bet all of your {stack} remaining chips."
    return opts


def _size_options(gs):
    """Candidate raise totals computed in code; label -> (total, description)."""
    pot, owed, lo, hi = gs["pot"], gs["amount_owed"], gs["min_raise_to"], _max_total(gs)
    base = pot + owed                     # pot once we have matched the current bet
    cands = [("min", lo),
             ("half_pot", gs["current_bet"] + owed + base // 2),
             ("pot", gs["current_bet"] + owed + base),
             ("two_pot", gs["current_bet"] + owed + 2 * base),
             ("all_in", hi)]
    out, seen = {}, set()
    for label, total in cands:
        total = max(lo, min(int(total), hi))
        if total in seen:
            continue
        seen.add(total)
        frac = (total - gs["current_bet"]) / base if base else 0
        desc = f"Raise to a total of {total} chips (about {frac:.1f}x the pot)"
        if total == hi:
            desc = f"All-in: raise to a total of {total} chips, your whole stack"
        elif total == lo:
            desc = f"Minimum raise to a total of {total} chips"
        out[label] = (total, desc)
    return out


# ── Jev request ──────────────────────────────────────────────────────────────
def _ask(state, questions, timeout_s):
    body = {"state": state, "model": MODEL, "questions": questions}
    r = _client.post(API_URL, content=json.dumps(body), timeout=timeout_s)
    r.raise_for_status()
    return r.json()


def _sample(answer, allowed):
    """Draw one option from Jev's probabilities, limited to the options we offered.
    Falls back to Jev's own choice if the distribution is missing or all zero."""
    probs = {k: p for k, p in answer.get("probabilities", {}).items() if k in allowed and p > 0}
    if not probs:
        return answer["choice"]
    return random.choices(list(probs), weights=list(probs.values()))[0]


def _fallback(gs, reason, extra=None):
    print(f"jev_bot_v2 fallback: {reason}")
    action = "check" if gs.get("can_check") else "fold"
    return {"action": action, "debug": {"error": reason, **(extra or {})}}


def decide(game_state: dict) -> dict:
    if game_state.get("type") == "warmup" or "your_cards" not in game_state:
        try:   # warm the TLS connection so the first real decision is faster
            _ask("warmup", {"ok": {"type": "noul", "instructions": "Is this a warmup?"}}, 20)
        except Exception as e:  # noqa: BLE001
            print(f"jev_bot_v2 warmup failed: {e!r}")
        return {"action": "check"}

    gs = game_state
    if not API_KEY:
        return _fallback(gs, "no_api_key")

    t0 = time.time()
    timeout_s = max(0.3, (gs.get("time_limit_ms", 2000) - SAFETY_MS) / 1000)

    state_game = dict(gs)
    if not INCLUDE_MATCH_LOG:
        state_game.pop("match_action_log", None)
    state = {"game": state_game}

    actions = _action_options(gs)
    questions = {
        "action": {
            "type": "choice",
            "instructions": {"task": "Choose the best action for `game.your_bot_id` "
                                     "in the current hand described by `game`.",
                             **SYSTEM_PROMPT},
            "criteria": actions,
        },
    }
    sizes = _size_options(gs) if "raise" in actions else {}
    if sizes:
        questions["raise_size"] = {
            "type": "choice",
            "instructions": {"task": "If `game.your_bot_id` raises or bets now, which "
                                     "total amount is best for the strength of "
                                     "`game.your_cards` on this board?",
                             "strategy": SYSTEM_PROMPT["strategy"]},
            "criteria": {k: v[1] for k, v in sizes.items()},
        }

    try:
        resp = _ask(state, questions, timeout_s)
    except httpx.TimeoutException:
        return _fallback(gs, "timeout", {"latency_ms": round((time.time() - t0) * 1000)})
    except httpx.HTTPStatusError as e:
        return _fallback(gs, f"http_{e.response.status_code}", {"body": e.response.text[:500]})
    except Exception as e:  # noqa: BLE001
        return _fallback(gs, f"error: {e!r}")

    ans = resp["answers"]["action"]
    action = _sample(ans, actions)
    debug = {"model": resp.get("model"), "latency_ms": round((time.time() - t0) * 1000),
             "confidence": round(ans.get("confidence", 0), 3),
             "probabilities": {k: round(v, 3) for k, v in ans["probabilities"].items()},
             "jev_choice": ans["choice"], "sampled": action,
             "usage": resp.get("usage")}
    result = {"action": action, "debug": debug}
    if action == "raise":
        sa = resp["answers"]["raise_size"]
        label = _sample(sa, sizes)
        result["amount"] = sizes[label][0]
        debug["size"] = label
        debug["jev_size_choice"] = sa["choice"]
        debug["size_probabilities"] = {k: round(v, 3) for k, v in sa["probabilities"].items()}
        if label == "all_in":
            result["action"] = "all_in"
    print(f"jev_bot_v2 {gs['street']} {gs['your_cards']} -> {result['action']} "
          f"{result.get('amount', '')} (jev: {ans['choice']}) conf={debug['confidence']} "
          f"{debug['latency_ms']}ms")
    return result
