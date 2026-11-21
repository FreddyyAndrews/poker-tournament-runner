"""
Bot processes: one bot, sandboxed, answering decisions over stdin/stdout.

Dev mode (USE_DOCKER=false):  bots run as local subprocesses via pokercore/sandbox/runner.py
Prod mode (USE_DOCKER=true):  bots run in isolated Docker containers

Submission formats supported (auto-detected from path):
  - bot.py         single-file bot (legacy)
  - bot/           directory containing bot.py + optional data/
  - bot.zip        archive containing bot.py at root + optional data/

The game engine is pure Python; this file handles the bot's process, its I/O and its limits.

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
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from pokercore.sandbox import guard

RUNNER_PATH    = Path(__file__).parent / "sandbox" / "runner.py"
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

# ---------------------------------------------------------------------------
# Bot mount preparation
# ---------------------------------------------------------------------------

# Single-file and zipped bots are unpacked into a temp dir that gets mounted
# into the container. It lives inside the repo by default because Docker VMs
# on macOS (Colima, and Docker Desktop in some setups) only share the home
# directory with containers, not the system temp dir.
BOT_MOUNT_DIR = Path(os.environ.get("BOT_MOUNT_DIR",
                                    Path(__file__).resolve().parent.parent / ".bot_mounts"))


def _mount_tmpdir():
    BOT_MOUNT_DIR.mkdir(parents=True, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="bot_", dir=str(BOT_MOUNT_DIR))
    os.chmod(tmpdir, 0o755)   # readable by the container's non-root user
    return tmpdir


def _prepare_bot_mount(bot_path):
    """Returns (mount_src, cleanup_dir).
    Accepts: directory, .zip archive (extracted into tempdir), or .py file (legacy, copied into tempdir).
    """
    p = os.path.abspath(bot_path)

    if os.path.isdir(p):
        return p, None

    if p.endswith(".zip") and os.path.isfile(p):
        tmpdir = _mount_tmpdir()
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
        tmpdir = _mount_tmpdir()
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
        self._container   = None
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
                     "WARMUP_TIMEOUT": str(WARMUP_TIMEOUT),
                     # One maths thread. A container sees every CPU of the host
                     # but may use half of one, so numpy's BLAS would start a
                     # thread per CPU and they would spend the bot's CPU share
                     # waiting on each other: a 0.2 s warmup took over 30 s.
                     "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        if self.seed is not None:
            extra_env["BOT_SEED"] = str(self.seed)

        if self.use_docker:
            # Named, so the container itself can be killed: killing the
            # `docker run` client alone leaves a stuck container running.
            self._container = "bot-" + uuid.uuid4().hex[:12]
            cmd = [
                "docker", "run",
                "--rm",
                "--name",    self._container,
                "-i",
                "--network", "none",
                "--memory",  CONTAINER_MEMORY,
                "--memory-swap", CONTAINER_MEMORY,
                "--cpus",    CONTAINER_CPUS,
                "--read-only",
                "--security-opt", "no-new-privileges",
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
        # Locally the bot process is the runner itself, so it gets only a short,
        # secret-free environment (pokercore/sandbox/guard.py); the docker client needs
        # the full one, and the container gets only what's passed with -e.
        env = {
            **(os.environ if self.use_docker else guard.safe_env(os.environ)),
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
        if self.use_docker and self._container:
            subprocess.run(["docker", "kill", self._container],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            if err != "disabled":   # already recorded once as disabled_after_restarts
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
                self._proc = None
            except subprocess.TimeoutExpired:
                self._kill()
        if self._cleanup_dir and os.path.isdir(self._cleanup_dir):
            shutil.rmtree(self._cleanup_dir, ignore_errors=True)
