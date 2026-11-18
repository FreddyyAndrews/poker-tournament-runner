"""Intake: turn the teams' submission links into tournament entries.

  poker intake submissions.txt            # on the server (Docker smoke matches)
  poker intake submissions.txt --local    # in this process, without Docker

submissions.txt has one team per line: a team name and where its code is,
which is anything `git clone` accepts (a GitHub URL, a local repo path, a git
bundle). Blank lines and lines starting with # are skipped:

  river_rats    https://github.com/someone/river-rats-bot
  tight_ship    https://github.com/someone-else/hackathon

For each team, intake:
  1. clones the repo now (this is the freeze: later pushes don't count) and
     records the commit and the time;
  2. finds the bot: my_bots/<team>/bot.py, or the only bot under my_bots/;
  3. copies just that folder (bot.py and data/) to entries/<team>/; nothing
     else from their repo, so changes to their copy of the kit don't matter;
  4. runs the validator's static checks (without importing the bot);
  5. plays a short smoke match against bots/template in the Docker sandbox,
     which proves the bot loads and plays under the real limits.

A team is accepted when every step passes. Entries are found by name like any
bot (entries/ comes first), so a qualifier can use them directly. The result
is saved as entries/intake.json and printed as a table.
"""

import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from arena import bots as botlib
from pokercore import structures
from pokercore.seats import BotSeat
from pokercore.seeds import derive_seed
from pokercore.table import MatchConfig, play_match
from pokercore.validator import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRIES_DIR = REPO_ROOT / "entries"
RESULT_FILE = "intake.json"
SMOKE_OPPONENT = "template"
SMOKE_HANDS = 30
TEAM_NAME = re.compile(r"^[a-z0-9][a-z0-9_]{1,39}$")


class _Log:
    def emit(self, event_type, **data):
        return {"type": event_type, **data}


@dataclass
class Submission:
    team: str
    source: str


def read_submissions(path) -> list:
    return read_submissions_text(Path(path).read_text())


def read_submissions_text(text: str) -> list:
    subs, seen = [], set()
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = re.split(r"[\s,]+", line, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"line {n}: expected 'team source', got {line!r}")
        team = parts[0].strip().lower().replace("-", "_")
        if not TEAM_NAME.match(team):
            raise ValueError(f"line {n}: team name {parts[0]!r} must be 2-40 letters, digits or _")
        if team in seen:
            raise ValueError(f"line {n}: team {team!r} is listed twice")
        seen.add(team)
        subs.append(Submission(team, parts[1].strip()))
    return subs


def _find_bot(repo: Path, team: str) -> Path:
    preferred = repo / "my_bots" / team / "bot.py"
    if preferred.is_file():
        return preferred.parent
    found = sorted(p.parent for p in (repo / "my_bots").glob("*/bot.py")) if (repo / "my_bots").is_dir() else []
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError("no bot found: expected my_bots/<team>/bot.py")
    names = ", ".join(p.name for p in found)
    raise ValueError(f"several bots under my_bots/ ({names}) and none named {team}")


def _copy_bot(src: Path, dest: Path):
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    shutil.copy2(src / "bot.py", dest / "bot.py")
    if (src / "data").is_dir():
        shutil.copytree(src / "data", dest / "data", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _smoke(team: str, path: Path, use_docker: bool) -> dict:
    opponent = botlib.resolve_all([SMOKE_OPPONENT])[0]
    seed = derive_seed("intake", team)
    config = MatchConfig(mode="fixed", hands=SMOKE_HANDS, structure=structures.flat(50, 100), seed=seed)
    me = botlib.BotSpec(team, str(path), botlib.bot_hash(str(path)), str(path))
    seats = [BotSeat(me, seed=derive_seed(seed, "bot", team), use_docker=use_docker),
             BotSeat(opponent, seed=derive_seed(seed, "bot", opponent.name), use_docker=use_docker)]
    result = play_match(seats, config, f"intake_{team}", _Log())
    mine = next(r for r in result["results"] if r["bot"] == team)
    summary = seats[0].summary()
    problems = list(dict.fromkeys(summary.get("errors_list") or []))
    return {"ok": not problems and not mine.get("disabled"), "hands": result["hands"],
            "decisions": mine.get("decisions"), "avg_think_ms": mine.get("avg_think_ms"),
            "max_think_ms": mine.get("max_think_ms"), "timeouts": mine.get("timeouts"),
            "errors": mine.get("errors"), "problems": problems[:10],
            "stderr_tail": (summary.get("stderr_tail") or [])[-15:]}


def intake_one(sub: Submission, entries_dir: Path, use_docker: bool) -> dict:
    rec = {"team": sub.team, "source": sub.source, "status": "failed", "problem": None}
    repo = entries_dir / "_repos" / sub.team
    try:
        shutil.rmtree(repo, ignore_errors=True)
        repo.parent.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(["git", "clone", "--depth", "1", "--quiet", sub.source, str(repo)],
                               capture_output=True, text=True, timeout=300)
        rec["cloned_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
        if clone.returncode != 0:
            rec["problem"] = "clone failed: " + (clone.stderr.strip().splitlines() or ["?"])[-1]
            return rec
        rec["commit"] = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                       capture_output=True, text=True).stdout.strip()
        src = _find_bot(repo, sub.team)
        rec["found_at"] = str(src.relative_to(repo))
        dest = entries_dir / sub.team
        _copy_bot(src, dest)
        rec["bot_hash"] = botlib.bot_hash(str(dest))

        v = validate(str(dest), static_only=True)
        rec["validator"] = {"errors": v["errors"], "warnings": v["warnings"]}
        if v["errors"]:
            rec["problem"] = "validator: " + v["errors"][0]
            return rec

        rec["smoke"] = smoke = _smoke(sub.team, dest, use_docker)
        for cache in dest.rglob("__pycache__"):          # left by a smoke match without Docker
            shutil.rmtree(cache, ignore_errors=True)
        if not smoke["ok"]:
            rec["problem"] = "smoke match: " + (smoke["problems"][0] if smoke["problems"] else "bot disabled")
            return rec
        rec["status"] = "accepted"
        return rec
    except Exception as e:                          # one bad submission never stops the rest
        rec["problem"] = rec["problem"] or f"{type(e).__name__}: {e}"
        return rec


def run_intake(subs: list, entries_dir: Path = ENTRIES_DIR, use_docker: bool = True,
               workers: int = 4, progress=None, only: list = None) -> dict:
    """Collect every submission, or with `only`, just those teams: the others
    keep their entries and records from the last intake (they stay frozen)."""
    entries_dir = Path(entries_dir)
    previous = latest(entries_dir) if only else None
    if only:
        unknown = set(only) - {s.team for s in subs}
        if unknown:
            raise ValueError(f"not in the submissions: {', '.join(sorted(unknown))}")
        if previous is None:
            raise ValueError("--only needs an earlier intake to keep the other teams from")
        all_subs, subs = subs, [s for s in subs if s.team in only]
    entries_dir.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%d %H:%M:%S %z")
    done = []

    def one(sub):
        rec = intake_one(sub, entries_dir, use_docker)
        done.append(rec)
        if progress:
            progress({"event": "team_done", "team": sub.team, "status": rec["status"],
                      "done": len(done), "total": len(subs)})
        return rec

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        records = list(pool.map(one, subs))
    if only:
        fresh = {r["team"]: r for r in records}
        before = {r["team"]: r for r in previous["teams"]}
        records = [fresh.get(s.team) or before.get(s.team)
                   or {"team": s.team, "source": s.source, "status": "failed", "problem": "not collected yet"}
                   for s in all_subs]
        subs = all_subs
    # Entries from an earlier intake that aren't in this one are removed, so the
    # entries folder is exactly this submission list.
    keep = {s.team for s in subs} | {"_repos"}
    for p in entries_dir.iterdir():
        if p.is_dir() and p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)
    for r in records:
        if r["status"] != "accepted":
            shutil.rmtree(entries_dir / r["team"], ignore_errors=True)
    if only:
        started = previous["started"] + f" (re-collected {', '.join(only)} at {started})"
    result = {"started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S %z"),
              "docker": use_docker, "teams": records,
              "accepted": [r["team"] for r in records if r["status"] == "accepted"]}
    (entries_dir / RESULT_FILE).write_text(json.dumps(result, indent=2) + "\n")
    return result


def latest(entries_dir: Path = ENTRIES_DIR):
    p = Path(entries_dir) / RESULT_FILE
    return json.loads(p.read_text()) if p.exists() else None


def table(result: dict) -> str:
    rows = [f"{'team':<18} {'status':<9} {'commit':<9} {'think avg/max ms':<17} problem"]
    for r in result["teams"]:
        s = r.get("smoke") or {}
        think = f"{s.get('avg_think_ms') or 0:.1f}/{s.get('max_think_ms') or 0:.1f}" if s else "-"
        rows.append(f"{r['team']:<18} {r['status']:<9} {(r.get('commit') or '-')[:7]:<9} {think:<17} "
                    f"{r.get('problem') or ''}")
    rows.append(f"\n{len(result['accepted'])} of {len(result['teams'])} accepted"
                + ("" if result["docker"] else "  (smoke matches ran WITHOUT Docker)"))
    return "\n".join(rows)
