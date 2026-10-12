"""`poker` command line. Every command takes --json for machine-readable output."""

import argparse
import json
import sys
from pathlib import Path

from arena import bots as botlib
from arena import formats, structures
from arena.events import DEFAULT_RUNS_DIR, SUMMARY_FILE
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


def _say(args, msg):
    """Progress output: to stderr, and only when not in --json mode."""
    if not getattr(args, "json", False):
        print(msg, file=sys.stderr, flush=True)


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
            mode, hands = ("cash" if args.cash else "fixed"), args.hands
            structure = structures.parse(args.blinds or "50/100", args.stack)
        config = MatchConfig(mode=mode, hands=hands, starting_stack=args.stack,
                             structure=structure, seed=args.seed,
                             time_limit_ms=args.time_limit_ms)
    except ValueError as e:
        _fail(str(e), args.json)

    live = _live_printer() if args.verbose and not args.json else None
    summary = run_match(specs, config, match_id=args.id, runs_dir=Path(args.out),
                        use_docker=True if args.docker else None,
                        listeners=[live] if live else [], pace_ms=args.pace)
    if args.json:
        _print_json(summary)
    else:
        _print_match(summary)


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


def _print_match(s):
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
# poker run
# ---------------------------------------------------------------------------

def cmd_run(args):
    overrides = {"format": args.format, "bots": args.bots, "names": args.name, "mode": args.mode,
                 "hands": args.hands, "starting_stack": args.stack, "blinds": args.blinds,
                 "time_limit_ms": args.time_limit_ms, "seed": args.seed,
                 "table_size": args.table_size, "duplicate": args.duplicate,
                 "seeding": args.seeding, "name": args.title,
                 "runs": args.runs, "workers": args.workers, "docker": args.docker or None}
    try:
        if args.config:
            cfg, opts = formats.load_config(args.config, overrides)
        else:
            if not args.format or not args.bots:
                raise ValueError("give a config file, or at least --format and --bots")
            opts = {k: overrides.pop(k) for k in formats.RUN_OPTIONS}
            cfg = formats.RunConfig(**{k: v for k, v in overrides.items() if v is not None})
    except (ValueError, TypeError, FileNotFoundError) as e:
        _fail(str(e), args.json)

    runs = opts.get("runs") or 1
    workers = opts.get("workers") or 1
    use_docker = True if opts.get("docker") else None
    try:
        if runs > 1:
            from arena.series import run_series

            def progress(p):
                _say(args, f"  run {p['runs_done']}/{p['runs']} done (winner: {p['winner']})")
            result = run_series(cfg, runs, series_id=args.id, runs_dir=Path(args.out),
                                workers=workers, use_docker=use_docker, progress=[progress])
        else:
            def progress(p):
                if p["event"] == "match_done":
                    _say(args, f"  {p['match_id']} done ({p['matches_done']} matches)")
            result = formats.run(cfg, run_id=args.id, runs_dir=Path(args.out), workers=workers,
                                 use_docker=use_docker, progress=[progress], pace_ms=args.pace)
    except ValueError as e:
        _fail(str(e), args.json)
    if args.json:
        _print_json(result)
    else:
        _print_result(result)


def _print_run(r):
    cfg = r["config"]
    title = f" '{r['name']}'" if r.get("name") else ""
    print(f"\n{r['format']} run{title} {r['run_id']}  ({len(r['standings'])} bots, "
          f"{len(r['matches'])} matches, seed {r['seed']}, {r['duration_s']}s)")
    print(f"Matches: {cfg['mode']}, {cfg['hands']} hands{' (cap)' if cfg['mode'] == 'freezeout' else ''}, "
          f"stack {cfg['starting_stack']}, blinds {cfg['blinds']}"
          + (", duplicate" if cfg.get("duplicate") else "") + "\n")
    fmt = r["format"]
    cols = [("#", 3, "place"), ("", 2, None), ("Bot", 22, "bot")]
    if fmt == "round_robin":
        cols += [("Pts", 6, "points"), ("W", 4, "wins"), ("D", 4, "draws"), ("L", 4, "losses")]
    elif fmt == "bracket":
        cols += [("W", 4, "wins"), ("L", 4, "losses")]
    elif fmt in ("table", "mtt"):
        cols += [("Stack", 9, "final_stack")]
    cols += [("Delta", 9, "delta"), ("bb/100", 9, "bb_per_100"), ("Hands", 7, "hands_played"),
             ("T/O", 5, "timeouts"), ("Err", 5, "errors")]
    hdr = "".join(f"{h:<{w}}" if k == "bot" else f"{h:>{w}}" for h, w, k in cols)
    print(hdr)
    print("-" * len(hdr))
    for row in r["standings"]:
        line = ""
        for h, w, k in cols:
            v = row.get(k) if k else ""
            if k is None:
                line += " " * w
            elif k == "bot":
                line += f"{v:<{w}}"
            elif isinstance(v, float):
                line += f"{v:>{w}.1f}"
            else:
                line += f"{'-' if v is None else v:>{w}}"
        print(line)
    if fmt == "bracket":
        print("\nBracket:")
        from arena.seeds import derive_seed
        for m in r["matches"]:
            res = {x["bot"]: x for x in m["results"]}
            w, l, how = formats.two_player_winner(m, derive_seed(r["seed"], "tiebreak", m["match_id"]))
            note = ", won on a coin flip" if how == "coin_flip" else ""
            print(f"  round {m['round']}: {w} beat {l}  ({res[w]['final_stack']}-{res[l]['final_stack']}, "
                  f"{m['hands']} hands{note})")
    if fmt == "round_robin" and r.get("head_to_head"):
        names = [row["bot"] for row in r["standings"]]
        print("\nHead to head (chips won by row vs column):")
        print(" " * 18 + "".join(f"{n[:9]:>10}" for n in names))
        for a in names:
            cells = "".join(f"{'':>10}" if a == b else f"{r['head_to_head'][a][b]['delta']:>+10}"
                            for b in names)
            print(f"{a[:17]:<18}{cells}")
    print(f"\nRun: {r['run_dir']}")
    print(f"Check it with: poker verify {r['run_dir']}")


def _print_series(s):
    print(f"\n{s['runs']} x {s['format']} {s['series_id']}  (seed {s['seed']}, {s['duration_s']}s)\n")
    hdr = f"{'Bot':<22}{'Wins':>6}{'Win %':>8}{'95% CI':>15}{'Top 3':>8}{'Avg place':>11}{'bb/100':>9}"
    print(hdr)
    print("-" * len(hdr))
    for a in s["aggregate"]:
        lo, hi = a["win_rate_ci"]
        print(f"{a['bot']:<22}{a['wins']:>6}{100 * a['win_rate']:>7.1f}%"
              f"{f'{100 * lo:.1f}-{100 * hi:.1f}%':>15}{100 * a['top3_rate']:>7.0f}%"
              f"{a['avg_place']:>11.2f}{a['bb_per_100']:>9.1f}")
    print(f"\nSeries: {s['run_dir']}")
    print(f"Check it with: poker verify {s['run_dir']}")


def _print_result(r):
    kind = r.get("kind")
    if kind == "series":
        _print_series(r)
    elif kind == "run":
        _print_run(r)
    else:
        _print_match(r)


# ---------------------------------------------------------------------------
# poker results / runs
# ---------------------------------------------------------------------------

def _load_result(ref):
    from arena.series import SERIES_FILE
    p = Path(ref).expanduser()
    if not p.exists():
        p = DEFAULT_RUNS_DIR / ref
    for name in (SERIES_FILE, formats.RUN_FILE, SUMMARY_FILE):
        if (p / name).is_file():
            return json.loads((p / name).read_text())
    raise FileNotFoundError(f"No results found for {ref!r}")


def cmd_results(args):
    try:
        r = _load_result(args.run)
    except FileNotFoundError as e:
        _fail(str(e), args.json)
    if args.json:
        _print_json(r)
    elif r.get("kind") == "run" and r.get("status") != "complete":
        print(f"Run {r['run_id']} is {r.get('status')}" + (f": {r['error']}" if r.get("error") else ""))
    else:
        _print_result(r)


def cmd_runs(args):
    from arena.series import SERIES_FILE
    root = Path(args.out)
    rows = []
    if root.is_dir():
        for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            for name, kind in ((SERIES_FILE, "series"), (formats.RUN_FILE, "run"), (SUMMARY_FILE, "match")):
                if (d / name).is_file():
                    data = json.loads((d / name).read_text())
                    rows.append({"id": d.name, "kind": kind,
                                 "format": data.get("format") or data.get("config", {}).get("mode"),
                                 "status": data.get("status", "complete"),
                                 "bots": len(data.get("standings") or data.get("aggregate")
                                             or data.get("results") or []),
                                 "runs": data.get("runs"), "path": str(d)})
                    break
    rows = rows[: args.limit]
    if args.json:
        _print_json(rows)
        return
    for r in rows:
        extra = f" x{r['runs']}" if r["runs"] else ""
        print(f"{r['id']:<48} {r['kind']:<7} {str(r['format']) + extra:<16} {r['bots']:>3} bots  {r['status']}")


# ---------------------------------------------------------------------------
# poker verify / export / bench / bots
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
        kind = report.get("kind", "match")
        if kind == "series":
            print(f"  series of {len(report['runs'])} runs")
        elif kind == "run":
            print(f"  {report['format']} run, {len(report['matches'])} match logs, "
                  f"{sum(m['hands'] for m in report['matches'])} hands")
        else:
            print(f"  {report['hands']} hands, {report['decisions']} decisions, {report['events']} events")
        for w in report["warnings"]:
            print("  warning: " + w)
        for p in report["problems"][:20]:
            print("  problem: " + p)
    sys.exit(0 if report["ok"] else 1)


def cmd_export(args):
    from arena.export import export
    p = Path(args.run).expanduser()
    if not p.exists():
        p = DEFAULT_RUNS_DIR / args.run
    if not p.is_dir():
        _fail(f"No run found for {args.run!r}", args.json)
    out = args.output or f"{p.name}{'_' + args.bot if args.bot else ''}.zip"
    result = export(p, out, bot=args.bot)
    if args.json:
        _print_json(result)
    else:
        who = f" (private data for {args.bot} only)" if args.bot else ""
        print(f"Wrote {result['path']}: {result['files']} files{who}")


def cmd_bench(args):
    from arena.bench import bench

    def progress(r):
        _say(args, f"  vs {r['opponent']:<16} {r['bb_per_100']:>+8.1f} bb/100 ± {r['ci95']:.1f}"
                   + (f"  ({r['timeouts']} timeouts)" if r["timeouts"] else ""))
    try:
        _say(args, f"Benchmarking {args.bot}: {args.hands} hands x 2 seats per opponent")
        result = bench(args.bot, opponents=args.opponents, hands=args.hands, workers=args.workers,
                       use_docker=True if args.docker else None, progress=[progress])
    except ValueError as e:
        _fail(str(e), args.json)
    if args.json:
        _print_json(result)
        return
    print(f"\n{result['bot']} ({result['hash']}): {result['overall_bb_per_100']:+.1f} bb/100 "
          f"± {result['overall_ci95']:.1f} (95% CI, average over {len(result['opponents'])} opponents)")
    if result["previous"]:
        print("\nPrevious benches (same opponents and hands):")
        for h in result["previous"]:
            tag = "  same code" if h["hash"] == result["hash"] else ""
            print(f"  {h['time']}  {h['hash']}  {h['overall_bb_per_100']:+8.1f} ± {h['overall_ci95']:.1f}{tag}")
        last = result["previous"][-1]
        diff = result["overall_bb_per_100"] - last["overall_bb_per_100"]
        print(f"  change vs last: {diff:+.1f} bb/100")


def cmd_serve(args):
    import uvicorn
    from arena.server import create_app
    url = f"http://127.0.0.1:{args.port}"
    print(f"Poker runner on {url}  (API docs: {url}/docs; Ctrl+C to stop)", file=sys.stderr)
    if args.open:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Only ever on this machine: bind to localhost.
    uvicorn.run(create_app(Path(args.out)), host="127.0.0.1", port=args.port, log_level="warning")


def cmd_bots(args):
    found = botlib.available()
    if args.json:
        _print_json([{"name": n, "path": p, "folder": f} for n, p, f in found])
        return
    for name, path, folder in found:
        print(f"{name:<22} {folder}/")


# ---------------------------------------------------------------------------

def _add_common_match_flags(p, run=False):
    p.add_argument("--seed", type=int, help="seed (same seed + bots + settings = same result)")
    p.add_argument("--time-limit-ms", type=int, help="per-decision time limit (default 2000)")
    p.add_argument("--stack", type=int, default=None if run else 10_000,
                   help="starting stack (default 10000)")
    p.add_argument("--docker", action="store_true", help="run bots in the Docker sandbox")
    p.add_argument("--pace", type=int, default=0, metavar="MS",
                   help="slow play down for watching: about MS milliseconds per action "
                        "(default 0 = full speed; ignored for series)")
    p.add_argument("--id", help="folder name for the results (default: generated)")
    p.add_argument("--out", default=str(DEFAULT_RUNS_DIR), help="results directory (default data/runs)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def build_parser():
    p = argparse.ArgumentParser(prog="poker", description="Poker bot matches and tournaments")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("match", help="play one table (2-9 bots)",
                       description="Play one table. Bots are names from my_bots/ or bots/, or paths.")
    m.add_argument("bots", nargs="+", help="2-9 bots: names (shark) or paths (my_bots/x/bot.py)")
    m.add_argument("--name", action="append", help="display name for each bot, in order (repeatable)")
    m.add_argument("--hands", type=int, default=100, help="hands to play (default 100)")
    m.add_argument("--cash", action="store_true",
                   help="every hand starts from the starting stack (nobody busts)")
    m.add_argument("--freezeout", action="store_true",
                   help="play until one bot has every chip (blinds rise; default structure 'standard')")
    m.add_argument("--max-hands", type=int, default=2000, help="freezeout safety cap (default 2000)")
    m.add_argument("--blinds", help="flat SB/BB[/ANTE] like 50/100, or a structure: "
                                    + ", ".join(structures.PRESETS))
    _add_common_match_flags(m)
    m.add_argument("-v", "--verbose", action="store_true", help="print progress while playing")
    m.set_defaults(func=cmd_match)

    r = sub.add_parser("run", help="run a tournament (optionally many times)",
                       description="Run a tournament from a YAML/JSON config and/or flags. "
                                   "Formats: " + ", ".join(formats.FORMATS) + ".")
    r.add_argument("config", nargs="?", help="config file (see configs/)")
    r.add_argument("--format", choices=formats.FORMATS)
    r.add_argument("--bots", nargs="+", help="bots (names or paths)")
    r.add_argument("--name", action="append", help="display name for each bot, in order")
    r.add_argument("--title", help="a name for this run")
    r.add_argument("--mode", choices=("fixed", "cash", "freezeout"), help="how each match is played")
    r.add_argument("--hands", type=int, help="hands per match (or freezeout cap)")
    r.add_argument("--blinds", help="SB/BB[/ANTE] or " + "|".join(structures.PRESETS))
    r.add_argument("--table-size", type=int, help="mtt: players per table (default 6)")
    r.add_argument("--duplicate", action=argparse.BooleanOptionalAction, default=None,
                   help="heads-up: replay each deal with seats swapped")
    r.add_argument("--seeding", choices=("random", "given"), help="bracket seeding")
    r.add_argument("--runs", type=int, help="play the tournament N times and aggregate (a series)")
    r.add_argument("--workers", type=int, help="matches/runs to play in parallel (default 1)")
    _add_common_match_flags(r, run=True)
    r.set_defaults(func=cmd_run)

    res = sub.add_parser("results", help="show the results of a match, run or series")
    res.add_argument("run", help="directory or id under data/runs")
    res.add_argument("--json", action="store_true")
    res.set_defaults(func=cmd_results)

    ls = sub.add_parser("runs", help="list recent matches, runs and series")
    ls.add_argument("--out", default=str(DEFAULT_RUNS_DIR))
    ls.add_argument("--limit", type=int, default=30)
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_runs)

    v = sub.add_parser("verify", help="check a match, run or series by replaying its logs")
    v.add_argument("run", help="directory, events.jsonl path, or id under data/runs")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)

    e = sub.add_parser("export", help="zip a match, run or series")
    e.add_argument("run", help="directory or id under data/runs")
    e.add_argument("-o", "--output", help="zip file to write (default <id>.zip)")
    e.add_argument("--bot", help="export for one bot: keep only its private data (logs, cards, ...)")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_export)

    bn = sub.add_parser("bench", help="score a bot against the reference bots",
                        description="Duplicate heads-up cash matches with fixed seeds against each "
                                    "opponent; reports bb/100 with 95%% CIs and keeps a history.")
    bn.add_argument("bot", help="bot name or path")
    bn.add_argument("--opponents", nargs="+", help="opponents (default: every bot in bots/)")
    bn.add_argument("--hands", type=int, default=500, help="hands per seat per opponent (default 500)")
    bn.add_argument("--workers", type=int, default=1)
    bn.add_argument("--docker", action="store_true")
    bn.add_argument("--json", action="store_true")
    bn.set_defaults(func=cmd_bench)

    sv = sub.add_parser("serve", help="start the web UI and API on this machine")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--out", default=str(DEFAULT_RUNS_DIR), help="results directory (default data/runs)")
    sv.add_argument("--open", action="store_true", help="open the UI in a browser")
    sv.set_defaults(func=cmd_serve)

    b = sub.add_parser("bots", help="list available bots")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_bots)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
