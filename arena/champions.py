"""The champion gauntlet: frozen copies of the strongest bots, used as the
benchmark once a bot beats the reference bots in bots/ by a wide margin
(they stop telling good bots apart).

champions/<bot>_<hash6>/ is an exact copy of the bot's folder at the version
that earned its place, so it keeps the bot's code hash and links to that
version's bench history. champions/index.json records where each came from:
the source folder, its hash, the git commit, when it was added and why.
Champions resolve by name like any bot (`poker match my_bot fable_bot_01052b`)
and `poker bench my_bot --gauntlet` plays every champion.

A champion must pass the validator: bots that need the network (the jev bots)
can't be champions, so a gauntlet bench never spends outside credits.
Nothing may edit a champion (the git hooks skip champions/); replace it by
adding the new version and removing the old one.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from arena import bots as botlib

REPO_ROOT = botlib.REPO_ROOT
CHAMPIONS_DIR = REPO_ROOT / "champions"
INDEX_FILE = CHAMPIONS_DIR / "index.json"
VALIDATOR = REPO_ROOT / "sandbox" / "validator.py"


def load_index() -> list:
    try:
        return json.loads(INDEX_FILE.read_text())
    except (OSError, ValueError):
        return []


def _write_index(entries: list):
    CHAMPIONS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(entries, key=lambda e: e["name"]), indent=2) + "\n")
    tmp.replace(INDEX_FILE)


def names() -> list:
    """Every champion, by name (folders that exist and are in the index)."""
    return [e["name"] for e in load_index() if (CHAMPIONS_DIR / e["name"] / "bot.py").is_file()]


def validate(bot_dir: Path) -> list:
    """The validator's errors for a bot folder (run in its own, confined process)."""
    out = subprocess.run([sys.executable, str(VALIDATOR), str(Path(bot_dir) / "bot.py"), "--json"],
                         capture_output=True, text=True, timeout=300)
    try:
        result = json.loads(out.stdout)
    except ValueError:
        return [f"validator crashed: {out.stderr.strip()[-300:]}"]
    return [] if result.get("passed") else result.get("errors") or ["failed"]


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except OSError:
        return ""


def add(spec: str, note: str = "") -> dict:
    """Freeze a bot (a name or folder) as a champion. Returns its index entry."""
    (bot,) = botlib.resolve_all([spec])
    src = Path(bot.path)
    if not src.is_dir():
        raise ValueError(f"{spec}: a champion must be a bot folder (bot.py and optional data/)")
    if Path(bot.path).resolve().parent == CHAMPIONS_DIR.resolve():
        raise ValueError(f"{spec} is already a champion")
    name = f"{bot.name}_{bot.hash[:6]}"
    entries = load_index()
    if any(e["name"] == name for e in entries):
        raise ValueError(f"{name} is already a champion (this exact version of {bot.name})")
    errors = validate(src)
    if errors:
        raise ValueError(f"{spec} fails the validator, so it can't be a champion (champions must run "
                         f"in the sandbox, with no network): {errors[0]}")
    dest = CHAMPIONS_DIR / name
    def skip(folder, names):
        """Not part of the bot: compiled files anywhere, and the top-level notes/."""
        top = Path(folder) == src
        return [n for n in names if n == "__pycache__" or n.endswith(".pyc") or (top and n == botlib.NOTES_DIR)]

    shutil.copytree(src, dest, ignore=skip)
    copied = botlib.bot_hash(str(dest))
    if copied != bot.hash:
        shutil.rmtree(dest)
        raise ValueError(f"the copy of {spec} hashes to {copied}, not {bot.hash}: "
                         "it holds files that aren't part of the bot (e.g. __pycache__)")
    entry = {"name": name, "bot": bot.name, "hash": bot.hash, "source": str(src.relative_to(REPO_ROOT))
             if src.is_relative_to(REPO_ROOT) else str(src), "commit": _commit(),
             "added": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note}
    _write_index(entries + [entry])
    return entry


def remove(name: str) -> dict:
    entries = load_index()
    entry = next((e for e in entries if e["name"] == name), None)
    if entry is None:
        raise ValueError(f"no champion named {name!r} (see `poker champions list`)")
    shutil.rmtree(CHAMPIONS_DIR / name, ignore_errors=True)
    _write_index([e for e in entries if e["name"] != name])
    return entry


def opponents_for(bot_hash: str) -> list:
    """The gauntlet for a bot: every champion except an identical copy of it."""
    return [e["name"] for e in load_index()
            if e["hash"] != bot_hash and (CHAMPIONS_DIR / e["name"] / "bot.py").is_file()]
