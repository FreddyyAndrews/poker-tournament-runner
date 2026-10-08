"""Seats: whoever sits at a table and makes decisions.

Every kind of player implements the same interface, so the table never needs
to know whether it is talking to a sandboxed bot, a human in the browser, or
an LLM over MCP (the last two arrive with remote seats).
"""

from sandbox.match import BotProcess

DEFAULT_TIME_LIMIT_MS = {"bot": 2000, "human": 30000, "mcp": 120000}


class Seat:
    kind = "seat"

    def __init__(self, name: str, time_limit_ms: int = None):
        self.name = name
        self.time_limit_ms = time_limit_ms or DEFAULT_TIME_LIMIT_MS.get(self.kind, 2000)

    def start(self):
        """Prepare to play (start processes, etc.). Called once per match."""

    def warmup(self) -> dict:
        """Last chance to load before hand 1. Returns info for the log."""
        return {"ok": True}

    def request_action(self, state: dict) -> dict:
        """Return a decision: {"action", "amount"?, "think_ms"?, "logs"?,
        "debug"?, "error"?, ...}. Must always return, folding on problems."""
        raise NotImplementedError

    def stop(self):
        """Release resources. Called once per match, even after errors."""

    def summary(self) -> dict:
        return {}

    def info(self) -> dict:
        return {"name": self.name, "kind": self.kind, "time_limit_ms": self.time_limit_ms}


class BotSeat(Seat):
    """A bot running in the sandbox (local process or Docker container)."""

    kind = "bot"

    def __init__(self, spec, seed: int, time_limit_ms: int = None, use_docker: bool = None):
        super().__init__(spec.name, time_limit_ms)
        self.spec = spec
        self.seed = seed
        self.use_docker = use_docker
        self.proc = None

    def start(self):
        self.proc = BotProcess(self.name, self.spec.path, seed=self.seed,
                               use_docker=self.use_docker)

    def warmup(self) -> dict:
        reply = self.proc.warmup()
        return {"ok": bool(reply.get("ok")), "error": reply.get("error"),
                "load_ms": self.proc.load_ms, "logs": self.proc.load_logs}

    def request_action(self, state: dict) -> dict:
        return self.proc.act(state, self.time_limit_ms)

    def stop(self):
        if self.proc is not None:
            self.proc.stop()

    def summary(self) -> dict:
        if self.proc is None:
            return {}
        return {**self.proc.summary(), "errors_list": self.proc.errors[:50],
                "stderr_tail": self.proc.stderr_lines()[-50:]}

    def info(self) -> dict:
        return {**super().info(), "path": self.spec.path, "hash": self.spec.hash,
                "source": self.spec.source, "docker": bool(self.proc and self.proc.use_docker)}
