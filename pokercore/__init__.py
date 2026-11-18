"""pokercore: everything a bot experiences in a match.

  engine.py        the hand engine: dealing, betting rules, the state a bot is sent
  table.py         seats, button, blinds by hand count, match_action_log, matches
  structures.py    blind structures (turbo / standard / slow, or flat blinds)
  seeds.py         seed derivation, so a seed replays the same cards
  seats.py         a seat at the table; BotSeat runs a sandboxed bot
  botproc.py       the bot's process or Docker container, time limits, restarts
  validator.py     static and runtime checks a bot must pass
  sandbox/         what runs next to the bot: runner.py (protocol), guard.py
                   (local confinement) and the Dockerfile (libraries, limits)

The tournament runner is built on this package, and the hackathon starter kit
is generated from it unchanged (scripts/publish_kit.py), so a bot meets exactly
the same code in the kit as on tournament day. core_hash() identifies this
exact code; every match records it.
"""

import hashlib
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent


def core_files(root: Path = None) -> list:
    """Every file that makes up the core, as sorted paths relative to root."""
    root = Path(root) if root else CORE_DIR
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def core_hash(root: Path = None) -> str:
    """A short fingerprint of the core's code (file names and contents). With
    root, fingerprints a copy of the core somewhere else, e.g. in the kit."""
    root = Path(root) if root else CORE_DIR
    h = hashlib.sha256()
    for rel in core_files(root):
        h.update(rel.encode() + b"\0" + (root / rel).read_bytes() + b"\0")
    return h.hexdigest()[:16]
