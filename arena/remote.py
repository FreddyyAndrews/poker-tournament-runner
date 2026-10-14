"""Remote seats: humans (in the browser or terminal) and LLMs (over MCP).

Both are the same thing to a table: a RemoteSeat. When it is asked to act it
publishes a pending decision (the state, a deadline) and waits until someone
submits an action for it, by seat token, through the API or MCP. If the
deadline passes it checks when it can and otherwise folds, and the decision is
logged with error "timeout", exactly like a bot timeout.

Seats are found by an unguessable token, created when the match is started
(only on this machine; the server binds to localhost).

An LLM seat belongs to one agent: it is claimed the first time its token is
used, or explicitly with claim_seat, and claimed seats are no longer offered to
anyone else (list_open_seats hides them and never shows tokens). Human seats are
not claimed: whoever is at this machine can rejoin them from the browser.
"""

import secrets
import threading
import time

from arena.pacing import Cancelled
from arena.seats import DEFAULT_TIME_LIMIT_MS, Seat


class RemoteSeat(Seat):
    def __init__(self, name: str, kind: str, time_limit_ms: int = None, cancel: threading.Event = None):
        if kind not in ("human", "mcp"):
            raise ValueError(f"remote seat kind must be human or mcp, got {kind!r}")
        self.kind = kind
        super().__init__(name, time_limit_ms or DEFAULT_TIME_LIMIT_MS[kind])
        self.token = secrets.token_urlsafe(12)
        self.match_key = None
        self.cancel = cancel or threading.Event()
        self._lock = threading.Lock()
        self._submitted = threading.Event()
        self._pending = None        # {"id", "state", "started", "deadline"}
        self._reply = None
        self._decisions = 0
        self._timeouts = 0
        self._think_total = 0.0
        self.finished = False
        self.claimed = False        # an agent holds this seat's token (see claim)

    # -- table side ------------------------------------------------------------

    def request_action(self, state: dict) -> dict:
        started = time.time()
        with self._lock:
            self._reply = None
            self._submitted.clear()
            self._pending = {"id": self._decisions + 1, "state": state, "started": started,
                             "deadline": started + self.time_limit_ms / 1000}
        try:
            while True:
                left = self._pending["deadline"] - time.time()
                if left <= 0 or self._submitted.wait(min(left, 0.1)):
                    break
                if self.cancel.is_set():
                    raise Cancelled()
        finally:
            with self._lock:
                reply, self._pending = self._reply, None
        think_ms = round((time.time() - started) * 1000, 1)
        self._decisions += 1
        self._think_total += think_ms
        if reply is None:
            self._timeouts += 1
            return {"action": "check" if state.get("can_check") else "fold", "error": "timeout",
                    "think_ms": think_ms}
        out = {"action": reply["action"], "think_ms": think_ms}
        if reply.get("amount") is not None:
            out["amount"] = reply["amount"]
        if reply.get("note"):
            out["logs"] = str(reply["note"])[:8192]
        return out

    def stop(self):
        self.finished = True

    def summary(self) -> dict:
        n = self._decisions
        return {"decisions": n, "avg_think_ms": round(self._think_total / n, 1) if n else 0.0,
                "max_think_ms": None, "timeouts": self._timeouts, "errors": self._timeouts,
                "restarts": 0, "disabled": False}

    # -- player side -------------------------------------------------------------

    def claim(self) -> bool:
        """Mark the seat as taken. True only for the caller that took it."""
        with self._lock:
            if self.claimed:
                return False
            self.claimed = True
            return True

    def pending(self):
        """The decision waiting for this seat, or None."""
        with self._lock:
            p = self._pending
            if not p:
                return None
            return {"id": p["id"], "state": p["state"], "deadline": p["deadline"],
                    "time_left_ms": max(0, int((p["deadline"] - time.time()) * 1000)),
                    "time_limit_ms": self.time_limit_ms}

    def submit(self, action: str, amount=None, note: str = None, decision_id: int = None) -> bool:
        """Submit an action for the pending decision. False if nothing is pending
        (or it is a different decision than the caller saw)."""
        with self._lock:
            if not self._pending or self._submitted.is_set():
                return False
            if decision_id is not None and decision_id != self._pending["id"]:
                return False
            self._reply = {"action": str(action), "amount": amount, "note": note}
            self._submitted.set()
            return True

    def info(self) -> dict:
        return {**super().info(), "remote": True}


class SeatHub:
    """All remote seats this server has created, by token and by match."""

    def __init__(self):
        self.by_token = {}
        self._lock = threading.Lock()

    def add(self, seat: RemoteSeat, match_key: str):
        seat.match_key = match_key
        with self._lock:
            self.by_token[seat.token] = seat

    def get(self, token: str):
        return self.by_token.get(token)

    def for_match(self, match_key: str) -> list:
        return [s for s in self.by_token.values() if s.match_key == match_key]

    def open_seats(self, kind: str = None) -> list:
        """Unfinished seats someone can still take: every human seat (rejoining
        from the browser), and LLM seats that nobody has claimed."""
        return [s for s in self.by_token.values()
                if not s.finished and (kind is None or s.kind == kind)
                and not (s.kind == "mcp" and s.claimed)]

    def claim(self, match_key: str, name: str, kind: str = "mcp") -> RemoteSeat:
        """Claim an open seat by match and seat name; raises ValueError if there
        is no such seat or another agent already has it."""
        seat = next((s for s in self.by_token.values()
                     if s.match_key == match_key and s.name == name and s.kind == kind), None)
        if seat is None or seat.finished:
            raise ValueError(f"no open {kind} seat {name!r} in match {match_key!r}")
        if not seat.claim():
            raise ValueError(f"seat {name!r} in match {match_key!r} is already taken by another player")
        return seat
