"""A bot's lab notes: my_bots/<name>/notes/, kept by the agent that iterates
on it and read by the commissioner's digest. The folder is left out of the
bot's code hash (arena/bots.py), so notes never make a new bot version.

  notes/status.json   machine-readable state: track, phase, best result,
                      last run, counts, the strategy in a paragraph
  notes/plan.md       the queue of hypotheses to try, one "- " line each,
                      best first
  notes/journal.md    one entry per iteration, newest last

Commands (run with the main checkout's Python):
  notes.py init <bot_dir> --track T --strategy "..." [--from SRC]
  notes.py show <bot_dir> [--journal N]        compact summary for an agent
  notes.py record <bot_dir> --result kept|reverted|skipped|error|training
           --experiment "..." --summary "..." [--hash H]
           [--gauntlet BB CI --hands N] [--slumbot BB CI] [--strategy "..."] [--next "..."]
  notes.py phase <bot_dir> iterating|training|paused|blocked [--reason "..."]
  notes.py pop <bot_dir>                       remove the first plan item and print it
  notes.py push <bot_dir> "idea" [--top]       add a plan item
"""

import argparse
import json
import sys
import time
from pathlib import Path

TRACKS = ("heuristic", "rl", "jev_prompt", "jev_tools")
RESULTS = ("kept", "reverted", "skipped", "error", "training")
PHASES = ("iterating", "training", "paused", "blocked")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def paths(bot_dir):
    d = Path(bot_dir) / "notes"
    return d, d / "status.json", d / "plan.md", d / "journal.md"


def load(bot_dir) -> dict:
    _, status, _, _ = paths(bot_dir)
    if not status.is_file():
        raise SystemExit(f"no notes in {bot_dir}: start the track with run-loop's start_track.sh")
    return json.loads(status.read_text())


def save(bot_dir, st: dict):
    _, status, _, _ = paths(bot_dir)
    st["updated"] = now()
    tmp = status.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2) + "\n")
    tmp.replace(status)


def plan_items(bot_dir) -> list:
    _, _, plan, _ = paths(bot_dir)
    if not plan.is_file():
        return []
    return [line[2:].strip() for line in plan.read_text().splitlines() if line.startswith("- ")]


def write_plan(bot_dir, items: list):
    _, _, plan, _ = paths(bot_dir)
    plan.write_text("# Plan\n\nHypotheses to try, best first. One line each.\n\n"
                    + "".join(f"- {i}\n" for i in items))


def cmd_init(a):
    d, status, plan, journal = paths(a.bot_dir)
    if status.exists():
        raise SystemExit(f"{status} already exists")
    d.mkdir(parents=True, exist_ok=True)
    name = Path(a.bot_dir).resolve().name
    save(a.bot_dir, {"bot": name, "track": a.track, "phase": "iterating", "blocked_reason": None,
                     "created": now(), "forked_from": a.from_, "strategy": a.strategy,
                     "trying_next": None, "iterations": 0, "kept": 0, "reverted": 0,
                     "best": None, "last_run": None})
    write_plan(a.bot_dir, a.idea or [])
    journal.write_text(f"# {name}: journal\n\nOne entry per iteration, newest last.\n")
    print(f"notes started in {d}")


def cmd_show(a):
    st = load(a.bot_dir)
    best = st.get("best") or {}
    last = st.get("last_run") or {}
    print(f"{st['bot']} ({st['track']}), phase {st['phase']}"
          + (f": {st['blocked_reason']}" if st.get("blocked_reason") else ""))
    print(f"iterations {st['iterations']}, kept {st['kept']}, reverted {st['reverted']}")
    if best:
        g = f"gauntlet {best.get('gauntlet_bb'):+.1f} ± {best.get('gauntlet_ci'):.1f}" \
            if best.get("gauntlet_bb") is not None else "no gauntlet yet"
        s = f", slumbot {best['slumbot_bb']:+.1f} ± {best['slumbot_ci']:.1f}" \
            if best.get("slumbot_bb") is not None else ""
        h = f" at {best['gauntlet_hands']} hands a seat" if best.get("gauntlet_hands") else " (hand count not recorded)"
        print(f"best: {best.get('hash')} {g}{h if best.get('gauntlet_bb') is not None else ''}{s} ({best.get('at')})")
    if last:
        print(f"last run: {last.get('at')} {last.get('result')}: {last.get('experiment')}")
    print(f"strategy: {st.get('strategy')}")
    items = plan_items(a.bot_dir)
    print(f"plan ({len(items)}):" + "".join(f"\n  - {i}" for i in items[:5]))
    _, _, _, journal = paths(a.bot_dir)
    entries = journal.read_text().split("\n## ")[1:] if journal.is_file() else []
    for e in entries[-a.journal:]:
        print("\n## " + e.strip()[:700])


def cmd_record(a):
    st = load(a.bot_dir)
    st["iterations"] += 1 if a.result != "training" else 0
    if a.result in ("kept", "reverted"):
        st[a.result] += 1
    run = {"at": now(), "result": a.result, "experiment": a.experiment, "summary": a.summary,
           "hash": a.hash}
    if a.gauntlet:
        run["gauntlet_bb"], run["gauntlet_ci"] = a.gauntlet
        run["gauntlet_hands"] = a.hands
    if a.slumbot:
        run["slumbot_bb"], run["slumbot_ci"] = a.slumbot
    st["last_run"] = run
    best = st.get("best") or {}
    if a.result == "kept" or (a.gauntlet and not best):
        new = dict(best)
        new.update(hash=a.hash or best.get("hash"), at=now())
        if a.gauntlet:
            new["gauntlet_bb"], new["gauntlet_ci"] = a.gauntlet
            new["gauntlet_hands"] = a.hands
        if a.slumbot:
            new["slumbot_bb"], new["slumbot_ci"] = a.slumbot
        st["best"] = new
    elif a.slumbot and best and a.hash == best.get("hash"):
        best["slumbot_bb"], best["slumbot_ci"] = a.slumbot       # a checkpoint of the best version
    if a.strategy:
        st["strategy"] = a.strategy
    if a.next is not None:
        st["trying_next"] = a.next
    if a.result in ("kept", "reverted", "skipped") and st["phase"] == "blocked":
        st["phase"], st["blocked_reason"] = "iterating", None
    save(a.bot_dir, st)
    _, _, _, journal = paths(a.bot_dir)
    lines = [f"\n## {run['at']} {a.result}: {a.experiment}", "", a.summary]
    if a.gauntlet:
        lines.append(f"\nGauntlet: {a.gauntlet[0]:+.1f} ± {a.gauntlet[1]:.1f} bb/100"
                     + (f" at {a.hands} hands a seat." if a.hands else "."))
    if a.slumbot:
        lines.append(f"Slumbot: {a.slumbot[0]:+.1f} ± {a.slumbot[1]:.1f} bb/100.")
    if a.hash:
        lines.append(f"Code hash: {a.hash}.")
    with open(journal, "a") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"recorded {a.result}: {a.experiment}")


def cmd_phase(a):
    st = load(a.bot_dir)
    st["phase"] = a.phase
    st["blocked_reason"] = a.reason if a.phase in ("blocked", "paused", "training") else None
    save(a.bot_dir, st)
    print(f"{st['bot']} is now {a.phase}" + (f": {a.reason}" if a.reason else ""))


def cmd_pop(a):
    items = plan_items(a.bot_dir)
    if not items:
        print("")
        return
    write_plan(a.bot_dir, items[1:])
    print(items[0])


def cmd_push(a):
    items = plan_items(a.bot_dir)
    items = [a.idea] + items if a.top else items + [a.idea]
    write_plan(a.bot_dir, items[:30])
    print(f"plan has {len(items[:30])} items")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("bot_dir")
    i.add_argument("--track", choices=TRACKS, required=True)
    i.add_argument("--strategy", required=True)
    i.add_argument("--from", dest="from_")
    i.add_argument("--idea", action="append", help="a first plan item (repeatable)")
    i.set_defaults(func=cmd_init)
    s = sub.add_parser("show")
    s.add_argument("bot_dir")
    s.add_argument("--journal", type=int, default=3)
    s.set_defaults(func=cmd_show)
    r = sub.add_parser("record")
    r.add_argument("bot_dir")
    r.add_argument("--result", choices=RESULTS, required=True)
    r.add_argument("--experiment", required=True)
    r.add_argument("--summary", required=True)
    r.add_argument("--hash")
    r.add_argument("--gauntlet", nargs=2, type=float, metavar=("BB", "CI"))
    r.add_argument("--hands", type=int, help="the gauntlet's hands per seat")
    r.add_argument("--slumbot", nargs=2, type=float, metavar=("BB", "CI"))
    r.add_argument("--strategy")
    r.add_argument("--next")
    r.set_defaults(func=cmd_record)
    ph = sub.add_parser("phase")
    ph.add_argument("bot_dir")
    ph.add_argument("phase", choices=PHASES)
    ph.add_argument("--reason")
    ph.set_defaults(func=cmd_phase)
    po = sub.add_parser("pop")
    po.add_argument("bot_dir")
    po.set_defaults(func=cmd_pop)
    pu = sub.add_parser("push")
    pu.add_argument("bot_dir")
    pu.add_argument("idea")
    pu.add_argument("--top", action="store_true")
    pu.set_defaults(func=cmd_push)
    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    sys.exit(main())
