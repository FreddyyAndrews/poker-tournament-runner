"""`poker bench`: a fixed benchmark for "is my bot getting better?".

The bot plays a duplicate heads-up cash match against each opponent (by
default every reference bot in bots/): the same deals twice with the seats
swapped, every hand starting from 100 big blinds. Seeds are fixed, so every
version of a bot faces exactly the same cards and the scores are comparable
from one version to the next.

Score: bb/100 against each opponent, with a 95% confidence interval computed
from duplicate hand pairs (pairing the same deal from both seats cancels most
of the card luck), and the average across opponents. Every bench is appended
to data/bench/<bot>.jsonl with the bot's content hash, so you can see how each
version did.

Opponents may include the external bots, slumbot and gto_wizard (never by
default). They deal on their own servers, so there are no fixed seeds or
duplicate: the bot plays 2 x hands hands against each, and the score is
their luck-adjusted bb/100 (Slumbot's baseline, GTO Wizard's AIVAT), with
its 95% CI. Raw chips are kept as chips_bb_per_100.
"""

import json
import math
import time
from pathlib import Path

from arena import bots as botlib
from arena import external, structures
from arena.events import read_events
from arena.seeds import derive_seed
from arena.stats import mean_ci
from arena.table import MatchConfig, run_match

BENCH_SEED = 20261001
BENCH_DIR = Path(__file__).resolve().parent.parent / "data" / "bench"
STACK_BB = 100


def default_opponents(exclude: str) -> list:
    return [name for name, _, folder in botlib.available()
            if folder == "bots" and name not in (exclude, "template")]


def _hand_deltas_bb(run_dir: Path, bot: str) -> list:
    out = []
    for e in read_events(run_dir):
        if e["type"] == "hand_start":
            bb = e["blinds"]["bb"]
        elif e["type"] == "hand_end":
            out.append(e["deltas"][bot] / bb)
    return out


def bench(bot: str, opponents=None, hands: int = 500, workers: int = 1, use_docker=None,
          bench_dir: Path = BENCH_DIR, progress=()) -> dict:
    (spec,) = botlib.resolve_all([bot])
    opponents = opponents or default_opponents(spec.name)
    ext = [external.find(o) for o in opponents if external.find(o)]
    for o in ext:
        try:
            o.check_configured()
            external.check_budget(o, spec.name, 2 * hands)
        except external.ExternalError as e:
            raise ValueError(str(e)) from None
    opp_specs = botlib.resolve_all([o for o in opponents if not external.find(o)]) + ext
    if any(o.name == spec.name for o in opp_specs):
        opp_specs = [o for o in opp_specs if o.name != spec.name]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    runs_dir = Path(bench_dir) / "runs" / f"{spec.name}_{stamp}"
    structure = structures.flat(50, 100)
    t0 = time.time()

    def play_external(opp):
        s = external.run_external_match(opp, spec, 2 * hands, match_id=f"vs_{opp.name}",
                                        runs_dir=runs_dir, use_docker=use_docker)
        if s["stopped"] != "hand_limit":
            raise RuntimeError(f"bench match against {opp.name} stopped: {s['stopped']}")
        mine = next(r for r in s["results"] if r["bot"] == spec.name)
        score = s["score"][spec.name]
        result = {"opponent": opp.name, "opponent_hash": None, "external": True,
                  "score_kind": score["kind"], "hands": s["hands"],
                  "bb_per_100": score["bb_per_100"], "ci95": score["ci95"],
                  "chips_bb_per_100": mine["bb_per_100"],
                  "timeouts": mine["timeouts"] or 0, "errors": mine["errors"] or 0,
                  "matches": [s["match_id"]]}
        for fn in progress:
            fn(result)
        return result

    def play_opponent(opp):
        if isinstance(opp, external.Opponent):
            return play_external(opp)
        seed = derive_seed(BENCH_SEED, "bench", opp.name, hands)
        legs = []
        for leg, order in ((1, [spec, opp]), (2, [opp, spec])):
            cfg = MatchConfig(mode="cash", hands=hands, starting_stack=STACK_BB * 100,
                              structure=structure, seed=seed)
            summary = run_match(order, cfg, match_id=f"vs_{opp.name}_leg{leg}", runs_dir=runs_dir,
                                use_docker=use_docker)
            legs.append(summary)
        a = _hand_deltas_bb(Path(legs[0]["run_dir"]), spec.name)
        b = _hand_deltas_bb(Path(legs[1]["run_dir"]), spec.name)
        pairs = [x + y for x, y in zip(a, b)]          # same deal from both seats
        mean, half, n = mean_ci(pairs)
        # per-pair mean is bb per 2 hands -> bb/100 hands
        result = {"opponent": opp.name, "opponent_hash": opp.hash, "hands": len(a) + len(b),
                  "bb_per_100": round(mean * 50, 2), "ci95": round(half * 50, 2),
                  "timeouts": sum(next(r for r in s["results"] if r["bot"] == spec.name)["timeouts"] or 0
                                  for s in legs),
                  "errors": sum(next(r for r in s["results"] if r["bot"] == spec.name)["errors"] or 0
                                for s in legs),
                  "matches": [s["match_id"] for s in legs]}
        for fn in progress:
            fn(result)
        return result

    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            per_opp = list(pool.map(play_opponent, opp_specs))
    else:
        per_opp = [play_opponent(o) for o in opp_specs]

    k = len(per_opp)
    overall = round(sum(r["bb_per_100"] for r in per_opp) / k, 2) if k else 0.0
    overall_ci = round(math.sqrt(sum(r["ci95"] ** 2 for r in per_opp)) / k, 2) if k else 0.0
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "bot": spec.name, "hash": spec.hash,
             "path": spec.path, "hands_per_leg": hands, "seed": BENCH_SEED,
             "overall_bb_per_100": overall, "overall_ci95": overall_ci,
             "opponents": per_opp, "duration_s": round(time.time() - t0, 2),
             "runs_dir": str(runs_dir)}
    history_file = Path(bench_dir) / f"{spec.name}.jsonl"
    history = load_history(spec.name, bench_dir)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "a") as fh:
        fh.write(json.dumps(entry) + "\n")
    previous = [h for h in history if h["hands_per_leg"] == hands and
                sorted(o["opponent"] for o in h["opponents"]) == sorted(o["opponent"] for o in per_opp)]
    return {**entry, "previous": previous[-5:]}


def load_history(bot_name: str, bench_dir: Path = BENCH_DIR) -> list:
    f = Path(bench_dir) / f"{bot_name}.jsonl"
    if not f.is_file():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
