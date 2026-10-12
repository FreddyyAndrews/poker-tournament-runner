"""The one status rule: live only while this server's job runs it; files only
say how something ended (status.py). Covers stale-Live and CLI matches."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from arena import bots as botlib
from arena.server import create_app
from arena.server.status import match_status, run_status
from arena.table import MatchConfig, run_match


def write_events(d: Path, events):
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


START = {"type": "match_start", "match_id": "x", "seed": 1, "config": {}, "seats": [{"name": "a"}, {"name": "b"}]}


# ---------------------------------------------------------------------------
# The rule itself

def test_match_status_rule(tmp_path):
    d = tmp_path / "m"
    assert match_status(d, active=True) == "pending"           # owner running, not started
    assert match_status(d, active=False) == "incomplete"
    write_events(d, [START])
    assert match_status(d, active=True) == "running"
    assert match_status(d, active=False) == "incomplete"       # a log alone never means live
    write_events(d, [START, {"type": "match_end", "stopped": "table_closed"}])
    assert match_status(d, active=False) == "complete"
    for stopped, expected in (("hand_limit", "complete"), ("cancelled", "cancelled"),
                              ("error: RuntimeError()", "error")):
        (d / "summary.json").write_text(json.dumps({"stopped": stopped}))
        assert match_status(d, active=True) == expected        # a final record wins


def test_run_status_rule(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    (d / "run.json").write_text(json.dumps({"status": "running"}))
    assert run_status(d, active=True) == "running"
    assert run_status(d, active=False) == "incomplete"         # stale "running" in the file
    (d / "run.json").write_text(json.dumps({"status": "complete"}))
    assert run_status(d, active=True) == "complete"            # e.g. a finished run in a live series
    (d / "run.json").write_text(json.dumps({"status": "cancelled"}))
    assert run_status(d, active=False) == "cancelled"


# ---------------------------------------------------------------------------
# Through the API

@pytest.fixture
def runs(tmp_path):
    return tmp_path / "runs"


def client_for(runs):
    return TestClient(create_app(runs))


def test_stale_live_after_server_died(runs):
    """Files left behind by a server that died mid-run must not show as live."""
    # a bracket run that was mid-way: run.json says running, one match half-written
    br = runs / "dead_bracket"
    br.mkdir(parents=True)
    (br / "run.json").write_text(json.dumps({
        "kind": "run", "run_id": "dead_bracket", "format": "bracket", "seed": 1, "status": "running",
        "config": {"format": "bracket", "bots": ["shark", "aggressor"], "blinds": "turbo", "seed": 1}}))
    write_events(br / "matches" / "r1_m1", [START])
    # a series that never finished
    se = runs / "dead_series"
    se.mkdir()
    (se / "series.json").write_text(json.dumps({"kind": "series", "status": "running", "runs": 5,
                                                "format": "table", "config": {}}))
    # a standalone match with a partial log
    write_events(runs / "dead_match", [START, {"type": "hand_start", "hand": 0}])

    with client_for(runs) as c:
        listed = {r["id"]: r["status"] for r in c.get("/api/runs").json()}
        assert listed == {"dead_bracket": "incomplete", "dead_series": "incomplete",
                          "dead_match": "incomplete"}
        run = c.get("/api/runs/dead_bracket").json()
        assert run["status"] == "incomplete"
        assert all(m["status"] == "incomplete" for m in run["matches"])
        ties = [t for r in run["progress"]["rounds"] for t in r["ties"]]
        assert "running" not in {t["status"] for t in ties}
        assert c.get("/api/runs/dead_series").json()["status"] == "incomplete"
        assert c.get("/api/matches/dead_match").json()["status"] == "incomplete"
        assert c.get("/api/matches/dead_match/events").json()["status"] == "incomplete"
        with c.stream("GET", "/api/matches/dead_match/stream") as r:       # ends, doesn't hang
            body = r.read().decode()
        assert "event: end" in body and '"incomplete"' in body


def test_cli_matches(runs):
    """A match the CLI played in its own process: complete when it finished,
    never live while it runs (the server isn't running it)."""
    specs = botlib.resolve_all(["shark", "mathematician"])
    run_match(specs, MatchConfig(hands=5, seed=3), match_id="cli_done", runs_dir=runs)
    write_events(runs / "cli_in_progress", [START, {"type": "hand_start", "hand": 0}])
    with client_for(runs) as c:
        listed = {r["id"]: r["status"] for r in c.get("/api/runs").json()}
        assert listed["cli_done"] == "complete"
        assert listed["cli_in_progress"] == "incomplete"
        assert c.get("/api/matches/cli_done").json()["status"] == "complete"


def test_live_only_while_the_job_runs(runs):
    with client_for(runs) as c:
        c.post("/api/matches", json={"bots": ["shark", "aggressor"], "hands": 300, "pace_ms": 200,
                                     "id": "live"})
        c.post("/api/runs", json={"format": "bracket", "bots": ["shark", "aggressor", "mathematician"],
                                  "blinds": "turbo", "pace_ms": 200, "id": "live_run"})
        time.sleep(0.8)
        assert c.get("/api/matches/live").json()["status"] == "running"
        listed = {r["id"]: r["status"] for r in c.get("/api/runs").json()}
        assert listed["live"] == "running" and listed["live_run"] == "running"
        run = c.get("/api/runs/live_run").json()
        assert run["status"] == "running"
        assert "running" in {t["status"] for r in run["progress"]["rounds"] for t in r["ties"]}

        c.post("/api/jobs/live/cancel")
        c.post("/api/jobs/live_run/cancel")
        for job in ("live", "live_run"):
            t0 = time.time()
            while c.get(f"/api/jobs/{job}").json()["status"] == "running":
                assert time.time() - t0 < 10
                time.sleep(0.05)
        listed = {r["id"]: r["status"] for r in c.get("/api/runs").json()}
        assert listed["live"] == "cancelled" and listed["live_run"] == "cancelled"
        run = c.get("/api/runs/live_run").json()
        assert "running" not in {t["status"] for r in run["progress"]["rounds"] for t in r["ties"]}

    # a new server (as after a restart) sees the same finished state, never live
    with client_for(runs) as c:
        listed = {r["id"]: r["status"] for r in c.get("/api/runs").json()}
        assert listed["live"] == "cancelled" and listed["live_run"] == "cancelled"
