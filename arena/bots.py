"""Finding bots: a CLI/config bot spec becomes a BotSpec with a name, the path
the sandbox loads, and a content hash identifying this version of the bot.

A spec can be:
  - a name in my_bots/ or bots/   e.g. "shark", "my_first_bot"
  - a path to bot.py, a directory containing bot.py, or a bot.zip
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIRS = [REPO_ROOT / "my_bots", REPO_ROOT / "bots"]

_NAME_OK = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class BotSpec:
    name: str        # unique within a match, used as bot_id everywhere
    path: str        # .py, directory, or .zip the sandbox loads
    hash: str        # sha256 of the bot's files (first 16 hex chars)
    source: str      # what the user asked for

    def to_dict(self):
        return {"name": self.name, "path": self.path, "hash": self.hash,
                "source": self.source}


def bot_hash(path: str) -> str:
    """Hash of everything the bot loads: bot.py, or a zip, or a whole dir."""
    p = Path(path)
    h = hashlib.sha256()
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                h.update(str(f.relative_to(p)).encode())
                h.update(f.read_bytes())
    else:
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _default_name(p: Path) -> str:
    if p.is_dir():
        name = p.name
    elif p.stem in ("bot",):
        name = p.parent.name
    else:
        name = p.stem
    return _NAME_OK.sub("_", name) or "bot"


def resolve(spec: str) -> tuple:
    """Return (path, default_name) for a spec, or raise ValueError."""
    p = Path(spec).expanduser()
    if p.exists():
        if p.is_dir() and not (p / "bot.py").is_file():
            raise ValueError(f"{spec}: directory has no bot.py")
        if p.is_file() and p.suffix not in (".py", ".zip"):
            raise ValueError(f"{spec}: expected a .py file, .zip, or directory")
        return str(p.resolve()), _default_name(p)
    if os.sep not in spec and "/" not in spec:
        for d in SEARCH_DIRS:
            if (d / spec / "bot.py").is_file():          # folder: bot.py + optional data/
                return str((d / spec).resolve()), spec
            for candidate in (d / spec / "bot.zip", d / (spec + ".py")):
                if candidate.is_file():
                    return str(candidate.resolve()), spec
    searched = ", ".join(str(d.relative_to(REPO_ROOT)) + "/" for d in SEARCH_DIRS)
    raise ValueError(f"Bot {spec!r} not found (not a path, and not a bot in {searched})")


def resolve_all(specs, names=None) -> list:
    """Resolve several specs, giving each a unique name (shark, shark_2, ...).
    `names` optionally overrides the default name for each spec."""
    out, used = [], set()
    for i, spec in enumerate(specs):
        path, default = resolve(spec)
        base = (names[i] if names and i < len(names) and names[i] else default)
        name, n = base, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        out.append(BotSpec(name=name, path=path, hash=bot_hash(path), source=spec))
    return out


def available() -> list:
    """Every bot found in my_bots/ and bots/, as (name, path, folder)."""
    found = []
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            for candidate in (sub / "bot.py", sub / "bot.zip"):
                if sub.is_dir() and candidate.is_file():
                    found.append((sub.name, str(candidate), d.name))
                    break
    return found
