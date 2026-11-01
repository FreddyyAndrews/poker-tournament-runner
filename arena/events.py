"""Match event logs: one JSON object per line, appended as the match is played
(so a running match can be followed live), plus a summary.json at the end.

While a match plays, its log is plain text (events.jsonl). When it ends, the
log is gzipped to events.jsonl.gz (about 20x smaller: most of a log is the
state each bot saw, repeated decision after decision) and the plain file is
removed. Nothing is lost: always open logs through open_events() /
read_events() / has_events(), which read either form. `poker compress`
compresses logs written before this. POKER_COMPRESS_LOGS=0 keeps them plain.

Event types (all carry "type"; per-player data is keyed by bot name):
  match_start  match_id, seed, config, seats[], engine_hash, arena_version
  level        level, sb, bb, ante, hand            (whenever blinds change)
  hand_start   hand, hand_seed, level, blinds, button, sb, bb,
               order[] (bot names by engine seat), seats{name: table seat},
               stacks{name}, hole{name: [c, c]}
  post         hand, bot, seat, kind (ante|sb|bb), amount
  decision     hand, bot, seat, street, state (what the bot saw),
               reply{action, amount}  (raw, as the bot returned it),
               applied{action, amount, pot_after, stack_after}  (after rules),
               think_ms, logs, logs_truncated, debug, debug_error, error, error_detail
  deal         hand, street, board[]
  showdown     hand, board[], reveal{name: [c, c]}, ranks{name}
  award        hand, bot, amount, pot (main|side)
  hand_end     hand, board[], pot, stacks{name}, deltas{name}
  elimination  hand, bot, place
  match_end    hands, duration_s, results[], stopped (reason)
"""

import gzip
import hashlib
import json
import os
import time
from pathlib import Path

DEFAULT_RUNS_DIR = Path(os.environ.get("POKER_RUNS_DIR",
                                       Path(__file__).resolve().parent.parent / "data" / "runs"))

EVENTS_FILE = "events.jsonl"
EVENTS_GZ = EVENTS_FILE + ".gz"
SUMMARY_FILE = "summary.json"
COMPRESS_LEVEL = 6
# Logs left plain this long with no summary and no match_end are treated as
# abandoned (a crash, a killed process) by `poker compress`, not as live.
STALE_S = 24 * 3600


def compress_on_close() -> bool:
    return os.environ.get("POKER_COMPRESS_LOGS", "1") != "0"


class EventLog:
    """Writes events for one match. Use as a context manager."""

    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / EVENTS_FILE
        self._fh = open(self.path, "w", encoding="utf-8")
        self.count = 0
        self.listeners = []   # callables(event) for live viewers

    def emit(self, event_type: str, **data):
        event = {"type": event_type, **data}
        self._fh.write(json.dumps(event, default=str) + "\n")
        self._fh.flush()
        self.count += 1
        for fn in self.listeners:
            fn(event)
        return event

    def write_summary(self, summary: dict):
        tmp = self.dir / (SUMMARY_FILE + ".tmp")
        tmp.write_text(json.dumps(summary, indent=2, default=str))
        tmp.replace(self.dir / SUMMARY_FILE)

    def close(self):
        if not self._fh.closed:
            self._fh.close()
            if compress_on_close():
                try:
                    compress_events(self.dir)
                except OSError:
                    pass                # a plain log is still a good log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# -- reading logs, plain or compressed -------------------------------------------

def has_events(run_dir: Path) -> bool:
    """Whether a match directory has a log, in either form."""
    d = Path(run_dir)
    return (d / EVENTS_FILE).is_file() or (d / EVENTS_GZ).is_file()


def is_compressed(run_dir: Path) -> bool:
    return (Path(run_dir) / EVENTS_GZ).is_file()


def open_events(run_dir: Path):
    """The match's log as a text file of lines. Prefers the compressed log
    (complete once it exists), else the plain one; safe against the plain
    log being compressed between the check and the open."""
    d = Path(run_dir)
    for _ in range(2):
        try:
            return gzip.open(d / EVENTS_GZ, "rt", encoding="utf-8")
        except FileNotFoundError:
            pass
        try:
            return open(d / EVENTS_FILE, encoding="utf-8")
        except FileNotFoundError:
            pass                        # compressed just now: look again
    raise FileNotFoundError(f"no match log in {d}")


def iter_lines(run_dir: Path, start: int = 0):
    """Complete lines of a log (without the newline), from line `start`."""
    with open_events(run_dir) as fh:
        for i, line in enumerate(fh):
            if not line.endswith("\n"):
                break                   # a partial line still being written
            if i >= start and line.strip():
                yield line[:-1]


def read_events(run_dir: Path) -> list:
    return [json.loads(line) for line in iter_lines(run_dir)]


def last_event(run_dir: Path):
    """The last complete event in a log, or None. Reads only the tail of a
    plain log; a compressed one is read through."""
    d = Path(run_dir)
    plain = d / EVENTS_FILE
    data = b""
    if not (d / EVENTS_GZ).is_file():
        try:
            with open(plain, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 65536))
                data = fh.read()
        except FileNotFoundError:
            pass
    if not data and (d / EVENTS_GZ).is_file():
        tail = b""
        with gzip.open(d / EVENTS_GZ, "rb") as fh:
            while chunk := fh.read(1 << 20):
                tail = (tail + chunk)[-65536:]
        data = tail
    lines = [l for l in data.split(b"\n") if l.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


def find_run(ref: str, runs_dir: Path = DEFAULT_RUNS_DIR) -> Path:
    """A run directory from a path, an events.jsonl(.gz) path, or a match id."""
    p = Path(ref).expanduser()
    if p.is_file() and p.name in (EVENTS_FILE, EVENTS_GZ):
        return p.parent
    if p.is_dir() and has_events(p):
        return p
    candidate = Path(runs_dir) / ref
    if has_events(candidate):
        return candidate
    raise FileNotFoundError(f"No match log found for {ref!r} (looked for a run directory "
                            f"or {candidate})")


# -- compressing logs ---------------------------------------------------------

def _sha256(fh) -> str:
    h = hashlib.sha256()
    while chunk := fh.read(1 << 20):
        h.update(chunk)
    return h.hexdigest()


def compress_events(run_dir: Path) -> bool:
    """Gzip a finished match's plain log to events.jsonl.gz, check that it
    decompresses to the same bytes, then remove the plain log. Keeps the
    file's and the directory's modification times (the UI sorts by them).
    Returns False if there was no plain log."""
    d = Path(run_dir)
    plain, gz, tmp = d / EVENTS_FILE, d / EVENTS_GZ, d / (EVENTS_GZ + ".tmp")
    try:
        st, dir_st = plain.stat(), d.stat()
    except FileNotFoundError:
        return False
    try:
        with open(plain, "rb") as src, open(tmp, "wb") as raw:
            with gzip.GzipFile(filename=EVENTS_FILE, mode="wb", fileobj=raw,
                               compresslevel=COMPRESS_LEVEL, mtime=0) as dst:
                h = hashlib.sha256()
                while chunk := src.read(1 << 20):
                    h.update(chunk)
                    dst.write(chunk)
        with gzip.open(tmp, "rb") as check:
            if _sha256(check) != h.hexdigest():
                raise OSError(f"compressed log in {d} doesn't match the original")
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, gz)             # complete before the plain log goes
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    plain.unlink()
    os.utime(d, ns=(dir_st.st_atime_ns, dir_st.st_mtime_ns))
    return True


def finished(run_dir: Path, now: float = None) -> bool:
    """Whether a plain log is finished, so safe to compress: the match wrote
    its summary, or the log ends with match_end, or nothing has touched it
    for STALE_S (abandoned)."""
    d = Path(run_dir)
    if (d / SUMMARY_FILE).is_file():
        return True
    last = last_event(d)
    if last and last.get("type") == "match_end":
        return True
    try:
        age = (now or time.time()) - (d / EVENTS_FILE).stat().st_mtime
    except FileNotFoundError:
        return False
    return age > STALE_S


def compress_all(roots, dry_run: bool = False, progress=None) -> dict:
    """Compress every finished plain log under `roots`. Returns counts and
    bytes before and after; logs that look live are skipped."""
    out = {"compressed": 0, "skipped_live": 0, "failed": 0, "before": 0, "after": 0, "errors": []}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for plain in sorted(root.rglob(EVENTS_FILE)):
            d = plain.parent
            if not finished(d):
                out["skipped_live"] += 1
                continue
            size = plain.stat().st_size
            if dry_run:
                out["compressed"] += 1
                out["before"] += size
                continue
            try:
                compress_events(d)
            except OSError as e:
                out["failed"] += 1
                out["errors"].append(f"{d}: {e}")
                continue
            out["compressed"] += 1
            out["before"] += size
            out["after"] += (d / EVENTS_GZ).stat().st_size
            if progress:
                progress(out)
    return out


def read_summary(run_dir: Path):
    p = Path(run_dir) / SUMMARY_FILE
    return json.loads(p.read_text()) if p.is_file() else None
