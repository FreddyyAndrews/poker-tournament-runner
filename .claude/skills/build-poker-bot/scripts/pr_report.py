#!/usr/bin/env python3
"""Metrics section for a bot's pull request, as Markdown on stdout.

    pr_report.py <bot> [--run RUN_ID ...] [--match MATCH_KEY ...]

- Bench: the latest `poker bench` result per opponent (bb/100, 95% CI) and the
  version history (one row per code hash) from data/bench/<bot>.jsonl.
- --run: standings (and head to head) for each run or series the bot played,
  e.g. a round robin or a bracket series against other agents' bots.
- --match: per-match results with think time, timeouts and errors.
Run it with the main worktree's python ($PY) so it reads the shared data/ dir.
"""
import argparse
import json
import os
import urllib.request
from pathlib import Path

from arena.bench import BENCH_DIR, load_history
from arena.bots import REPO_ROOT
from arena.cli import _load_result



def runs_dir():
    """The central server's runs dir (where `poker match` / `poker run` wrote), else data/runs."""
    try:
        url = os.environ.get("POKER_SERVER", "http://127.0.0.1:8765").rstrip("/") + "/api/health"
        return Path(json.load(urllib.request.urlopen(url, timeout=3))["runs_dir"])
    except Exception:
        return REPO_ROOT / "data" / "runs"


RUNS = runs_dir()


def ci(v, c):
    return f"{v:+.1f} ± {c:.1f}"


def bench_section(bot):
    hist = load_history(bot, BENCH_DIR)
    if not hist:
        return [f"_No bench history for {bot}: run `$POKER bench my_bots/{bot} --workers 4`._", ""]
    last = hist[-1]
    out = [f"### Bench (duplicate heads-up, {last['hands_per_leg']} hands per seat, seed {last['seed']})", "",
           f"Code hash `{last['hash']}`, overall **{ci(last['overall_bb_per_100'], last['overall_ci95'])} bb/100**.", "",
           "| Opponent | Hands | bb/100 (95% CI) | Timeouts | Errors |", "|---|---:|---:|---:|---:|"]
    for o in last["opponents"]:
        out.append(f"| {o['opponent']} | {o['hands']} | {ci(o['bb_per_100'], o['ci95'])} | {o['timeouts']} | {o['errors']} |")
    out += ["", "### Version history", "", "| When | Hash | Overall bb/100 (95% CI) |", "|---|---|---:|"]
    seen = {}
    for h in hist:                      # newest result per code hash, in order of first appearance
        seen[h["hash"]] = h
    for h in seen.values():
        out.append(f"| {h['time']} | `{h['hash']}` | {ci(h['overall_bb_per_100'], h['overall_ci95'])} |")
    return out + [""]


def run_section(bot, run_id):
    try:
        meta = _load_result(str(RUNS / run_id))
    except FileNotFoundError:
        return [f"_No run {run_id} in {RUNS}._", ""]
    cfg = meta.get("config", {})
    out = [f"### {meta.get('format')} {meta.get('kind')} `{run_id}` ({cfg.get('mode', '')}, "
           f"{cfg.get('hands', '')} hands per match, seed {meta.get('seed')}, status {meta.get('status')})", ""]
    if meta.get("kind") == "series":
        out += [f"{len(meta.get('run_ids', []))} runs.", "",
                "| Bot | Win rate (95% CI) | Avg place | bb/100 (95% CI) |", "|---|---:|---:|---:|"]
        for r in meta.get("aggregate", []):
            name = f"**{r['bot']}**" if r["bot"] == bot else r["bot"]
            lo, hi = r["win_rate_ci"]
            out.append(f"| {name} | {100 * r['win_rate']:.0f}% ({100 * lo:.0f}-{100 * hi:.0f}%) | "
                       f"{r['avg_place']:.2f} | {ci(r['bb_per_100'], r['bb_per_100_ci'])} |")
        return out + [""]
    st = meta.get("standings") or []
    if st:
        out += ["| Place | Bot | Points | W-D-L | bb/100 | Timeouts | Errors |", "|---:|---|---:|---|---:|---:|---:|"]
        for r in st:
            name = f"**{r['bot']}**" if r["bot"] == bot else r["bot"]
            wdl = "-".join(str(r.get(k, "")) for k in ("wins", "draws", "losses") if k in r)
            out.append(f"| {r.get('place', '')} | {name} | {r.get('points', '')} | {wdl} | "
                       f"{r.get('bb_per_100', 0):+.1f} | {r.get('timeouts', 0)} | {r.get('errors', 0)} |")
    h2h = (meta.get("head_to_head") or {}).get(bot)
    if h2h:
        out += ["", f"Head to head for {bot}: " + ", ".join(f"{o} {v['result']} ({v['delta']:+,})" for o, v in h2h.items())]
    return out + [""]


def match_section(bot, keys):
    out = ["### Matches", "", "| Match | Place | Chips | bb/100 | Hands | Avg / max think (ms) | Timeouts | Errors |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k in keys:
        f = next(RUNS.glob(f"**/{k}/summary.json"), None) if not (RUNS / k / "summary.json").is_file() else RUNS / k / "summary.json"
        if not f:
            out.append(f"| {k} | not found | | | | | | |")
            continue
        r = next((x for x in json.loads(f.read_text())["results"] if x["bot"] == bot), None)
        if r:
            out.append(f"| {k} | {r['place']} | {r['delta']:+,} | {r['bb_per_100']:+.1f} | {r['hands_played']} | "
                       f"{r['avg_think_ms']:.2f} / {r['max_think_ms']:.2f} | {r['timeouts']} | {r['errors']} |")
    return out + [""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot")
    ap.add_argument("--run", action="append", default=[])
    ap.add_argument("--match", action="append", default=[])
    a = ap.parse_args()
    out = ["## Metrics", ""] + bench_section(a.bot)
    for r in a.run:
        out += run_section(a.bot, r)
    if a.match:
        out += match_section(a.bot, a.match)
    print("\n".join(out))


if __name__ == "__main__":
    main()
