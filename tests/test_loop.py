"""Tests for the autonomous loop's scripts: the bot notes tool
(.claude/skills/iterate-bot/scripts/notes.py) and the commissioner's pacing
(.claude/skills/run-loop/scripts/commission.py)."""
import calendar
import importlib.util
import json
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


notes = load(".claude/skills/iterate-bot/scripts/notes.py", "loop_notes")
commission = load(".claude/skills/run-loop/scripts/commission.py", "loop_commission")


@pytest.fixture
def bot(tmp_path):
    d = tmp_path / "my_bots" / "demo_bot"
    d.mkdir(parents=True)
    notes.main(["init", str(d), "--track", "heuristic", "--strategy", "s0", "--idea", "a", "--idea", "b"])
    return d


def status(bot):
    return json.loads((bot / "notes" / "status.json").read_text())


def test_kept_runs_become_the_best_and_reverted_ones_dont(bot, capsys):
    notes.main(["record", str(bot), "--result", "kept", "--experiment", "baseline", "--summary", "x",
                "--hash", "h1", "--gauntlet", "-10", "2", "--hands", "2000"])
    notes.main(["record", str(bot), "--result", "reverted", "--experiment", "worse", "--summary", "y",
                "--hash", "h2", "--gauntlet", "-20", "2", "--hands", "2000"])
    st = status(bot)
    assert st["best"]["hash"] == "h1" and st["best"]["gauntlet_bb"] == -10
    assert st["best"]["gauntlet_hands"] == 2000
    assert (st["iterations"], st["kept"], st["reverted"]) == (2, 1, 1)
    assert st["last_run"]["experiment"] == "worse"
    journal = (bot / "notes" / "journal.md").read_text()
    assert "kept: baseline" in journal and "reverted: worse" in journal and "at 2000 hands a seat" in journal


def test_a_slumbot_checkpoint_updates_the_best_version(bot):
    notes.main(["record", str(bot), "--result", "kept", "--experiment", "baseline", "--summary", "x",
                "--hash", "h1", "--gauntlet", "5", "2", "--hands", "2000"])
    notes.main(["record", str(bot), "--result", "kept", "--experiment", "slumbot checkpoint", "--summary", "z",
                "--hash", "h1", "--slumbot", "-30", "40"])
    best = status(bot)["best"]
    assert best["gauntlet_bb"] == 5 and best["slumbot_bb"] == -30


def test_plan_queue_and_phases(bot, capsys):
    notes.main(["pop", str(bot)])
    assert capsys.readouterr().out.strip() == "a"
    notes.main(["push", str(bot), "c", "--top"])
    assert notes.plan_items(bot) == ["c", "b"]
    notes.main(["phase", str(bot), "blocked", "--reason", "merge conflict"])
    assert status(bot)["phase"] == "blocked"
    notes.main(["record", str(bot), "--result", "reverted", "--experiment", "e", "--summary", "s"])
    assert status(bot)["phase"] == "iterating"           # a normal run clears a block


def usage(weekly, five=10, resets_in_days=3.5, now=None):
    now = now or time.time()
    resets = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now + resets_in_days * 86400)) + ".000Z"
    return {"plan": {"windows": [{"label": "5-hour limit", "percentUsed": five},
                                 {"label": "Weekly · all models", "percentUsed": weekly, "resetsAt": resets}]}}


def test_pacing_spreads_the_week_and_keeps_a_reserve():
    now = calendar.timegm(time.gmtime())
    half = commission.pace(usage(30, now=now), 0.2, now)       # half the week gone: 40% planned
    assert half["run"] and half["target"] == pytest.approx(40, abs=0.5)
    ahead = commission.pace(usage(50, now=now), 0.2, now)
    assert not ahead["run"] and "ahead of pace" in ahead["reason"]
    hot = commission.pace(usage(10, five=95, now=now), 0.2, now)
    assert not hot["run"] and "5-hour" in hot["reason"]
    assert commission.pace(None, 0.2, now)["run"]              # no data: no pacing
