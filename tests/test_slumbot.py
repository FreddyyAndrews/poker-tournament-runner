"""Tests for matches against Slumbot (arena/slumbot.py), against recorded API
responses: no network."""
import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arena import external, slumbot
from arena.events import read_events
from arena.seats import Seat
from arena.verify import verify_match_dir

# Four real hands (always checking or calling; the last shoves and Slumbot folds)
# and one synthetic all-in that runs out the board. Each is the list of
# {"sent", "resp"}: new_hand first, then one act per action.
HANDS = json.loads((Path(__file__).parent / "fixtures" / "slumbot_hands.json").read_text())


class FakeClient:
    """Replays recorded hands in order, checking that we send what was sent
    then. One session per hand in play, like the real API."""

    def __init__(self, hands=HANDS, strict=True):
        self.hands = hands
        self.strict = strict        # False: answer with the recording whatever we send
        self.started = 0
        self.open = {}              # token -> responses still to come
        self.lock = threading.Lock()

    def login(self):
        return None

    def new_hand(self, token=None):
        with self.lock:
            seq = [json.loads(json.dumps(x)) for x in self.hands[self.started % len(self.hands)]]
            self.started += 1
            token = token or f"tok{self.started}"
            assert not self.open.get(token), "a session played two hands at once"
            for x in seq:
                x["resp"]["token"] = token
            self.open[token] = seq[1:]
            return seq[0]["resp"]

    def act(self, token, incr):
        with self.lock:
            x = self.open[token].pop(0)
        assert incr == x["sent"] or not self.strict, f"sent {incr}, the recording sent {x['sent']}"
        return x["resp"]


class Caller(Seat):
    """Checks or calls; shoves on the first decision of hands 4 and 5, like the recording."""

    kind = "bot"

    def __init__(self):
        super().__init__("caller")
        self.seen = []

    def request_action(self, state):
        self.seen.append(state)
        if state["hand_num"] % 5 >= 3 and len(state["action_log"]) == 2:
            return {"action": "all_in"}
        return {"action": "call"}


def play(tmp_path, hands=len(HANDS), workers=1):
    seat = Caller()
    summary = external.run_external_match(slumbot.Slumbot(), seat, hands, match_id="s",
                                          runs_dir=tmp_path, backend=slumbot.Backend(FakeClient()),
                                          workers=workers)
    return seat, summary, read_events(Path(summary["run_dir"]))


def of_type(events, t):
    return [e for e in events if e["type"] == t]


def test_history_tokens():
    assert slumbot.history("b200c/kb400") == ["b200", "c", "_", "k", "b400"]
    assert slumbot.history("b20000c///") == ["b20000", "c", "_", "_", "_"]
    assert slumbot.history("") == []
    with pytest.raises(slumbot.SlumbotError):
        slumbot.history("b200x")


def test_match_follows_the_recording_and_verifies(tmp_path):
    seat, summary, events = play(tmp_path)
    ends = of_type(events, "hand_end")
    last = [h[-1]["resp"] for h in HANDS]
    assert [e["deltas"]["caller"] for e in ends] == [r["winnings"] for r in last]
    assert [e["score"]["caller"] for e in ends] == [r["winnings"] - r["baseline_winnings"] for r in last]
    assert summary["dealer"] == "slumbot" and summary["score"]["caller"]["kind"] == "baseline"
    report = verify_match_dir(Path(summary["run_dir"]))
    assert report["ok"], report["problems"]
    assert any("Slumbot" in w for w in report["warnings"])


def test_positions_and_what_the_bot_sees(tmp_path):
    seat, _, events = play(tmp_path, hands=2)
    # hand 1: we are the big blind and Slumbot opens to 200
    start = of_type(events, "hand_start")[0]
    assert (start["sb"], start["bb"]) == (1, 0)
    first = seat.seen[0]
    assert (first["amount_owed"], first["pot"], first["min_raise_to"], first["can_check"]) == (100, 300, 300, False)
    assert first["legal_actions"] == ["f", "c", "b"]
    # hand 2: we are the small blind; we limp, Slumbot raises to 300
    h2 = [s for s in seat.seen if s["hand_num"] == 1]
    assert (h2[0]["amount_owed"], h2[0]["seat_to_act"], h2[0]["sb_seat"]) == (50, 0, 0)
    assert h2[1]["amount_owed"] == 200 and h2[1]["min_raise_to"] == 500
    show = of_type(events, "showdown")[0]
    assert set(show["reveal"]) == {"caller", "slumbot"} and len(show["reveal"]["slumbot"]) == 2


def test_fold_and_all_in_runout(tmp_path):
    _, _, events = play(tmp_path)
    # hand 4: our shove, Slumbot folds
    d4 = [e for e in of_type(events, "decision") if e["hand"] == 3]
    assert [(e["bot"], e["applied"]["action"]) for e in d4] == [("caller", "all_in"), ("slumbot", "fold")]
    assert d4[0]["applied"]["amount"] == 20000
    # hand 5: shove, call, and the turn and river dealt with no more action
    deals = [e["street"] for e in of_type(events, "deal") if e["hand"] == 4]
    assert deals == ["flop", "turn", "river"]
    show = [e for e in of_type(events, "showdown") if e["hand"] == 4][0]
    assert show["ranks"] == {"caller": "Pair", "slumbot": "Pair"}
    end = of_type(events, "hand_end")[4]
    assert end["deltas"]["caller"] == 20000 and end["pot"] == 40000


def test_sessions_play_one_hand_at_a_time(tmp_path):
    _, summary, events = play(tmp_path, hands=15, workers=3)
    assert summary["hands"] == 15 and len(of_type(events, "hand_end")) == 15
    report = verify_match_dir(Path(summary["run_dir"]))
    assert report["ok"], report["problems"]


def test_too_many_workers_is_refused(tmp_path):
    with pytest.raises(ValueError, match="1-3"):
        play(tmp_path, hands=1, workers=4)


# ---------------------------------------------------------------------------
# poker bench with external opponents, and the usage log / GTO Wizard's cap
# ---------------------------------------------------------------------------

@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    f = tmp_path / "usage.jsonl"
    monkeypatch.setattr(external, "USAGE_FILE", f)
    return f


def test_bench_against_slumbot_scores_luck_adjusted(tmp_path, monkeypatch, usage_file):
    from arena.bench import bench
    # a real bot won't play like the recording, so take its answers regardless
    monkeypatch.setattr(slumbot, "Client", lambda: FakeClient(strict=False))
    r = bench("ref_bot_2", opponents=["slumbot", "aggressor"], hands=6, bench_dir=tmp_path / "bench")
    by = {o["opponent"]: o for o in r["opponents"]}
    s = by["slumbot"]
    assert s["external"] and s["score_kind"] == "baseline" and s["hands"] == 12
    assert s["bb_per_100"] is not None and s["ci95"] is not None and "chips_bb_per_100" in s
    assert not by["aggressor"].get("external") and by["aggressor"]["hands"] == 12
    assert external.usage("slumbot", "ref_bot_2") == 12


def test_gto_wizard_daily_cap(usage_file, monkeypatch):
    from arena import gtow
    g = gtow.GTOWizard()
    monkeypatch.setenv("GTO_WIZARD_DAILY_HANDS_PER_BOT", "100")
    external.check_budget(g, "mybot", 100)
    external.record_usage(g, "mybot", 60, "m1")
    external.record_usage(g, "other", 90, "m2")             # other bots don't count
    external.check_budget(g, "mybot", 40)
    with pytest.raises(external.ExternalError, match="daily cap of 100"):
        external.check_budget(g, "mybot", 41)
    external.check_budget(slumbot.Slumbot(), "mybot", 10 ** 6)   # Slumbot has no cap
