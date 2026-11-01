"""The bot loop's checks and status page. Every bot run calls this before
its experiment (to decide whether to run at all) and after it (to report).

  loop_status.py [--usage FILE] [--publish] [--by NAME] [--reserve 0.2]
                 [--stale-hours 6] [--json]

What it does:
  - services: whether the central server and the outside-bot proxy answer;
  - tracks: every worktree ../poker-bot-<name> whose bot has notes
    (my_bots/<name>/notes/), with its phase, best result, last run and
    recent journal entries;
  - budgets: Slumbot hands and Jev spend from the proxy, the disk use of
    data/ and free space, and the Claude plan usage in --usage (the JSON of
    the app's get_usage tool, saved by the run);
  - pacing: spreads the weekly Claude limit over the week, keeping
    --reserve of it for the user's own work, and writes the verdict to
    data/loop/pace.json ("run": true or false, and why). A run that finds
    "run": false records a skip and stops;
  - the status page: STATUS.md, everything above for a person checking in
    from GitHub. With --publish it is committed to the `loop-status` branch
    (a branch holding only that file, in its own worktree
    ../poker-loop-status) and pushed, so it reads at
    https://github.com/<owner>/<repo>/blob/loop-status/STATUS.md;
  - alerts: printed as "ALERT: ..." lines and listed at the top of the page.
"""

import argparse
import calendar
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MAIN = Path(__file__).resolve().parents[4]
LOOP_DIR = MAIN / "data" / "loop"
STATUS_BRANCH = "loop-status"
STATUS_WORKTREE = MAIN.parent / "poker-loop-status"
SERVER = os.environ.get("POKER_SERVER", "http://127.0.0.1:8765")
PROXY = os.environ.get("POKER_PROXY_URL", "http://127.0.0.1:8766")
WEEK_S = 7 * 86400
DATA_BUDGET_GB = 20
MIN_FREE_GB = 20
RECENT_RUNS = 12


def get_json(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def git(*args, cwd=MAIN, check=False) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip() if out.returncode == 0 else ""


def repo_url() -> str:
    """https://github.com/<owner>/<repo> from the origin remote, or ""."""
    url = git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return f"https://github.com/{m.group(1)}" if m else ""


def journal_entries(path: Path) -> list:
    """(time, result, experiment) for each journal heading, oldest first."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        m = re.match(r"## (\S+) (\w+): (.*)", line)
        if m:
            out.append(m.groups())
    return out


def tracks() -> list:
    """Every worktree's bot that has notes, with its status and git facts."""
    out, current = [], {}
    for line in git("worktree", "list", "--porcelain").splitlines() + [""]:
        if line.startswith("worktree "):
            current = {"path": Path(line[9:])}
        elif line.startswith("branch "):
            current["branch"] = line[7:].replace("refs/heads/", "")
        elif not line and current:
            wt, current = current, {}
            name = wt["path"].name.removeprefix("poker-bot-")
            notes = wt["path"] / "my_bots" / name / "notes"
            if not wt["path"].name.startswith("poker-bot-") or not (notes / "status.json").is_file():
                continue
            try:
                st = json.loads((notes / "status.json").read_text())
            except ValueError:
                st = {"bot": name, "phase": "unreadable"}
            st.update(worktree=str(wt["path"]), branch=wt.get("branch"),
                      journal=journal_entries(notes / "journal.md"))
            out.append(st)
    return out


def dir_size_gb(path: Path) -> float:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total / 1e9


def pace(usage: dict, reserve: float, now: float) -> dict:
    """Whether iterations may run now, from the Claude plan usage."""
    windows = ((usage or {}).get("plan") or {}).get("windows") or []
    weekly = next((w for w in windows if w.get("label", "").lower().startswith("weekly")
                   and "all" in w.get("label", "").lower()), None)
    five = next((w for w in windows if "5-hour" in w.get("label", "")), None)
    out = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "reserve": reserve}
    if not weekly:
        return {**out, "run": True, "reason": "no Claude usage data, so running without pacing"}
    try:
        resets = calendar.timegm(time.strptime(weekly["resetsAt"][:19], "%Y-%m-%dT%H:%M:%S"))  # UTC
    except (KeyError, ValueError):
        return {**out, "run": True, "reason": "unreadable weekly reset time, so running without pacing"}
    elapsed = min(1.0, max(0.0, 1 - (resets - now) / WEEK_S))
    used = float(weekly.get("percentUsed") or 0)
    target = round(elapsed * (1 - reserve) * 100, 1)
    out.update(weekly_used=used, week_elapsed=round(elapsed * 100, 1), target=target)
    if five and float(five.get("percentUsed") or 0) >= 90:
        return {**out, "run": False, "five_hour_used": five["percentUsed"],
                "reason": f"the 5-hour window is {five['percentUsed']}% used; waiting for it to reset"}
    if used > target + 5:
        return {**out, "run": False,
                "reason": f"ahead of pace: {used:.0f}% of the weekly Claude limit used, {target:.0f}% planned "
                          f"by now (keeping {reserve:.0%} for the user)"}
    return {**out, "run": True, "reason": f"on pace: {used:.0f}% of the weekly Claude limit used, "
                                          f"{target:.0f}% planned by now"}


def collect(usage, reserve, stale_hours, now) -> dict:
    alerts = []
    server = get_json(SERVER + "/api/health")
    proxy = get_json(PROXY + "/status")
    if not server:
        alerts.append(f"The central server isn't answering at {SERVER}.")
    if not proxy:
        alerts.append(f"The outside-bot proxy isn't answering at {PROXY}.")
    pacing = pace(usage, reserve, now)
    if not pacing["run"]:
        alerts.append("Bot runs are paused: " + pacing["reason"] + ".")

    rows = []
    for st in tracks():
        last = st.get("last_run") or {}
        last_at = time.mktime(time.strptime(last["at"], "%Y-%m-%dT%H:%M:%S")) if last.get("at") else None
        idle_h = round((now - last_at) / 3600, 1) if last_at else None
        if st.get("phase") == "blocked":
            alerts.append(f"{st['bot']} is blocked: {st.get('blocked_reason')}.")
        elif st.get("phase") == "iterating" and pacing["run"] and idle_h is not None and idle_h > stale_hours:
            alerts.append(f"{st['bot']} hasn't recorded a run for {idle_h:.0f} hours.")
        if last.get("result") == "error":
            alerts.append(f"{st['bot']}'s last run ended in an error: {last.get('summary')}")
        rows.append({**{k: st.get(k) for k in ("bot", "track", "phase", "blocked_reason", "strategy",
                                                "trying_next", "best", "branch", "journal")},
                     "iterations": st.get("iterations", 0), "kept": st.get("kept", 0),
                     "reverted": st.get("reverted", 0), "last_run": last or None, "hours_since_run": idle_h})
    rows.sort(key=lambda r: -((r.get("best") or {}).get("gauntlet_bb") or -1e9))

    budgets = {}
    if proxy:
        s, j = proxy["upstreams"].get("slumbot", {}), proxy["upstreams"].get("jev", {})
        budgets["slumbot"] = {k: s.get(k) for k in ("hands_today", "daily_hands", "allowed", "why")}
        budgets["jev"] = {k: j.get(k) for k in ("spent_today_usd", "allowance_today_usd", "spent_month_usd",
                                                 "monthly_budget_usd", "bots_today_usd", "allowed", "why")}
        if s.get("daily_hands") and (s.get("hands_today") or 0) >= 0.9 * s["daily_hands"]:
            alerts.append(f"Slumbot: {s['hands_today']:,} of {s['daily_hands']:,} hands used today.")
        if j.get("monthly_budget_usd") and (j.get("spent_month_usd") or 0) >= 0.8 * j["monthly_budget_usd"]:
            alerts.append(f"Jev: ${j['spent_month_usd']:.2f} of the ${j['monthly_budget_usd']:.2f} monthly "
                          "budget is spent.")
    data_gb, free_gb = dir_size_gb(MAIN / "data"), shutil.disk_usage(MAIN).free / 1e9
    budgets["disk"] = {"data_gb": round(data_gb, 2), "free_gb": round(free_gb, 1)}
    if data_gb > DATA_BUDGET_GB:
        alerts.append(f"data/ holds {data_gb:.1f} GB, over the {DATA_BUDGET_GB} GB budget.")
    if free_gb < MIN_FREE_GB:
        alerts.append(f"Only {free_gb:.0f} GB of disk is left.")
    budgets["claude"] = [{"label": w.get("label"), "percent_used": w.get("percentUsed"),
                          "resets_in": w.get("resetsIn")}
                         for w in ((usage or {}).get("plan") or {}).get("windows") or []]

    idx = MAIN / "champions" / "index.json"
    champions = json.loads(idx.read_text()) if idx.is_file() else []
    return {"generated": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
            "services": {"server": bool(server), "proxy": bool(proxy)}, "pace": pacing,
            "tracks": rows, "champions": champions, "budgets": budgets, "alerts": alerts}


def fmt_score(bb, ci):
    return f"{bb:+.1f} ± {ci:.1f}" if bb is not None else "–"


def render(d: dict, by: str = None) -> str:
    """STATUS.md: the loop at a glance, for a person checking in."""
    url = repo_url()
    notes_url = (lambda bot, branch, f: f"{url}/blob/{branch}/my_bots/{bot}/notes/{f}") if url else None
    p = d["pace"]
    lines = ["# Poker bot loop", "",
             f"Updated {d['generated']}" + (f" by {by}'s run" if by else "") + ". "
             f"Bot runs are **{'running' if p['run'] else 'paused'}**: {p['reason']}. "
             f"The central server is {'up' if d['services']['server'] else '**down**'} and the outside-bot "
             f"proxy is {'up' if d['services']['proxy'] else '**down**'}.", "", "## Needs attention", ""]
    lines += [f"- {a}" for a in d["alerts"]] or ["Nothing: every service is up and every budget is within its limit."]

    lines += ["", "## Standings", "",
              "Each bot's best version, in big blinds per 100 hands with its 95% confidence interval: against the "
              "champion gauntlet, and luck-adjusted against Slumbot. Higher is better.", ""]
    if d["tracks"]:
        lines += ["| Bot | Track | Phase | Gauntlet | Slumbot | Runs | Kept | Last run |",
                  "|---|---|---|---|---|---:|---:|---|"]
        for r in d["tracks"]:
            b, last = r.get("best") or {}, r.get("last_run") or {}
            name = f"[{r['bot']}]({notes_url(r['bot'], r['branch'], 'journal.md')})" \
                if notes_url and r.get("branch") else r["bot"]
            ago = f"{r['hours_since_run']:.1f} h ago, {last.get('result')}" if r.get("hours_since_run") is not None \
                else "not yet"
            lines.append(f"| {name} | {r['track']} | {r['phase']} | {fmt_score(b.get('gauntlet_bb'), b.get('gauntlet_ci'))} "
                         f"| {fmt_score(b.get('slumbot_bb'), b.get('slumbot_ci'))} | {r['iterations']} | {r['kept']} "
                         f"| {ago} |")
    else:
        lines.append("No bot tracks yet. Start one with the run-loop skill's `start_track.sh`.")

    if d["tracks"]:
        lines += ["", "## What each bot is doing"]
        for r in d["tracks"]:
            last = r.get("last_run") or {}
            lines += ["", f"### {r['bot']}", "", f"**Strategy:** {r.get('strategy')}", ""]
            if last:
                lines += [f"**Last run** ({last.get('at', '').replace('T', ' ')[:16]}, {last.get('result')}): "
                          f"{last.get('experiment')}. {last.get('summary') or ''}".rstrip(), ""]
            if r.get("trying_next"):
                lines += [f"**Trying next:** {r['trying_next']}", ""]
            if r.get("blocked_reason"):
                lines += [f"**{r['phase'].capitalize()}:** {r['blocked_reason']}", ""]
            if notes_url and r.get("branch"):
                lines.append(f"Notes: [journal]({notes_url(r['bot'], r['branch'], 'journal.md')}), "
                             f"[plan]({notes_url(r['bot'], r['branch'], 'plan.md')}), "
                             f"[status]({notes_url(r['bot'], r['branch'], 'status.json')}).")

    runs = sorted(((t, r["bot"], res, exp) for r in d["tracks"] for t, res, exp in r.get("journal") or []),
                  reverse=True)[:RECENT_RUNS]
    if runs:
        lines += ["", "## Recent runs", "", "| When | Bot | Result | Experiment |", "|---|---|---|---|"]
        lines += [f"| {t.replace('T', ' ')[:16]} | {bot} | {res} | {exp.replace('|', '/')} |" for t, bot, res, exp in runs]

    b = d["budgets"]
    lines += ["", "## Budgets", "", "| Budget | Used | Limit | Note |", "|---|---:|---:|---|"]
    for w in b.get("claude", []):
        if w.get("percent_used") is not None:
            lines.append(f"| Claude: {w['label']} | {w['percent_used']}% | 100% | resets in {w.get('resets_in')} |")
    s, j = b.get("slumbot") or {}, b.get("jev") or {}
    if s.get("daily_hands"):
        lines.append(f"| Slumbot hands today | {s.get('hands_today') or 0:,} | {s['daily_hands']:,} | across every bot |")
    if j.get("allowance_today_usd") is not None:
        lines.append(f"| Jev today | ${j.get('spent_today_usd') or 0:.2f} | ${j['allowance_today_usd']:.2f} "
                     "| what's left of the month over the days left |")
        lines.append(f"| Jev this month | ${j.get('spent_month_usd') or 0:.2f} | ${j.get('monthly_budget_usd') or 0:.2f} "
                     "| monthly budget |")
    disk = b.get("disk") or {}
    lines.append(f"| Match data on disk | {disk.get('data_gb')} GB | {DATA_BUDGET_GB} GB | {disk.get('free_gb')} GB free |")

    lines += ["", "## Champions", "", "The frozen bots every bot is benched against.", "",
              "| Champion | From | Added | Why |", "|---|---|---|---|"]
    lines += [f"| {c['name']} | {c['bot']} | {c['added'][:10]} | {c.get('note') or ''} |" for c in d["champions"]]
    return "\n".join(lines) + "\n"


def publish(md: str, by: str = None) -> str:
    """Commit STATUS.md to the loop-status branch and push it. Returns what happened."""
    LOOP_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOOP_DIR / "publish.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)                 # two runs finishing at once take turns
        remote = bool(git("ls-remote", "--heads", "origin", STATUS_BRANCH))
        wt = STATUS_WORKTREE
        if not (wt / ".git").exists():
            if remote:
                git("fetch", "-q", "origin", STATUS_BRANCH, check=True)
                git("worktree", "add", "-q", "-B", STATUS_BRANCH, str(wt), f"origin/{STATUS_BRANCH}", check=True)
            else:
                git("worktree", "add", "-q", "--orphan", "-b", STATUS_BRANCH, str(wt), check=True)
        elif remote:
            git("fetch", "-q", "origin", STATUS_BRANCH, cwd=wt, check=True)
            git("reset", "-q", "--hard", f"origin/{STATUS_BRANCH}", cwd=wt, check=True)
        f = wt / "STATUS.md"
        if f.is_file() and f.read_text() == md:
            return "unchanged"
        f.write_text(md)
        git("add", "STATUS.md", cwd=wt, check=True)
        git("commit", "-q", "-m", f"Loop status {time.strftime('%Y-%m-%d %H:%M')}" + (f" ({by})" if by else ""),
            cwd=wt, check=True)
        git("push", "-q", "origin", STATUS_BRANCH, cwd=wt, check=True)
        return "pushed"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--usage", help="JSON of the app's get_usage")
    p.add_argument("--reserve", type=float, default=0.2, help="share of the weekly limit kept for the user")
    p.add_argument("--stale-hours", type=float, default=6)
    p.add_argument("--publish", action="store_true", help="commit STATUS.md to the loop-status branch and push it")
    p.add_argument("--by", help="the bot whose run is calling, for the page")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    now = time.time()
    LOOP_DIR.mkdir(parents=True, exist_ok=True)
    usage = None
    if a.usage:
        try:
            usage = json.loads(Path(a.usage).read_text())
        except (OSError, ValueError):
            print(f"note: couldn't read Claude usage from {a.usage}; running without pacing", file=sys.stderr)
    d = collect(usage, a.reserve, a.stale_hours, now)
    (LOOP_DIR / "pace.json").write_text(json.dumps(d["pace"], indent=2) + "\n")
    (LOOP_DIR / "digest.json").write_text(json.dumps(d, indent=2) + "\n")
    md = render(d, a.by)
    (LOOP_DIR / "STATUS.md").write_text(md)
    published = None
    if a.publish:
        try:
            published = publish(md, a.by)
        except RuntimeError as e:
            published = f"failed: {e}"
    if a.json:
        print(json.dumps({**d, "published": published}, indent=2))
        return
    print(f"Loop at {d['generated']}: runs {'allowed' if d['pace']['run'] else 'PAUSED'} ({d['pace']['reason']}); "
          f"server {'up' if d['services']['server'] else 'DOWN'}, proxy {'up' if d['services']['proxy'] else 'DOWN'}"
          + (f"; status page {published}" if published else ""))
    for r in d["tracks"]:
        b = r.get("best") or {}
        print(f"  {r['bot']:<16} {r['track']:<10} {r['phase']:<9} best {fmt_score(b.get('gauntlet_bb'), b.get('gauntlet_ci'))}"
              f", runs {r['iterations']} (kept {r['kept']})")
    for msg in d["alerts"]:
        print("ALERT: " + msg)


if __name__ == "__main__":
    sys.exit(main())
