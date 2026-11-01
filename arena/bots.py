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
# champions/: frozen copies of the strongest bots (arena/champions.py)
SEARCH_DIRS = [REPO_ROOT / "my_bots", REPO_ROOT / "bots", REPO_ROOT / "champions"]

_worktree_cache = {"at": 0.0, "dirs": []}


def worktree_bot_dirs() -> list:
    """my_bots/ folders of this repo's *other* git worktrees, as (path, branch).

    Agents build bots in their own worktrees; the central server (run from the
    main worktree) finds them here so everyone can play everyone. Cached for a
    few seconds; empty if git isn't available."""
    import subprocess
    import time
    if time.time() - _worktree_cache["at"] < 5:
        return _worktree_cache["dirs"]
    dirs = []
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=5).stdout
        path = branch = None
        for line in out.splitlines() + [""]:
            if line.startswith("worktree "):
                path = Path(line[9:])
            elif line.startswith("branch "):
                branch = line[7:].removeprefix("refs/heads/")
            elif not line and path is not None:
                if path.resolve() != REPO_ROOT and (path / "my_bots").is_dir():
                    dirs.append((path / "my_bots", branch or path.name))
                path = branch = None
    except (OSError, subprocess.SubprocessError):
        pass
    _worktree_cache.update(at=time.time(), dirs=dirs)
    return dirs

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


NOTES_DIR = "notes"     # a bot folder's notes/: the agent's journal, not part of the bot


def bot_hash(path: str) -> str:
    """Hash of everything the bot loads: bot.py, or a zip, or a whole dir.
    A folder's top-level notes/ (the iteration journal, plan and status that
    agents keep, see .claude/skills/iterate-bot) is left out, so writing
    notes doesn't make a new version of the bot."""
    p = Path(path)
    h = hashlib.sha256()
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            rel = f.relative_to(p)
            if f.is_file() and "__pycache__" not in f.parts and rel.parts[0] != NOTES_DIR:
                h.update(str(rel).encode())
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
        # this worktree's my_bots/ and bots/ first, then other worktrees' my_bots/
        for d in SEARCH_DIRS + [d for d, _ in worktree_bot_dirs()]:
            if (d / spec / "bot.py").is_file():          # folder: bot.py + optional data/
                return str((d / spec).resolve()), spec
            for candidate in (d / spec / "bot.zip", d / (spec + ".py")):
                if candidate.is_file():
                    return str(candidate.resolve()), spec
    searched = ", ".join(str(d.relative_to(REPO_ROOT)) + "/" for d in SEARCH_DIRS)
    raise ValueError(f"Bot {spec!r} not found (not a path, and not a bot in {searched} "
                     "or another worktree's my_bots/)")


REMOTE_KINDS = ("human", "mcp")


@dataclass(frozen=True)
class RemoteSpec:
    """A seat played by a person or an LLM instead of a bot: "human:Name" / "mcp:Name"."""
    name: str
    kind: str        # human | mcp
    source: str

    def to_dict(self):
        return {"name": self.name, "kind": self.kind, "source": self.source}


def remote_kind(spec: str):
    """("human"|"mcp", name) for "human:Name" / "mcp:Name" specs, else None."""
    if isinstance(spec, str) and ":" in spec:
        kind, _, rest = spec.partition(":")
        if kind in REMOTE_KINDS:
            name = _NAME_OK.sub("_", rest.strip()) or ("You" if kind == "human" else "LLM")
            return kind, name
    return None


def resolve_all(specs, names=None, allow_remote=False) -> list:
    """Resolve several specs, giving each a unique name (shark, shark_2, ...).
    `names` optionally overrides the default name for each spec.
    With allow_remote, "human:Name" / "mcp:Name" become RemoteSpecs."""
    out, used = [], set()
    for i, spec in enumerate(specs):
        remote = remote_kind(spec)
        if remote and not allow_remote:
            raise ValueError(f"{spec!r}: human and MCP seats can only play single matches "
                             "(start them with POST /api/matches, the New page, or poker play)")
        if remote:
            path, default = None, remote[1]
        else:
            path, default = resolve(spec)
        base = (names[i] if names and i < len(names) and names[i] else default)
        name, n = base, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        if remote:
            out.append(RemoteSpec(name=name, kind=remote[0], source=spec))
        else:
            out.append(BotSpec(name=name, path=path, hash=bot_hash(path), source=spec))
    return out


def available() -> list:
    """Every bot found in my_bots/ and bots/, then in other worktrees' my_bots/
    (folder "worktree:<branch>"), as (name, path, folder). A name already found
    earlier is not listed again (it would resolve to the earlier one)."""
    found, seen = [], set()
    for d, folder in [(d, d.name) for d in SEARCH_DIRS] + \
                     [(d, f"worktree:{b}") for d, b in worktree_bot_dirs()]:
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            for candidate in (sub / "bot.py", sub / "bot.zip"):
                if sub.is_dir() and candidate.is_file() and sub.name not in seen:
                    found.append((sub.name, str(candidate), folder))
                    seen.add(sub.name)
                    break
    return found
