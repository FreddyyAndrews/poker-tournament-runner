"""The commissioner's checks for the autonomous bot loop. Deterministic and
cheap: the commissioner session runs this, reads its short report, and
publishes the digest.

  commission.py [--usage FILE] [--reserve 0.2] [--stale-hours 6] [--json]

What it does:
  - services: the central server and the outside-bot proxy answer;
  - tracks: every worktree ../poker-bot-<name> whose bot has notes
    (my_bots/<name>/notes/status.json): phase, best result, last run, how
    long since it last ran or committed;
  - budgets: Slumbot hands and Jev spend from the proxy, disk use of data/
    and free space;
  - pacing: from the Claude plan usage in --usage (the JSON of the app's
    get_usage, saved by the commissioner session), whether iterations may
    run now, written to data/loop/pace.json for every iteration to read:
    the weekly limit is spread over the week, keeping --reserve of it for
    the user's own work;
  - the digest: data/loop/digest.json, everything above in one file;
  - alerts: printed as "ALERT: ..." lines; the session notifies the user
    only when there are any.
"""

import argparse
import calendar
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MAIN = Path(__file__).resolve().parents[4]
LOOP_DIR = MAIN / "data" / "loop"
SERVER = os.environ.get("POKER_SERVER", "http://127.0.0.1:8765")
PROXY = os.environ.get("POKER_PROXY_URL", "http://127.0.0.1:8766")
WEEK_S = 7 * 86400
DATA_BUDGET_GB = 20
MIN_FREE_GB = 20


def get_json(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def git(*args, cwd=MAIN) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


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
            status_file = wt["path"] / "my_bots" / name / "notes" / "status.json"
            if not wt["path"].name.startswith("poker-bot-") or not status_file.is_file():
                continue
            try:
                st = json.loads(status_file.read_text())
            except ValueError:
                st = {"bot": name, "phase": "unreadable"}
            last_commit = git("log", "-1", "--format=%ct", cwd=wt["path"])
            commits_today = git("rev-list", "--count", "--since=midnight", "HEAD", cwd=wt["path"])
            st.update(worktree=str(wt["path"]), branch=wt.get("branch"),
                      last_commit=int(last_commit) if last_commit else None,
                      commits_today=int(commits_today or 0))
            out.append(st)
    return out


def latest_gauntlet(bot: str):
    f = MAIN / "data" / "bench" / f"{bot}.jsonl"
    if not f.is_file():
        return None
    latest = None
    for line in f.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("suite") == "gauntlet":
            latest = e
    if not latest:
        return None
    return {"time": latest["time"], "hash": latest["hash"], "bb": latest["overall_bb_per_100"],
            "ci": latest["overall_ci95"], "opponents": {o["opponent"]: [o["bb_per_100"], o["ci95"]]
                                                        for o in latest["opponents"]}}


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
        return {**out, "run": True, "reason": "no Claude usage data: running without pacing"}
    try:
        resets = calendar.timegm(time.strptime(weekly["resetsAt"][:19], "%Y-%m-%dT%H:%M:%S"))  # UTC
    except (KeyError, ValueError):
        return {**out, "run": True, "reason": "unreadable weekly reset time: running without pacing"}
    elapsed = min(1.0, max(0.0, 1 - (resets - now) / WEEK_S))
    used = float(weekly.get("percentUsed") or 0)
    target = round(elapsed * (1 - reserve) * 100, 1)
    out.update(weekly_used=used, week_elapsed=round(elapsed * 100, 1), target=target)
    if five and float(five.get("percentUsed") or 0) >= 90:
        return {**out, "run": False, "five_hour_used": five["percentUsed"],
                "reason": f"the 5-hour window is {five['percentUsed']}% used; waiting for it to reset"}
    if used > target + 5:
        return {**out, "run": False,
                "reason": f"ahead of pace: {used:.0f}% of the weekly limit used, {target:.0f}% planned by now "
                          f"(keeping {reserve:.0%} for the user)"}
    return {**out, "run": True, "reason": f"on pace: {used:.0f}% used, {target:.0f}% planned by now"}


def write_dashboard_files(digest: dict):
    """The digest as row files for the dashboard (data/loop/dash/*.json):
    one file per dataset, each a JSON array of flat row objects."""
    out = LOOP_DIR / "dash"
    out.mkdir(parents=True, exist_ok=True)
    tracks_rows = []
    for r in digest["tracks"]:
        b, last = r.get("best") or {}, r.get("last_run") or {}
        tracks_rows.append({"bot": r["bot"], "track": r.get("track"), "phase": r.get("phase"),
                            "gauntlet_bb": b.get("gauntlet_bb"), "gauntlet_ci": b.get("gauntlet_ci"),
                            "slumbot_bb": b.get("slumbot_bb"), "slumbot_ci": b.get("slumbot_ci"),
                            "runs": r.get("iterations", 0), "kept": r.get("kept", 0),
                            "hours_since_run": r.get("hours_since_run"), "last_result": last.get("result"),
                            "last_experiment": last.get("experiment"), "trying_next": r.get("trying_next"),
                            "strategy": r.get("strategy"), "blocked_reason": r.get("blocked_reason")})
    b = digest["budgets"]
    budget_rows = []
    for w in b.get("claude", []):
        if w.get("percent_used") is not None:
            budget_rows.append({"budget": "Claude " + (w["label"] or "").replace("limit", "").strip(),
                                "used": w["percent_used"], "limit": 100, "unit": "%",
                                "note": f"resets in {w.get('resets_in')}"})
    s, j = b.get("slumbot") or {}, b.get("jev") or {}
    if s.get("daily_hands"):
        budget_rows.append({"budget": "Slumbot hands today", "used": s.get("hands_today") or 0,
                            "limit": s["daily_hands"], "unit": "hands", "note": "across every bot"})
    if j.get("allowance_today_usd") is not None:
        budget_rows.append({"budget": "Jev today", "used": j.get("spent_today_usd") or 0,
                            "limit": j["allowance_today_usd"], "unit": "USD", "note": "today's share of the month"})
        budget_rows.append({"budget": "Jev this month", "used": j.get("spent_month_usd") or 0,
                            "limit": j.get("monthly_budget_usd") or 0, "unit": "USD", "note": "monthly budget"})
    d = b.get("disk") or {}
    budget_rows.append({"budget": "Match data on disk", "used": d.get("data_gb"), "limit": DATA_BUDGET_GB,
                        "unit": "GB", "note": f"{d.get('free_gb')} GB free on the disk"})
    pace = digest["pace"]
    status_rows = [{"generated": digest["generated"].replace("T", " ")[:16],
                    "server": "up" if digest["services"]["server"] else "down",
                    "proxy": "up" if digest["services"]["proxy"] else "down",
                    "iterations": "running" if pace["run"] else "paused",
                    "pace_reason": pace["reason"], "tracks": len(tracks_rows),
                    "alerts": len(digest["alerts"])}]
    alert_rows = [{"n": i + 1, "message": m} for i, m in enumerate(digest["alerts"])]
    champion_rows = [{"name": c["name"], "bot": c["bot"], "added": c["added"][:10], "note": c.get("note") or ""}
                     for c in digest["champions"]]
    for name, rows in (("status", status_rows), ("tracks", tracks_rows), ("budgets", budget_rows),
                       ("alerts", alert_rows), ("champions", champion_rows)):
        (out / f"{name}.json").write_text(json.dumps(rows, indent=1) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--usage", help="JSON of the app's get_usage")
    p.add_argument("--reserve", type=float, default=0.2, help="share of the weekly limit kept for the user")
    p.add_argument("--stale-hours", type=float, default=6)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    now = time.time()
    LOOP_DIR.mkdir(parents=True, exist_ok=True)
    alerts = []

    server = get_json(SERVER + "/api/health")
    proxy = get_json(PROXY + "/status")
    if not server:
        alerts.append(f"the central server isn't answering at {SERVER}")
    if not proxy:
        alerts.append(f"the outside-bot proxy isn't answering at {PROXY}")

    usage = None
    if a.usage:
        try:
            usage = json.loads(Path(a.usage).read_text())
        except (OSError, ValueError):
            alerts.append(f"couldn't read Claude usage from {a.usage}")
    pacing = pace(usage, a.reserve, now)
    (LOOP_DIR / "pace.json").write_text(json.dumps(pacing, indent=2) + "\n")
    if not pacing["run"]:
        alerts.append("iterations are paused: " + pacing["reason"])

    rows = []
    for st in tracks():
        last = st.get("last_run") or {}
        last_at = time.mktime(time.strptime(last["at"], "%Y-%m-%dT%H:%M:%S")) if last.get("at") else None
        idle_h = round((now - last_at) / 3600, 1) if last_at else None
        if st.get("phase") == "blocked":
            alerts.append(f"{st['bot']} is blocked: {st.get('blocked_reason')}")
        elif st.get("phase") == "iterating" and pacing["run"] and (idle_h is None or idle_h > a.stale_hours):
            alerts.append(f"{st['bot']} hasn't recorded a run for "
                          + (f"{idle_h} hours" if idle_h is not None else "ever"))
        if last.get("result") == "error":
            alerts.append(f"{st['bot']}'s last run ended in an error: {last.get('summary')}")
        rows.append({"bot": st["bot"], "track": st.get("track"), "phase": st.get("phase"),
                     "blocked_reason": st.get("blocked_reason"), "strategy": st.get("strategy"),
                     "trying_next": st.get("trying_next"), "iterations": st.get("iterations", 0),
                     "kept": st.get("kept", 0), "reverted": st.get("reverted", 0),
                     "best": st.get("best"), "last_run": last or None, "hours_since_run": idle_h,
                     "commits_today": st.get("commits_today"), "branch": st.get("branch"),
                     "latest_gauntlet": latest_gauntlet(st["bot"])})
    rows.sort(key=lambda r: -((r.get("best") or {}).get("gauntlet_bb") or -1e9))

    budgets = {}
    if proxy:
        ups = proxy["upstreams"]
        s, j = ups.get("slumbot", {}), ups.get("jev", {})
        budgets["slumbot"] = {"hands_today": s.get("hands_today"), "daily_hands": s.get("daily_hands"),
                              "allowed": s.get("allowed"), "why": s.get("why")}
        budgets["jev"] = {k: j.get(k) for k in ("spent_today_usd", "allowance_today_usd", "spent_month_usd",
                                                 "monthly_budget_usd", "bots_today_usd", "allowed", "why")}
        if s.get("daily_hands") and (s.get("hands_today") or 0) >= 0.9 * s["daily_hands"]:
            alerts.append(f"Slumbot: {s['hands_today']:,} of {s['daily_hands']:,} hands used today")
        if j.get("monthly_budget_usd") and (j.get("spent_month_usd") or 0) >= 0.8 * j["monthly_budget_usd"]:
            alerts.append(f"Jev: ${j['spent_month_usd']:.2f} of the ${j['monthly_budget_usd']:.2f} monthly budget spent")
    data_gb = dir_size_gb(MAIN / "data")
    free_gb = shutil.disk_usage(MAIN).free / 1e9
    budgets["disk"] = {"data_gb": round(data_gb, 2), "free_gb": round(free_gb, 1)}
    if data_gb > DATA_BUDGET_GB:
        alerts.append(f"data/ holds {data_gb:.1f} GB, over the {DATA_BUDGET_GB} GB budget")
    if free_gb < MIN_FREE_GB:
        alerts.append(f"only {free_gb:.0f} GB of disk left")
    windows = ((usage or {}).get("plan") or {}).get("windows") or []
    budgets["claude"] = [{"label": w.get("label"), "percent_used": w.get("percentUsed"),
                          "resets_in": w.get("resetsIn")} for w in windows]

    champions = []
    idx = MAIN / "champions" / "index.json"
    if idx.is_file():
        champions = [{"name": e["name"], "bot": e["bot"], "added": e["added"], "note": e.get("note")}
                     for e in json.loads(idx.read_text())]

    digest = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
              "services": {"server": bool(server), "proxy": bool(proxy)}, "pace": pacing,
              "tracks": rows, "champions": champions, "budgets": budgets, "alerts": alerts}
    (LOOP_DIR / "digest.json").write_text(json.dumps(digest, indent=2) + "\n")
    write_dashboard_files(digest)

    if a.json:
        print(json.dumps(digest, indent=2))
        return
    print(f"Loop at {digest['generated']}: server {'up' if server else 'DOWN'}, proxy {'up' if proxy else 'DOWN'}; "
          f"pace: {pacing['reason']}")
    for r in rows:
        b = r.get("best") or {}
        g = f"{b['gauntlet_bb']:+.1f} ± {b['gauntlet_ci']:.1f}" if b.get("gauntlet_bb") is not None else "no score yet"
        print(f"  {r['bot']:<16} {r['track']:<10} {r['phase']:<9} best {g:<16} runs {r['iterations']} "
              f"(kept {r['kept']}), last {r['hours_since_run']} h ago")
    if not rows:
        print("  no tracks yet")
    for msg in alerts:
        print("ALERT: " + msg)


if __name__ == "__main__":
    sys.exit(main())
