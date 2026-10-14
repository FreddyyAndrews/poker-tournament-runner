"""MCP server: lets an LLM (Claude Code, Claude Desktop, any MCP client) take a
seat and play, through exactly the same RemoteSeat a human uses.

Served by `poker serve` at http://127.0.0.1:<port>/mcp (streamable HTTP):

    claude mcp add --transport http poker http://127.0.0.1:8765/mcp

Typical loop: start_match (or list_open_seats + claim_seat for a seat someone
created in the UI), then repeat wait_for_turn -> act until the match is over.
Each seat is played by one agent: claim_seat hands a seat's token to exactly one
caller, and claimed seats disappear from list_open_seats.
"""

import asyncio
import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from arena.events import SUMMARY_FILE

INSTRUCTIONS = """You are playing No-Limit Texas Hold'em against poker bots.
1. Call start_match with the opponents you want (see list_bots). Or, to join a
   match someone else set up, call list_open_seats and then claim_seat(match_key, seat)
   to get that seat's token. A seat belongs to whoever claims it first; if the claim
   fails, pick another open seat. Never use a token you did not get yourself.
2. Loop: wait_for_turn(token) until status is "your_turn" or "match_over".
   When it is your turn, read `summary` and `legal`, then call act(token, action, amount).
   amount is the TOTAL you are raising to, not the raise size.
3. You have a time limit per decision (time_left_ms); if it runs out you check or fold.
The match can be watched live in the web UI at the watch_url."""

SUIT = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}


def _cards(cards) -> str:
    return " ".join(c[0].replace("T", "10") + SUIT.get(c[1], c[1]) for c in cards) if cards else "none"


def legal_actions(state: dict) -> dict:
    owed, stack = state["amount_owed"], state["your_stack"]
    legal = {}
    if state["can_check"]:
        legal["check"] = {}
    else:
        legal["fold"] = {}
        legal["call"] = {"amount": min(owed, stack)}
    max_to = stack + state["your_bet_this_street"]
    if stack > owed and max_to > state["current_bet"]:
        legal["raise"] = {"min_to": min(state["min_raise_to"], max_to), "max_to": max_to}
    legal["all_in"] = {"amount": max_to}
    return legal


def describe(state: dict) -> str:
    """A plain-English description of a decision, for an LLM."""
    me = state["your_bot_id"]
    pos = {state["button_seat"]: "button", state["sb_seat"]: "small blind", state["bb_seat"]: "big blind"}
    b = state["blinds"]
    lines = [
        f"Hand {state['hand_num'] + 1}, {state['street']}. Blinds {b['sb']}/{b['bb']}"
        + (f", ante {b['ante']}" if b.get("ante") else "") + ".",
        f"You are {me} in seat {state['seat_to_act'] + 1}"
        + (f" ({pos[state['seat_to_act']]})" if state["seat_to_act"] in pos else "") + ".",
        f"Your cards: {_cards(state['your_cards'])}. Board: {_cards(state['community_cards'])}.",
        f"Pot {state['pot']}. Your stack {state['your_stack']} "
        f"({state['your_bet_this_street']} already in this street). To call: {state['amount_owed']}.",
        "Players:",
    ]
    for p in state["players"]:
        tag = "you" if p["bot_id"] == me else p["state"]
        lines.append(f"  seat {p['seat'] + 1} {p['bot_id']}: {p['stack']} chips, "
                     f"bet {p['bet_this_street']} this street ({tag}"
                     + (f", {pos[p['seat']]}" if p["seat"] in pos else "") + ")")
    if state["action_log"]:
        lines.append("This hand so far: " + "; ".join(
            f"{a['bot_id']} {a['action'].replace('_', ' ')}" + (f" {a['amount']}" if a["amount"] else "")
            for a in state["action_log"]))
    legal = legal_actions(state)
    opts = []
    for k, v in legal.items():
        if k == "raise":
            opts.append(f"raise (amount = total to raise to, {v['min_to']}..{v['max_to']})")
        elif k == "call":
            opts.append(f"call {v['amount']}")
        elif k == "all_in":
            opts.append(f"all_in ({v['amount']})")
        else:
            opts.append(k)
    lines.append("Legal actions: " + " | ".join(opts))
    return "\n".join(lines)


def build_mcp(jobs, runs_dir: Path, base_url: str = "http://127.0.0.1:8765") -> MCPServer:
    mcp = MCPServer("poker", instructions=INSTRUCTIONS)
    runs_dir = Path(runs_dir)

    def seat_or_error(token):
        seat = jobs.hub.get(token)
        if seat is None:
            raise ValueError("Unknown seat token. Use start_match or list_open_seats.")
        return seat

    def results(match_key):
        f = runs_dir / match_key / SUMMARY_FILE
        if not f.is_file():
            return None
        s = json.loads(f.read_text())
        return [{k: r[k] for k in ("bot", "place", "final_stack", "delta", "hands_played")}
                for r in s.get("results") or []]

    @mcp.tool()
    async def list_bots() -> dict:
        """Bots you can play against (names from my_bots/ and bots/)."""
        from arena import bots as botlib
        return {"bots": [{"name": n, "folder": f} for n, _, f in botlib.available()]}

    @mcp.tool()
    async def start_match(opponents: list[str], seat_name: str = "claude", hands: int = 50,
                          mode: str = "cash", blinds: str = "50/100", starting_stack: int = 10000,
                          time_limit_ms: int = 120000, pace_ms: int = 0) -> dict:
        """Start a match with you in a seat against 1-8 bots.
        mode: "cash" (each hand from the starting stack), "fixed" (stacks carry over)
        or "freezeout" (until one player has every chip; use blinds "turbo"/"standard").
        pace_ms > 0 slows the bots down so people can watch.
        Returns your seat token; then call wait_for_turn(token)."""
        p = {"bots": [f"mcp:{seat_name}", *opponents], "mode": mode, "hands": hands,
             "blinds": blinds, "starting_stack": starting_stack, "pace_ms": pace_ms,
             "time_limits": {"mcp": time_limit_ms}}
        job = jobs.start_match(p)
        seat = job.seats[0]
        jobs.hub.get(seat["token"]).claim()          # your seat; any other mcp: seats stay open
        return {"match_key": job.id, "token": seat["token"], "seat": seat["name"],
                "time_limit_ms": seat["time_limit_ms"],
                "watch_url": f"{base_url}/#/match/{job.id}"}

    @mcp.tool()
    async def list_open_seats() -> dict:
        """MCP seats nobody has claimed yet (e.g. created on the web UI's New page,
        or as mcp: opponents in another agent's start_match). Take one with claim_seat."""
        return {"seats": [{"seat": s.name, "match_key": s.match_key,
                           "waiting_on_this_seat": s.pending() is not None,
                           "watch_url": f"{base_url}/#/match/{s.match_key}"}
                          for s in jobs.hub.open_seats("mcp")]}

    @mcp.tool()
    async def claim_seat(match_key: str, seat: str) -> dict:
        """Take an open seat (from list_open_seats) for yourself and get its token.
        Only one player can claim a seat: if someone already has it, choose another."""
        try:
            s = jobs.hub.claim(match_key, seat)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "match_key": s.match_key, "seat": s.name, "token": s.token,
                "time_limit_ms": s.time_limit_ms, "watch_url": f"{base_url}/#/match/{s.match_key}"}

    @mcp.tool()
    async def wait_for_turn(token: str, timeout_s: float = 60) -> dict:
        """Wait (up to timeout_s) until it is your turn or the match ends.
        Returns status "your_turn" (with summary, legal actions, state, decision_id,
        time_left_ms), "match_over" (with results), or "waiting" (call again)."""
        seat = seat_or_error(token)
        seat.claim()                                  # using a token claims the seat
        deadline = asyncio.get_running_loop().time() + max(1.0, min(timeout_s, 600))
        while True:
            p = seat.pending()
            if p:
                state = {k: v for k, v in p["state"].items() if k != "match_action_log"}
                return {"status": "your_turn", "decision_id": p["id"], "time_left_ms": p["time_left_ms"],
                        "summary": describe(state), "legal": legal_actions(state), "state": state}
            if seat.finished:
                for _ in range(50):              # the summary is written just after the last hand
                    if results(seat.match_key) is not None:
                        break
                    await asyncio.sleep(0.1)
                return {"status": "match_over", "match_key": seat.match_key,
                        "results": results(seat.match_key)}
            if asyncio.get_running_loop().time() >= deadline:
                return {"status": "waiting", "message": "Not your turn yet; call wait_for_turn again."}
            await asyncio.sleep(0.1)

    @mcp.tool()
    async def act(token: str, action: str, amount: int | None = None, decision_id: int | None = None,
                  reasoning: str | None = None) -> dict:
        """Make your move: action is fold, check, call, raise or all_in.
        For raise, amount is the TOTAL bet to raise to (see legal.raise.min_to/max_to).
        Pass the decision_id from wait_for_turn to be sure you act on that decision.
        reasoning (optional) is saved with the decision and shown in the replay."""
        seat = seat_or_error(token)
        seat.claim()
        if action not in ("fold", "check", "call", "raise", "all_in"):
            return {"ok": False, "error": "action must be fold, check, call, raise or all_in"}
        if action == "raise" and amount is None:
            return {"ok": False, "error": "raise needs an amount (the total to raise to)"}
        if not seat.submit(action, amount, note=reasoning, decision_id=decision_id):
            return {"ok": False, "error": "It is not your turn (or that decision has passed). "
                                          "Call wait_for_turn."}
        return {"ok": True}

    @mcp.tool()
    async def get_match(match_key: str) -> dict:
        """Status and results of a match."""
        d = runs_dir / match_key
        if not d.is_dir():
            return {"error": f"no match {match_key!r}"}
        res = results(match_key)
        return {"match_key": match_key, "status": "complete" if res is not None else "running",
                "results": res, "watch_url": f"{base_url}/#/match/{match_key}"}

    return mcp
