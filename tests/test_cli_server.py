"""The CLI runs matches and tournaments on the server (the default), follows
them, and cancels them on Ctrl+C. --local runs in-process."""
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp.client import Client

from arena.server import create_app

REPO = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(runs, port=port), host="127.0.0.1", port=port,
                                        log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "runs": runs}
    srv.should_exit = True
    t.join(10)


def cli(server, *args, **kw):
    env = {**os.environ, "POKER_SERVER": server["url"]}
    return subprocess.run([sys.executable, "-m", "arena.cli", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=300, **kw)


def api(server, path):
    import urllib.request
    return json.load(urllib.request.urlopen(server["url"] + path))


def test_match_runs_on_the_server(server):
    out = cli(server, "match", "shark", "aggressor", "--cash", "--hands", "15", "--seed", "4", "--id", "s1", "--json")
    assert out.returncode == 0, out.stderr
    summary = json.loads(out.stdout)
    assert summary["hands"] == 15 and summary["match_id"] == "s1"
    assert Path(summary["run_dir"]).parent == server["runs"]           # written by the server
    assert api(server, "/api/jobs/s1")["status"] == "complete"         # tracked as a server job
    assert {r["id"]: r["status"] for r in api(server, "/api/runs")}["s1"] == "complete"
    assert out.stderr == ""                                            # --json: stdout only
    plain = cli(server, "match", "shark", "mathematician", "--cash", "--hands", "3", "--id", "s1b")
    assert plain.returncode == 0 and "Playing on" in plain.stderr and "#/match/s1b" in plain.stderr
    assert "Match s1b" in plain.stdout


def test_run_and_series_on_the_server(server):
    out = cli(server, "run", "configs/bracket.yaml", "--blinds", "turbo", "--seed", "2", "--id", "b1")
    assert out.returncode == 0, out.stderr
    assert "Bracket:" in out.stdout and "matches done" in out.stderr
    assert api(server, "/api/runs/b1")["status"] == "complete"
    out = cli(server, "run", "--format", "table", "--bots", "shark", "aggressor", "--hands", "10",
              "--runs", "3", "--workers", "3", "--seed", "1", "--id", "ser1", "--json")
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["kind"] == "series" and sum(a["wins"] for a in data["aggregate"]) == 3


def test_ctrl_c_cancels_on_the_server(server):
    env = {**os.environ, "POKER_SERVER": server["url"]}
    p = subprocess.Popen([sys.executable, "-m", "arena.cli", "match", "shark", "aggressor", "--cash",
                          "--hands", "300", "--pace", "200", "--id", "c1"], cwd=REPO, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        try:
            if api(server, "/api/matches/c1")["status"] == "running":
                break
        except Exception:
            pass
        time.sleep(0.05)
    p.send_signal(signal.SIGINT)
    _, err = p.communicate(timeout=30)
    assert p.returncode == 1 and "Cancelling on the server" in err
    assert api(server, "/api/jobs/c1")["status"] == "cancelled"
    assert api(server, "/api/matches/c1")["status"] == "cancelled"


def test_errors(server):
    out = cli(server, "match", "shark", "aggressor", "--out", "/tmp/somewhere-else")
    assert out.returncode == 2 and "--out only applies with --local" in out.stderr
    out = cli(server, "match", "shark", "nope_not_a_bot")
    assert out.returncode == 2 and "not found" in out.stderr
    env_bad = {**os.environ, "POKER_SERVER": f"http://127.0.0.1:{free_port()}"}
    out = subprocess.run([sys.executable, "-m", "arena.cli", "match", "shark", "aggressor"], cwd=REPO,
                         env=env_bad, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and "poker serve" in out.stderr and "--local" in out.stderr


def test_local_never_touches_the_server(server, tmp_path):
    out = cli(server, "match", "--local", "shark", "aggressor", "--hands", "5", "--out", str(tmp_path),
              "--id", "loc", "--json")
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "loc" / "summary.json").is_file()
    assert "loc" not in {r["id"] for r in api(server, "/api/runs")}
    out = cli(server, "match", "--local", "mcp:claude", "shark")
    assert out.returncode == 2 and "single matches" in out.stderr


def test_cli_match_with_an_mcp_seat(server):
    """An agent starts a match from the CLI and plays it over MCP."""
    env = {**os.environ, "POKER_SERVER": server["url"]}
    p = subprocess.Popen([sys.executable, "-m", "arena.cli", "match", "mcp:claude", "mathematician",
                          "--cash", "--hands", "3", "--id", "m1", "--json"], cwd=REPO, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    token, box = None, {}

    def read_token():
        for line in p.stderr:
            if "mcp seat claude: token" in line:
                box["token"] = line.split("token ")[1].split()[0]
                return
    reader = threading.Thread(target=read_token, daemon=True)
    reader.start()
    reader.join(20)
    token = box.get("token")
    if not token:
        p.kill()
    assert token, "the CLI never printed the MCP seat token"

    async def play():
        async with Client(server["url"] + "/mcp") as c:
            while True:
                r = await c.call_tool("wait_for_turn", {"token": token, "timeout_s": 10})
                t = json.loads(r.content[0].text)
                if t["status"] == "match_over":
                    return
                if t["status"] == "your_turn":
                    act = "check" if "check" in t["legal"] else "call"
                    await c.call_tool("act", {"token": token, "action": act, "decision_id": t["decision_id"]})

    asyncio.run(play())
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 0
    summary = json.loads(out)
    assert {r["bot"] for r in summary["results"]} == {"claude", "mathematician"}
