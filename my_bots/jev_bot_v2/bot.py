"""jev_bot_v2: jev_bot with a mixed strategy.

Same request as jev_bot, but instead of always playing Jev's top choice the
action is sampled from Jev's probability distribution: if Jev gives raise 0.6
and call 0.4, the bot raises 60% of the time and calls 40% of the time in that
spot. The raise size is sampled the same way from the size distribution.

Every decision is one HTTP request to Jev. The request's `state` is a compact
summary of the current hand built in code (cards, position, pot odds, stacks
and the action so far split by street), not the raw game state: the raw state's
match_action_log alone was ~70% of the tokens. A short strategy guide is the
"system prompt" (the instructions of a Choice question), and the legal actions
are the Choice options. A second, speculative Choice picks a raise size from a
few pot-relative amounts that are computed in code, since Jev is not a
calculator. There is no equity calculation and no opponent modelling on
purpose: this version measures how far Jev's judgment alone gets.

Needs network access, so it only runs locally (never in the Docker sandbox).
API key: JEV_API_KEY in the environment or in the repo root .env.

Env knobs:
  JEV_MODEL              model id or alias (default jev-latest)
  JEV_SAFETY_MS          time left unused under the decision deadline (default 400)
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

_client = httpx.Client(headers={"Authorization": f"Bearer {API_KEY}",
                                "Content-Type": "application/json"})


# ── The "system prompt" ──────────────────────────────────────────────────────
SYSTEM_PROMPT = {
    "role": "You are a strong, disciplined No-Limit Texas Hold'em player choosing "
            "one action for `game.you`.",
    "how_to_read_game": [
        "`game.cards` are your hole cards and `game.board` the community cards; "
        "cards are rank+suit, ranks 2-9, T, J, Q, K, A, suits s, h, d, c.",
        "`game.to_call` is what a call costs now and `game.pot_odds` the share of the "
        "final pot you would be paying; `game.action` is this hand's betting by street; "
        "`game.opponents` lists the players still in the hand.",
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


# ── Compact game summary ─────────────────────────────────────────────────────
STREETS = ["preflop", "flop", "turn", "river"]
_POSTS = {"ante", "small_blind", "big_blind"}


def _by_street(gs):
    """Split this hand's action_log into streets ("opus_bot raise 300"), since the
    log has no street markers. A street ends once every player still able to act
    has acted since the last raise and matched the highest bet."""
    players = {p["bot_id"]: p for p in gs["players"]}
    log = gs.get("action_log", [])
    last_idx = {a["bot_id"]: i for i, a in enumerate(log)}
    out, bets, acted, folded, allin = [[]], {}, set(), set(), set()
    for i, a in enumerate(log):
        b, act, amt = a["bot_id"], a["action"], a.get("amount", 0)
        if act in _POSTS:
            if act != "ante":
                bets[b] = bets.get(b, 0) + amt
            if players.get(b, {}).get("is_all_in") and last_idx[b] == i:   # all-in posting
                allin.add(b)
            continue
        if act == "fold":
            folded.add(b)
            out[-1].append(f"{b} fold")
        elif act in ("raise", "all_in"):
            verb = "all-in" if act == "all_in" else "raise to" if any(bets.values()) else "bet"
            bets[b] = amt
            acted = {b}
            out[-1].append(f"{b} {verb} {amt}")
        elif act == "call":
            bets[b] = bets.get(b, 0) + amt
            out[-1].append(f"{b} call {amt}")
        else:
            out[-1].append(f"{b} {act}")
        acted.add(b)
        if act == "all_in" or (players.get(b, {}).get("is_all_in") and last_idx[b] == i):
            allin.add(b)
        pending = set(players) - folded - allin
        top = max(bets.values(), default=0)
        if pending <= acted and all(bets.get(p, 0) == top for p in pending):
            out.append([])
            bets, acted = {}, set()
    if len(out) != STREETS.index(gs["street"]) + 1:   # all-in run-outs etc.: no labels
        return [x for street in out for x in street]
    return {s: ", ".join(x) or "-" for s, x in zip(STREETS, out)}


def _position(gs):
    """e.g. "button (acts last postflop), 5 seated"."""
    by_seat = {p["seat"]: p for p in gs["players"] if p["state"] != "out"}
    seats, me, btn = sorted(by_seat), gs["seat_to_act"], gs.get("button_seat")
    if btn in seats:                      # postflop order: left of the button first
        i = seats.index(btn)
        seats = seats[i + 1:] + seats[:i + 1]
    names = {btn: "button", gs.get("sb_seat"): "small blind", gs.get("bb_seat"): "big blind"}
    live = [s for s in seats if not by_seat[s]["is_folded"]]
    last = " (acts last postflop)" if live and live[-1] == me else ""
    role = names.get(me, f"{seats.index(me) + 1} of {len(seats)} to act postflop")
    return f"{role}{last}, {len(seats)} seated"


def _summary(gs):
    owed, pot = gs["amount_owed"], gs["pot"]
    bb = (gs.get("blinds") or {}).get("bb") or 100
    opps = [f"{p['bot_id']}: stack {p['stack']}"
            + (f", bet {p['bet_this_street']}" if p["bet_this_street"] else "")
            + (", all-in" if p["is_all_in"] else "")
            for p in gs["players"]
            if p["bot_id"] != gs["your_bot_id"] and not p["is_folded"] and p["state"] != "out"]
    return {
        "you": gs["your_bot_id"],
        "cards": " ".join(gs["your_cards"]),
        "board": " ".join(gs["community_cards"]) or "none",
        "street": gs["street"],
        "position": _position(gs),
        "pot": pot,
        "to_call": owed,
        "pot_odds": f"{owed / (pot + owed):.0%}" if owed else "free",
        "your_stack": f"{gs['your_stack']} ({gs['your_stack'] / bb:.0f} big blinds)",
        "blinds": f"{bb // 2}/{bb}",
        "opponents": opps,
        "action": _by_street(gs),
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

    state = {"game": _summary(gs)}

    actions = _action_options(gs)
    questions = {
        "action": {
            "type": "choice",
            "instructions": {"task": "Choose the best action for `game.you` "
                                     "in the current hand described by `game`.",
                             **SYSTEM_PROMPT},
            "criteria": actions,
        },
    }
    sizes = _size_options(gs) if "raise" in actions else {}
    if sizes:   # the strategy guide is sent once, with the action question
        questions["raise_size"] = {
            "type": "choice",
            "instructions": "If you raise or bet now, which total amount is best for "
                            "the strength of `game.cards` on `game.board`? Bigger with "
                            "strong hands, smaller with medium hands.",
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
