"""Side prizes: the measured prizes, tracked across a run from its match logs.

  bluffer           biggest bluffer: chips won by bluffs per 1,000 hands
                    played. A bluff is a hand the bot won by betting or raising
                    after the flop with under BLUFF_EQUITY equity against a
                    random hand (from its own cards and the board, as the
                    timeline's bluff markers) and getting a fold. A bot
                    needs BLUFFER_MIN_HANDS hands to be ranked. Its biggest
                    single bluff is shown too.
  seven_two         most chips won off seven-two, per seven-two hand dealt:
                    the chips won on every hand the bot won holding a seven
                    and a two (suited or not; with or without a showdown;
                    losses don't count against it), divided by how many times
                    it was dealt seven-two. A bot needs SEVEN_TWO_MIN such
                    hands to be ranked. Its best single hand is shown too.
  lowest_showdown   the weakest hand, by rank, to win at showdown, where the
                    winner took at least LOWEST_SHOWDOWN_MIN chips from its
                    opponent and there was a bet on the river (split pots
                    don't count)
  comeback          in a match the bot won, the smallest share of the chips it
                    fell to on the way

Beside the prizes, for the commentator (no prize attached):

  showdowns         the best hands shown down in the run: every showdown on a
                    full board is listed under its best hand, strongest first
                    (a royal flush is named as one). Equal hands are ordered
                    by the hand they beat, then by the chips won.

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

from arena.annotate import BLUFF_EQUITY, equity_vs_random
from arena.events import SUMMARY_FILE, read_events

PRIZES_FILE = "prizes.json"
VERSION = 6
SHOWDOWNS_KEPT = 10            # best showdowns kept per match
SHOWDOWNS_SHOWN = 30           # and listed for a run
LOWEST_SHOWDOWN_MIN = 1000     # chips the winner must take for a showdown to count
BLUFFER_MIN_HANDS = 500        # hands played before a bot is ranked as a bluffer
SEVEN_TWO_MIN = 30            # seven-two hands dealt before a bot is ranked
BOARDS = ("bluffer", "seven_two", "lowest_showdown", "comeback")
# how entries compare: the value that ranks them, and whether more is better
_RANK = {"bluffer": ("amount", True), "seven_two": ("amount", True),
         "lowest_showdown": ("score", False), "comeback": ("share", False)}


def better(board: str, a: dict, b: dict) -> bool:
    """Is entry a better than entry b on this board? Ties go to the earlier one seen."""
    key, more = _RANK[board]
    return a[key] > b[key] if more else a[key] < b[key]


def _is_seven_two(cards) -> bool:
    return sorted(c[0] for c in cards) == ["2", "7"]


_ROYAL = eval7.evaluate([eval7.Card(c) for c in ("As", "Ks", "Qs", "Js", "Ts")])


def hand_name(score: int) -> str:
    return "Royal Flush" if score == _ROYAL else str(eval7.handtype(score))


def _showdown_key(e: dict):
    """Sorts showdown entries best first."""
    return (-e["score"], -(e.get("opponent_score") or 0), -e["amount"])


def _showdown_entry(n, hole, board_cards, deltas, blinds):
    """One showdown as an entry under its best hand; None if it can't be scored."""
    try:
        cards = [eval7.Card(c) for c in board_cards]
        scores = {bot: eval7.evaluate([eval7.Card(c) for c in hc] + cards) for bot, hc in hole.items()
                  if hc and len(hc) == 2 and bot in deltas}
    except Exception:
        return None
    if len(scores) < 2:
        return None
    bot = max(scores, key=lambda b: (scores[b], deltas.get(b, 0)))
    opp = max((b for b in scores if b != bot), key=lambda b: scores[b])
    return {"hand": n, "bot": bot, "cards": hole[bot], "made": hand_name(scores[bot]), "score": scores[bot],
            "opponent": opp, "opponent_cards": hole[opp], "opponent_made": hand_name(scores[opp]),
            "opponent_score": scores[opp], "board": list(board_cards), "amount": deltas.get(bot, 0),
            "split": scores[bot] == scores[opp], "big_blind": blinds.get("bb")}


COUNTS = ("hands", "bluffs", "bluff_won", "seven_two_dealt", "seven_two_wins", "seven_two_won")


def _bluff(bot, folder, hole, board, street):
    """The bluff, if `bot`'s bet on `board` that made `folder` fold was made
    with under BLUFF_EQUITY equity against a random hand; None otherwise. Both
    hands are described as they stood on that board."""
    mine, theirs = hole.get(bot), hole.get(folder)
    if not mine or not theirs or len(board) < 3:
        return None
    try:
        equity = equity_vs_random(tuple(mine), tuple(board))
        cards = [eval7.Card(c) for c in board]
        a = eval7.evaluate([eval7.Card(c) for c in mine] + cards)
        b = eval7.evaluate([eval7.Card(c) for c in theirs] + cards)
    except Exception:
        return None
    if equity >= BLUFF_EQUITY:
        return None
    return {"bot": bot, "cards": mine, "made": str(eval7.handtype(a)), "equity": round(equity, 3),
            "opponent": folder, "opponent_cards": theirs, "opponent_made": str(eval7.handtype(b)),
            "folded_better": b > a, "board": list(board), "street": street, "showdown": False}


def match_entries(events) -> dict:
    """{board: {bot: best entry}} for one match's events, plus "counts":
    {bot: {"seven_two_dealt", "seven_two_wins", "seven_two_won"}}."""
    best = {b: {} for b in BOARDS}
    counts = {}
    showdowns = []

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
                    "showdown": None, "blinds": e.get("blinds") or {}, "aggressor": None, "bluff": None}
        elif t == "match_end":
            first = [r["bot"] for r in e.get("results") or [] if r.get("place") == 1]
            winner = first[0] if len(first) == 1 and e.get("stopped") == "one_player_left" else None
        elif hand is None:
            continue
        elif t == "decision":
            a, st = e["applied"], e.get("state") or {}
            aggressive = a["action"] == "raise" or (
                a["action"] == "all_in" and (a.get("amount") or 0) > st.get("current_bet", 0))
            if e.get("street") == "river" and aggressive:
                hand["river_bet"] = True
            if aggressive:          # remember the latest bet: who, and on which cards
                hand["aggressor"] = (e["bot"], e.get("street"), list(st.get("community_cards") or []))
            elif a["action"] == "fold" and hand["aggressor"] and hand["aggressor"][0] != e["bot"] \
                    and hand["aggressor"][1] != "preflop":
                hand["bluff"] = _bluff(hand["aggressor"][0], e["bot"], hand["hole"], hand["aggressor"][2],
                                       hand["aggressor"][1])
        elif t == "showdown":
            hand["showdown"] = e
        elif t == "hand_end":
            deltas, stacks, board_cards = e.get("deltas") or {}, e.get("stacks") or {}, e.get("board") or []
            total = sum(stacks.values())
            for bot, s in stacks.items():
                if total and (bot not in low or s / total < low[bot][0]):
                    low[bot] = (s / total, hand["n"], s, total)
            for bot, d in deltas.items():
                k = counts.setdefault(bot, dict.fromkeys(COUNTS, 0))
                k["hands"] += 1
                bl = hand["bluff"]
                if bl and bl["bot"] == bot and d > 0:
                    k["bluffs"] += 1
                    k["bluff_won"] += d
                    offer("bluffer", bot, {**{x: v for x, v in bl.items() if x != "bot"}, "hand": hand["n"],
                                           "amount": d, "big_blind": hand["blinds"].get("bb")})
                if _is_seven_two(hand["hole"].get(bot) or []):
                    k["seven_two_dealt"] += 1
                    if d > 0:
                        k["seven_two_wins"] += 1
                        k["seven_two_won"] += d
            if hand["showdown"] is not None and len(board_cards) == 5:
                shown = _showdown_entry(hand["n"], hand["hole"], board_cards, deltas, hand["blinds"])
                if shown:
                    showdowns.append(shown)
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
                if entry["cards"] and _is_seven_two(entry["cards"]):
                    offer("seven_two", bot, entry)
                if sd and hand["river_bet"] and len(winners) == 1 and len(board_cards) == 5 and entry["cards"] \
                        and deltas[bot] >= LOWEST_SHOWDOWN_MIN:
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
    best["counts"] = counts
    best["showdowns"] = sorted(showdowns, key=_showdown_key)[:SHOWDOWNS_KEPT]
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
    totals = {}
    showdowns = []
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
        for bot, k in (boards.get("counts") or {}).items():
            t = totals.setdefault(bot, dict.fromkeys(COUNTS, 0))
            for name in t:
                t[name] += k.get(name, 0)
        for entry in boards.get("showdowns") or []:
            showdowns.append({**entry, "match_id": d.name, "key": f"{run_id}/matches/{d.name}" if run_id else d.name})
        for board in BOARDS:
            for bot, entry in (boards.get(board) or {}).items():
                cur = best[board].get(bot)
                if cur is None or better(board, entry, cur):
                    best[board][bot] = {**entry, "bot": bot, "match_id": d.name,
                                        "key": f"{run_id}/matches/{d.name}" if run_id else d.name}
    out = {}
    for board in BOARDS:
        if board == "seven_two":
            out[board] = _seven_two_rows(totals, best[board])
            continue
        if board == "bluffer":
            out[board] = _bluffer_rows(totals, best[board])
            continue
        key, more = _RANK[board]
        rows = sorted(best[board].values(), key=lambda r: (-r[key] if more else r[key], r["bot"]))
        rank, prev = 0, None
        for i, r in enumerate(rows):
            if r[key] != prev:
                rank, prev = i + 1, r[key]
            r["rank"] = rank
        out[board] = rows
    showdowns.sort(key=lambda e: (*_showdown_key(e), e["match_id"], e["hand"]))
    showdowns = showdowns[:SHOWDOWNS_SHOWN]
    for i, e in enumerate(showdowns):
        e["rank"] = i + 1
    return {"boards": out, "showdowns": showdowns, "matches": counted, "finished": finished, "seven_two_min": SEVEN_TWO_MIN,
            "bluffer_min_hands": BLUFFER_MIN_HANDS, "lowest_showdown_min": LOWEST_SHOWDOWN_MIN}


def _ranked(rows: list, key: str) -> list:
    rows.sort(key=lambda r: (not r["eligible"], -r[key], r["bot"]))
    rank, prev = 0, None
    for i, r in enumerate(rows):
        if not r["eligible"]:
            r["rank"] = None
            continue
        if r[key] != prev:
            rank, prev = i + 1, r[key]
        r["rank"] = rank
    return rows


def _bluffer_rows(totals: dict, best_bluffs: dict) -> list:
    """Every bot: chips won by bluffs per 1,000 hands played, best first."""
    rows = [{"bot": bot, "hands": t["hands"], "bluffs": t["bluffs"], "won": t["bluff_won"],
             "per_1000": round(1000 * t["bluff_won"] / t["hands"], 1),
             "eligible": t["hands"] >= BLUFFER_MIN_HANDS, "best": best_bluffs.get(bot)}
            for bot, t in totals.items() if t["hands"]]
    return _ranked(rows, "per_1000")


def _seven_two_rows(totals: dict, best_hands: dict) -> list:
    """Every bot dealt seven-two: chips won per seven-two dealt, best first.
    Bots short of SEVEN_TWO_MIN hands are listed after the ranked ones, unranked."""
    rows = []
    for bot, t in totals.items():
        if not t["seven_two_dealt"]:
            continue
        rows.append({"bot": bot, "dealt": t["seven_two_dealt"], "wins": t["seven_two_wins"],
                     "won": t["seven_two_won"], "per_dealt": round(t["seven_two_won"] / t["seven_two_dealt"], 1),
                     "eligible": t["seven_two_dealt"] >= SEVEN_TWO_MIN, "best": best_hands.get(bot)})
    return _ranked(rows, "per_dealt")
