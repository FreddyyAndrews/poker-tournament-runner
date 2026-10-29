"""Tests for matches against GTO Wizard (arena/gtow.py), against recorded API
responses: no network, and none of the monthly hand quota."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arena import external, gtow
from arena.events import read_events
from arena.seats import Seat
from arena.verify import verify_match_dir

# Five real hands, played by always checking or calling. Each is the list of
# responses: POST /hands, then one POST /act per action (with "sent": what we sent).
HANDS = json.loads((Path(__file__).parent / "fixtures" / "gtow_hands.json").read_text())


class FakeClient:
    """Replays recorded hands (in order, repeating), checking that we send what
    was sent then. Safe to use from several threads, like the real API."""

    def __init__(self, hands=HANDS, delay_s=0.0, strict=True):
        self.hands = hands
        self.strict = strict        # False: answer with the recording whatever we send
        self.started = 0
        self.open = {}              # hand_id -> responses still to come
        self.in_play = self.max_in_play = 0
        self.delay_s = delay_s
        self.lock = threading.Lock()

    def new_hand(self):
        with self.lock:
            seq = [json.loads(json.dumps(r)) for r in self.hands[self.started % len(self.hands)]]
            self.started += 1
            hand_id = 1000 + self.started
            for r in seq:
                r["hand_id"] = hand_id
            first = seq.pop(0)
            self.open[hand_id] = seq
            self._track(first)
        return first

    def act(self, hand_id, action, amount=None):
        time.sleep(self.delay_s)
        with self.lock:
            resp = self.open[hand_id].pop(0)
            self._track(resp)
        body = {"action": action} if amount is None else {"action": action, "amount": amount}
        assert body == resp["sent"] or not self.strict, \
            f"sent {body}, the recording sent {resp['sent']}"
        return resp

    def _track(self, resp):
        """Count hands in play: one starts with each new_hand, ends when over."""
        if "sent" not in resp:
            self.in_play += 1
            self.max_in_play = max(self.max_in_play, self.in_play)
        if resp["game_state"]["is_hand_over"]:
            self.in_play -= 1


class Caller(Seat):
    """Checks or calls everything, like the recording; remembers what it saw."""

    kind = "bot"

    def __init__(self, name="caller"):
        super().__init__(name)
        self.seen = []

    def request_action(self, state):
        self.seen.append(state)
        return {"action": "call", "think_ms": 1.0, "logs": "", "debug": {"n": len(self.seen)}}


def play(tmp_path, seat=None, hands=len(HANDS), workers=1, client=None):
    seat = seat or Caller()
    summary = gtow.run_gtow_match(seat, hands, match_id="g", runs_dir=tmp_path,
                                  client=client or FakeClient(), workers=workers)
    return seat, summary, read_events(Path(summary["run_dir"]))


def of_type(events, t):
    return [e for e in events if e["type"] == t]


def test_match_follows_the_recording_and_verifies(tmp_path):
    seat, summary, events = play(tmp_path)
    assert summary["hands"] == 5 and summary["stopped"] == "hand_limit"
    ends = of_type(events, "hand_end")
    winnings = [h[-1]["game_state"]["winnings"] for h in HANDS]
    assert [e["deltas"]["caller"] for e in ends] == winnings
    assert all(e["deltas"]["gto_wizard"] == -e["deltas"]["caller"] for e in ends)
    assert [e["score"]["caller"] for e in ends] == [h[-1]["game_state"]["aivat_score"] for h in HANDS]
    result = {r["bot"]: r for r in summary["results"]}
    assert result["caller"]["delta"] == sum(winnings)
    assert result["caller"]["bb_per_100"] == round(sum(winnings) / 100 * 100 / 5, 2)
    assert summary["score"]["caller"]["hands"] == 5 and summary["score"]["caller"]["kind"] == "AIVAT"

    report = verify_match_dir(Path(summary["run_dir"]))
    assert report["ok"], report["problems"]
    assert any("GTO Wizard" in w for w in report["warnings"])


def test_gto_wizard_folding_preflop(tmp_path):
    _, _, events = play(tmp_path, hands=1)
    start = of_type(events, "hand_start")[0]
    assert start["sb"] == 1 and start["bb"] == 0          # GTO Wizard posted the small blind
    assert start["hole"] == {"caller": ["Ac", "Ts"]}       # its cards are unknown until the end
    (fold,) = of_type(events, "decision")
    assert fold["bot"] == "gto_wizard" and fold["applied"]["action"] == "fold"
    assert of_type(events, "award") == [{"type": "award", "hand": 0, "bot": "caller",
                                         "amount": 150, "pot": "main"}]
    end = of_type(events, "hand_end")[0]
    assert end["opp_hole"] == {"gto_wizard": ["Td", "2h"]} and end["deltas"] == {"caller": 50, "gto_wizard": -50}
    assert not of_type(events, "showdown")


def test_bot_sees_the_engine_state_format(tmp_path):
    seat, _, events = play(tmp_path, hands=2)
    # hand 2 (index 1), flop: GTO Wizard (BB) bets 231 into 700 after we limped and called 350
    s = next(x for x in seat.seen if x["hand_num"] == 1 and x["street"] == "flop")
    assert s["your_cards"] == ["Ah", "6c"] and s["community_cards"] == ["6h", "4c", "7d"]
    assert (s["pot"], s["amount_owed"], s["current_bet"], s["min_raise_to"]) == (931, 231, 231, 462)
    assert (s["your_stack"], s["can_check"], s["seat_to_act"], s["button_seat"]) == (19650, False, 0, 0)
    assert s["legal_actions"] == ["f", "c", "b"]
    assert [a["action"] for a in s["action_log"]] == \
        ["small_blind", "big_blind", "call", "raise", "call", "raise"]
    assert [p["bot_id"] for p in s["players"]] == ["caller", "gto_wizard"]
    # match_action_log carries the previous hand too
    assert s["match_action_log"][0]["hand_num"] == 0
    # every hand_start is followed by both blinds, and the logged pot matches the API
    assert len(of_type(events, "post")) == 4
    show = of_type(events, "showdown")[0]
    assert show["reveal"] == {"caller": ["Ah", "6c"], "gto_wizard": ["As", "Jc"]}
    assert show["ranks"]["caller"] == "Pair"
    assert [d["board"] for d in of_type(events, "deal")] == \
        [["6h", "4c", "7d"], ["6h", "4c", "7d", "Ts"], ["6h", "4c", "7d", "Ts", "5c"]]


def test_gto_wizard_decisions_are_logged_with_what_it_saw(tmp_path):
    _, _, events = play(tmp_path, hands=3)
    # hand 3: GTO Wizard opens to 275 from the small blind before we ever act
    first = [e for e in of_type(events, "decision") if e["hand"] == 2][0]
    assert first["bot"] == "gto_wizard" and first["applied"]["action"] == "raise"
    assert first["applied"]["amount"] == 275 and first["applied"]["pot_after"] == 375
    assert first["state"]["your_cards"] == [] and first["state"]["amount_owed"] == 50
    assert first["think_ms"] is None
    ours = [e for e in of_type(events, "decision") if e["bot"] == "caller"]
    assert ours[0]["reply"] == {"action": "call"} and ours[0]["debug"] == {"n": 1}


def test_concurrent_hands_are_logged_in_order(tmp_path):
    client = FakeClient(delay_s=0.002)
    seat, summary, events = play(tmp_path, hands=20, workers=4, client=client)
    assert client.max_in_play > 1 and summary["workers"] == 4
    # every hand's events are contiguous and in hand order
    hands = [e["hand"] for e in events if "hand" in e and e["type"] != "level"]
    assert hands == sorted(hands) and len(of_type(events, "hand_start")) == 20
    # same results as playing the recording one hand at a time, four times over
    ends = of_type(events, "hand_end")
    winnings = [h[-1]["game_state"]["winnings"] for h in HANDS] * 4
    assert [e["deltas"]["caller"] for e in ends] == winnings
    report = verify_match_dir(Path(summary["run_dir"]))
    assert report["ok"], report["problems"]
    # match_action_log holds earlier hands (those already logged) and this one
    for s in seat.seen:
        assert {a["hand_num"] for a in s["match_action_log"]} <= set(range(s["hand_num"] + 1))
        own = [a for a in s["match_action_log"] if a["hand_num"] == s["hand_num"]]
        assert [{k: a[k] for k in ("bot_id", "action", "amount")} for a in own] == \
            [{k: a[k] for k in ("bot_id", "action", "amount")} for a in s["action_log"]]


def test_workers_default_to_one_when_watched_or_played_live():
    g = gtow.GTOWizard()
    assert external.default_workers(g, Caller()) == gtow.DEFAULT_WORKERS
    assert external.default_workers(g, Caller(), pace_ms=600) == 1
    human = Caller()
    human.kind = "human"
    assert external.default_workers(g, human) == 1


def test_specs_must_be_one_player_against_gto_wizard():
    opp, hero, idx = external.split_specs(["shark", "gto_wizard"])
    assert (opp.name, hero, idx) == ("gto_wizard", "shark", 0)
    opp, hero, idx = external.split_specs(["SLUMBOT", "human:Me"])
    assert (opp.name, hero, idx) == ("slumbot", "human:Me", 1)
    for bad in (["shark", "aggressor", "gto_wizard"], ["gto_wizard", "gto_wizard"], ["gto_wizard"],
                ["gto_wizard", "slumbot"]):
        with pytest.raises(ValueError, match="heads-up"):
            external.split_specs(bad)


@pytest.mark.parametrize("reply, owed, legal, expected", [
    ({"action": "fold"}, 0, ["k", "b"], ("k", None)),        # GTO Wizard won't take a free fold
    ({"action": "fold"}, 100, ["f", "c", "b"], ("f", None)),
    ({"action": "check"}, 100, ["f", "c", "b"], ("c", None)),
    ({"action": "call"}, 0, ["k", "b"], ("k", None)),
    ({"action": "raise", "amount": 10}, 0, ["k", "b"], ("b", 100)),      # snapped up to the min
    ({"action": "raise", "amount": 99999}, 0, ["k", "b"], ("b", 19725)),  # capped at the stack
    ({"action": "raise", "amount": "lots"}, 0, ["k", "b"], ("b", 100)),
    ({"action": "all_in"}, 0, ["k", "b"], ("b", 19725)),
    ({"action": "all_in"}, 500, ["f", "c"], ("c", None)),     # can't raise: call instead
    ({"action": "dance"}, 100, ["f", "c", "b"], ("f", None)),
    ({}, 0, ["k", "b"], ("k", None)),
])
def test_replies_become_api_actions(reply, owed, legal, expected):
    rr = {"min": 100, "max": 19725} if "b" in legal else None
    assert external.to_api_action(reply, {"amount_owed": owed}, legal, rr) == expected


def test_a_changed_game_is_refused():
    resp = json.loads(json.dumps(HANDS[0][0]))
    resp["game"]["starting_stack"] = 10000.0
    with pytest.raises(gtow.GTOWizardError, match="game changed"):
        gtow.check_game(resp)
    gtow.check_game(HANDS[0][0])


def test_tracker_all_in_and_runout(tmp_path):
    t = external.HandTracker({"a": 0, "b": 1}, sb="a", bb="b", stack=20000,
                         blinds={"sb": 50, "bb": 100, "ante": 0})
    assert t.apply("b20000")["action"] == "all_in"
    call = t.apply("c")
    assert call == {"bot": "b", "action": "call", "amount": 19900, "pot_after": 40000, "stack_after": 0}


# ---------------------------------------------------------------------------
# On the server: POST /api/matches with "gto_wizard" as the opponent
# ---------------------------------------------------------------------------

@pytest.fixture
def jobs(tmp_path, monkeypatch):
    from arena.server.jobs import JobManager
    monkeypatch.setenv("GTO_WIZARD_API_KEY", "test-key")
    # real bots don't play like the recording, so take its answers regardless
    monkeypatch.setattr(gtow, "Client", lambda: FakeClient(strict=False))
    return JobManager(tmp_path)


def test_server_job_plays_a_bot_against_gto_wizard(jobs, tmp_path):
    job = jobs.start_match({"bots": ["ref_bot_2", "gto_wizard"], "names": ["me", None],
                            "mode": "cash", "hands": 7, "starting_stack": 10000, "workers": 3})
    job.thread.join(30)
    assert job.status == "complete", job.error
    summary = json.loads((tmp_path / job.id / "summary.json").read_text())
    assert summary["dealer"] == "gto_wizard" and summary["hands"] == 7 and summary["workers"] == 3
    assert {r["bot"] for r in summary["results"]} == {"me", "gto_wizard"}
    assert job.id.startswith("gtow_")


def test_server_job_gives_a_human_seat_against_gto_wizard(jobs):
    job = jobs.start_match({"bots": ["human:Ann", "gto_wizard"], "hands": 1})
    assert [s["name"] for s in job.seats] == ["Ann"] and job.seats[0]["kind"] == "human"
    seat = jobs.hub.get(job.seats[0]["token"])
    # hand 1 of the recording: GTO Wizard folds its small blind, so Ann never acts
    job.thread.join(30)
    assert job.status == "complete", job.error
    assert seat.pending() is None


@pytest.mark.parametrize("p, match", [
    ({"bots": ["shark", "aggressor", "gto_wizard"]}, "heads-up"),
    ({"bots": ["shark", "gto_wizard"], "mode": "freezeout"}, "sets the game"),
    ({"bots": ["shark", "gto_wizard"], "blinds": "100/200"}, "sets the game"),
    ({"bots": ["shark", "gto_wizard"], "seed": 3}, "sets the game"),
])
def test_server_refuses_what_gto_wizard_cant_play(jobs, p, match):
    with pytest.raises(ValueError, match=match):
        jobs.start_match(p)


def test_server_refuses_without_a_key(jobs, monkeypatch):
    monkeypatch.delenv("GTO_WIZARD_API_KEY")
    monkeypatch.setattr(external, "REPO_ENV", Path("/nonexistent/.env"))
    with pytest.raises(ValueError, match="GTO_WIZARD_API_KEY"):
        jobs.start_match({"bots": ["shark", "gto_wizard"]})


def test_cli_refuses_flags_that_change_the_game(capsys):
    from arena import cli
    with pytest.raises(SystemExit):
        cli.main(["match", "shark", "gto_wizard", "--stack", "5000", "--local"])
    assert "--stack" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["match", "shark", "aggressor", "--workers", "3", "--local"])
    assert "only applies to matches against gto_wizard" in capsys.readouterr().err


def test_api_lists_external_bots_and_whether_they_can_play(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from arena.server import create_app
    monkeypatch.setattr(external, "REPO_ENV", Path("/nonexistent/.env"))
    monkeypatch.delenv("GTO_WIZARD_API_KEY", raising=False)
    with TestClient(create_app(tmp_path)) as c:
        bots = {b["name"]: b for b in c.get("/api/external").json()}
        assert bots["gto_wizard"]["configured"] is False and "GTO_WIZARD_API_KEY" in bots["gto_wizard"]["why"]
        assert bots["slumbot"]["configured"] is True          # no key needed
        monkeypatch.setenv("GTO_WIZARD_API_KEY", "k")
        bots = {b["name"]: b for b in c.get("/api/external").json()}
    assert bots["gto_wizard"]["configured"] is True
    assert (bots["slumbot"]["max_workers"], bots["slumbot"]["score_kind"]) == (3, "baseline")
