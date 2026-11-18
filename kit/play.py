#!/usr/bin/env python3
"""Play one match between two (or more) bots, on the same table code, blind
structure and sandbox as the tournament.

  python3 play.py my_bots/mybot bots/caller              # heads-up freezeout, standard blinds
  python3 play.py my_bots/mybot bots/caller --show       # print every hand as it's played
  python3 play.py my_bots/mybot bots/caller --docker     # inside the tournament's Docker sandbox
  python3 play.py my_bots/mybot bots/caller --seed 7     # the same seed deals the same cards

A bot is a folder with bot.py (and optionally data/), a bot.py file, or a .zip.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pokercore import core_hash, structures
from pokercore.seats import BotSeat
from pokercore.seeds import derive_seed, new_seed
from pokercore.table import MODES, MatchConfig, play_match


@dataclass
class Bot:
    name: str
    path: str
    hash: str = None
    source: str = "local"


class Log:
    """Collects a match's events; optionally prints hands or writes JSON lines."""

    def __init__(self, show=False, out=None):
        self.n = 0
        self.show = show
        self.out = open(out, "w") if out else None

    def emit(self, event_type, **data):
        event = {"i": self.n, "type": event_type, **data}
        self.n += 1
        if self.out:
            self.out.write(json.dumps(event, default=str) + "\n")
        if self.show:
            _print_event(event)
        return event

    def close(self):
        if self.out:
            self.out.close()


def _print_event(e):
    t = e["type"]
    if t == "level":
        print(f"-- level {e['level']}: blinds {e['sb']}/{e['bb']}" + (f", ante {e['ante']}" if e["ante"] else ""))
    elif t == "hand_start":
        stacks = ", ".join(f"{n} {s:,}" for n, s in e.get("stacks", {}).items())
        print(f"\nHand {e['hand'] + 1}  ({stacks})")
    elif t == "decision":
        a = e["applied"]
        cards = " ".join(e["state"].get("your_cards") or [])
        amount = f" {a['amount']:,}" if a.get("amount") else ""
        problem = f"  [{e['reply'].get('error')}]" if e.get("reply", {}).get("error") else ""
        print(f"  {e['street']:<7} {e['bot']} [{cards}] {a['action']}{amount}{problem}")
    elif t == "deal":
        print(f"  board   {' '.join(e['board'])}")
    elif t == "showdown":
        print("  showdown: " + ", ".join(f"{n} {' '.join(c)}" for n, c in (e.get("reveal") or {}).items()))
    elif t == "award":
        print(f"  {e['bot']} wins {e['amount']:,}")
    elif t == "elimination":
        print(f"** {e['bot']} is out (place {e['place']})")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("bots", nargs="+", help="2-9 bot folders, bot.py files or .zip archives")
    p.add_argument("--mode", choices=MODES, default="freezeout",
                   help="freezeout (default): until one bot has every chip; fixed: N hands, stacks carry "
                        "over; cash: N hands, every hand from the starting stack")
    p.add_argument("--hands", type=int, default=2000, help="hands to play (fixed/cash) or safety cap (freezeout)")
    p.add_argument("--blinds", default="standard",
                   help="turbo, standard (default) or slow (rising), or flat blinds like 50/100")
    p.add_argument("--stack", type=int, default=10_000, help="starting stack (default 10,000)")
    p.add_argument("--seed", type=int, help="match seed (default: random); the same seed deals the same cards")
    p.add_argument("--docker", action="store_true", help="run bots in the Docker sandbox (make sandbox first)")
    p.add_argument("--time-limit-ms", type=int, default=None, help="per decision (default 2000)")
    p.add_argument("--show", action="store_true", help="print every hand")
    p.add_argument("--log", metavar="FILE", help="write every event, including what each bot saw, as JSON lines")
    args = p.parse_args(argv)

    if not 2 <= len(args.bots) <= 9:
        p.error("a table needs 2-9 bots")
    bots, names = [], set()
    for path in args.bots:
        bp = Path(path)
        if bp.name == "bot.py":
            base = bp.resolve().parent.name
        elif bp.suffix in (".py", ".zip"):
            base = bp.stem
        else:
            base = bp.resolve().name
        name, k = base, 2
        while name in names:
            name, k = f"{base}_{k}", k + 1
        names.add(name)
        bots.append(Bot(name, path))

    seed = args.seed if args.seed is not None else new_seed()
    config = MatchConfig(mode=args.mode, hands=args.hands, starting_stack=args.stack,
                         structure=structures.parse(args.blinds, args.stack), seed=seed,
                         time_limit_ms=args.time_limit_ms)
    seats = [BotSeat(b, seed=derive_seed(seed, "bot", b.name), time_limit_ms=args.time_limit_ms,
                     use_docker=args.docker) for b in bots]

    print(f"{' vs '.join(b.name for b in bots)}: {args.mode}, blinds {args.blinds}, "
          f"stack {args.stack:,}, seed {seed}" + (", Docker sandbox" if args.docker else "")
          + f"  (core {core_hash()})")
    log = Log(show=args.show, out=args.log)
    try:
        result = play_match(seats, config, "local", log)
    except KeyboardInterrupt:
        print("\nstopped")
        return 130
    finally:
        log.close()

    print(f"\n{result['hands']} hands ({result['stopped'].replace('_', ' ')})")
    for r in result["results"]:
        flags = [f"{r[k]} {k}" for k in ("timeouts", "errors", "restarts") if r.get(k)]
        print(f"  {r['place'] or '-'}. {r['bot']:<20} {r['final_stack']:>9,}  ({r['delta']:+,})"
              f"  avg think {r.get('avg_think_ms') or 0:.0f} ms" + (f"  {', '.join(flags)}" if flags else ""))
    problems = False
    for s in seats:
        errs = s.summary().get("errors_list") or []
        if errs:
            problems = True
            print(f"\n{s.name} had problems: {', '.join(errs[:5])}" + (" ..." if len(errs) > 5 else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
