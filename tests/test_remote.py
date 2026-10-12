"""Tests for remote seats (humans and LLMs over MCP)."""
import asyncio
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from mcp.client import Client

from arena.pacing import Cancelled
from arena.remote import RemoteSeat
from arena.server import create_app
from arena.server.mcp_server import describe, legal_actions

STATE = {"type": "action_request", "hand_id": "h", "hand_num": 0, "street": "preflop", "seat_to_act": 0,
         "your_bot_id": "me", "pot": 150, "community_cards": [], "current_bet": 100, "min_raise_to": 200,
         "amount_owed": 50, "can_check": False, "your_cards": ["As", "Kh"], "your_stack": 9950,
         "your_bet_this_street": 50, "action_log": [{"seat": 0, "bot_id": "me", "action": "small_blind", "amount": 50},
                                                    {"seat": 1, "bot_id": "bot", "action": "big_blind", "amount": 100}],
         "players": [{"seat": 0, "bot_id": "me", "stack": 9950, "state": "active", "is_folded": False,
                      "is_all_in": False, "bet_this_street": 50},
                     {"seat": 1, "bot_id": "bot", "stack": 9900, "state": "active", "is_folded": False,
                      "is_all_in": False, "bet_this_street": 100}],
         "blinds": {"sb": 50, "bb": 100, "ante": 0}, "button_seat": 0, "sb_seat": 0, "bb_seat": 1}


def ask(seat, state):
    box = {}
    t = threading.Thread(target=lambda: box.update(r=seat.request_action(state)))
    t.start()
    for _ in range(100):
        if seat.pending():
            break
        time.sleep(0.01)
    return t, box


def test_remote_seat_submit():
    seat = RemoteSeat("me", "human", time_limit_ms=5000)
    assert seat.pending() is None and not seat.submit("call")
    t, box = ask(seat, STATE)
    p = seat.pending()
    assert p["state"]["your_cards"] == ["As", "Kh"] and 0 < p["time_left_ms"] <= 5000
    assert not seat.submit("call", decision_id=p["id"] + 1)          # stale decision id
    assert seat.submit("raise", 600, note="value", decision_id=p["id"])
    assert not seat.submit("fold")                                   # already answered
    t.join(2)
    assert box["r"]["action"] == "raise" and box["r"]["amount"] == 600 and box["r"]["logs"] == "value"
    assert seat.pending() is None


@pytest.mark.parametrize("can_check, expected", [(False, "fold"), (True, "check")])
def test_remote_seat_timeout(can_check, expected):
    seat = RemoteSeat("me", "mcp", time_limit_ms=150)
    r = seat.request_action({**STATE, "can_check": can_check})
    assert r["action"] == expected and r["error"] == "timeout"
    assert seat.summary()["timeouts"] == 1


def test_remote_seat_cancel():
    cancel = threading.Event()
    seat = RemoteSeat("me", "human", time_limit_ms=10_000, cancel=cancel)
    threading.Timer(0.2, cancel.set).start()
    with pytest.raises(Cancelled):
        seat.request_action(STATE)


def test_describe_and_legal_actions():
    legal = legal_actions(STATE)
    assert set(legal) == {"fold", "call", "raise", "all_in"}
    assert legal["call"]["amount"] == 50 and legal["raise"] == {"min_to": 200, "max_to": 10000}
    text = describe(STATE)
    assert "A♠ K♥" in text and "To call: 50" in text and "Legal actions:" in text
    assert set(legal_actions({**STATE, "amount_owed": 0, "can_check": True})) == {"check", "raise", "all_in"}


# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "runs")) as c:
        yield c


def test_human_seat_over_the_api(client):
    r = client.post("/api/matches", json={"bots": ["human:Ann", "mathematician"], "mode": "cash", "hands": 3,
                                          "seed": 2, "pace_ms": 0, "time_limits": {"human": 20000}, "id": "h1"})
    assert r.status_code == 201
    seats = r.json()["seats"]
    assert seats == [{"name": "Ann", "kind": "human", "token": seats[0]["token"], "time_limit_ms": 20000}]
    token = seats[0]["token"]
    assert "token" not in json.dumps(client.get("/api/jobs/h1").json())     # tokens never listed
    acted = 0
    t0 = time.time()
    while client.get("/api/jobs/h1").json()["status"] == "running":
        assert time.time() - t0 < 30
        sv = client.get(f"/api/seats/{token}").json()
        if sv["pending"]:
            assert client.get("/api/matches/h1/turn").json()[0]["bot"] == "Ann"
            st = sv["pending"]["state"]
            action = "check" if st["can_check"] else "call"
            assert client.post(f"/api/seats/{token}/act",
                               json={"action": action, "decision_id": sv["pending"]["id"]}).status_code == 200
            acted += 1
        time.sleep(0.02)
    assert acted > 0
    assert client.post(f"/api/seats/{token}/act", json={"action": "fold"}).status_code == 409
    ev = client.get("/api/matches/h1/events").json()["events"]
    mine = [e for e in ev if e["type"] == "decision" and e["bot"] == "Ann"]
    assert len(mine) == acted and all(e["error"] is None for e in mine)
    assert ev[0]["seats"][0]["kind"] == "human"
    assert client.get("/api/verify/h1").json()["ok"]

    # Ann's view hides the bot's hole cards and private decision data
    view = client.get("/api/matches/h1/events", params={"as": "Ann"}).json()["events"]
    for e in view:
        if e["type"] == "hand_start":
            assert set(e["hole"]) == {"Ann"}
        if e["type"] == "decision" and e["bot"] != "Ann":
            assert "state" not in e and "logs" not in e


def test_remote_seats_only_in_single_matches(client):
    r = client.post("/api/runs", json={"format": "bracket", "bots": ["human:Ann", "shark"]})
    assert r.status_code == 400 and "single matches" in r.json()["detail"]
    assert client.get("/api/seats/nope").status_code == 404


def test_llm_plays_over_mcp(client):
    mcp = client.app.state.mcp

    async def play():
        async with Client(mcp) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            assert {"start_match", "wait_for_turn", "act", "list_open_seats"} <= set(names)

            def data(r):
                assert not r.is_error and len(r.content) == 1      # one JSON object per tool call
                return json.loads(r.content[0].text)
            m = data(await c.call_tool("start_match", {"opponents": ["mathematician"], "hands": 3}))
            assert m["seat"] == "claude" and m["match_key"]
            open_seats = data(await c.call_tool("list_open_seats", {}))["seats"]
            assert any(s["token"] == m["token"] for s in open_seats)
            assert any(b["name"] == "shark" for b in data(await c.call_tool("list_bots", {}))["bots"])
            turns = 0
            while True:
                t = data(await c.call_tool("wait_for_turn", {"token": m["token"], "timeout_s": 10}))
                if t["status"] == "match_over":
                    return m, turns, t
                if t["status"] == "your_turn":
                    turns += 1
                    assert "Legal actions" in t["summary"]
                    bad = data(await c.call_tool("act", {"token": m["token"], "action": "raise"}))
                    assert not bad["ok"]
                    act = "check" if "check" in t["legal"] else "call"
                    ok = data(await c.call_tool("act", {"token": m["token"], "action": act,
                                                         "decision_id": t["decision_id"], "reasoning": "test"}))
                    assert ok["ok"]

    m, turns, over = asyncio.run(play())
    assert turns > 0 and len(over["results"]) == 2
    ev = client.get(f"/api/matches/{m['match_key']}/events").json()["events"]
    mine = [e for e in ev if e["type"] == "decision" and e["bot"] == "claude"]
    assert len(mine) == turns and all(e["logs"] == "test" for e in mine)
    assert client.get(f"/api/verify/{m['match_key']}").json()["ok"]
