"""
Match Orchestrator
Runs a multi-hand match between N bots (2-9).

Dev mode (USE_DOCKER=false):  bots run as local subprocesses via runner.py
Prod mode (USE_DOCKER=true):  bots run in isolated Docker containers

Submission formats supported (auto-detected from path):
  - bot.py         single-file bot (legacy)
  - bot/           directory containing bot.py + optional data/
  - bot.zip        archive containing bot.py at root + optional data/

The game engine is pure Python — this file handles all I/O and process management.

Host-side safety: replies are read on a background thread with a deadline of
the bot's time limit plus a grace period. A process that stops responding (or
dies) is killed and restarted, and that decision is a fold. Each bot's stderr
is drained continuously into a ring buffer so a chatty bot can never block.

Reproducibility: every match has a seed (one is generated if not given). It
determines the deck for every hand, and each bot's RNG is seeded from it (see
runner.py), so the same seed and bots replay the same match.
"""

import collections
import hashlib
import json
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.game import PokerEngine, STARTING_STACK

RUNNER_PATH    = Path(__file__).parent / "runner.py"
SANDBOX_IMAGE  = os.environ.get("SANDBOX_IMAGE", "fullhouse-sandbox:latest")
USE_DOCKER     = os.environ.get("USE_DOCKER", "false").lower() == "true"
ACTION_TIMEOUT = float(os.environ.get("ACTION_TIMEOUT", "2"))     # seconds
WARMUP_TIMEOUT = float(os.environ.get("WARMUP_TIMEOUT", "30"))    # seconds

# Extra time the host waits beyond a bot's own deadline before declaring the
# process stuck. Covers IPC and container overhead; the runner normally
# answers (with a timeout fold) right at the deadline.
HOST_GRACE_S   = float(os.environ.get("HOST_GRACE", "1.5"))
MAX_RESTARTS   = int(os.environ.get("BOT_MAX_RESTARTS", "3"))
STDERR_LINES   = 200

# Resource limits enforced at the container level. Bumped from 256 -> 768 MB
# in May 2026 to accommodate optional /bot/data/ payloads (CFR blueprints,
# NN weights, lookup tables) that bots load at module-import time.
CONTAINER_MEMORY     = os.environ.get("BOT_MEMORY", "768m")
CONTAINER_CPUS       = os.environ.get("BOT_CPUS",   "0.5")
CONTAINER_TMPFS_SIZE = os.environ.get("BOT_TMPFS",  "20m")

# Per-match rolling action log exposed to bots in state["match_action_log"].
# Lets bots build cross-hand opponent models within a match.
MATCH_LOG_MAX_ENTRIES = 200


# ---------------------------------------------------------------------------
# Bot mount preparation
# ---------------------------------------------------------------------------

def _prepare_bot_mount(bot_path):
    """Returns (mount_src, cleanup_dir).
    Accepts: directory, .zip archive (extracted into tempdir), or .py file (legacy, copied into tempdir).
    """
    p = os.path.abspath(bot_path)

    if os.path.isdir(p):
        return p, None

    if p.endswith(".zip") and os.path.isfile(p):
        tmpdir = tempfile.mkdtemp(prefix="fhbot_")
        with zipfile.ZipFile(p) as zf:
            for member in zf.infolist():
                name = member.filename
                if name.startswith("/") or name.startswith("\\"):
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    raise ValueError("Unsafe zip path (absolute): " + repr(name))
                norm = os.path.normpath(os.path.join(tmpdir, name))
                if not norm.startswith(tmpdir + os.sep) and norm != tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    raise ValueError("Unsafe zip path (traversal): " + repr(name))
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    raise ValueError("Unsafe zip path (symlink): " + repr(name))
            zf.extractall(tmpdir)
        if not os.path.isfile(os.path.join(tmpdir, "bot.py")):
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise ValueError("Zip archive must contain bot.py at the root")
        return tmpdir, tmpdir

    if p.endswith(".py") and os.path.isfile(p):
        tmpdir = tempfile.mkdtemp(prefix="fhbot_")
        shutil.copy(p, os.path.join(tmpdir, "bot.py"))
        return tmpdir, tmpdir

    raise ValueError("Unsupported bot path (must be .py, .zip, or directory): " + repr(p))


# ---------------------------------------------------------------------------
# Bot process wrapper
# ---------------------------------------------------------------------------

def derive_seed(*parts) -> int:
    """Stable 63-bit seed from any parts (same inputs -> same seed)."""
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


class BotProcess:
    """Wraps one bot in a subprocess or Docker container.
    Communication: newline-delimited JSON over stdin/stdout (protocol v2,
    see runner.py).
    """

    def __init__(self, bot_id, bot_path, seed=None, use_docker=None,
                 max_restarts=MAX_RESTARTS):
        self.bot_id       = bot_id
        self.bot_path     = bot_path
        self.seed         = seed
        self.use_docker   = USE_DOCKER if use_docker is None else use_docker
        self.max_restarts = max_restarts
        self.errors       = []
        self.restarts     = 0
        self.disabled     = False
        self.load_logs    = ""
        self.load_ms      = None
        self.stats        = {"decisions": 0, "timeouts": 0, "errors": 0,
                             "think_ms_total": 0.0, "think_ms_max": 0.0}
        self._stderr      = collections.deque(maxlen=STDERR_LINES)
        self._proc        = None
        self._replies     = None
        self._cleanup_dir = None

        try:
            self._mount_src, self._cleanup_dir = _prepare_bot_mount(bot_path)
        except Exception as e:
            self.errors.append("mount_prep_failed: " + str(e))
            self.disabled = True
            return

        self._start()

    # -- process lifecycle ---------------------------------------------------

    def _start(self):
        container_bot_py = "/bot/bot.py"
        extra_env = {"ACTION_TIMEOUT": str(ACTION_TIMEOUT),
                     "WARMUP_TIMEOUT": str(WARMUP_TIMEOUT)}
        if self.seed is not None:
            extra_env["BOT_SEED"] = str(self.seed)

        if self.use_docker:
            cmd = [
                "docker", "run",
                "--rm",
                "-i",
                "--network", "none",
                "--memory",  CONTAINER_MEMORY,
                "--memory-swap", CONTAINER_MEMORY,
                "--cpus",    CONTAINER_CPUS,
                "--read-only",
                "--no-new-privileges",
                "--user",    "1000:1000",
                "--tmpfs",   "/tmp:size=" + CONTAINER_TMPFS_SIZE,
                "-v",        self._mount_src + ":/bot:ro",
                "-e",        "BOT_PATH=" + container_bot_py,
                "-e",        "BOT_DATA_DIR=/bot/data",
            ]
            for k, v in extra_env.items():
                cmd += ["-e", k + "=" + v]
            cmd.append(SANDBOX_IMAGE)
        else:
            cmd = [sys.executable, "-u", str(RUNNER_PATH)]

        host_bot_py = os.path.join(self._mount_src, "bot.py")
        env = {
            **os.environ,
            **extra_env,
            "BOT_PATH":     container_bot_py if self.use_docker else host_bot_py,
            "BOT_DATA_DIR": "/bot/data" if self.use_docker else os.path.join(self._mount_src, "data"),
        }
        if self.seed is None:
            env.pop("BOT_SEED", None)

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        # Fresh reply queue per process, so nothing from a killed process
        # can be mistaken for an answer from its replacement.
        self._replies = queue.Queue()
        threading.Thread(target=self._read_stdout, args=(self._proc, self._replies),
                         daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self._proc,),
                         daemon=True).start()

    @staticmethod
    def _read_stdout(proc, replies):
        try:
            for line in proc.stdout:
                replies.put(line)
        except Exception:
            pass
        finally:
            replies.put(None)  # EOF sentinel

    def _read_stderr(self, proc):
        try:
            for line in proc.stderr:
                self._stderr.append(line.rstrip("\n"))
        except Exception:
            pass

    def _kill(self):
        if self._proc is None:
            return
        try:
            self._proc.kill()
            self._proc.wait(timeout=5)
        except Exception:
            pass
        self._proc = None

    def _restart(self, reason):
        self._kill()
        if self.restarts >= self.max_restarts:
            self.disabled = True
            self.errors.append("disabled_after_restarts: " + reason)
            return
        self.restarts += 1
        self._start()
        self.warmup()

    # -- requests ------------------------------------------------------------

    def _request(self, msg, timeout_s):
        """Send one message and wait for its reply.
        Returns (reply_dict, None) or (None, problem) where problem is
        'host_timeout' or 'process_died'."""
        try:
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
        except Exception:
            return None, "process_died"

        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "host_timeout"
            try:
                line = self._replies.get(timeout=remaining)
            except queue.Empty:
                return None, "host_timeout"
            if line is None:
                return None, "process_died"
            try:
                return json.loads(line), None
            except json.JSONDecodeError:
                self.errors.append("bad_reply: " + line.strip()[:200])

    def warmup(self):
        """One-shot 'wake up the bot before hand 1' call.

        Bots that load CFR blueprints, NN weights, or large lookup tables
        at module-import time can take 10-30s to do their first decision.
        The runner gives this call WARMUP_TIMEOUT instead of the per-action
        limit. The reply carries anything the bot printed while importing.
        Returns the reply dict (or a synthesised failure).
        """
        if self.disabled or self._proc is None:
            return {"ok": False, "error": "disabled"}
        reply, problem = self._request({"type": "warmup"}, WARMUP_TIMEOUT + HOST_GRACE_S + 10)
        if problem:
            self.errors.append("warmup_failed: " + problem)
            return {"ok": False, "error": problem}
        self.load_logs += reply.get("logs", "")
        if self.load_ms is None:
            self.load_ms = reply.get("load_ms")
        error, detail = reply.get("error"), reply.get("error_detail")
        if error == "warmup_exception":
            # Many bots don't expect the {"type": "warmup"} message and raise
            # on it. Harmless (the bot is loaded); keep it in the load logs.
            self.load_logs += "[warmup raised — bots can ignore state['type'] == 'warmup']\n"
            self.load_logs += detail or ""
        elif error:
            self.errors.append(error + (": " + detail.strip().splitlines()[-1]
                                        if detail else ""))
        return reply

    def act(self, game_state, time_limit_ms=None):
        """Ask the bot for a decision. Always returns a dict with at least
        "action"; problems are reported in "error" and the action is a fold.
        Also returned: logs, logs_truncated, think_ms, debug (if any)."""
        if time_limit_ms is None:
            time_limit_ms = int(ACTION_TIMEOUT * 1000)
        if self.disabled or self._proc is None:
            return self._record({"action": "fold", "error": "disabled"})

        msg = dict(game_state)
        msg["time_limit_ms"] = time_limit_ms
        reply, problem = self._request(msg, time_limit_ms / 1000 + HOST_GRACE_S)
        if problem:
            self._restart(problem)
            return self._record({"action": "fold", "error": problem,
                                 "think_ms": float(time_limit_ms)})
        return self._record(reply)

    def _record(self, reply):
        s = self.stats
        s["decisions"] += 1
        think = reply.get("think_ms")
        if isinstance(think, (int, float)):
            s["think_ms_total"] += think
            s["think_ms_max"] = max(s["think_ms_max"], think)
        err = reply.get("error")
        if err:
            s["errors"] += 1
            if err in ("timeout", "host_timeout"):
                s["timeouts"] += 1
            self.errors.append(err)
        return reply

    def summary(self):
        s = self.stats
        n = s["decisions"]
        return {
            "decisions":    n,
            "avg_think_ms": round(s["think_ms_total"] / n, 3) if n else 0.0,
            "max_think_ms": round(s["think_ms_max"], 3),
            "timeouts":     s["timeouts"],
            "errors":       s["errors"],
            "restarts":     self.restarts,
            "disabled":     self.disabled,
            "load_ms":      self.load_ms,
        }

    def stderr_lines(self):
        """Most recent lines the bot process wrote to stderr outside of
        decide() calls (runner diagnostics, crashes, stray threads)."""
        return list(self._stderr)

    def stop(self):
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._cleanup_dir and os.path.isdir(self._cleanup_dir):
            shutil.rmtree(self._cleanup_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Match runner
# ---------------------------------------------------------------------------

def _inject_match_log(state, match_log):
    if state.get("type") == "action_request":
        state["match_action_log"] = match_log[-MATCH_LOG_MAX_ENTRIES:]
    return state


def run_match(match_id, bot_paths, n_hands=400, verbose=False, seed=None,
              time_limit_ms=None, use_docker=None):
    """Play n_hands (or until one bot has all the chips).

    time_limit_ms: per-decision limit, an int for every bot or a dict
                   {bot_id: ms}; defaults to ACTION_TIMEOUT.
    seed:          match seed; generated (and returned) when omitted.
    """
    bot_ids = list(bot_paths.keys())
    n = len(bot_ids)
    assert 2 <= n <= 9, "Need 2-9 bots, got " + str(n)

    if seed is None:
        seed = random.SystemRandom().randrange(2 ** 31)
    if not isinstance(time_limit_ms, dict):
        time_limit_ms = {bid: time_limit_ms for bid in bot_ids}

    procs   = {bid: BotProcess(bid, path, seed=derive_seed(seed, "bot", bid),
                               use_docker=use_docker)
               for bid, path in bot_paths.items()}
    stacks  = {bid: STARTING_STACK for bid in bot_ids}
    hand_log = []
    match_action_log = []
    dealer = 0
    start_ts = time.time()

    try:
        # Warm-up: give every bot one long-deadline call so they can finish
        # heavy imports / lookup-table loads before hand 1. In parallel, so a
        # table of slow loaders takes as long as the slowest, not the sum.
        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(lambda p: p.warmup(), procs.values()))

        for hand_num in range(n_hands):
            alive = [bid for bid in bot_ids if stacks[bid] > 0]
            if len(alive) < 2:
                break

            hand_id = match_id + "_h" + str(hand_num).zfill(4)
            hand_seed = seed * 1000003 + hand_num
            engine = PokerEngine(
                hand_id        = hand_id,
                bot_ids        = alive,
                dealer_seat    = dealer % len(alive),
                starting_stacks= {bid: stacks[bid] for bid in alive},
                seed           = hand_seed,
                hand_num       = hand_num,
            )

            result = _play_hand(engine, procs, alive, match_action_log, hand_num,
                                verbose, time_limit_ms)
            hand_log.append({"hand_num": hand_num, "hand_id": hand_id, **result})

            for bid, s in result["final_stacks"].items():
                stacks[bid] = s

            dealer += 1

            if verbose and hand_num % 25 == 0:
                _print_stacks(hand_num, n_hands, stacks)

    finally:
        for p in procs.values():
            p.stop()

    return {
        "match_id":     match_id,
        "bot_ids":      bot_ids,
        "seed":         seed,
        "n_hands":      len(hand_log),
        "duration_s":   round(time.time() - start_ts, 2),
        "final_stacks": stacks,
        "chip_delta":   {bid: stacks[bid] - STARTING_STACK for bid in bot_ids},
        "bot_errors":   {bid: procs[bid].errors for bid in bot_ids},
        "bot_stats":    {bid: procs[bid].summary() for bid in bot_ids},
        "bot_load_logs": {bid: procs[bid].load_logs for bid in bot_ids},
        "bot_stderr":   {bid: procs[bid].stderr_lines() for bid in bot_ids},
        "hands":        hand_log,
    }


def _play_hand(engine, procs, active_bots, match_action_log, hand_num, verbose,
               time_limit_ms=None):
    state = _inject_match_log(engine.start_hand(), match_action_log)
    decisions = []
    steps = 0
    time_limit_ms = time_limit_ms or {}

    while state.get("type") == "action_request":
        seat   = state["seat_to_act"]
        bot_id = active_bots[seat]
        reply  = procs[bot_id].act(state, time_limit_ms.get(bot_id))
        action = {"action": reply.get("action")}
        if "amount" in reply:
            action["amount"] = reply["amount"]

        if verbose:
            print("  [" + bot_id + "] " + str(action), file=sys.stderr)

        decisions.append({
            "seat":           seat,
            "bot_id":         bot_id,
            "street":         state["street"],
            "action":         action["action"],
            "amount":         action.get("amount"),
            "think_ms":       reply.get("think_ms"),
            "logs":           reply.get("logs", ""),
            "logs_truncated": reply.get("logs_truncated", False),
            "debug":          reply.get("debug"),
            "debug_error":    reply.get("debug_error"),
            "error":          reply.get("error"),
            "error_detail":   reply.get("error_detail"),
        })
        match_action_log.append({
            "hand_num": hand_num,
            "seat":     seat,
            "bot_id":   bot_id,
            "action":   action["action"],
            "amount":   action.get("amount"),
        })

        state = _inject_match_log(engine.apply_action(seat, action), match_action_log)
        steps += 1

        if steps > 1000:
            raise RuntimeError("Hand exceeded 1000 steps: " + engine.hand_id)

    state["decisions"] = decisions
    return state


def _print_stacks(hand_num, total, stacks):
    print("\n  === Hand " + str(hand_num) + "/" + str(total) + " ===", file=sys.stderr)
    for bid, s in sorted(stacks.items(), key=lambda x: -x[1]):
        bar = "X" * (s // 1000)
        print("  " + bid.ljust(20) + " " + str(s).rjust(7) + "  " + bar, file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entrypoint for local testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a match locally")
    parser.add_argument("bots", nargs="+",
                        help="Paths to bot.py files, bot directories, or bot.zip archives")
    parser.add_argument("--hands", type=int, default=400)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="Output result as JSON (for worker)")
    parser.add_argument("--match-id", default=None)
    parser.add_argument("--seed", type=int, default=None,
                        help="Match seed. Same seed + same bots = same cards and same bot RNG. "
                             "Generated (and printed) when omitted.")
    parser.add_argument("--time-limit-ms", type=int, default=None,
                        help="Per-decision time limit (default: ACTION_TIMEOUT, 2000 ms)")
    parser.add_argument("--docker", action="store_true",
                        help="Run bots in the Docker sandbox (build it first: ./sandbox.sh build)")
    args = parser.parse_args()

    paths = {}
    for i, path in enumerate(args.bots):
        pp = Path(path)
        suffix = pp.suffix
        # Default bot_id from filename, but if it would collide (very common
        # with the "bots/<name>/bot.py" layout where every stem is "bot"),
        # fall back to the parent directory name. If that also collides
        # (e.g. the same bot path passed twice for self-play testing),
        # append a numeric suffix so every entry is unique.
        if suffix in (".py", ".zip"):
            bot_id = pp.stem
        else:
            bot_id = pp.name or "bot_" + str(i)
        if bot_id in paths or bot_id in ("bot",):
            bot_id = pp.parent.name or ("bot_" + str(i))
        base = bot_id or "bot_" + str(i)
        bot_id = base
        n = 2
        while bot_id in paths:
            bot_id = base + "_" + str(n)
            n += 1
        paths[bot_id] = path

    match_id = args.match_id or os.environ.get("MATCH_ID") or "local_" + uuid.uuid4().hex[:8]

    if not args.json:
        print("Starting match " + match_id + " with " + str(len(paths)) + " bots, " + str(args.hands) + " hands\n")

    result = run_match(match_id, paths, n_hands=args.hands, verbose=args.verbose, seed=args.seed,
                       time_limit_ms=args.time_limit_ms, use_docker=args.docker or None)

    if args.json:
        print(json.dumps({
            "match_id":     result["match_id"],
            "seed":         result["seed"],
            "n_hands":      result["n_hands"],
            "duration_s":   result["duration_s"],
            "final_stacks": result["final_stacks"],
            "chip_delta":   result["chip_delta"],
            "bot_errors":   result["bot_errors"],
            "bot_stats":    result["bot_stats"],
        }))
        sys.exit(0)

    print("\n" + "=" * 50)
    print("Match complete in " + str(result["duration_s"]) + "s  (seed " + str(result["seed"]) + ")")
    print("=" * 50)
    print("Bot".ljust(25) + " " + "Final Stack".rjust(12) + " " + "Delta".rjust(10))
    print("-" * 50)
    for bid in sorted(result["bot_ids"], key=lambda b: -result["final_stacks"][b]):
        delta = result["chip_delta"][bid]
        sign  = "+" if delta >= 0 else ""
        print(bid.ljust(25) + " " + str(result["final_stacks"][bid]).rjust(12) + " " + sign + str(delta).rjust(9))
    print("\nHands played: " + str(result["n_hands"]))

    print("\n" + "Bot".ljust(25) + " " + "Decisions".rjust(9) + " " + "Avg ms".rjust(8)
          + " " + "Max ms".rjust(8) + " " + "Timeouts".rjust(8) + " " + "Errors".rjust(6))
    for bid in result["bot_ids"]:
        st = result["bot_stats"][bid]
        print(bid.ljust(25) + " " + str(st["decisions"]).rjust(9)
              + " " + ("%.2f" % st["avg_think_ms"]).rjust(8)
              + " " + ("%.2f" % st["max_think_ms"]).rjust(8)
              + " " + str(st["timeouts"]).rjust(8) + " " + str(st["errors"]).rjust(6))

    errs = {b: e for b, e in result["bot_errors"].items() if e}
    if errs:
        print("\nBot errors: " + str(errs))
