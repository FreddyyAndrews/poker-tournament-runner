"""Side prizes: the measured prizes, tracked across a run from its match logs.

  single_hand       most chips won off a single hand (net: what the bot took
                    from its opponent)
  seven_two         most chips won off a hand holding a seven and a two
                    (suited or not; with or without a showdown)
  lowest_showdown   the weakest hand, by rank, to win a pot at showdown when
                    there was a bet on the river (split pots don't count)
  comeback          in a match the bot won, the smallest share of the chips it
                    fell to on the way

Each board ranks the bots by their best entry. Every entry names the match
and hand, both players' cards (the logs hold every hole card, shown or not),
the board and the chips won, so the page can link straight to the hand.

Each finished match is scanned once and its best entries saved next to its
log (prizes.json); a run's boards merge them. The first pass over a large run
happens in the background.
"""

import json
import queue
import threading
from pathlib import Path

import eval7

from arena.events import SUMMARY_FILE, read_events

PRIZES_FILE = "prizes.json"
VERSION = 1
BOARDS = ("single_hand", "seven_two", "lowest_showdown", "comeback")
# how entries compare: the value that ranks them, and whether more is better
_RANK = {"single_hand": ("amount", True), "seven_two": ("amount", True),
         "lowest_showdown": ("score", False), "comeback": ("share", False)}


def better(board: str, a: dict, b: dict) -> bool:
    """Is entry a better than entry b on this board? Ties go to the earlier one seen."""
    key, more = _RANK[board]
    return a[key] > b[key] if more else a[key] < b[key]


def _is_seven_two(cards) -> bool:
    return sorted(c[0] for c in cards) == ["2", "7"]


def match_entries(events) -> dict:
    """{board: {bot: best entry}} for one match's events."""
    best = {b: {} for b in BOARDS}

    def offer(board, bot, entry):
        cur = best[board].get(bot)
        if cur is None or better(board, entry, cur):
            best[board][bot] = entry

    hand = None
    low = {}                      # bot -> (share, hand) at its lowest point so far
    winner = None
    for e in events:
        t = e["type"]
        if t == "hand_start":
            hand = {"n": e["hand"], "hole": e.get("hole") or {}, "bb": e.get("bb"), "river_bet": False,
                    "showdown": None, "blinds": e.get("blinds") or {}}
        elif t == "match_end":
            first = [r["bot"] for r in e.get("results") or [] if r.get("place") == 1]
            winner = first[0] if len(first) == 1 and e.get("stopped") == "one_player_left" else None
        elif hand is None:
            continue
        elif t == "decision":
            a, st = e["applied"], e.get("state") or {}
            if e.get("street") == "river" and (a["action"] == "raise" or (
                    a["action"] == "all_in" and (a.get("amount") or 0) > st.get("current_bet", 0))):
                hand["river_bet"] = True
        elif t == "showdown":
            hand["showdown"] = e
        elif t == "hand_end":
            deltas, stacks, board_cards = e.get("deltas") or {}, e.get("stacks") or {}, e.get("board") or []
            total = sum(stacks.values())
            for bot, s in stacks.items():
                if total and (bot not in low or s / total < low[bot][0]):
                    low[bot] = (s / total, hand["n"], s, total)
            winners = [b for b, d in deltas.items() if d > 0]
            for bot in winners:
                opp = next((o for o in deltas if o != bot), None)
                entry = {"hand": hand["n"], "amount": deltas[bot], "cards": hand["hole"].get(bot),
                         "opponent": opp, "opponent_cards": hand["hole"].get(opp), "board": board_cards,
                         "big_blind": hand["blinds"].get("bb"), "showdown": hand["showdown"] is not None}
                sd = hand["showdown"]
                if sd:
                    ranks = sd.get("ranks") or {}
                    entry["made"], entry["opponent_made"] = ranks.get(bot), ranks.get(opp)
                offer("single_hand", bot, entry)
                if entry["cards"] and _is_seven_two(entry["cards"]):
                    offer("seven_two", bot, entry)
                if sd and hand["river_bet"] and len(winners) == 1 and len(board_cards) == 5 and entry["cards"]:
                    try:
                        score = eval7.evaluate([eval7.Card(c) for c in entry["cards"] + board_cards])
                    except Exception:
                        score = None
                    if score is not None:
                        offer("lowest_showdown", bot, {**entry, "score": score})
            hand = None
    if winner and winner in low:
        share, n, chips, total = low[winner]
        best["comeback"][winner] = {"hand": n, "share": round(share, 4), "chips": chips, "total": total,
                                    "opponent": next((b for b in low if b != winner), None)}
    return best


# -- per-match cache, filled in the background ---------------------------------

_queue = queue.Queue()
_pending = set()
_lock = threading.Lock()
_worker = None


def _load(match_dir: Path):
    f = Path(match_dir) / PRIZES_FILE
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text())
    except ValueError:
        return None
    return data.get("boards") if data.get("version") == VERSION else None


def compute(match_dir: Path) -> dict:
    boards = match_entries(read_events(match_dir))
    f = Path(match_dir) / PRIZES_FILE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "boards": boards}))
    tmp.replace(f)
    return boards


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
            _worker = threading.Thread(target=_work, name="sideprizes", daemon=True)
            _worker.start()
    _queue.put(match_dir)


def run_boards(match_dirs, run_id: str = None, wait: bool = False) -> dict:
    """The boards over the finished matches among `match_dirs`:
    {"boards": {board: [entries, best first, each with "bot", "rank", "match_id"
    and "key"]}, "matches": counted, "finished": finished}."""
    best = {b: {} for b in BOARDS}
    counted = finished = 0
    for d in sorted(Path(x) for x in match_dirs):
        if not (d / SUMMARY_FILE).is_file():
            continue
        finished += 1
        boards = _load(d)
        if boards is None:
            if not wait:
                _schedule(d)
                continue
            boards = compute(d)
        counted += 1
        for board in BOARDS:
            for bot, entry in (boards.get(board) or {}).items():
                cur = best[board].get(bot)
                if cur is None or better(board, entry, cur):
                    best[board][bot] = {**entry, "bot": bot, "match_id": d.name,
                                        "key": f"{run_id}/matches/{d.name}" if run_id else d.name}
    out = {}
    for board in BOARDS:
        key, more = _RANK[board]
        rows = sorted(best[board].values(), key=lambda r: (-r[key] if more else r[key], r["bot"]))
        rank, prev = 0, None
        for i, r in enumerate(rows):
            if r[key] != prev:
                rank, prev = i + 1, r[key]
            r["rank"] = rank
        out[board] = rows
    return {"boards": out, "matches": counted, "finished": finished}
