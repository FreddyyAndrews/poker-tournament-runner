"""
Bot Runner (protocol v2) — executes inside the Docker sandbox (or directly
under Python locally for dev). Loads /bot/bot.py, reads requests from stdin,
writes replies to stdout. All communication is newline-delimited JSON.

Requests
--------
  {"type": "warmup"}                      one-shot call before hand 1
  {"type": "action_request", ...state}    a decision; state["time_limit_ms"]
                                          sets the deadline for this call

Replies
-------
  warmup:   {"ok", "logs", "logs_truncated", "load_ms", "error"?, "error_detail"?}
  decision: {"action", "amount"?, "debug"?, "debug_error"?, "logs",
             "logs_truncated", "think_ms", "error"?, "error_detail"?}

Bot output
----------
Anything the bot prints (stdout or stderr, including module-level prints at
import) is captured per call and returned as "logs" instead of corrupting the
protocol. The real stdout file descriptor is reserved for replies; fd 1 is
pointed at stderr so even low-level writes (os.write(1, ...)) can't break it.

Bots may add an optional JSON-serialisable "debug" value to their returned
action dict; it is passed back to the host and recorded with the decision.

Determinism
-----------
If BOT_SEED is set, `random` (and numpy's global RNG, if installed) are seeded
before the bot is imported and re-seeded before every decision from
(BOT_SEED, hand_num, decision index). The same match seed then gives the same
decisions, even if earlier decisions timed out. Bots that create their own
unseeded RNGs (random.Random(), os.urandom, time-based seeds) stay random.

Timeouts
--------
decide() runs on a worker thread; we wait on an Event with a timeout. Python
can't kill a thread, so a timed-out decide() may keep running in the
background (its output is still captured, but discarded). If the whole
process stops responding, the host kills and restarts it (see match.py).
"""

import hashlib
import importlib.util
import json
import os
import random
import sys
import threading
import time
import traceback

BOT_PATH       = os.environ.get("BOT_PATH", "/bot/bot.py")
TIMEOUT_S      = float(os.environ.get("ACTION_TIMEOUT", "2"))
WARMUP_TIMEOUT = float(os.environ.get("WARMUP_TIMEOUT", "30"))
BOT_SEED       = os.environ.get("BOT_SEED")

LOG_LIMIT    = int(os.environ.get("BOT_LOG_LIMIT", "8192"))     # chars per call
DEBUG_LIMIT  = int(os.environ.get("BOT_DEBUG_LIMIT", "16384"))  # chars of JSON
DETAIL_LIMIT = 4000                                             # traceback chars


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------

# Reserve the real stdout for protocol replies, then point fd 1 at stderr so
# nothing the bot does to stdout can reach the protocol channel.
_PROTO = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)
_REAL_ERR = sys.__stderr__


class _CallLog:
    """Bounded text buffer for one call's output."""

    def __init__(self, limit=LOG_LIMIT):
        self.limit     = limit
        self.parts     = []
        self.size      = 0
        self.truncated = False

    def write(self, s):
        room = self.limit - self.size
        if room <= 0:
            self.truncated = self.truncated or bool(s)
            return
        if len(s) > room:
            s, self.truncated = s[:room], True
        self.parts.append(s)
        self.size += len(s)

    def text(self):
        return "".join(self.parts)


_buffers = {}  # thread ident -> _CallLog


class _Capture:
    """sys.stdout/sys.stderr replacement that routes writes to the buffer of
    the calling thread, or to the real stderr when nothing is capturing."""

    encoding = "utf-8"
    errors   = "replace"

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        buf = _buffers.get(threading.get_ident())
        if buf is not None:
            buf.write(s)
        else:
            _REAL_ERR.write(s)
        return len(s)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return 2


sys.stdout = _Capture()
sys.stderr = _Capture()


def emit(obj: dict):
    _PROTO.write(json.dumps(obj, default=str) + "\n")
    _PROTO.flush()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _derive_seed(*parts) -> int:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _seed_rngs(seed: int):
    random.seed(seed)
    np = sys.modules.get("numpy")
    if np is not None:
        try:
            np.random.seed(seed % (2 ** 32))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Calling the bot
# ---------------------------------------------------------------------------

class _BotTimeout(Exception):
    pass


def _call_captured(fn, arg, timeout_s, log):
    """Run fn(arg) on a worker thread whose output goes to `log`.
    Returns (value, think_ms). Raises _BotTimeout or whatever fn raised."""
    box  = {"value": None, "error": None, "ms": None}
    done = threading.Event()

    def _worker():
        _buffers[threading.get_ident()] = log
        t0 = time.perf_counter()
        try:
            box["value"] = fn(arg)
        except BaseException as e:   # bots can raise anything
            box["error"] = e
        finally:
            box["ms"] = (time.perf_counter() - t0) * 1000
            _buffers.pop(threading.get_ident(), None)
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    if not done.wait(timeout_s):
        raise _BotTimeout()
    if box["error"] is not None:
        raise box["error"]
    return box["value"], box["ms"]


def _detail():
    return traceback.format_exc()[-DETAIL_LIMIT:]


def _clean_action(raw):
    """Normalise the bot's return value into JSON-safe protocol fields."""
    if not isinstance(raw, dict) or "action" not in raw:
        raise ValueError("decide() must return a dict with an 'action' key, got "
                         + repr(raw)[:200])
    out = {"action": str(raw["action"])}
    if raw.get("amount") is not None:
        try:
            out["amount"] = int(raw["amount"])   # numpy ints, floats, "1200"
        except (TypeError, ValueError):
            out["amount"] = 0
    if "debug" in raw:
        try:
            encoded = json.dumps(raw["debug"])
        except (TypeError, ValueError) as e:
            out["debug_error"] = "debug is not JSON-serialisable: " + str(e)[:200]
        else:
            if len(encoded) > DEBUG_LIMIT:
                out["debug"] = {"_truncated": True, "preview": encoded[:DEBUG_LIMIT]}
            else:
                out["debug"] = raw["debug"]
    return out


def _with_logs(reply, log):
    reply["logs"] = log.text()
    reply["logs_truncated"] = log.truncated
    return reply


def load_bot(path: str):
    spec   = importlib.util.spec_from_file_location("bot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "decide"):
        raise AttributeError("bot.py must define a decide() function")
    return module


def main():
    # Import the bot with its output captured, so module-level prints show up
    # in the warmup reply rather than on the protocol channel.
    load_log = _CallLog()
    _buffers[threading.get_ident()] = load_log
    t0 = time.perf_counter()
    bot, load_error = None, None
    try:
        if BOT_SEED is not None:
            try:
                import numpy  # noqa: F401  (so module-level np.random is seeded)
            except ImportError:
                pass
            _seed_rngs(_derive_seed(BOT_SEED, "load"))
        bot = load_bot(BOT_PATH)
    except BaseException:
        load_error = _detail()
    finally:
        _buffers.pop(threading.get_ident(), None)
    load_ms = (time.perf_counter() - t0) * 1000
    load_reported = False

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            emit({"action": "fold", "error": "bad_json", "error_detail": str(e)})
            continue

        is_warmup = msg.get("type") == "warmup"

        # Loading failed: answer every request with a fold, and report the
        # traceback (and any import-time output) once.
        if bot is None:
            reply = ({"ok": False, "load_ms": load_ms} if is_warmup
                     else {"action": "fold"})
            reply["error"] = "load_failed"
            if not load_reported:
                reply["error_detail"] = load_error
                _with_logs(reply, load_log)
                load_reported = True
            emit(reply)
            continue

        log = _CallLog()
        if not load_reported:
            log.write(load_log.text())
            log.truncated = load_log.truncated
            load_reported = True

        if is_warmup:
            if BOT_SEED is not None:
                _seed_rngs(_derive_seed(BOT_SEED, "warmup"))
            reply = {"ok": True, "load_ms": load_ms}
            try:
                _call_captured(bot.decide, msg, WARMUP_TIMEOUT, log)
            except _BotTimeout:
                reply.update(ok=False, error="warmup_timeout")
            except Exception:
                reply.update(ok=False, error="warmup_exception", error_detail=_detail())
            emit(_with_logs(reply, log))
            continue

        try:
            timeout_s = float(msg["time_limit_ms"]) / 1000
        except (KeyError, TypeError, ValueError):
            timeout_s = TIMEOUT_S

        if BOT_SEED is not None:
            _seed_rngs(_derive_seed(BOT_SEED, msg.get("hand_num"),
                                    len(msg.get("action_log") or [])))

        try:
            raw, think_ms = _call_captured(bot.decide, msg, timeout_s, log)
        except _BotTimeout:
            reply = {"action": "fold", "error": "timeout",
                     "think_ms": round(timeout_s * 1000, 3)}
        except Exception:
            reply = {"action": "fold", "error": "exception", "error_detail": _detail()}
        else:
            try:
                reply = _clean_action(raw)
            except ValueError as e:
                reply = {"action": "fold", "error": "invalid_return",
                         "error_detail": str(e)}
            reply["think_ms"] = round(think_ms, 3)
        emit(_with_logs(reply, log))


if __name__ == "__main__":
    main()
