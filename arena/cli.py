"""`poker` command line. Every command takes --json for machine-readable output."""

import argparse
import json
import sys
from pathlib import Path

from arena import bots as botlib
from arena import formats, structures
from arena.events import DEFAULT_RUNS_DIR, SUMMARY_FILE
from arena.seats import Seat
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

def _check_external_args(args):
    """A match against an external bot (GTO Wizard, Slumbot): its game is
    fixed, so refuse the flags that would change it. Returns the opponent,
    the other player's spec and its index."""
    from arena import external
    opponent, hero, idx = external.split_specs(args.bots)
    fixed = [flag for flag, given in (("--freezeout", args.freezeout), ("--blinds", args.blinds),
                                      ("--seed", args.seed is not None),
                                      ("--stack", args.stack != 10_000)) if given]
    if fixed:
        raise ValueError(f"{opponent.title} sets the game (heads-up, 200bb at 50/100, stacks "
                         f"reset every hand), so {', '.join(fixed)} can't be used")
    opponent.check_configured()
    return opponent, hero, idx


def cmd_match(args):
    from arena import external
    if any(external.find(b) for b in args.bots):
        try:
            opponent, hero, idx = _check_external_args(args)
            if args.local:
                names = args.name or []
                spec = botlib.resolve_all([hero], names=[names[idx] if idx < len(names) else None])[0]
        except (ValueError, external.ExternalError) as e:
            _fail(str(e), args.json)
        live = _live_printer() if args.verbose and not args.json else None
        if args.local:
            summary = external.run_external_match(
                opponent, spec, args.hands, match_id=args.id,
                runs_dir=Path(args.out or DEFAULT_RUNS_DIR), use_docker=True if args.docker else None,
                listeners=[live] if live else [], pace_ms=args.pace,
                time_limit_ms=args.time_limit_ms, workers=args.workers)
        else:
            summary = _match_on_server(args, live)
        if args.json:
            _print_json(summary)
        else:
            _print_match(summary)
        if str(summary.get("stopped", "")).startswith(("error", "cancelled")):
            sys.exit(1)
        return
    if args.workers:
        _fail("--workers only applies to matches against gto_wizard or slumbot", args.json)
    try:
        specs = botlib.resolve_all(args.bots, names=args.name, allow_remote=not args.local)
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
    if args.local:
        summary = run_match(specs, config, match_id=args.id, runs_dir=Path(args.out or DEFAULT_RUNS_DIR),
                            use_docker=True if args.docker else None,
                            listeners=[live] if live else [], pace_ms=args.pace)
    else:
        summary = _match_on_server(args, live)
    if args.json:
        _print_json(summary)
    else:
        _print_match(summary)
    if str(summary.get("stopped", "")).startswith(("error", "cancelled")):
        sys.exit(1)


# ---------------------------------------------------------------------------
# Running on the server (the default for match and run)
# ---------------------------------------------------------------------------

def _server(args):
    """Connect to the server, checking --out against where it writes."""
    from arena.client import Server, ServerUnavailable
    server = Server(args.server)
    try:
        health = server.health()
    except ServerUnavailable as e:
        _fail(f"{e}. Start one with `poker serve`, or use --local to run in this process.", args.json)
    if args.out and Path(args.out).resolve() != Path(health["runs_dir"]).resolve():
        _fail(f"the server at {server.url} writes results to {health['runs_dir']}; "
              f"--out only applies with --local", args.json)
    return server


def _server_bots(specs):
    """Bot specs as the server should see them: names stay names, paths become
    absolute (the server may run from another directory)."""
    out = []
    for spec in specs:
        p = Path(spec).expanduser()
        out.append(str(p.resolve()) if not botlib.remote_kind(spec) and p.exists() else spec)
    return out


def _follow_job(server, job_id, args):
    """Wait for a job; Ctrl+C cancels it on the server. Returns the final job."""
    import time as _t
    try:
        while True:
            job = server.job(job_id)
            if job["status"] not in ("queued", "running"):
                return job
            _t.sleep(0.25)
    except KeyboardInterrupt:
        server.cancel(job_id)
        _say(args, "\nCancelling on the server...")
        while server.job(job_id)["status"] in ("queued", "running"):
            _t.sleep(0.2)
        return server.job(job_id)


def _match_on_server(args, live):
    import time as _t
    from arena.client import ApiError
    server = _server(args)
    body = {"bots": _server_bots(args.bots), "names": args.name,
            "mode": "freezeout" if args.freezeout else ("cash" if args.cash else "fixed"),
            "hands": args.max_hands if args.freezeout else args.hands,
            "starting_stack": args.stack, "blinds": args.blinds, "seed": args.seed,
            "time_limit_ms": args.time_limit_ms, "docker": bool(args.docker),
            "pace_ms": args.pace, "id": args.id, "workers": args.workers}
    try:
        started = server.start_match({k: v for k, v in body.items() if v is not None})
    except ApiError as e:
        _fail(e.detail, args.json)
    key = started["match_key"]
    _say(args, f"Playing on {server.url}  (watch: {server.watch_url(key=key)})")
    for seat in started.get("seats", []):
        how = ("take it with the MCP tools (wait_for_turn / act)" if seat["kind"] == "mcp"
               else f"play it at {server.watch_url(key=key)}?seat={seat['token']}&as={seat['name']}")
        # always shown (even with --json): without the token nobody can take the seat
        print(f"  {seat['kind']} seat {seat['name']}: token {seat['token']} - {how}",
              file=sys.stderr, flush=True)
    try:
        for event in server.stream(key):
            if live:
                live(event)
    except KeyboardInterrupt:
        server.cancel(key)
        _say(args, "\nCancelling on the server...")
    job = _follow_job(server, key, args)
    for _ in range(100):                           # the summary is written just after match_end
        meta = server.match(key)
        if meta.get("summary"):
            return meta["summary"]
        _t.sleep(0.1)
    _fail(f"match {key} ended without results ({job['status']}: {job.get('error')})", args.json, code=1)


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
    if not s.get("results"):
        print(f"\nMatch {s['match_id']} stopped ({s['stopped']}) before it produced results.")
        print(f"Log so far: {s['run_dir']}")
        return
    dealer = next((x.get("title") for x in s.get("seats", []) if x["name"] == s.get("dealer")), None)
    dealt = f"dealt by {dealer or s['dealer']}" if s.get("dealer") else f"seed {s['seed']}"
    print(f"\nMatch {s['match_id']}  ({cfg['mode']}, {s['hands']} hands, {dealt}, "
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
    for bot, a in (s.get("score") or {}).items():
        if a.get("bb_per_100") is not None:
            ci = f" +/- {a['ci95']}" if a.get("ci95") is not None else ""
            print(f"\nLuck-adjusted ({a.get('kind')}) for {bot}: {a['bb_per_100']:+.1f} bb/100{ci} "
                  f"(95% CI, {a['hands']} hands). Chips won are noisier: judge by this.")
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
    if not args.local:
        result = _run_on_server(args, cfg, runs, workers, use_docker)
        if args.json:
            _print_json(result)
        else:
            _print_result(result)
        if result.get("status") != "complete":
            sys.exit(1)
        return
    args.out = args.out or str(DEFAULT_RUNS_DIR)
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


def _run_on_server(args, cfg, runs, workers, use_docker):
    from arena.client import ApiError
    server = _server(args)
    body = {**{k: v for k, v in cfg.to_dict().items() if v is not None},
            "bots": _server_bots(cfg.bots), "runs": runs, "workers": workers,
            "docker": bool(use_docker), "pace_ms": args.pace, "id": args.id}
    try:
        started = server.start_run({k: v for k, v in body.items() if v is not None})
    except ApiError as e:
        _fail(e.detail, args.json)
    run_id = started["run_id"]
    _say(args, f"Running on {server.url}  (watch: {server.watch_url(run_id=run_id)})")

    seen = {"matches_done": 0, "runs_done": 0}

    def progress(job):
        for field, label in (("matches_done", "matches"), ("runs_done", "runs")):
            if job.get(field, 0) > seen[field]:
                seen[field] = job[field]
                _say(args, f"  {job[field]}{f'/{runs}' if field == 'runs_done' else ''} {label} done")

    import time as _t
    try:
        while True:
            job = server.job(run_id)
            progress(job)
            if job["status"] not in ("queued", "running"):
                break
            _t.sleep(0.3)
    except KeyboardInterrupt:
        server.cancel(run_id)
        _say(args, "\nCancelling on the server...")
        _follow_job(server, run_id, args)
    data = server.run(run_id)
    result = data.get("data") or {}
    result.setdefault("status", data.get("status"))
    if data.get("status") != "complete":
        job = server.job(run_id)
        _fail(f"run {run_id} {data.get('status')}" + (f": {job['error']}" if job.get("error") else ""),
              args.json, code=1)
    return result


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
        how = (f"  ({r['score_kind']}, luck-adjusted; chips {r['chips_bb_per_100']:+.1f})"
               if r.get("external") else "")
        _say(args, f"  vs {r['opponent']:<16} {r['bb_per_100']:>+8.1f} bb/100 ± {r['ci95'] or 0:.1f}"
                   + how + (f"  ({r['timeouts']} timeouts)" if r["timeouts"] else ""))
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


class TerminalSeat(Seat):
    """A human seat played in the terminal (poker play). No time limit."""

    kind = "human"

    def __init__(self, name):
        super().__init__(name, 10 * 60 * 1000)
        self.decisions = 0

    def summary(self):
        return {"decisions": self.decisions, "timeouts": 0, "errors": 0}

    def request_action(self, state):
        import time as _t
        from arena.server.mcp_server import describe, legal_actions
        print("\n" + describe(state))
        legal = legal_actions(state)
        hint = "f=fold " if "fold" in legal else ""
        hint += "x=check " if "check" in legal else f"c=call {legal['call']['amount']} "
        if "raise" in legal:
            hint += f"r <amount>=raise to ({legal['raise']['min_to']}..{legal['raise']['max_to']}) "
        hint += "a=all-in"
        t0 = _t.time()
        while True:
            try:
                raw = input(f"[{hint}] > ").strip().lower()
            except EOFError:
                raw = "f"
            parts = raw.split()
            if not parts:
                continue
            word = {"f": "fold", "x": "check", "k": "check", "c": "call", "r": "raise", "a": "all_in",
                    "allin": "all_in"}.get(parts[0], parts[0])
            if word == "raise":
                if len(parts) < 2 or not parts[1].isdigit():
                    print("  raise needs an amount, e.g. r 600")
                    continue
                reply = {"action": "raise", "amount": int(parts[1])}
            elif word in ("fold", "check", "call", "all_in"):
                reply = {"action": word}
            else:
                print("  unknown action")
                continue
            self.decisions += 1
            reply["think_ms"] = round((_t.time() - t0) * 1000, 1)
            return reply


def cmd_play(args):
    """You against one or more bots, in the terminal."""
    try:
        specs = botlib.resolve_all(args.bots)
        if args.freezeout:
            mode, hands, blinds = "freezeout", args.max_hands, args.blinds or "standard"
        else:
            mode, hands, blinds = ("fixed" if args.fixed else "cash"), args.hands, args.blinds or "50/100"
        config = MatchConfig(mode=mode, hands=hands, starting_stack=args.stack,
                             structure=structures.parse(blinds, args.stack), seed=args.seed)
    except ValueError as e:
        _fail(str(e))
    if args.name in [s.name for s in specs]:
        _fail(f"{args.name!r} is also a bot name; pick another with --name")
    me = TerminalSeat(args.name)

    def show(e):
        t = e["type"]
        if t == "hand_start":
            print(f"\n=== Hand {e['hand'] + 1}  blinds {e['blinds']['sb']}/{e['blinds']['bb']}  "
                  f"your cards: {' '.join(e['hole'].get(args.name, []))} ===")
        elif t == "decision" and e["bot"] != args.name:
            a = e["applied"]
            print(f"  {e['bot']}: {a['action'].replace('_', ' ')}" + (f" {a['amount']}" if a["amount"] else ""))
        elif t == "deal":
            print(f"  -- {e['street']}: {' '.join(e['board'])}")
        elif t == "showdown":
            for n, c in e["reveal"].items():
                print(f"  {n} shows {' '.join(c)} ({e['ranks'].get(n)})")
        elif t == "award":
            print(f"  {e['bot']} wins {e['amount']}")

    seats = [me, *specs] if not args.last else [*specs, me]
    summary = run_match(seats, config, match_id=args.id, runs_dir=Path(args.out), listeners=[show])
    _print_match(summary)


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
    print(f"LLM seats over MCP: claude mcp add --transport http poker {url}/mcp", file=sys.stderr)
    app = create_app(Path(args.out), port=args.port)

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # Cancel running jobs first: their matches then end within moments,
            # which also ends live streams, so the graceful shutdown that follows
            # (closing connections, then waiting for jobs to write their files)
            # doesn't wait on a paced match.
            if not app.state.jobs.stopping:
                n = app.state.jobs.begin_shutdown()
                if n:
                    print(f"\nStopping: cancelling {n} running job(s) and saving their results...",
                          file=sys.stderr, flush=True)
            super().handle_exit(sig, frame)

    Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")).run()


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
    p.add_argument("--server", help="poker server to run on (default $POKER_SERVER or "
                                    "http://127.0.0.1:8765; start one with `poker serve`)")
    p.add_argument("--local", action="store_true",
                   help="run in this process instead of on the server (no live view, no "
                        "web cancel; for tests, CI and offline use)")
    p.add_argument("--out", help="with --local: results directory (default data/runs)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def build_parser():
    p = argparse.ArgumentParser(prog="poker", description="Poker bot matches and tournaments")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("match", help="play one table (2-9 bots), or one bot against gto_wizard/slumbot",
                       description="Play one table. Bots are names from my_bots/ or bots/, or paths. "
                                   "`poker match my_bot gto_wizard` plays GTO Wizard AI heads-up "
                                   "through its API (needs GTO_WIZARD_API_KEY); `slumbot` plays "
                                   "Slumbot the same way (free, no key). "
                                   "Runs on the poker server (watch it live in the web UI) unless --local.")
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
    m.add_argument("--workers", type=int,
                   help="against gto_wizard or slumbot: hands played at once (default 5 for "
                        "gto_wizard, at most 20; 2 for slumbot, at most 3; 1 with --pace or a "
                        "human/MCP seat)")
    m.add_argument("-v", "--verbose", action="store_true", help="print progress while playing")
    m.set_defaults(func=cmd_match)

    r = sub.add_parser("run", help="run a tournament (optionally many times)",
                       description="Run a tournament from a YAML/JSON config and/or flags. "
                                   "Formats: " + ", ".join(formats.FORMATS) + ". "
                                   "Runs on the poker server unless --local.")
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
    bn.add_argument("--opponents", nargs="+",
                    help="opponents (default: every bot in bots/); may include slumbot and "
                         "gto_wizard, played through their APIs and scored luck-adjusted")
    bn.add_argument("--hands", type=int, default=500, help="hands per seat per opponent (default 500)")
    bn.add_argument("--workers", type=int, default=1)
    bn.add_argument("--docker", action="store_true")
    bn.add_argument("--json", action="store_true")
    bn.set_defaults(func=cmd_bench)

    pl = sub.add_parser("play", help="play against bots yourself, in the terminal",
                        description="You take a seat against 1-8 bots. Cash hands by default.")
    pl.add_argument("bots", nargs="+", help="opponents: names or paths")
    pl.add_argument("--name", default="you", help="your name at the table (default: you)")
    pl.add_argument("--hands", type=int, default=20, help="hands to play (default 20)")
    pl.add_argument("--fixed", action="store_true", help="stacks carry over between hands")
    pl.add_argument("--freezeout", action="store_true", help="play until one player has every chip")
    pl.add_argument("--max-hands", type=int, default=500)
    pl.add_argument("--stack", type=int, default=10_000)
    pl.add_argument("--blinds", help="SB/BB[/ANTE] or a structure")
    pl.add_argument("--seed", type=int)
    pl.add_argument("--last", action="store_true", help="sit after the bots instead of in seat 1")
    pl.add_argument("--id", help="match id (default: generated)")
    pl.add_argument("--out", default=str(DEFAULT_RUNS_DIR))
    pl.set_defaults(func=cmd_play)

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
