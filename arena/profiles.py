"""Player profiles: how each bot plays, from its match logs.

Standard poker statistics, over every hand a bot played in a run:

  vpip           % of hands it chose to put chips in before the flop (call or
                 raise; the forced blinds don't count, nor the big blind checking)
  pfr            % of hands it raised before the flop
  three_bet      % of the time it re-raised when facing a single preflop raise
  aggression     postflop bets and raises per postflop call
  fold_to_cbet   % of the time it folded to a continuation bet: the preflop
                 raiser's first bet on the flop
  wtsd           % of the hands where it saw the flop that went to showdown
  won_at_sd      % of its showdowns that won chips
  bluff          % of its postflop bets and raises made with under BLUFF_EQUITY
                 equity against a random hand (as the timeline's bluff markers)
  think_ms       average decision time

Each finished match's counts are worked out once and saved next to its log
(stats.json); a run's profiles add them up, so even a qualifier of thousands
of matches costs little after the first pass, which runs in the background.
"""

import json
import queue
import threading
from collections import defaultdict
from pathlib import Path

from arena.annotate import BLUFF_EQUITY, equity_vs_random
from arena.events import SUMMARY_FILE, read_events

STATS_FILE = "stats.json"
VERSION = 1
COUNTERS = ("hands", "vpip", "pfr", "three_bet_chances", "three_bets", "post_aggressive", "post_calls",
            "cbets_faced", "cbets_folded", "saw_flop", "showdowns", "won_showdowns",
            "aggressive_with_equity", "bluffs", "decisions", "think_ms_total")


def match_counts(events) -> dict:
    """{bot: {counter: n}} for one match's events."""
    c = defaultdict(lambda: dict.fromkeys(COUNTERS, 0))
    hand = None
    for e in events:
        t = e["type"]
        if t == "hand_start":
            hand = {"order": list(e["order"]), "folded": set(), "raises": 0, "last_raiser": None,
                    "pf_raiser": None, "vpip": set(), "pfr": set(), "flop_bettor": None,
                    "cbet_open": False, "at_showdown": set(), "won": set()}
            for p in hand["order"]:
                c[p]["hands"] += 1
        elif hand is None:
            continue
        elif t == "decision":
            _decision(c, hand, e)
        elif t == "deal" and e.get("street") == "flop":
            for p in hand["order"]:
                if p not in hand["folded"]:
                    c[p]["saw_flop"] += 1
        elif t == "showdown":
            hand["at_showdown"] = set(e.get("reveal") or {})
        elif t == "award":
            if e["bot"] in hand["at_showdown"] and (e.get("amount") or 0) > 0:
                hand["won"].add(e["bot"])
        elif t == "hand_end":
            for key, players in (("vpip", hand["vpip"]), ("pfr", hand["pfr"]),
                                 ("showdowns", hand["at_showdown"]), ("won_showdowns", hand["won"])):
                for p in players:
                    c[p][key] += 1
            hand = None
    return {bot: dict(v) for bot, v in c.items()}


def _decision(c, hand, e):
    b, a = e["bot"], e["applied"]["action"]
    amount, st = e["applied"].get("amount") or 0, e.get("state") or {}
    cur = st.get("current_bet", 0)
    aggressive = a == "raise" or (a == "all_in" and amount > cur)
    calling = a == "call" or (a == "all_in" and amount <= cur)
    c[b]["decisions"] += 1
    if isinstance(e.get("think_ms"), (int, float)):
        c[b]["think_ms_total"] += e["think_ms"]
    if e["street"] == "preflop":
        if aggressive or calling:
            hand["vpip"].add(b)
        if hand["raises"] == 1 and hand["last_raiser"] != b:      # facing a single raise
            c[b]["three_bet_chances"] += 1
            c[b]["three_bets"] += aggressive
        if aggressive:
            hand["pfr"].add(b)
            hand["raises"] += 1
            hand["last_raiser"] = hand["pf_raiser"] = b
    else:
        c[b]["post_aggressive"] += aggressive
        c[b]["post_calls"] += calling
        if aggressive:
            cards, board = st.get("your_cards"), st.get("community_cards")
            if cards and board and len(cards) == 2 and len(board) >= 3:
                try:
                    eq = equity_vs_random(tuple(cards), tuple(board))
                except Exception:                    # a malformed card: no equity
                    eq = None
                if eq is not None:
                    c[b]["aggressive_with_equity"] += 1
                    c[b]["bluffs"] += eq < BLUFF_EQUITY
        if e["street"] == "flop":
            if hand["flop_bettor"] is None and aggressive:
                hand["flop_bettor"] = b
                hand["cbet_open"] = b == hand["pf_raiser"]
            elif hand["cbet_open"] and b != hand["flop_bettor"]:
                c[b]["cbets_faced"] += 1                 # the first answer to a c-bet
                c[b]["cbets_folded"] += a == "fold"
                hand["cbet_open"] = False
    if a == "fold":
        hand["folded"].add(b)


def profile(counts: dict) -> dict:
    """The statistics from one bot's summed counters (None where there's no sample)."""
    def ratio(a, b, pct=True):
        if not counts.get(b):
            return None
        return round(100 * counts[a] / counts[b], 1) if pct else round(counts[a] / counts[b], 2)
    return {
        "hands": counts["hands"],
        "vpip": ratio("vpip", "hands"),
        "pfr": ratio("pfr", "hands"),
        "three_bet": ratio("three_bets", "three_bet_chances"),
        "aggression": ratio("post_aggressive", "post_calls", pct=False),
        "fold_to_cbet": ratio("cbets_folded", "cbets_faced"),
        "wtsd": ratio("showdowns", "saw_flop"),
        "won_at_sd": ratio("won_showdowns", "showdowns"),
        "bluff": ratio("bluffs", "aggressive_with_equity"),
        "think_ms": round(counts["think_ms_total"] / counts["decisions"], 1) if counts["decisions"] else None,
    }


# -- per-match cache, filled in the background ---------------------------------

_queue = queue.Queue()
_pending = set()
_lock = threading.Lock()
_worker = None


def _stats_file(match_dir: Path) -> Path:
    return Path(match_dir) / STATS_FILE


def _load(match_dir: Path):
    f = _stats_file(match_dir)
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text())
    except ValueError:
        return None
    return data.get("bots") if data.get("version") == VERSION else None


def compute(match_dir: Path) -> dict:
    """Work out and save one finished match's counts."""
    bots = match_counts(read_events(match_dir))
    tmp = _stats_file(match_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "bots": bots}))
    tmp.replace(_stats_file(match_dir))
    return bots


def _work():
    while True:
        d = _queue.get()
        try:
            compute(d)
        except Exception:                # an unreadable log: skip it, don't stop the worker
            pass
        finally:
            with _lock:
                _pending.discard(d)


def _schedule(match_dir: Path):
    global _worker
    with _lock:
        if match_dir in _pending:
            return
        _pending.add(match_dir)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="profiles", daemon=True)
            _worker.start()
    _queue.put(match_dir)


def run_profiles(match_dirs, wait: bool = False) -> dict:
    """Profiles from the finished matches among `match_dirs`.
    {"bots": {bot: profile}, "matches": counted, "finished": finished}. Matches
    not counted yet are queued for the background worker (or, with wait,
    counted now)."""
    totals = defaultdict(lambda: dict.fromkeys(COUNTERS, 0))
    counted = finished = 0
    for d in match_dirs:
        d = Path(d)
        if not (d / SUMMARY_FILE).is_file():
            continue                                 # still playing
        finished += 1
        bots = _load(d)
        if bots is None:
            if not wait:
                _schedule(d)
                continue
            bots = compute(d)
        counted += 1
        for bot, counts in bots.items():
            for k in COUNTERS:
                totals[bot][k] += counts.get(k, 0)
    return {"bots": {bot: profile(t) for bot, t in totals.items()}, "matches": counted, "finished": finished}
