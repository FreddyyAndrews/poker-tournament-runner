"""Shutting the server down waits for running jobs, so their files are
finished (match_end, summary.json, run.json / series.json) and their bot
processes are stopped; new jobs are refused while stopping."""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from arena.server import create_app
from arena.server.jobs import JobManager, ServerStopping

REPO = Path(__file__).resolve().parent.parent
SLOW_BOT = ("import time\ndef decide(s):\n    if s.get('type') == 'warmup':\n        return {}\n"
            "    time.sleep(1.0)\n    return {'action': 'call'}\n")


def child_bot_processes():
    out = subprocess.run(["pgrep", "-P", str(os.getpid()), "-f", "sandbox/runner.py"],
                         capture_output=True, text=True).stdout
    return out.split()


def test_shutdown_waits_for_a_decision_in_progress(tmp_path):
    (tmp_path / "slow.py").write_text(SLOW_BOT)
    jobs = JobManager(tmp_path / "runs")
    jobs.start_match({"bots": [str(tmp_path / "slow.py"), "shark"], "mode": "cash", "hands": 100,
                      "id": "slow"})
    time.sleep(1.5)                                  # the slow bot is mid-decision
    assert child_bot_processes()
    t0 = time.time()
    assert jobs.shutdown() == []                     # nothing left running
    assert time.time() - t0 > 0.2                    # it actually waited
    assert jobs.get("slow").status == "cancelled"
    d = tmp_path / "runs" / "slow"
    summary = json.loads((d / "summary.json").read_text())
    assert summary["stopped"] == "cancelled"
    assert {r["bot"] for r in summary["results"]} == {"slow", "shark"}   # standings kept
    lines = (d / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["type"] == "match_end"
    assert not child_bot_processes()


def test_runs_and_series_finish_their_files(tmp_path):
    jobs = JobManager(tmp_path / "runs")
    jobs.start_run({"format": "bracket", "bots": ["shark", "aggressor", "mathematician", "template"],
                    "blinds": "turbo", "pace_ms": 200, "id": "b"})
    jobs.start_run({"format": "table", "bots": ["shark", "aggressor"], "mode": "cash", "hands": 3000,
                    "runs": 4, "workers": 2, "id": "s"})
    time.sleep(1.0)
    assert jobs.shutdown() == []
    assert json.loads((tmp_path / "runs" / "b" / "run.json").read_text())["status"] == "cancelled"
    assert json.loads((tmp_path / "runs" / "s" / "series.json").read_text())["status"] == "cancelled"
    for f in (tmp_path / "runs").rglob("events.jsonl"):
        assert f.read_text().endswith("\n")
    assert not child_bot_processes()


def test_no_new_jobs_while_stopping(tmp_path):
    jobs = JobManager(tmp_path / "runs")
    jobs.begin_shutdown()
    with pytest.raises(ServerStopping):
        jobs.start_match({"bots": ["shark", "aggressor"], "hands": 3})
    with TestClient(create_app(tmp_path / "runs2")) as c:
        c.app.state.jobs.begin_shutdown()
        r = c.post("/api/matches", json={"bots": ["shark", "aggressor"], "hands": 3})
        assert r.status_code == 503 and "shutting down" in r.json()["detail"]
        r = c.post("/api/runs", json={"format": "bracket", "bots": ["shark", "aggressor"]})
        assert r.status_code == 503


def test_ctrl_c_on_poker_serve_with_a_live_viewer(tmp_path):
    runs = tmp_path / "runs"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    srv = subprocess.Popen([sys.executable, "-m", "arena.cli", "serve", "--port", str(port), "--out", str(runs)],
                           cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/api/health")
                break
            except Exception:
                time.sleep(0.1)
        req = urllib.request.Request(url + "/api/matches", headers={"content-type": "application/json"},
                                     data=json.dumps({"bots": ["shark", "aggressor"], "mode": "cash",
                                                      "hands": 500, "pace_ms": 300, "id": "m"}).encode())
        urllib.request.urlopen(req)
        ended = {}

        def watch():
            with urllib.request.urlopen(url + "/api/matches/m/stream") as r:
                for line in r:
                    if line.startswith(b"event: end"):
                        ended["yes"] = True

        threading.Thread(target=watch, daemon=True).start()
        time.sleep(1.5)
        srv.send_signal(signal.SIGINT)
        out, _ = srv.communicate(timeout=60)         # exits; an open stream doesn't hold it
    finally:
        if srv.poll() is None:
            srv.kill()
    assert "Stopping" in out
    assert ended.get("yes")
    summary = json.loads((runs / "m" / "summary.json").read_text())
    assert summary["stopped"] == "cancelled" and summary["results"]
    assert json.loads((runs / "m" / "events.jsonl").read_text().splitlines()[-1])["type"] == "match_end"
