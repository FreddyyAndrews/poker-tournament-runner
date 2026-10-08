"""`poker` command line. Every command takes --json for machine-readable output."""

import argparse
import json
import sys
from pathlib import Path

from arena import bots as botlib
from arena import structures
from arena.events import DEFAULT_RUNS_DIR
from arena.table import MatchConfig, run_match
from arena.verify import verify_run


def _print_json(obj):
    print(json.dumps(obj, indent=2, default=str))


def _fail(msg, as_json=False, code=2):
    if as_json:
        _print_json({"ok": False, "error": msg})
    else:
        print("error: " + msg, file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# poker match
# ---------------------------------------------------------------------------

def cmd_match(args):
    try:
        specs = botlib.resolve_all(args.bots, names=args.name)
        if args.freezeout:
            mode, hands = "freezeout", args.max_hands
            structure = structures.parse(args.blinds or "standard", args.stack)
        else:
            mode, hands = "fixed", args.hands
            structure = structures.parse(args.blinds or "50/100", args.stack)
        config = MatchConfig(mode=mode, hands=hands, starting_stack=args.stack,
                             structure=structure, seed=args.seed,
                             time_limit_ms=args.time_limit_ms)
    except ValueError as e:
        _fail(str(e), args.json)

    live = None
    if args.verbose and not args.json:
        live = _live_printer()
    summary = run_match(specs, config, match_id=args.id, runs_dir=Path(args.out),
                        use_docker=True if args.docker else None,
                        listeners=[live] if live else [])
    if args.json:
        _print_json(summary)
        return
    _print_summary(summary)


def _live_printer():
    def show(e):
        t = e["type"]
        if t == "level":
            print(f"  -- level {e['level']}: blinds {e['sb']}/{e['bb']}"
                  + (f" ante {e['ante']}" if e["ante"] else ""))
        elif t == "elimination":
            print(f"  xx {e['bot']} busts on hand {e['hand']} (place {e['place']})")
        elif t == "hand_end" and e["hand"] % 25 == 0:
            stacks = "  ".join(f"{n} {s}" for n, s in sorted(e["stacks"].items(), key=lambda x: -x[1]))
            print(f"  hand {e['hand']}: {stacks}")
    return show


def _print_summary(s):
    cfg = s["config"]
    print(f"\nMatch {s['match_id']}  ({cfg['mode']}, {s['hands']} hands, seed {s['seed']}, "
          f"{s['duration_s']}s)")
    print(f"Ended: {s['stopped']}\n")
    hdr = f"{'#':>2}  {'Bot':<22}{'Stack':>9}{'Delta':>9}{'bb/100':>9}{'Hands':>7}" \
          f"{'Avg ms':>8}{'T/O':>5}{'Err':>5}"
    print(hdr)
    print("-" * len(hdr))
    for r in s["results"]:
        print(f"{r['place'] or '-':>2}  {r['bot']:<22}{r['final_stack']:>9}{r['delta']:>+9}"
              f"{r['bb_per_100']:>9.1f}{r['hands_played']:>7}{(r['avg_think_ms'] or 0):>8.2f}"
              f"{r['timeouts'] or 0:>5}{r['errors'] or 0:>5}")
    problems = {b: d["errors_list"] for b, d in s.get("bot_diagnostics", {}).items()
                if d.get("errors_list")}
    if problems:
        print("\nBot errors:")
        for b, errs in problems.items():
            print(f"  {b}: {errs[:5]}" + (" ..." if len(errs) > 5 else ""))
    print(f"\nLog: {s['run_dir']}")
    print(f"Check it with: poker verify {s['run_dir']}")


# ---------------------------------------------------------------------------
# poker verify
# ---------------------------------------------------------------------------

def cmd_verify(args):
    try:
        report = verify_run(args.run)
    except FileNotFoundError as e:
        _fail(str(e), args.json)
    if args.json:
        _print_json(report)
    else:
        status = "OK" if report["ok"] else "FAILED"
        print(f"{status}: {report['run_dir']}")
        print(f"  {report['hands']} hands, {report['decisions']} decisions, {report['events']} events")
        for w in report["warnings"]:
            print("  warning: " + w)
        for p in report["problems"]:
            print("  problem: " + p)
    sys.exit(0 if report["ok"] else 1)


# ---------------------------------------------------------------------------
# poker bots
# ---------------------------------------------------------------------------

def cmd_bots(args):
    found = botlib.available()
    if args.json:
        _print_json([{"name": n, "path": p, "folder": f} for n, p, f in found])
        return
    for name, path, folder in found:
        print(f"{name:<22} {folder}/")


# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="poker", description="Poker bot matches and tournaments")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("match", help="play one table (2-9 bots)",
                       description="Play one table. Bots are names from my_bots/ or bots/, or paths.")
    m.add_argument("bots", nargs="+", help="2-9 bots: names (shark) or paths (my_bots/x/bot.py)")
    m.add_argument("--name", action="append", help="display name for each bot, in order (repeatable)")
    m.add_argument("--hands", type=int, default=100, help="hands to play (fixed mode, default 100)")
    m.add_argument("--freezeout", action="store_true",
                   help="play until one bot has every chip (blinds rise; default structure 'standard')")
    m.add_argument("--max-hands", type=int, default=2000, help="freezeout safety cap (default 2000)")
    m.add_argument("--stack", type=int, default=10_000, help="starting stack (default 10000)")
    m.add_argument("--blinds", help="flat SB/BB[/ANTE] like 50/100, or a structure: "
                                    + ", ".join(structures.PRESETS))
    m.add_argument("--seed", type=int, help="match seed (same seed + bots = same match)")
    m.add_argument("--time-limit-ms", type=int, help="per-decision time limit (default 2000)")
    m.add_argument("--docker", action="store_true", help="run bots in the Docker sandbox")
    m.add_argument("--id", help="match id / folder name (default: generated)")
    m.add_argument("--out", default=str(DEFAULT_RUNS_DIR), help="runs directory (default data/runs)")
    m.add_argument("-v", "--verbose", action="store_true", help="print progress while playing")
    m.add_argument("--json", action="store_true", help="print the summary as JSON")
    m.set_defaults(func=cmd_match)

    v = sub.add_parser("verify", help="check a match log by replaying it",
                       description="Replay a match log through the engine and check it.")
    v.add_argument("run", help="run directory, events.jsonl path, or match id")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)

    b = sub.add_parser("bots", help="list available bots")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_bots)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
