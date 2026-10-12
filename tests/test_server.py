"""Tests for the HTTP API: jobs, live streams, runs, verify, export."""
import io
import json
import os
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from arena.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "runs")
    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still running")


def stream(client, key, start=0):
    """Collect (events, end_reason) from the SSE stream."""
    events, reason = [], None
    with client.stream("GET", f"/api/matches/{key}/stream", params={"from": start}) as r:
        assert r.status_code == 200
        kind = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                kind = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if kind == "end":
                    reason = data["reason"]
                    break
                events.append(data)
    return events, reason


def test_meta_endpoints(client):
    assert client.get("/api/health").json()["ok"]
    bots = {b["name"] for b in client.get("/api/bots").json()}
    assert {"shark", "aggressor", "mathematician"} <= bots
    st = client.get("/api/structures", params={"stack": 20000}).json()
    assert set(st["presets"]) == {"turbo", "standard", "slow"}
    assert st["presets"]["turbo"]["levels"][0]["bb"] == 100
    assert st["formats"]["bracket"]["mode"] == "freezeout"


def test_match_job_events_summary_verify(client):
    r = client.post("/api/matches", json={"bots": ["shark", "aggressor"], "hands": 10,
                                          "seed": 3, "pace_ms": 0, "id": "m1"})
    assert r.status_code == 201 and r.json()["match_key"] == "m1"
    assert wait_job(client, "m1")["status"] == "complete"
    m = client.get("/api/matches/m1").json()
    assert m["status"] == "complete" and m["summary"]["hands"] == 10
    assert m["match_start"]["type"] == "match_start"
    ev = client.get("/api/matches/m1/events").json()
    assert ev["next"] == m["events"] == len(ev["events"])
    assert ev["events"][-1]["type"] == "match_end"
    tail = client.get("/api/matches/m1/events", params={"from": 5}).json()
    assert tail["events"] == ev["events"][5:]
    assert client.get("/api/verify/m1").json()["ok"]


def test_live_stream_follows_a_paced_match(client):
    client.post("/api/matches", json={"bots": ["shark", "aggressor", "mathematician"],
                                      "hands": 3, "seed": 4, "pace_ms": 40, "id": "live"})
    t0 = time.time()
    events, reason = stream(client, "live")
    elapsed = time.time() - t0
    assert reason == "match_end"
    assert events[0]["type"] == "match_start" and events[-1]["type"] == "match_end"
    assert elapsed > 0.5                       # it really was paced, not instant
    logged = client.get("/api/matches/live/events").json()["events"]
    assert events == logged
    # replay from the middle of a finished match
    later, reason = stream(client, "live", start=10)
    assert later == logged[10:] and reason == "match_end"


def test_cancel_a_match(client):
    client.post("/api/matches", json={"bots": ["shark", "aggressor"], "hands": 500,
                                      "pace_ms": 200, "id": "slow"})
    time.sleep(0.5)
    client.post("/api/jobs/slow/cancel")
    job = wait_job(client, "slow", timeout=10)
    assert job["status"] == "cancelled"
    ev = client.get("/api/matches/slow/events").json()["events"]
    assert ev[-1]["type"] == "match_end" and ev[-1]["stopped"] == "cancelled"
    assert client.get("/api/matches/slow").json()["summary"]["stopped"] == "cancelled"


def test_tournament_run(client):
    r = client.post("/api/runs", json={"format": "bracket", "bots": ["shark", "aggressor",
                                       "mathematician", "ref_bot_2"], "blinds": "turbo",
                                       "seed": 5, "pace_ms": 0, "id": "br"})
    assert r.status_code == 201 and r.json()["kind"] == "run"
    assert wait_job(client, "br")["status"] == "complete"
    run = client.get("/api/runs/br").json()
    assert run["kind"] == "run" and run["data"]["status"] == "complete"
    assert len(run["matches"]) == 3
    key = run["matches"][0]["key"]
    assert key.startswith("br/matches/")
    assert client.get(f"/api/matches/{key}").json()["status"] == "complete"
    assert client.get("/api/verify/br").json()["ok"]
    listed = {r["id"]: r for r in client.get("/api/runs").json()}
    assert listed["br"]["kind"] == "run" and listed["br"]["winner"]


def test_series_run(client):
    r = client.post("/api/runs", json={"format": "table", "bots": ["shark", "aggressor"],
                                       "hands": 10, "runs": 3, "workers": 3, "seed": 1,
                                       "id": "ser"})
    assert r.json()["kind"] == "series"
    job = wait_job(client, "ser")
    assert job["status"] == "complete" and job["runs_done"] == 3
    data = client.get("/api/runs/ser").json()["data"]
    assert data["kind"] == "series" and sum(a["wins"] for a in data["aggregate"]) == 3


def test_export_download(client):
    client.post("/api/matches", json={"bots": ["shark", "aggressor"], "hands": 5, "seed": 1,
                                      "pace_ms": 0, "id": "ex"})
    wait_job(client, "ex")
    r = client.get("/api/export/ex", params={"bot": "shark"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "ex/events.jsonl" in names


@pytest.mark.parametrize("path, body", [
    ("/api/matches", {"bots": ["shark", "no_such_bot"]}),
    ("/api/matches", {"bots": ["shark"]}),
    ("/api/matches", {"bots": ["shark", "aggressor"], "blinds": "10/5"}),
    ("/api/runs", {"format": "swiss", "bots": ["shark", "aggressor"]}),
    ("/api/runs", {"format": "table", "bots": ["shark", "aggressor"], "mode": "bogus"}),
])
def test_bad_requests(client, path, body):
    assert client.post(path, json=body).status_code == 400


def test_duplicate_ids_and_missing_things(client):
    body = {"bots": ["shark", "aggressor"], "hands": 2, "pace_ms": 0, "id": "dup"}
    assert client.post("/api/matches", json=body).status_code == 201
    wait_job(client, "dup")
    assert client.post("/api/matches", json=body).status_code == 400
    assert client.get("/api/matches/nope").status_code == 404
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/matches/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/matches/../../etc/events").status_code == 404


def test_cancel_a_tournament_run(client):
    client.post("/api/runs", json={"format": "round_robin", "bots": ["shark", "aggressor",
                                   "mathematician"], "hands": 300, "pace_ms": 100, "id": "rr"})
    time.sleep(0.6)
    live = client.get("/api/runs/rr").json()
    assert live["job"]["status"] == "running" and live["job"]["live"]
    assert all(k.startswith("rr/matches/") for k in live["job"]["live"])
    client.post("/api/jobs/rr/cancel")
    assert wait_job(client, "rr", timeout=10)["status"] == "cancelled"
    assert client.get("/api/runs/rr").json()["data"]["status"] == "cancelled"
