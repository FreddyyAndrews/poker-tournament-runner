"""Keeps bot code away from the rest of the machine when it runs locally
(the Docker sandbox isolates it on its own: no network, a read-only mount of
the bot folder only).

Locally a bot is an ordinary Python process, so without this it could read
any file the user can (the repo's secrets, other bots' code) or the
environment's API keys, and print them into match logs. Two parts:

  safe_env(environ)   the environment a bot process gets: a short allowlist,
                      and never a name that looks like a secret.
  install(bot_dir)    an audit hook (PEP 578), installed before the bot is
                      imported and impossible to remove afterwards, that
                      refuses:
                        - opening or listing files outside the bot's own
                          folder and the Python installation;
                        - writing files anywhere but __pycache__ folders;
                        - starting processes;
                        - network connections to anything but this machine
                          (jev bots reach the outside-bot proxy on 127.0.0.1).

It stops careless code and casual attempts, not a determined attacker:
audit hooks can be bypassed from native code, which is why the validator
also bans ctypes and similar modules. For untrusted code use --docker.
"""

import os
import re
import site
import sys
import sysconfig

SAFE_NAMES = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "POKER_PROXY_URL"}
SAFE_PREFIXES = ("BOT_", "JEV_", "PYTHON")
SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASS|CREDENTIAL|AUTH|COOKIE|SESSION", re.IGNORECASE)
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
PROCESS_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
                  "os.fork", "os.forkpty", "pty.spawn", "os.startfile"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def safe_env(environ) -> dict:
    """The variables a bot process may see."""
    return {k: v for k, v in environ.items()
            if (k in SAFE_NAMES or k.startswith(SAFE_PREFIXES)) and not SECRETISH.search(k)}


def python_roots() -> list:
    """Where the Python installation lives: the standard library and site-packages."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        p = sysconfig.get_paths().get(key)
        if p:
            roots.add(p)
    try:
        roots.update(site.getsitepackages())
    except AttributeError:
        pass
    return sorted({os.path.realpath(r) for r in roots if r and os.path.isdir(r)})


class Refused(PermissionError):
    pass


def _within(path: str, roots) -> bool:
    return any(path == r or path.startswith(r + os.sep) for r in roots)


def install(bot_dir: str):
    """Confine this process to its bot folder from now on (see the module docstring)."""
    bot = os.path.realpath(bot_dir)
    readable = [bot, "/dev"] + python_roots()

    def where(path) -> str:
        return os.path.realpath(os.path.abspath(os.fsdecode(path)))

    def hook(event, args):
        if event == "open":
            path, mode, flags = args
            if path is None or isinstance(path, int):
                return                                  # an already-open file descriptor
            p = where(path)
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                (isinstance(flags, int) and flags & WRITE_FLAGS)
            if writing:
                if p.startswith("/dev/") or ("__pycache__" in p.split(os.sep) and _within(p, readable)):
                    return
                raise Refused(f"bots may not write files ({p})")
            if not _within(p, readable):
                raise Refused(f"bots may only open files in their own folder ({p})")
        elif event in ("os.listdir", "os.scandir", "glob.glob"):
            target = args[0] if args else None
            p = where(target if target not in (None, "") else ".")
            if not _within(p, readable):
                raise Refused(f"bots may only list their own folder ({p})")
        elif event in PROCESS_EVENTS:
            raise Refused("bots may not start processes")
        elif event == "socket.connect":
            addr = args[1] if len(args) > 1 else None
            host = addr[0] if isinstance(addr, tuple) and addr else addr
            if isinstance(host, str) and host not in LOOPBACK:
                raise Refused(f"bots may only connect to this machine (not {host})")

    sys.addaudithook(hook)
