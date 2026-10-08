"""Tests for the bot runner protocol v2 and host-side process handling.

Each test writes a small (often badly behaved) bot to a temp dir and drives it
through the real BotProcess / run_match machinery in local (non-Docker) mode.
"""
import os
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sandbox.match as match
from sandbox.match import BotProcess, run_match


STATE = {
    "type": "action_request", "hand_id": "t_h0000", "hand_num": 0,
    "street": "preflop", "seat_to_act": 0, "your_bot_id": "me", "pot": 150,
    "community_cards": [], "current_bet": 100, "min_raise_to": 200,
    "amount_owed": 100, "can_check": False, "your_cards": ["As", "Kh"],
    "your_stack": 9900, "your_bet_this_street": 0, "players": [], "action_log": [],
}


@pytest.fixture(autouse=True)
def fast_host(monkeypatch):
    monkeypatch.setattr(match, "HOST_GRACE_S", 1.0)


@pytest.fixture
def make_bot(tmp_path):
    procs = []

    def _make(source, name="bot", **kwargs):
        path = tmp_path / (name + ".py")
        path.write_text(textwrap.dedent(source))
        proc = BotProcess(name, str(path), **kwargs)
        procs.append(proc)
        return proc

    yield _make
    for p in procs:
        p.stop()


def state(**overrides):
    return {**STATE, **overrides}


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------

def test_prints_are_captured_not_protocol(make_bot):
    bot = make_bot("""
        import os, sys
        print("loading model")
        def decide(state):
            if state.get("type") == "warmup":
                return {}
            print("thinking about", state["your_cards"])
            print("to stderr", file=sys.stderr)
            sys.__stdout__.write("raw stdout\\n"); sys.__stdout__.flush()
            os.write(1, b"fd one\\n")
            return {"action": "call"}
    """)
    warm = bot.warmup()
    assert warm["ok"] is True
    assert "loading model" in bot.load_logs

    reply = bot.act(state())
    assert reply["action"] == "call"
    assert "error" not in reply
    assert "thinking about ['As', 'Kh']" in reply["logs"]
    assert "to stderr" in reply["logs"]
    assert reply["logs_truncated"] is False
    assert reply["think_ms"] >= 0
    # The second decision still works: nothing leaked into the protocol.
    assert bot.act(state())["action"] == "call"


def test_huge_output_is_truncated(make_bot):
    bot = make_bot("""
        def decide(state):
            print("x" * 100_000)
            return {"action": "check"}
    """)
    bot.warmup()
    reply = bot.act(state())
    assert reply["action"] == "check"
    assert reply["logs_truncated"] is True
    assert len(reply["logs"]) <= 8192


def test_chatty_stderr_never_blocks(make_bot):
    # 1 MB straight to fd 2 at import: far more than a pipe buffer holds.
    bot = make_bot("""
        import os
        os.write(2, b"noise\\n" * 200_000)
        def decide(state):
            return {"action": "fold"}
    """)
    assert bot.warmup()["ok"] is True
    assert bot.act(state())["action"] == "fold"
    assert "noise" in bot.stderr_lines()


# ---------------------------------------------------------------------------
# Return values
# ---------------------------------------------------------------------------

def test_debug_is_returned(make_bot):
    bot = make_bot("""
        def decide(state):
            return {"action": "raise", "amount": 300,
                    "debug": {"equity": 0.62, "limit": state["time_limit_ms"]}}
    """)
    bot.warmup()
    reply = bot.act(state(), time_limit_ms=1500)
    assert reply["action"] == "raise" and reply["amount"] == 300
    assert reply["debug"] == {"equity": 0.62, "limit": 1500}


def test_bad_debug_and_numpy_amount(make_bot):
    bot = make_bot("""
        import numpy as np
        def decide(state):
            return {"action": "raise", "amount": np.int64(450), "debug": {"s": {1, 2}}}
    """)
    bot.warmup()
    reply = bot.act(state())
    assert reply["action"] == "raise" and reply["amount"] == 450
    assert "debug" not in reply
    assert "not JSON-serialisable" in reply["debug_error"]


def test_exception_and_invalid_return(make_bot):
    bot = make_bot("""
        def decide(state):
            if state.get("type") == "warmup":
                return {}
            if state["hand_num"] == 0:
                raise ValueError("boom")
            return "call"
    """)
    bot.warmup()
    reply = bot.act(state(hand_num=0))
    assert reply == {**reply, "action": "fold", "error": "exception"}
    assert "ValueError: boom" in reply["error_detail"]
    reply = bot.act(state(hand_num=1))
    assert reply["action"] == "fold" and reply["error"] == "invalid_return"


def test_load_failure_reported(make_bot):
    bot = make_bot("""
        import not_a_real_module
        def decide(state):
            return {"action": "call"}
    """)
    warm = bot.warmup()
    assert warm["ok"] is False and warm["error"] == "load_failed"
    assert "not_a_real_module" in warm["error_detail"]
    assert any(e.startswith("load_failed") for e in bot.errors)
    reply = bot.act(state())
    assert reply["action"] == "fold" and reply["error"] == "load_failed"


# ---------------------------------------------------------------------------
# Timeouts, stalls, crashes
# ---------------------------------------------------------------------------

def test_slow_decision_times_out_then_recovers(make_bot):
    bot = make_bot("""
        import time
        def decide(state):
            if state.get("hand_num") == 0:
                time.sleep(2)
            return {"action": "call"}
    """)
    bot.warmup()
    t0 = time.monotonic()
    reply = bot.act(state(hand_num=0), time_limit_ms=200)
    assert time.monotonic() - t0 < 1.0
    assert reply["action"] == "fold" and reply["error"] == "timeout"
    assert bot.act(state(hand_num=1), time_limit_ms=200)["action"] == "call"
    assert bot.summary()["timeouts"] == 1
    assert bot.restarts == 0


def test_frozen_process_is_restarted(make_bot, tmp_path, monkeypatch):
    marker = tmp_path / "froze_once"
    monkeypatch.setenv("TEST_MARKER", str(marker))
    bot = make_bot("""
        import os, signal
        def decide(state):
            if state.get("type") != "warmup" and not os.path.exists(os.environ["TEST_MARKER"]):
                open(os.environ["TEST_MARKER"], "w").close()
                os.kill(os.getpid(), signal.SIGSTOP)   # whole process stops responding
            return {"action": "call"}
    """)
    bot.warmup()
    t0 = time.monotonic()
    reply = bot.act(state(), time_limit_ms=200)
    assert time.monotonic() - t0 < 5
    assert reply["action"] == "fold" and reply["error"] == "host_timeout"
    assert bot.restarts == 1
    assert bot.act(state(), time_limit_ms=200)["action"] == "call"


def test_crashed_process_is_restarted(make_bot, tmp_path, monkeypatch):
    marker = tmp_path / "crashed_once"
    monkeypatch.setenv("TEST_MARKER", str(marker))
    bot = make_bot("""
        import os
        def decide(state):
            if state.get("type") != "warmup" and not os.path.exists(os.environ["TEST_MARKER"]):
                open(os.environ["TEST_MARKER"], "w").close()
                os._exit(1)
            return {"action": "call"}
    """)
    bot.warmup()
    reply = bot.act(state())
    assert reply["action"] == "fold" and reply["error"] == "process_died"
    assert bot.act(state())["action"] == "call"


def test_bot_disabled_after_max_restarts(make_bot):
    bot = make_bot("""
        import os
        def decide(state):
            if state.get("type") != "warmup":
                os._exit(1)
            return {}
    """, max_restarts=1)
    bot.warmup()
    assert bot.act(state())["error"] == "process_died"   # restart 1
    assert bot.act(state())["error"] == "process_died"   # out of restarts
    assert bot.disabled
    t0 = time.monotonic()
    assert bot.act(state())["error"] == "disabled"
    assert time.monotonic() - t0 < 0.1


# ---------------------------------------------------------------------------
# Matches: decisions, determinism, warmup
# ---------------------------------------------------------------------------

RANDOM_BOT = """
import random
SALT = random.random()          # module-level randomness is seeded too
def decide(state):
    if state.get("type") == "warmup":
        return {}
    r = random.random()
    print("roll", r)
    if state["can_check"] and r < 0.5:
        return {"action": "check", "debug": {"r": r, "salt": SALT}}
    if r < 0.15:
        return {"action": "fold", "debug": {"r": r}}
    if r < 0.4:
        return {"action": "raise", "amount": state["min_raise_to"] * 2, "debug": {"r": r}}
    return {"action": "call", "debug": {"r": r}}
"""


def _random_match(tmp_path, seed, n_hands=40):
    path = tmp_path / "random_bot.py"
    path.write_text(RANDOM_BOT)
    bots = {name: str(path) for name in ("alpha", "beta", "gamma")}
    return run_match("m_" + str(seed), bots, n_hands=n_hands, seed=seed)


def _fingerprint(result):
    return [(h["community_cards"], [(d["bot_id"], d["action"], d["amount"], d["debug"])
                                    for d in h["decisions"]])
            for h in result["hands"]]


def test_same_seed_same_match(tmp_path):
    a = _random_match(tmp_path, seed=11)
    b = _random_match(tmp_path, seed=11)
    c = _random_match(tmp_path, seed=12)
    assert a["final_stacks"] == b["final_stacks"]
    assert _fingerprint(a) == _fingerprint(b)
    assert _fingerprint(a) != _fingerprint(c)


def test_seed_generated_when_missing(tmp_path):
    path = tmp_path / "random_bot.py"
    path.write_text(RANDOM_BOT)
    result = run_match("m", {"a": str(path), "b": str(path)}, n_hands=3)
    assert isinstance(result["seed"], int)


def test_match_records_decisions_and_stats(tmp_path):
    result = _random_match(tmp_path, seed=5, n_hands=10)
    decisions = [d for h in result["hands"] for d in h["decisions"]]
    assert decisions
    for d in decisions:
        assert d["bot_id"] in ("alpha", "beta", "gamma")
        assert d["logs"].startswith("roll ")
        assert "r" in d["debug"]
        assert d["error"] is None
    stats = result["bot_stats"]
    assert sum(s["decisions"] for s in stats.values()) == len(decisions)
    assert all(s["timeouts"] == 0 and s["errors"] == 0 for s in stats.values())


def test_warmup_runs_in_parallel(tmp_path):
    path = tmp_path / "slow_loader.py"
    path.write_text(textwrap.dedent("""
        import time
        def decide(state):
            if state.get("type") == "warmup":
                time.sleep(1.5)
                return {}
            return {"action": "fold"}
    """))
    t0 = time.monotonic()
    run_match("m", {b: str(path) for b in ("a", "b", "c", "d")}, n_hands=1, seed=1)
    assert time.monotonic() - t0 < 4.0   # sequential would be >= 6 s
