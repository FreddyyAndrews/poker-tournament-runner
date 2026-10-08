"""Tests for tournament formats, series, run verification, export and bench."""
import json
import os
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arena import formats
from arena.bench import bench, load_history
from arena.events import read_events
from arena.export import export
from arena.formats import RunConfig, bracket, load_config, run
from arena.series import run_series
from arena.verify import verify_run

REPO = Path(__file__).resolve().parent.parent
BOTS = ["shark", "aggressor", "mathematician", "ref_bot_2", "template"]


def events_of(run_result, match_id):
    return read_events(Path(run_result["run_dir"]) / "matches" / match_id)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_format_defaults_and_validation():
    cfg = RunConfig(format="bracket", bots=["shark", "aggressor"])
    assert (cfg.mode, cfg.blinds) == ("freezeout", "standard")
    cfg = RunConfig(format="round_robin", bots=["shark", "aggressor"])
    assert (cfg.mode, cfg.duplicate) == ("cash", True)
    for bad in ({"format": "swiss"}, {"mode": "turbo"}, {"blinds": "9/1"}, {"table_size": 12},
                {"seeding": "elo"}):
        with pytest.raises(ValueError):
            RunConfig(**{"format": "table", "bots": ["shark", "aggressor"], **bad})


def test_load_config_yaml_overrides_and_unknown_keys(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("format: round_robin\nbots: [shark, aggressor]\nhands: 50\nruns: 3\nworkers: 2\n")
    cfg, opts = load_config(str(f), {"hands": 20, "seed": 7})
    assert cfg.hands == 20 and cfg.seed == 7 and opts == {"runs": 3, "workers": 2, "docker": None}
    f.write_text("format: table\nbots: [shark, aggressor]\nhandz: 5\n")
    with pytest.raises(ValueError, match="unknown settings"):
        load_config(str(f))


def test_example_configs_load():
    for path in sorted((REPO / "configs").glob("*.yaml")):
        cfg, _ = load_config(str(path))
        assert cfg.format in formats.FORMATS, path


# ---------------------------------------------------------------------------
# Table format
# ---------------------------------------------------------------------------

def test_heads_up_duplicate_swaps_seats_with_same_cards(tmp_path):
    r = run(RunConfig(format="table", bots=["shark", "aggressor"], mode="cash", hands=40,
                      duplicate=True, seed=3), runs_dir=tmp_path)
    leg1, leg2 = events_of(r, "table_leg1"), events_of(r, "table_leg2")
    h1 = [e for e in leg1 if e["type"] == "hand_start"]
    h2 = [e for e in leg2 if e["type"] == "hand_start"]
    assert len(h1) == len(h2) == 40
    for a, b in zip(h1, h2):
        assert a["hole"]["shark"] == b["hole"]["aggressor"]
        assert a["hole"]["aggressor"] == b["hole"]["shark"]
        assert a["button"] == b["button"]
    assert sum(s["delta"] for s in r["standings"]) == 0
    assert verify_run(r["run_dir"])["ok"]


def test_cash_mode_never_busts(tmp_path):
    r = run(RunConfig(format="table", bots=["aggressor", "aggressor", "shark"], mode="cash",
                      hands=60, seed=4), runs_dir=tmp_path)
    ev = events_of(r, "table")
    assert sum(1 for e in ev if e["type"] == "hand_start") == 60
    assert not [e for e in ev if e["type"] == "elimination"]
    assert all(set(e["stacks"].values()) == {10_000} for e in ev if e["type"] == "hand_start")
    assert sum(s["final_stack"] for s in r["standings"]) == 30_000
    assert verify_run(r["run_dir"])["ok"]


# ---------------------------------------------------------------------------
# Bracket
# ---------------------------------------------------------------------------

def test_bracket_order():
    assert bracket.bracket_order(2) == [1, 2]
    assert bracket.bracket_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]
    assert sorted(bracket.bracket_order(32)) == list(range(1, 33))


def _fake_bracket_records(cfg):
    """Records for a whole bracket where the alphabetically-first bot always wins."""
    records, current, rnd = [], bracket.first_round_slots(cfg), 1
    while len(current) > 1:
        nxt = []
        for i in range(0, len(current), 2):
            a, b = current[i], current[i + 1]
            if a and b:
                w, l = sorted([a, b])
                records.append({"match_id": bracket.match_id(rnd, i // 2), "round": rnd,
                                "slot": i // 2, "players": [a, b], "hands": 10, "stopped": "x",
                                "results": [{"bot": w, "place": 1, "final_stack": 20000,
                                             "delta": 10000, "hands_played": 10, "bb_per_100": 0.0,
                                             "busted_on_hand": None, "timeouts": 0, "errors": 0},
                                            {"bot": l, "place": 2, "final_stack": 0,
                                             "delta": -10000, "hands_played": 10, "bb_per_100": 0.0,
                                             "busted_on_hand": 9, "timeouts": 0, "errors": 0}]})
                nxt.append(w)
            else:
                nxt.append(a or b)
        current, rnd = nxt, rnd + 1
    return records


@pytest.mark.parametrize("n", [2, 5, 8, 12, 40])
def test_bracket_places_and_byes(n):
    cfg = RunConfig(format="bracket", bots=["shark"] * n, seed=1, seeding="given")
    slots = bracket.first_round_slots(cfg)
    size = len(slots)
    assert size >= n > size // 2
    # byes go to the top seeds: seed k has a bye iff k <= size - n
    seeds = bracket.bracket_order(size)
    for i in range(0, size, 2):
        pair = (seeds[i], seeds[i + 1])
        assert (slots[i + 1] is None) == (pair[1] > n)
    records = _fake_bracket_records(cfg)
    assert len(records) == n - 1
    rows = bracket.standings(cfg, records)
    assert len(rows) == n and len({r["bot"] for r in rows}) == n
    counts = Counter(r["place"] for r in rows)
    assert counts[1] == 1 and (n == 2 or counts[2] == 1)
    for place, k in counts.items():
        assert place == 1 or k <= place - 1          # e.g. at most 2 thirds, 4 fifths


def test_bracket_rejects_records_that_dont_fit():
    cfg = RunConfig(format="bracket", bots=["shark"] * 4, seed=1, seeding="given")
    records = _fake_bracket_records(cfg)
    records[-1]["players"] = list(reversed(records[-1]["players"][:1])) + ["not_a_bot"]
    with pytest.raises(ValueError):
        bracket.standings(cfg, records)


def test_bracket_run_and_verify(tmp_path):
    r = run(RunConfig(format="bracket", bots=BOTS, blinds="turbo", seed=8), runs_dir=tmp_path,
            workers=3)
    assert sorted(s["place"] for s in r["standings"]) == [1, 2, 3, 3, 5]
    assert len(r["matches"]) == 4
    assert verify_run(r["run_dir"])["ok"]


def test_run_verify_catches_edited_standings_and_missing_logs(tmp_path):
    r = run(RunConfig(format="bracket", bots=BOTS[:4], blinds="turbo", seed=8), runs_dir=tmp_path)
    run_file = Path(r["run_dir"]) / "run.json"
    data = json.loads(run_file.read_text())
    data["standings"][0], data["standings"][1] = data["standings"][1], data["standings"][0]
    run_file.write_text(json.dumps(data))
    assert not verify_run(r["run_dir"])["ok"]
    data["standings"][0], data["standings"][1] = data["standings"][1], data["standings"][0]
    run_file.write_text(json.dumps(data))
    assert verify_run(r["run_dir"])["ok"]
    (Path(r["run_dir"]) / "matches" / data["matches"][0]["match_id"] / "events.jsonl").unlink()
    assert not verify_run(r["run_dir"])["ok"]


# ---------------------------------------------------------------------------
# Round robin
# ---------------------------------------------------------------------------

def test_round_robin(tmp_path):
    bots = BOTS[:4]
    r = run(RunConfig(format="round_robin", bots=bots, hands=40, seed=2), runs_dir=tmp_path,
            workers=4)
    assert len(r["matches"]) == 6 * 2                     # pairs x duplicate legs
    assert sum(s["points"] for s in r["standings"]) == 6  # one point per pairing
    h2h = r["head_to_head"]
    for a in bots:
        for b in bots:
            if a != b:
                assert h2h[a][b]["delta"] == -h2h[b][a]["delta"]
    assert sum(s["delta"] for s in r["standings"]) == 0
    assert verify_run(r["run_dir"])["ok"]


# ---------------------------------------------------------------------------
# MTT
# ---------------------------------------------------------------------------

def test_mtt_tables_balanced_and_everyone_placed(tmp_path):
    bots = BOTS * 4                                      # 20 entrants
    r = run(RunConfig(format="mtt", bots=bots, table_size=6, blinds="turbo", seed=4),
            runs_dir=tmp_path, workers=4)
    assert sorted(s["place"] for s in r["standings"]) == list(range(1, 21))
    assert r["standings"][0]["final_stack"] == 20 * 10_000
    ev = events_of(r, "mtt")
    per_tick = defaultdict(dict)
    for e in ev:
        if e["type"] == "hand_start":
            assert len(e["order"]) <= 6
            per_tick[e["hand"]][e["table"]] = len(e["order"])
    for tick, tables in per_tick.items():
        if len(tables) > 1:
            assert max(tables.values()) - min(tables.values()) <= 1, (tick, tables)
    assert [e["type"] for e in ev].count("table_closed") == 3   # 4 tables -> 1
    assert verify_run(r["run_dir"])["ok"]


def test_mtt_is_deterministic(tmp_path):
    cfg = dict(format="mtt", bots=BOTS * 2, table_size=4, blinds="turbo", seed=12)
    a = run(RunConfig(**cfg), runs_dir=tmp_path / "a", run_id="x", workers=4)
    b = run(RunConfig(**cfg), runs_dir=tmp_path / "b", run_id="x", workers=1)
    strip = lambda evs: [{k: v for k, v in e.items() if k not in ("think_ms", "duration_s", "seats", "results")}
                         for e in evs]
    assert strip(events_of(a, "mtt")) == strip(events_of(b, "mtt"))


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def test_series_aggregates_and_is_reproducible(tmp_path):
    cfg = dict(format="bracket", bots=BOTS[:4], blinds="turbo", seed=21)
    s1 = run_series(RunConfig(**cfg), runs=12, series_id="s", runs_dir=tmp_path / "a", workers=4)
    s2 = run_series(RunConfig(**cfg), runs=12, series_id="s", runs_dir=tmp_path / "b", workers=1)
    assert sum(a["wins"] for a in s1["aggregate"]) == 12
    assert s1["winners"] == s2["winners"]
    assert [a["wins"] for a in s1["aggregate"]] == [a["wins"] for a in s2["aggregate"]]
    for a in s1["aggregate"]:
        lo, hi = a["win_rate_ci"]
        assert 0 <= lo <= a["win_rate"] <= hi <= 1
        assert sum(a["place_counts"].values()) == 12
    assert verify_run(s1["run_dir"])["ok"]


# ---------------------------------------------------------------------------
# Export and bench
# ---------------------------------------------------------------------------

def test_export_for_one_bot_hides_other_private_data(tmp_path):
    r = run(RunConfig(format="table", bots=["shark", "aggressor"], hands=60, seed=5),
            runs_dir=tmp_path)
    out = export(r["run_dir"], tmp_path / "out.zip", bot="shark")
    z = zipfile.ZipFile(out["path"])
    name = next(n for n in z.namelist() if n.endswith("events.jsonl"))
    ev = [json.loads(l) for l in z.read(name).decode().splitlines()]
    shown = {e["hand"] for e in ev if e["type"] == "showdown" and "aggressor" in e["reveal"]}
    for e in ev:
        if e["type"] == "hand_start":
            assert "shark" in e["hole"]
            assert ("aggressor" in e["hole"]) == (e["hand"] in shown)
        if e["type"] == "decision" and e["bot"] == "aggressor":
            assert not {"state", "logs", "debug", "reply", "error_detail"} & set(e)
        if e["type"] == "decision" and e["bot"] == "shark":
            assert "state" in e and "logs" in e
    full = zipfile.ZipFile(export(r["run_dir"], tmp_path / "full.zip")["path"])
    assert sorted(full.namelist()) == sorted(z.namelist())


def test_bench_is_repeatable_and_keeps_history(tmp_path):
    a = bench("shark", opponents=["mathematician", "ref_bot_2"], hands=30, bench_dir=tmp_path)
    b = bench("shark", opponents=["mathematician", "ref_bot_2"], hands=30, bench_dir=tmp_path)
    assert a["overall_bb_per_100"] == b["overall_bb_per_100"]
    assert [o["opponent"] for o in a["opponents"]] == ["mathematician", "ref_bot_2"]
    assert all(o["hands"] == 60 for o in a["opponents"])
    assert len(load_history("shark", tmp_path)) == 2
    assert b["previous"] and b["previous"][-1]["hash"] == a["hash"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "arena.cli", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=600)


def test_cli_run_results_verify(tmp_path):
    out = run_cli("run", "configs/bracket.yaml", "--blinds", "turbo", "--seed", "3",
                  "--id", "b", "--out", str(tmp_path), "--json")
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["kind"] == "run" and result["format"] == "bracket"
    shown = run_cli("results", str(tmp_path / "b"))
    assert shown.returncode == 0 and "Bracket:" in shown.stdout
    assert run_cli("verify", str(tmp_path / "b")).returncode == 0


def test_cli_run_series_from_flags(tmp_path):
    out = run_cli("run", "--format", "table", "--bots", "shark", "aggressor", "--hands", "20",
                  "--runs", "3", "--workers", "3", "--seed", "1", "--id", "s",
                  "--out", str(tmp_path), "--json")
    assert out.returncode == 0, out.stderr
    s = json.loads(out.stdout)
    assert s["kind"] == "series" and s["runs"] == 3 and len(s["winners"]) == 3
