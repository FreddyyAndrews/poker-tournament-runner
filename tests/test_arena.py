"""Tests for the arena: blind structures, bot lookup, tables, matches, logs, verify, CLI."""
import json
import os
import subprocess
import sys
import textwrap
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arena import bots as botlib
from arena import structures
from arena.events import read_events
from arena.table import MatchConfig, run_match
from arena.verify import verify_events, verify_run

REPO = Path(__file__).resolve().parent.parent


def play(tmp_path, names, match_id="m", **cfg):
    specs = botlib.resolve_all(names)
    config = MatchConfig(**cfg)
    summary = run_match(specs, config, match_id=match_id, runs_dir=tmp_path)
    return summary, read_events(Path(summary["run_dir"]))


def of_type(events, t):
    return [e for e in events if e["type"] == t]


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

def test_flat_structure_never_changes():
    s = structures.parse("50/100/10")
    assert s.level(0) == s.level(5000) == structures.Level(50, 100, 10, 0)


def test_preset_levels_rise_by_hand_count_and_keep_doubling():
    s = structures.preset("turbo")
    assert s.level(0).bb == 50 and s.level(9).bb == 50 and s.level(10).bb == 100
    assert s.level(0).ante == 0 and s.level(60).ante > 0
    last = s.levels[-1]
    beyond = s.level(10 * len(s.levels))
    assert beyond.bb == last.bb * 2
    bbs = [s.level(h).bb for h in range(0, 400, 10)]
    assert bbs == sorted(bbs)


def test_preset_scales_with_starting_stack():
    small, big = structures.preset("standard", 1000), structures.preset("standard", 100_000)
    assert small.level(0).bb < 50 < big.level(0).bb
    assert all(l.sb <= l.bb for l in small.levels)


@pytest.mark.parametrize("spec", ["100/50", "0/10", "a/b", "1/2/3/4", "hyper"])
def test_bad_structure_specs_rejected(spec):
    with pytest.raises(ValueError):
        structures.parse(spec)


def test_structure_round_trips_through_dict():
    s = structures.preset("slow", 20_000)
    assert structures.Structure.from_dict(json.loads(json.dumps(s.to_dict()))) == s


# ---------------------------------------------------------------------------
# Bot lookup
# ---------------------------------------------------------------------------

def test_resolve_names_paths_and_duplicates(tmp_path):
    specs = botlib.resolve_all(["shark", "shark", str(REPO / "bots/aggressor/bot.py")])
    assert [s.name for s in specs] == ["shark", "shark_2", "aggressor"]
    assert specs[0].path == str(REPO / "bots" / "shark")
    assert specs[0].hash == specs[1].hash and len(specs[0].hash) == 16


def test_resolve_custom_names_and_unknown_bot():
    specs = botlib.resolve_all(["shark", "aggressor"], names=["Alice", None])
    assert [s.name for s in specs] == ["Alice", "aggressor"]
    with pytest.raises(ValueError, match="not found"):
        botlib.resolve("no_such_bot_anywhere")


def test_hash_changes_with_code(tmp_path):
    bot = tmp_path / "bot.py"
    bot.write_text("def decide(s): return {'action': 'fold'}\n")
    h1 = botlib.bot_hash(str(bot))
    bot.write_text("def decide(s): return {'action': 'call'}\n")
    assert botlib.bot_hash(str(bot)) != h1


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

def test_fixed_match_plays_requested_hands(tmp_path):
    summary, events = play(tmp_path, ["shark", "mathematician", "ref_bot_2"], hands=30, seed=1)
    assert summary["hands"] == 30 and summary["stopped"] == "hand_limit"
    assert len(of_type(events, "hand_start")) == 30
    assert sum(r["final_stack"] for r in summary["results"]) == 30_000
    assert sum(r["delta"] for r in summary["results"]) == 0
    assert of_type(events, "match_end")[0]["results"] == summary["results"]


def test_blinds_and_starting_stack_applied(tmp_path):
    _, events = play(tmp_path, ["shark", "mathematician"], hands=3, seed=2,
                     starting_stack=5000, structure=structures.parse("100/200/25"))
    first = of_type(events, "hand_start")[0]
    assert first["stacks"] == {"shark": 5000, "mathematician": 5000}
    assert first["blinds"] == {"sb": 100, "bb": 200, "ante": 25}
    posts = [p for p in events if p["type"] == "post" and p["hand"] == 0]
    assert sorted(p["kind"] for p in posts) == ["ante", "ante", "bb", "sb"]
    assert {p["kind"]: p["amount"] for p in posts if p["kind"] != "ante"} == {"sb": 100, "bb": 200}


def test_freezeout_places_everyone_once(tmp_path):
    names = ["shark", "aggressor", "mathematician", "ref_bot_2", "template"]
    summary, events = play(tmp_path, names, mode="freezeout", hands=2000, seed=11,
                           structure=structures.preset("turbo"))
    assert summary["stopped"] == "one_player_left"
    places = sorted(r["place"] for r in summary["results"])
    assert places[0] == 1 and len(set(places)) == len(places) == 5
    elims = of_type(events, "elimination")
    assert len(elims) == 4
    assert [e["place"] for e in elims] == sorted((e["place"] for e in elims), reverse=True)
    winner = summary["results"][0]
    assert winner["place"] == 1 and winner["final_stack"] == 50_000
    levels = of_type(events, "level")
    assert len(levels) > 1 and levels[-1]["bb"] > levels[0]["bb"]


def test_freezeout_hand_cap_ranks_by_stack(tmp_path):
    summary, _ = play(tmp_path, ["mathematician", "ref_bot_2", "template"], mode="freezeout",
                      hands=5, seed=3, structure=structures.preset("slow"))
    assert summary["stopped"] == "hand_limit" and summary["hands"] == 5
    ranked = sorted(summary["results"], key=lambda r: r["place"])
    assert [r["final_stack"] for r in ranked] == sorted((r["final_stack"] for r in ranked), reverse=True)


def test_seats_stay_fixed_and_button_skips_busted_seats(tmp_path):
    names = ["shark", "aggressor", "mathematician", "ref_bot_2"]
    _, events = play(tmp_path, names, mode="freezeout", hands=2000, seed=11,
                     structure=structures.preset("turbo"))
    seat_of = {s["name"]: s["seat"] for s in of_type(events, "match_start")[0]["seats"]}
    for d in of_type(events, "decision"):
        st = d["state"]
        assert d["seat"] == seat_of[d["bot"]] == st["seat_to_act"]
        assert {p["bot_id"]: p["seat"] for p in st["players"]}.items() <= seat_of.items()
        assert all(a["seat"] == seat_of[a["bot_id"]] for a in st["action_log"])
    busted_at = {e["bot"]: e["hand"] for e in of_type(events, "elimination")}
    prev = None
    for h in of_type(events, "hand_start"):
        assert h["seats"] == {n: seat_of[n] for n in h["order"]}
        assert not any(busted_at.get(n, 10**9) < h["hand"] for n in h["order"])
        button_bot = names[h["button"]]
        assert button_bot in h["order"]          # button is always on a live player
        if prev is not None:
            # button moved clockwise to the next seat that is still occupied
            n = len(names)
            nxt = next((prev + k) % n for k in range(1, n + 1) if names[(prev + k) % n] in h["order"])
            assert h["button"] == nxt
        prev = h["button"]


def test_same_seed_same_events(tmp_path):
    names = ["shark", "aggressor", "mathematician"]
    _, a = play(tmp_path / "a", names, hands=60, seed=42)
    _, b = play(tmp_path / "b", names, hands=60, seed=42)
    _, c = play(tmp_path / "c", names, hands=60, seed=43)
    drop = lambda evs: [{k: v for k, v in e.items() if k not in ("think_ms", "duration_s", "results")}
                        for e in evs if e["type"] not in ("match_start",)]
    assert drop(a) == drop(b)
    assert drop(a) != drop(c)


def test_decisions_record_logs_debug_and_what_the_bot_saw(tmp_path):
    bot = tmp_path / "chatty.py"
    bot.write_text(textwrap.dedent("""
        def decide(state):
            if state.get("type") == "warmup":
                return {}
            print("hand", state["hand_num"], "cards", state["your_cards"])
            return {"action": "call", "debug": {"owed": state["amount_owed"]}}
    """))
    specs = botlib.resolve_all([str(bot), "mathematician"])
    summary = run_match(specs, MatchConfig(hands=5, seed=9), match_id="m", runs_dir=tmp_path / "runs")
    events = read_events(Path(summary["run_dir"]))
    mine = [d for d in of_type(events, "decision") if d["bot"] == "chatty"]
    assert mine
    hole = {h["hand"]: h["hole"]["chatty"] for h in of_type(events, "hand_start")}
    for d in mine:
        assert d["logs"].strip() == f"hand {d['hand']} cards {hole[d['hand']]}"
        assert d["debug"] == {"owed": d["state"]["amount_owed"]}
        assert d["state"]["your_cards"] == hole[d["hand"]]
        assert d["reply"] == {"action": "call"}
        assert "match_action_log" not in d["state"]


def test_table_size_limits(tmp_path):
    with pytest.raises(ValueError):
        play(tmp_path, ["shark"], hands=1)
    with pytest.raises(ValueError):
        play(tmp_path, ["shark"] * 10, hands=1)


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def logged_match(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("verify")
    summary, events = play(tmp, ["shark", "aggressor", "mathematician", "template"],
                           mode="freezeout", hands=2000, seed=5,
                           structure=structures.preset("turbo"))
    return summary, events


def test_verify_accepts_a_clean_log(logged_match):
    summary, _ = logged_match
    report = verify_run(summary["run_dir"])
    assert report["ok"], report["problems"]
    assert report["hands"] == summary["hands"]


def _tamper(events, pick, change):
    events = json.loads(json.dumps(events))
    target = next(e for e in events if pick(e))
    change(target)
    return events


@pytest.mark.parametrize("what, pick, change", [
    ("award amount", lambda e: e["type"] == "award" and e["hand"] > 2,
     lambda e: e.update(amount=e["amount"] + 50)),
    ("hole card", lambda e: e["type"] == "hand_start" and e["hand"] == 4,
     lambda e: e["hole"][next(iter(e["hole"]))].__setitem__(0, "2c")),
    ("board", lambda e: e["type"] == "deal",
     lambda e: e["board"].__setitem__(0, "Ah" if e["board"][0] != "Ah" else "Kh")),
    ("bot reply", lambda e: e["type"] == "decision" and e["reply"]["action"] == "fold",
     lambda e: e.update(reply={"action": "all_in"})),
    ("final result", lambda e: e["type"] == "match_end",
     lambda e: e["results"][0].update(final_stack=e["results"][0]["final_stack"] - 1)),
    ("winner", lambda e: e["type"] == "award",
     lambda e: e.update(bot="template" if e["bot"] != "template" else "shark")),
])
def test_verify_catches_tampering(logged_match, what, pick, change):
    _, events = logged_match
    report = verify_events(_tamper(events, pick, change))
    assert not report["ok"], what


def test_verify_catches_deleted_and_truncated_events(logged_match):
    _, events = logged_match
    i = next(i for i, e in enumerate(events) if e["type"] == "decision")
    assert not verify_events(events[:i] + events[i + 1:])["ok"]
    assert not verify_events(events[: len(events) // 2])["ok"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "arena.cli", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=300)


def test_cli_match_and_verify_json(tmp_path):
    out = run_cli("match", "shark", "aggressor", "--hands", "20", "--seed", "4",
                  "--blinds", "25/50", "--stack", "5000", "--id", "cli", "--out", str(tmp_path),
                  "--json")
    assert out.returncode == 0, out.stderr
    summary = json.loads(out.stdout)
    assert summary["config"]["starting_stack"] == 5000
    assert summary["config"]["structure"]["levels"][0]["bb"] == 50
    v = run_cli("verify", summary["run_dir"], "--json")
    assert v.returncode == 0 and json.loads(v.stdout)["ok"] is True


def test_cli_errors_are_clean(tmp_path):
    out = run_cli("match", "shark", "nope_not_a_bot", "--out", str(tmp_path))
    assert out.returncode == 2 and "not found" in out.stderr
    out = run_cli("verify", str(tmp_path / "missing"))
    assert out.returncode == 2 and "No match log" in out.stderr
