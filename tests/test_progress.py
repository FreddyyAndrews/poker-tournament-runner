"""Tests for live run progress (dashboards) and multi-table per-table logs."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from arena.events import read_events
from arena.server import create_app

BOTS = ["shark", "aggressor", "mathematician", "ref_bot_2", "template"]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "runs")) as c:
        c.runs_dir = tmp_path / "runs"
        yield c


def run(client, **body):
    r = client.post("/api/runs", json={"pace_ms": 0, "seed": 4, **body})
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    t0 = time.time()
    while client.get(f"/api/jobs/{rid}").json()["status"] in ("queued", "running"):
        assert time.time() - t0 < 120
        time.sleep(0.05)
    return client.get(f"/api/runs/{rid}").json()


def test_bracket_progress_matches_standings(client):
    r = run(client, format="bracket", bots=BOTS + ["shark"], blinds="turbo", id="b")
    p = r["progress"]
    assert p["kind"] == "bracket"
    assert [x["name"] for x in p["rounds"]] == ["Quarter-finals", "Semi-finals", "Final"]
    assert p["champion"] == r["data"]["standings"][0]["bot"]
    byes = [t for t in p["rounds"][0]["ties"] if t["bye"]]
    assert len(byes) == 2 and all(t["seeds"][0] in (1, 2) for t in byes)
    played = [t for rnd in p["rounds"] for t in rnd["ties"] if not t["bye"]]
    assert len(played) == len(r["data"]["matches"]) and all(t["status"] == "complete" for t in played)
    for t in played:
        assert t["winner"] in t["players"] and t["stacks"][t["winner"]] > 0


def test_round_robin_progress(client):
    r = run(client, format="round_robin", bots=BOTS[:4], hands=30, id="rr")
    p = r["progress"]
    assert len(p["pairings"]) == 6 and all(x["status"] == "complete" for x in p["pairings"])
    for x in p["pairings"]:
        a, b = x["players"]
        assert x["delta"][a] == -x["delta"][b] and len(x["legs"]) == 2
    assert p["standings"] == r["data"]["standings"]


def test_mtt_table_logs_match_the_main_log(client):
    r = run(client, format="mtt", bots=BOTS * 3, table_size=6, blinds="turbo", id="m")
    p = r["progress"]
    assert len(p["tables"]) == 3 and p["players_left"] == 1
    assert sum(1 for t in p["tables"] if not t["closed"]) == 1
    assert len(p["eliminations"]) == 14
    main = read_events(client.runs_dir / "m" / "matches" / "mtt")
    for t in p["tables"]:
        own = read_events(client.runs_dir / "m" / "matches" / "mtt" / "tables" / f"t{t['table']}")
        assert own[0]["type"] == "match_start" and own[-1]["type"] == "match_end"
        strip = lambda e: {k: v for k, v in e.items() if k != "think_ms"}
        assert [strip(e) for e in own[1:-1]] == [strip(e) for e in main if e.get("table") == t["table"]]
        status = client.get(f"/api/matches/{t['key']}").json()["status"]
        assert status == "complete"
    assert client.get("/api/verify/m").json()["ok"]


def test_series_progress(client):
    r = run(client, format="table", bots=["shark", "aggressor"], hands=10, runs=5, workers=5, id="s")
    p = r["progress"]
    assert p["runs_done"] == 5 and p["runs"] == 5
    assert p["aggregate"] == r["data"]["aggregate"]
    assert r["data"]["status"] == "complete"
