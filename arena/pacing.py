"""Pacing and cancellation for matches people watch live.

A Pacer is called after every event a table logs. With pace_ms > 0 it sleeps
so the match plays out at a watchable speed (deals and showdowns linger longer
than a post). A bot's own think time counts toward the pause, so a slow bot
isn't slowed further and the bot always keeps its full time limit.

Pacing never changes what happens in a match, only when; results and logs are
identical with or without it (verify ignores timing).

The same hook lets a job be cancelled: once the cancel event is set, the next
logged event raises Cancelled, which ends the match cleanly (match_end is still
written, with stopped = "cancelled").
"""

import threading

# Pause per event type, as a multiple of pace_ms.
PAUSE = {
    "hand_start": 1.0,
    "post":       0.25,
    "decision":   1.0,
    "deal":       1.25,
    "showdown":   2.0,
    "award":      1.0,
    "hand_end":   1.5,
    "elimination": 2.0,
    "level":      1.0,
    "seat_change": 0.5,
    "table_closed": 1.0,
}

DEFAULT_PACE_MS = 600


class Cancelled(Exception):
    pass


class Pacer:
    def __init__(self, pace_ms: int = 0, cancel: threading.Event = None):
        self.pace_ms = max(0, int(pace_ms or 0))
        self.cancel = cancel or threading.Event()

    def __call__(self, event: dict):
        if self.cancel.is_set():
            raise Cancelled()
        if not self.pace_ms:
            return
        ms = self.pace_ms * PAUSE.get(event.get("type"), 0.0)
        if event.get("type") == "decision":
            ms -= event.get("think_ms") or 0
        if ms > 0 and self.cancel.wait(ms / 1000):
            raise Cancelled()
