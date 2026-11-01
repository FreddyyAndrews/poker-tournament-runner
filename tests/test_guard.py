"""Tests for keeping bot code away from the rest of the machine
(sandbox/guard.py): the environment bots get, the audit hook that confines a
local bot process to its folder, and the validator's secrets rules."""
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sandbox import guard
from sandbox.match import BotProcess
from sandbox.validator import check_static

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VALIDATOR = os.path.join(REPO, "sandbox", "validator.py")

STATE = {"type": "action_request", "hand_id": "h1", "hand_num": 0, "street": "preflop",
         "seat_to_act": 0, "your_bot_id": "probe", "pot": 150, "community_cards": [],
         "current_bet": 100, "min_raise_to": 200, "amount_owed": 50, "can_check": False,
         "your_cards": ["Ah", "Kd"], "your_stack": 9950, "your_bet_this_street": 50,
         "players": [], "action_log": [], "time_limit_ms": 2000}


def test_bots_get_a_short_environment_without_secrets():
    env = guard.safe_env({"PATH": "/bin", "HOME": "/h", "BOT_SEED": "1", "JEV_MODEL": "jev-latest",
                          "JEV_API_KEY": "k", "GTO_WIZARD_API_KEY": "k", "AWS_SECRET_ACCESS_KEY": "k",
                          "GITHUB_TOKEN": "t", "BOT_PASSWORD": "p", "EDITOR": "vim",
                          "POKER_PROXY_URL": "http://127.0.0.1:8766"})
    assert env == {"PATH": "/bin", "HOME": "/h", "BOT_SEED": "1", "JEV_MODEL": "jev-latest",
                   "POKER_PROXY_URL": "http://127.0.0.1:8766"}


def static_errors(tmp_path, source):
    p = tmp_path / "bot.py"
    p.write_text(textwrap.dedent(source) + "\ndef decide(state):\n    return {'action': 'fold'}\n")
    return [w["message"] for w in check_static(str(p)) if w["level"] == "error"]


@pytest.mark.parametrize("source, found", [
    ("from dotenv import load_dotenv", "dotenv"),
    ("P = '../../.env'", "secrets file"),
    ("P = '.env.local'", "secrets file"),
    ("import os\nK = os.environ['JEV_API_KEY']", "'JEV_API_KEY'"),
    ("import os\nK = os.environ.get('SLUMBOT_PASSWORD')", "'SLUMBOT_PASSWORD'"),
    ("import os\nK = os.getenv('GITHUB_TOKEN')", "'GITHUB_TOKEN'"),
])
def test_validator_rejects_reaching_for_secrets(tmp_path, source, found):
    assert any(found in e for e in static_errors(tmp_path, source))


def test_validator_allows_ordinary_settings_and_prose(tmp_path):
    assert static_errors(tmp_path, '''
        """Reads its settings from the environment, never from a .env file."""
        import os
        MODEL = os.environ.get("JEV_MODEL", "jev-latest")
        DATA = os.path.join(os.path.dirname(__file__), "data", "table.json")
    ''') == []


def test_the_validator_confines_the_bot_it_imports(tmp_path):
    outside = os.path.join(REPO, "README.md")
    (tmp_path / "bot.py").write_text(f"OUTSIDE = open({outside!r}).read()\n"
                                     "def decide(state):\n    return {'action': 'fold'}\n")
    out = subprocess.run([sys.executable, VALIDATOR, str(tmp_path / "bot.py"), "--json"],
                         capture_output=True, text=True, cwd=REPO,
                         env={**os.environ, "SOME_API_KEY": "abc"})
    result = json.loads(out.stdout)
    assert not result["passed"]
    assert "may only open files in their own folder" in result["errors"][0]


# -- a real local bot process ------------------------------------------------------

@pytest.fixture
def run_probe(tmp_path, monkeypatch):
    """Play one decision of a bot folder with the given decide() body; returns its debug."""
    procs = []

    def run(body, data=None):
        d = tmp_path / "probe"
        (d / "data").mkdir(parents=True, exist_ok=True)
        for name, text in (data or {}).items():
            (d / "data" / name).write_text(text)
        (d / "bot.py").write_text("import os, socket, json\n\n" + textwrap.dedent(body))
        bot = BotProcess("probe", str(d), use_docker=False)
        procs.append(bot)
        bot.warmup()
        reply = bot.act(dict(STATE), time_limit_ms=2000)
        assert reply.get("error") is None, reply
        return reply["debug"]
    yield run
    for p in procs:
        p.stop()


ATTEMPT = '''
def attempt(fn):
    try:
        fn()
        return "allowed"
    except Exception as e:
        return type(e).__name__ + ": " + str(e)
'''


def test_a_bot_reads_its_own_folder_and_nothing_else(run_probe):
    outside = os.path.join(REPO, "README.md")
    debug = run_probe(ATTEMPT + f'''
def decide(state):
    here = os.path.dirname(__file__)
    return {{"action": "fold", "debug": {{
        "own": json.load(open(os.path.join(here, "data", "own.json"))),
        "own_list": sorted(os.listdir(os.path.join(here, "data"))),
        "outside": attempt(lambda: open({outside!r}).read()),
        "outside_list": attempt(lambda: os.listdir({REPO!r})),
        "write": attempt(lambda: open(os.path.join(here, "data", "new.txt"), "w")),
        "traversal": attempt(lambda: open(os.path.join(here, "..", "..", "x")))}}}}
''', data={"own.json": '{"ok": 1}'})
    assert debug["own"] == {"ok": 1} and debug["own_list"] == ["own.json"]
    assert debug["outside"].startswith("Refused: bots may only open files in their own folder")
    assert debug["outside_list"].startswith("Refused: bots may only list their own folder")
    assert debug["write"].startswith("Refused: bots may not write files")
    assert debug["traversal"].startswith("Refused")


def test_a_bot_gets_no_secrets_from_the_environment(run_probe, monkeypatch):
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", "abc123")
    monkeypatch.setenv("JEV_MODEL", "jev-test")
    debug = run_probe('''
def decide(state):
    return {"action": "fold", "debug": {"env": dict(os.environ)}}
''')
    assert "FAKE_SERVICE_API_KEY" not in debug["env"] and "abc123" not in json.dumps(debug["env"])
    assert debug["env"]["JEV_MODEL"] == "jev-test"


def test_a_bot_reaches_only_this_machine_and_starts_no_processes(run_probe):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    threading.Thread(target=lambda: server.accept(), daemon=True).start()
    try:
        debug = run_probe(ATTEMPT + f'''
def decide(state):
    return {{"action": "fold", "debug": {{
        "loopback": attempt(lambda: socket.create_connection(("127.0.0.1", {port}), timeout=2).close()),
        "internet": attempt(lambda: socket.create_connection(("1.1.1.1", 80), timeout=2)),
        "process": attempt(lambda: os.system("true"))}}}}
''')
    finally:
        server.close()
    assert debug["loopback"] == "allowed"
    assert debug["internet"].startswith("Refused: bots may only connect to this machine")
    assert debug["process"].startswith("Refused: bots may not start processes")
