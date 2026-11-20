"""Tournament day: intake (submission links -> entries/) and the qualifier
(time-boxed duplicate round robins)."""

import shutil
import subprocess
from pathlib import Path

from arena import formats, intake, verify

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "bots" / "template" / "bot.py"


def _team_repo(tmp_path, team, source=None, folder=None):
    repo = tmp_path / "repos" / team
    bot = repo / "my_bots" / (folder or team)
    bot.mkdir(parents=True)
    (bot / "bot.py").write_text(source if source is not None else TEMPLATE.read_text())
    (repo / "pokercore").mkdir()
    (repo / "pokercore" / "engine.py").write_text("# a team's edits to their copy of the kit don't matter\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "bot"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


def test_intake_freezes_validates_and_smoke_tests(tmp_path):
    good = _team_repo(tmp_path, "good_team")
    other = _team_repo(tmp_path, "renamed", folder="whatever")         # the only bot under my_bots/
    pandas = _team_repo(tmp_path, "pandas_team", "import pandas\n" + TEMPLATE.read_text())
    crash = _team_repo(tmp_path, "crash_team", "def decide(s):\n    return 1 / 0\n")
    subs = intake.read_submissions_text(
        f"# links\ngood_team {good}\nrenamed {other}\npandas_team {pandas}\ncrash_team {crash}\n"
        f"missing_team {tmp_path / 'nope'}\n")
    entries = tmp_path / "entries"
    result = intake.run_intake(subs, entries_dir=entries, use_docker=False, workers=2)
    by = {r["team"]: r for r in result["teams"]}
    assert result["accepted"] == ["good_team", "renamed"]
    assert len(by["good_team"]["commit"]) == 40 and by["renamed"]["found_at"] == "my_bots/whatever"
    assert "pandas" in by["pandas_team"]["problem"]
    assert by["crash_team"]["problem"].startswith("smoke match")
    assert by["missing_team"]["problem"].startswith("clone failed")
    assert sorted(p.name for p in entries.iterdir() if p.is_dir()) == ["_repos", "good_team", "renamed"]
    assert sorted(p.name for p in (entries / "good_team").iterdir()) == ["bot.py"]    # only the bot
    assert "accepted" in intake.table(result)

    # the crashing team fixes its bot; re-collecting it leaves everyone else frozen
    fixed = crash / "my_bots" / "crash_team" / "bot.py"
    fixed.write_text(TEMPLATE.read_text())
    subprocess.run(["git", "-C", str(crash), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qam", "fix"], check=True)
    (good / "my_bots" / "good_team" / "bot.py").write_text("late = True\n" + TEMPLATE.read_text())
    subprocess.run(["git", "-C", str(good), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qam", "pushed after the deadline"], check=True)
    again = intake.run_intake(subs, entries_dir=entries, use_docker=False, only=["crash_team"])
    assert again["accepted"] == ["good_team", "renamed", "crash_team"]
    assert {r["team"]: r for r in again["teams"]}["good_team"]["commit"] == by["good_team"]["commit"]
    assert "late" not in (entries / "good_team" / "bot.py").read_text()


def test_read_submissions_rejects_bad_lines(tmp_path):
    import pytest
    for text in ("just_a_name\n", "Bad-Name! x\n", "a_team x\na_team y\n"):
        with pytest.raises(ValueError):
            intake.read_submissions_text(text)


def test_qualifier_plays_whole_cycles_and_verifies(tmp_path):
    cfg = formats.RunConfig(format="qualifier", bots=["template", "aggressor", "mathematician"],
                            blinds="turbo", time_budget_min=10, max_cycles=2, seed=3)
    res = formats.run(cfg, run_id="q", runs_dir=tmp_path, workers=3)
    assert res["cycles"] == 2 and len(res["matches"]) == 2 * 3 * 2
    table = res["standings"]
    assert sum(r["wins"] + r["draws"] + r["losses"] for r in table) == 2 * 2 * 3    # 3 pairings x 2 cycles, 2 bots each
    assert sum(r["points"] for r in table) == 2 * 3                                  # one point per pairing
    assert all(r["qualified"] for r in table)                                        # fewer than 4 bots
    assert verify.verify_run_dir(tmp_path / "q")["ok"]


def test_qualifier_stops_when_the_budget_cant_fit_another_cycle(tmp_path):
    cfg = formats.RunConfig(format="qualifier", bots=["template", "aggressor"], blinds="turbo",
                            time_budget_min=0.0001, seed=4)
    res = formats.run(cfg, run_id="q", runs_dir=tmp_path, workers=2)
    assert res["cycles"] == 1                       # the first cycle always plays, then time is up
    shutil.rmtree(tmp_path / "q")


def test_broadcast_replays_a_finished_bracket_as_a_new_run(tmp_path):
    from arena.broadcast import broadcast
    cfg = formats.RunConfig(format="bracket", bots=["shark", "aggressor", "mathematician", "template"],
                            blinds="turbo", mode="freezeout", hands=2000, seeding="given", seed=11)
    src = formats.run(cfg, run_id="final", runs_dir=tmp_path, workers=2)
    seen = []
    out = broadcast("final", "final_show", tmp_path, name="Final", pace_ms=0,
                    progress=[lambda e: seen.append((e["event"], e["match_id"]))])
    order = [m["match_id"] for m in src["matches"]]
    assert seen == [(ev, m) for m in order for ev in ("match_start", "match_done")]   # in playing order
    assert out["status"] == "complete" and out["broadcast"] is True
    assert out["standings"] == src["standings"]
    assert verify.verify_run_dir(tmp_path / "final_show")["ok"]


def test_a_qualifier_is_never_paced(tmp_path):
    from arena.server.jobs import JobManager
    jobs = JobManager(tmp_path)
    job = jobs.start_run({"format": "qualifier", "bots": ["template", "aggressor"], "blinds": "turbo",
                          "max_cycles": 1, "pace_ms": 600})
    job.thread.join(120)
    assert job.status == "complete"


def test_bots_see_the_street_of_every_action_and_cards_shown_at_showdown(tmp_path):
    from pokercore import structures
    from pokercore.seats import BotSeat
    from pokercore.table import MatchConfig, play_match
    from arena.verify import MemoryLog
    from arena import bots as botlib
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "bot.py").write_text(
        "import json\n"
        "def decide(s):\n"
        "    every = s['action_log'] + s['match_action_log']\n"
        "    shows = [a for a in s['match_action_log'] if a['action'] == 'show']\n"
        "    print(json.dumps({'streets': sorted({a.get('street', '?') for a in every}), 'shows': shows[-2:]}))\n"
        "    return {'action': 'check'} if s['can_check'] else {'action': 'call'}\n")
    seats = [BotSeat(s, seed=1, use_docker=False) for s in botlib.resolve_all([str(probe), "template"])]
    log = MemoryLog()
    play_match(seats, MatchConfig(mode="fixed", hands=30, structure=structures.flat(50, 100), seed=5), "t", log)
    import json
    seen = [json.loads(e["logs"]) for e in log.events if e["type"] == "decision" and e["bot"] == "probe"]
    streets = {st for s in seen for st in s["streets"]}
    assert streets <= {"preflop", "flop", "turn", "river", "showdown"} and "flop" in streets
    shows = [a for s in seen for a in s["shows"]]
    assert shows and all(len(a["cards"]) == 2 and a["street"] == "showdown" for a in shows)
    assert {a["bot_id"] for a in shows} == {"probe", "template"}            # both players' cards


def test_equity_against_a_random_hand():
    from arena.annotate import equity_vs_random
    assert equity_vs_random(("Ah", "5h"), ("Kh", "9h", "2h", "Tc", "3d")) == 1.0      # nut flush, river (exact)
    assert 0.80 < equity_vs_random(("Ah", "Ad"), ("7c", "8d", "2s")) < 0.90
    assert equity_vs_random(("7h", "2c"), ("As", "Kd", "Qc")) < 0.30
    assert equity_vs_random(("9s", "8s"), ("Ts", "Jd", "2c")) == equity_vs_random(("9s", "8s"), ("Ts", "Jd", "2c"))


def test_spectators_see_the_equity_behind_postflop_bets_and_players_dont(tmp_path):
    from fastapi.testclient import TestClient
    from arena.server.app import create_app
    from arena.table import run_match, MatchConfig
    from arena import bots as botlib
    from pokercore import structures
    run_match(botlib.resolve_all(["aggressor", "template"]),
              MatchConfig(mode="fixed", hands=40, structure=structures.flat(50, 100), seed=3),
              match_id="m", runs_dir=tmp_path)
    client = TestClient(create_app(runs_dir=tmp_path))
    seen = client.get("/api/matches/m/events").json()["events"]
    bets = [e for e in seen if e["type"] == "decision" and e["street"] != "preflop"
            and e["applied"]["action"] in ("raise", "all_in")]
    assert bets and all(0 <= e["equity"] <= 1 for e in bets)
    others = [e for e in seen if e["type"] == "decision" and e not in bets]
    assert all("equity" not in e for e in others)
    as_player = client.get("/api/matches/m/events?as=template").json()["events"]
    assert all("equity" not in e for e in as_player)


def test_player_profiles_describe_how_each_bot_plays(tmp_path):
    from arena.profiles import run_profiles, STATS_FILE
    from arena.server.progress import qualifier_progress
    cfg = formats.RunConfig(format="qualifier", bots=["aggressor", "mathematician", "template"],
                            blinds="turbo", time_budget_min=10, max_cycles=2, seed=5)
    formats.run(cfg, run_id="q", runs_dir=tmp_path, workers=3)
    matches = sorted((tmp_path / "q" / "matches").iterdir())
    prof = run_profiles(matches, wait=True)
    assert prof["matches"] == prof["finished"] == 12
    assert all((m / STATS_FILE).is_file() for m in matches)                   # counted once, saved
    bots = prof["bots"]
    assert set(bots) == {"aggressor", "mathematician", "template"}
    assert bots["aggressor"]["pfr"] > 50 and bots["mathematician"]["pfr"] == 0  # raises always / never
    for p in bots.values():
        assert p["hands"] > 0 and 0 <= p["vpip"] <= 100 and p["pfr"] <= p["vpip"]
    assert qualifier_progress(tmp_path / "q", cfg, "q")["profiles"]["bots"].keys() == bots.keys()


def test_qualifier_ties_on_points_are_settled_head_to_head():
    from arena.formats import qualifier

    def leg(cycle, a, b, winner, leg_no):
        loser = b if winner == a else a
        res = [{"bot": winner, "place": 1, "final_stack": 20000, "delta": 10000, "hands_played": 50,
                "bb_per_100": 10.0, "busted_on_hand": None, "timeouts": 0, "errors": 0},
               {"bot": loser, "place": 2, "final_stack": 0, "delta": -10000, "hands_played": 50,
                "bb_per_100": -10.0, "busted_on_hand": 49, "timeouts": 0, "errors": 0}]
        return {"match_id": f"c{cycle:02d}_{a}_vs_{b}_leg{leg_no}", "cycle": cycle, "players": [a, b],
                "leg": leg_no, "results": res, "hands": 50, "stopped": "one_player_left"}

    def pairing(a, b, winner):                      # winner takes both legs
        return [leg(1, a, b, winner, 1), leg(1, a, b, winner, 2)]
    # zed and amy both finish on 2 points; zed beat amy, so zed is ahead despite the alphabet
    recs = (pairing("amy", "zed", "zed") + pairing("amy", "bob", "amy") + pairing("amy", "cat", "amy")
            + pairing("zed", "bob", "zed") + pairing("zed", "cat", "cat") + pairing("bob", "cat", "bob"))
    cfg = formats.RunConfig(format="qualifier", bots=["amy", "bob", "cat", "zed"])
    table = qualifier.standings(cfg, recs)
    assert [(r["bot"], r["points"]) for r in table[:2]] == [("zed", 2.0), ("amy", 2.0)]
    assert table[0]["head_to_head"] == 1.0 and table[1]["head_to_head"] == 0.0
    assert [(r["bot"], r["place"]) for r in table] == [("zed", 1), ("amy", 2), ("bob", 3), ("cat", 4)]


def test_a_stopped_qualifier_still_shows_standings_for_its_complete_cycles(tmp_path):
    import threading
    import pytest
    from arena.pacing import Cancelled
    from arena.server.progress import qualifier_progress
    cancel = threading.Event()
    done = []

    def progress(e):
        if e["event"] == "match_done":
            done.append(e)
            if len(done) == 8:                      # cycle 1 (6 matches) done, cycle 2 under way
                cancel.set()
    cfg = formats.RunConfig(format="qualifier", bots=["aggressor", "mathematician", "template"],
                            blinds="turbo", time_budget_min=10, max_cycles=3, seed=6)
    with pytest.raises(Cancelled):
        formats.run(cfg, run_id="q", runs_dir=tmp_path, workers=1, progress=[progress], cancel=cancel)
    p = qualifier_progress(tmp_path / "q", cfg, "q", active=False)
    assert p["cycles_complete"] == [1]
    assert len(p["standings"]) == 3 and sum(r["points"] for r in p["standings"]) == 3   # cycle 1 only


def test_side_prize_boards_from_a_qualifier(tmp_path):
    from arena.events import read_events
    from arena.server.progress import qualifier_progress
    from arena.sideprizes import BOARDS, PRIZES_FILE, run_boards
    cfg = formats.RunConfig(format="qualifier", bots=["aggressor", "mathematician", "template", "shark"],
                            blinds="turbo", time_budget_min=10, max_cycles=2, seed=8)
    formats.run(cfg, run_id="q", runs_dir=tmp_path, workers=4)
    mroot = tmp_path / "q" / "matches"
    out = run_boards(mroot.iterdir(), "q", wait=True)
    assert out["matches"] == out["finished"] == 24 and all((d / PRIZES_FILE).is_file() for d in mroot.iterdir())
    boards = out["boards"]
    assert set(boards) == set(BOARDS)

    def hand_events(entry):
        ev = read_events(mroot / entry["match_id"])
        return [e for e in ev if e.get("hand") == entry["hand"]]

    # biggest bluffer: every counted bluff is a low-equity postflop bet that got a fold
    from arena.annotate import equity_vs_random
    total_hands = {}
    for m in mroot.iterdir():
        for e in read_events(m):
            if e["type"] == "hand_end":
                for bot in e["deltas"]:
                    total_hands[bot] = total_hands.get(bot, 0) + 1
    for r in boards["bluffer"]:
        assert r["hands"] == total_hands[r["bot"]] and r["per_1000"] == round(1000 * r["won"] / r["hands"], 1)
        assert r["eligible"] == (r["hands"] >= 500)
        best = r["best"]
        if not best:
            assert r["bluffs"] == 0
            continue
        assert best["key"] == f"q/matches/{best['match_id']}" and best["amount"] > 0 and len(best["board"]) >= 3
        evs = hand_events(best)
        start = next(e for e in evs if e["type"] == "hand_start")
        assert start["hole"][r["bot"]] == best["cards"] and start["hole"][best["opponent"]] == best["opponent_cards"]
        fold = next(e for e in evs if e["type"] == "decision" and e["applied"]["action"] == "fold")
        assert fold["bot"] == best["opponent"] and fold["street"] != "preflop"
        assert not any(e["type"] == "showdown" for e in evs)
        bets = [e for e in evs if e["type"] == "decision" and e["bot"] == r["bot"] and e["street"] != "preflop"
                and e["applied"]["action"] in ("raise", "all_in")]
        last = bets[-1]["state"]                      # the bet that got the fold
        assert last["community_cards"] == best["board"]
        assert equity_vs_random(tuple(best["cards"]), tuple(best["board"])) < 0.4 and best["equity"] < 0.4
    assert sum(r["bluffs"] for r in boards["bluffer"]) > 0
    # seven-two: chips won on winning hands per seven-two hand dealt, checked against the logs
    dealt, won = {}, {}
    for m in mroot.iterdir():
        hole = {}
        for e in read_events(m):
            if e["type"] == "hand_start":
                hole = e["hole"]
            elif e["type"] == "hand_end":
                for bot, d in e["deltas"].items():
                    if sorted(c[0] for c in hole[bot]) == ["2", "7"]:
                        dealt[bot] = dealt.get(bot, 0) + 1
                        won[bot] = won.get(bot, 0) + max(d, 0)
    assert {r["bot"] for r in boards["seven_two"]} == set(dealt)
    for r in boards["seven_two"]:
        assert r["dealt"] == dealt[r["bot"]] and r["won"] == won[r["bot"]]
        assert r["per_dealt"] == round(won[r["bot"]] / dealt[r["bot"]], 1)
        assert r["eligible"] == (r["dealt"] >= 30) and (r["rank"] is None) == (not r["eligible"])
        if r["best"]:
            assert sorted(c[0] for c in r["best"]["cards"]) == ["2", "7"] and r["best"]["amount"] > 0
    ranked = [r["per_dealt"] for r in boards["seven_two"] if r["eligible"]]
    assert ranked == sorted(ranked, reverse=True)
    for r in boards["lowest_showdown"]:
        assert r["amount"] >= 1000
        evs = hand_events(r)
        assert any(e["type"] == "showdown" for e in evs)
        assert any(e["type"] == "decision" and e["street"] == "river" and e["applied"]["action"] in ("raise", "all_in")
                   for e in evs)
    scores = [r["score"] for r in boards["lowest_showdown"]]
    assert scores == sorted(scores)                                  # weakest winning hand first
    for r in boards["comeback"]:                                     # only match winners, at their low point
        ev = read_events(mroot / r["match_id"])
        assert next(x["bot"] for x in ev[-1]["results"] if x["place"] == 1) == r["bot"]
        lows = [e["stacks"][r["bot"]] for e in ev if e["type"] == "hand_end"]
        assert r["chips"] == min(lows)
    assert qualifier_progress(tmp_path / "q", cfg, "q")["side_prizes"]["boards"].keys() == boards.keys()
