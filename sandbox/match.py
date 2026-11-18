"""
Legacy match runner (from Full House Engine): plays a multi-hand match between
2-9 bots without the arena (no event log, tables or formats). The bot process
itself lives in pokercore/botproc.py; this module re-exports it for old callers.

  python3 sandbox/match.py bots/a/bot.py bots/b/bot.py --hands 200
  USE_DOCKER=true python3 sandbox/match.py ...   (or ./sandbox.sh match ...)
"""

import json
import random
import time
import uuid
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pokercore.botproc import (  # noqa: E402,F401
    ACTION_TIMEOUT, HOST_GRACE_S, USE_DOCKER, WARMUP_TIMEOUT, BotProcess, derive_seed,
)
from pokercore.engine import PokerEngine, STARTING_STACK  # noqa: E402

# Per-match rolling action log exposed to bots in state["match_action_log"].
# Lets bots build cross-hand opponent models within a match.
MATCH_LOG_MAX_ENTRIES = 200


# ---------------------------------------------------------------------------
# Match runner
# ---------------------------------------------------------------------------

def _inject_match_log(state, match_log):
    if state.get("type") == "action_request":
        state["match_action_log"] = match_log[-MATCH_LOG_MAX_ENTRIES:]
    return state


def run_match(match_id, bot_paths, n_hands=400, verbose=False, seed=None,
              time_limit_ms=None, use_docker=None):
    """Play n_hands (or until one bot has all the chips).

    time_limit_ms: per-decision limit, an int for every bot or a dict
                   {bot_id: ms}; defaults to ACTION_TIMEOUT.
    seed:          match seed; generated (and returned) when omitted.
    """
    bot_ids = list(bot_paths.keys())
    n = len(bot_ids)
    assert 2 <= n <= 9, "Need 2-9 bots, got " + str(n)

    if seed is None:
        seed = random.SystemRandom().randrange(2 ** 31)
    if not isinstance(time_limit_ms, dict):
        time_limit_ms = {bid: time_limit_ms for bid in bot_ids}

    procs   = {bid: BotProcess(bid, path, seed=derive_seed(seed, "bot", bid),
                               use_docker=use_docker)
               for bid, path in bot_paths.items()}
    stacks  = {bid: STARTING_STACK for bid in bot_ids}
    hand_log = []
    match_action_log = []
    dealer = 0
    start_ts = time.time()

    try:
        # Warm-up: give every bot one long-deadline call so they can finish
        # heavy imports / lookup-table loads before hand 1. In parallel, so a
        # table of slow loaders takes as long as the slowest, not the sum.
        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(lambda p: p.warmup(), procs.values()))

        for hand_num in range(n_hands):
            alive = [bid for bid in bot_ids if stacks[bid] > 0]
            if len(alive) < 2:
                break

            hand_id = match_id + "_h" + str(hand_num).zfill(4)
            hand_seed = seed * 1000003 + hand_num
            engine = PokerEngine(
                hand_id        = hand_id,
                bot_ids        = alive,
                dealer_seat    = dealer % len(alive),
                starting_stacks= {bid: stacks[bid] for bid in alive},
                seed           = hand_seed,
                hand_num       = hand_num,
            )

            result = _play_hand(engine, procs, alive, match_action_log, hand_num,
                                verbose, time_limit_ms)
            hand_log.append({"hand_num": hand_num, "hand_id": hand_id, **result})

            for bid, s in result["final_stacks"].items():
                stacks[bid] = s

            dealer += 1

            if verbose and hand_num % 25 == 0:
                _print_stacks(hand_num, n_hands, stacks)

    finally:
        for p in procs.values():
            p.stop()

    return {
        "match_id":     match_id,
        "bot_ids":      bot_ids,
        "seed":         seed,
        "n_hands":      len(hand_log),
        "duration_s":   round(time.time() - start_ts, 2),
        "final_stacks": stacks,
        "chip_delta":   {bid: stacks[bid] - STARTING_STACK for bid in bot_ids},
        "bot_errors":   {bid: procs[bid].errors for bid in bot_ids},
        "bot_stats":    {bid: procs[bid].summary() for bid in bot_ids},
        "bot_load_logs": {bid: procs[bid].load_logs for bid in bot_ids},
        "bot_stderr":   {bid: procs[bid].stderr_lines() for bid in bot_ids},
        "hands":        hand_log,
    }


def _extend_match_log(engine, match_action_log, hand_num, start):
    """Copy the engine's action_log entries from `start` (posts and applied
    actions) into the match log. Returns the new count."""
    for a in engine.action_log[start:]:
        match_action_log.append({"hand_num": hand_num, **a})
    return len(engine.action_log)


def _play_hand(engine, procs, active_bots, match_action_log, hand_num, verbose,
               time_limit_ms=None):
    state = engine.start_hand()
    logged = _extend_match_log(engine, match_action_log, hand_num, 0)   # blind/ante posts
    state = _inject_match_log(state, match_action_log)
    decisions = []
    steps = 0
    time_limit_ms = time_limit_ms or {}

    while state.get("type") == "action_request":
        seat   = state["seat_to_act"]
        bot_id = active_bots[seat]
        reply  = procs[bot_id].act(state, time_limit_ms.get(bot_id))
        action = {"action": reply.get("action")}
        if "amount" in reply:
            action["amount"] = reply["amount"]

        if verbose:
            print("  [" + bot_id + "] " + str(action), file=sys.stderr)

        decisions.append({
            "seat":           seat,
            "bot_id":         bot_id,
            "street":         state["street"],
            "action":         action["action"],
            "amount":         action.get("amount"),
            "think_ms":       reply.get("think_ms"),
            "logs":           reply.get("logs", ""),
            "logs_truncated": reply.get("logs_truncated", False),
            "debug":          reply.get("debug"),
            "debug_error":    reply.get("debug_error"),
            "error":          reply.get("error"),
            "error_detail":   reply.get("error_detail"),
        })
        state = engine.apply_action(seat, action)
        logged = _extend_match_log(engine, match_action_log, hand_num, logged)
        state = _inject_match_log(state, match_action_log)
        steps += 1

        if steps > 1000:
            raise RuntimeError("Hand exceeded 1000 steps: " + engine.hand_id)

    state["decisions"] = decisions
    return state


def _print_stacks(hand_num, total, stacks):
    print("\n  === Hand " + str(hand_num) + "/" + str(total) + " ===", file=sys.stderr)
    for bid, s in sorted(stacks.items(), key=lambda x: -x[1]):
        bar = "X" * (s // 1000)
        print("  " + bid.ljust(20) + " " + str(s).rjust(7) + "  " + bar, file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entrypoint for local testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a match locally")
    parser.add_argument("bots", nargs="+",
                        help="Paths to bot.py files, bot directories, or bot.zip archives")
    parser.add_argument("--hands", type=int, default=400)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="Output result as JSON (for worker)")
    parser.add_argument("--match-id", default=None)
    parser.add_argument("--seed", type=int, default=None,
                        help="Match seed. Same seed + same bots = same cards and same bot RNG. "
                             "Generated (and printed) when omitted.")
    parser.add_argument("--time-limit-ms", type=int, default=None,
                        help="Per-decision time limit (default: ACTION_TIMEOUT, 2000 ms)")
    parser.add_argument("--docker", action="store_true",
                        help="Run bots in the Docker sandbox (build it first: ./sandbox.sh build)")
    args = parser.parse_args()

    paths = {}
    for i, path in enumerate(args.bots):
        pp = Path(path)
        suffix = pp.suffix
        # Default bot_id from filename, but if it would collide (very common
        # with the "bots/<name>/bot.py" layout where every stem is "bot"),
        # fall back to the parent directory name. If that also collides
        # (e.g. the same bot path passed twice for self-play testing),
        # append a numeric suffix so every entry is unique.
        if suffix in (".py", ".zip"):
            bot_id = pp.stem
        else:
            bot_id = pp.name or "bot_" + str(i)
        if bot_id in paths or bot_id in ("bot",):
            bot_id = pp.parent.name or ("bot_" + str(i))
        base = bot_id or "bot_" + str(i)
        bot_id = base
        n = 2
        while bot_id in paths:
            bot_id = base + "_" + str(n)
            n += 1
        paths[bot_id] = path

    match_id = args.match_id or os.environ.get("MATCH_ID") or "local_" + uuid.uuid4().hex[:8]

    if not args.json:
        print("Starting match " + match_id + " with " + str(len(paths)) + " bots, " + str(args.hands) + " hands\n")

    result = run_match(match_id, paths, n_hands=args.hands, verbose=args.verbose, seed=args.seed,
                       time_limit_ms=args.time_limit_ms, use_docker=args.docker or None)

    if args.json:
        print(json.dumps({
            "match_id":     result["match_id"],
            "seed":         result["seed"],
            "n_hands":      result["n_hands"],
            "duration_s":   result["duration_s"],
            "final_stacks": result["final_stacks"],
            "chip_delta":   result["chip_delta"],
            "bot_errors":   result["bot_errors"],
            "bot_stats":    result["bot_stats"],
        }))
        sys.exit(0)

    print("\n" + "=" * 50)
    print("Match complete in " + str(result["duration_s"]) + "s  (seed " + str(result["seed"]) + ")")
    print("=" * 50)
    print("Bot".ljust(25) + " " + "Final Stack".rjust(12) + " " + "Delta".rjust(10))
    print("-" * 50)
    for bid in sorted(result["bot_ids"], key=lambda b: -result["final_stacks"][b]):
        delta = result["chip_delta"][bid]
        sign  = "+" if delta >= 0 else ""
        print(bid.ljust(25) + " " + str(result["final_stacks"][bid]).rjust(12) + " " + sign + str(delta).rjust(9))
    print("\nHands played: " + str(result["n_hands"]))

    print("\n" + "Bot".ljust(25) + " " + "Decisions".rjust(9) + " " + "Avg ms".rjust(8)
          + " " + "Max ms".rjust(8) + " " + "Timeouts".rjust(8) + " " + "Errors".rjust(6))
    for bid in result["bot_ids"]:
        st = result["bot_stats"][bid]
        print(bid.ljust(25) + " " + str(st["decisions"]).rjust(9)
              + " " + ("%.2f" % st["avg_think_ms"]).rjust(8)
              + " " + ("%.2f" % st["max_think_ms"]).rjust(8)
              + " " + str(st["timeouts"]).rjust(8) + " " + str(st["errors"]).rjust(6))

    errs = {b: e for b, e in result["bot_errors"].items() if e}
    if errs:
        print("\nBot errors: " + str(errs))
