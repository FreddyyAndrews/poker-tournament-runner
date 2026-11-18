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
