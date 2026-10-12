"""Tournament formats.

A *run* is one tournament: a format, a field of bots, and match settings. It
plays one or more matches (each with its own event log under
<run>/matches/<match_id>/) and produces standings, saved as <run>/run.json.

Formats:
  table        one table of 2-9 bots (heads-up is a table of 2)
  bracket      single-elimination heads-up knockout
  round_robin  every pair plays heads-up (optionally duplicate)
  mtt          multi-table freezeout with table balancing

Each format module exposes:
  DEFAULTS                    match settings that suit the format
  play(cfg, ctx) -> dict      play the run; returns the run result
  standings(cfg, records)     pure: standings from per-match records, so
                              `poker verify` can recompute them from the logs
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from arena import bots as botlib
from arena import structures
from arena.events import DEFAULT_RUNS_DIR
from arena.pacing import Cancelled
from arena.seeds import derive_seed, new_seed
from arena.table import MODES, MatchConfig, run_match

FORMATS = ("table", "bracket", "round_robin", "mtt")
RUN_FILE = "run.json"


@dataclass
class RunConfig:
    format: str
    bots: list
    names: list = None                 # optional display names, same order as bots
    mode: str = None                   # fixed | cash | freezeout (format default if None)
    hands: int = None                  # hands per match (fixed/cash) or safety cap (freezeout)
    starting_stack: int = 10_000
    blinds: str = None                 # "50/100[/ante]" or turbo|standard|slow
    time_limit_ms: int = None
    seed: int = None
    table_size: int = 6                # mtt
    duplicate: bool = None             # round_robin / heads-up table: replay deals with seats swapped
    seeding: str = "random"            # bracket: random | given (list order = seed order)
    name: str = None

    def __post_init__(self):
        if self.format not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}, got {self.format!r}")
        if not isinstance(self.bots, list) or not self.bots:
            raise ValueError("bots must be a non-empty list")
        defaults = _module(self.format).DEFAULTS
        for key, value in defaults.items():
            if getattr(self, key) is None:
                setattr(self, key, value)
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.seeding not in ("random", "given"):
            raise ValueError("seeding must be 'random' or 'given'")
        if not 2 <= self.table_size <= 9:
            raise ValueError("table_size must be 2-9")
        structures.parse(self.blinds, self.starting_stack)   # validate early

    def match_config(self, seed: int) -> MatchConfig:
        return MatchConfig(mode=self.mode, hands=self.hands, starting_stack=self.starting_stack,
                           structure=structures.parse(self.blinds, self.starting_stack),
                           seed=seed, time_limit_ms=self.time_limit_ms)

    def to_dict(self):
        return asdict(self)


RUN_OPTIONS = ("runs", "workers", "docker")


def load_config(path: str, overrides: dict = None) -> tuple:
    """Read a YAML/JSON run config. Returns (RunConfig, options) where options
    holds runs / workers / docker. `overrides` (e.g. from CLI flags) win."""
    text = Path(path).read_text()
    data = yaml.safe_load(text) if str(path).endswith((".yaml", ".yml")) else json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of settings")
    data.update({k: v for k, v in (overrides or {}).items() if v is not None})
    unknown = set(data) - set(RunConfig.__dataclass_fields__) - set(RUN_OPTIONS)
    if unknown:
        raise ValueError(f"{path}: unknown settings {sorted(unknown)}")
    options = {k: data.pop(k, None) for k in RUN_OPTIONS}
    return RunConfig(**data), options


def _module(fmt):
    if fmt == "table":
        from arena.formats import table_fmt as m
    elif fmt == "bracket":
        from arena.formats import bracket as m
    elif fmt == "round_robin":
        from arena.formats import round_robin as m
    elif fmt == "mtt":
        from arena.formats import mtt as m
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return m


@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    seed: int
    specs: list                       # BotSpec per bot, unique names
    workers: int = 1
    use_docker: bool = None
    progress: list = field(default_factory=list)   # callables(dict) for status updates
    pace_ms: int = 0                                 # slow matches down for live viewing
    cancel: object = None                            # threading.Event: stop the run
    _matches: int = 0

    def match_seed(self, *key) -> int:
        return derive_seed(self.seed, "match", *key)

    def play(self, specs, match_cfg: MatchConfig, match_id: str) -> dict:
        """Play one match inside this run; returns its summary."""
        for fn in self.progress:
            fn({"event": "match_start", "match_id": match_id})
        summary = run_match(specs, match_cfg, match_id=match_id,
                            runs_dir=self.run_dir / "matches", use_docker=self.use_docker,
                            pace_ms=self.pace_ms, cancel=self.cancel)
        self._matches += 1
        for fn in self.progress:
            fn({"event": "match_done", "match_id": match_id, "matches_done": self._matches})
        return summary

    def parallel(self, fn, items) -> list:
        if self.workers <= 1 or len(items) <= 1:
            return [fn(x) for x in items]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(pool.map(fn, items))

    def save(self, result: dict):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.run_dir / (RUN_FILE + ".tmp")
        tmp.write_text(json.dumps(result, indent=2, default=str))
        tmp.replace(self.run_dir / RUN_FILE)


def match_record(summary: dict, **extra) -> dict:
    """The parts of a match summary that standings are computed from."""
    return {"match_id": summary["match_id"], **extra,
            "results": [{k: r[k] for k in ("bot", "place", "final_stack", "delta",
                                             "hands_played", "bb_per_100", "busted_on_hand",
                                             "timeouts", "errors")}
                        for r in summary["results"]],
            "hands": summary["hands"], "stopped": summary["stopped"]}


def run(cfg: RunConfig, run_id: str = None, runs_dir: Path = DEFAULT_RUNS_DIR,
        workers: int = 1, use_docker: bool = None, progress=(), pace_ms: int = 0,
        cancel=None) -> dict:
    """Play one run of `cfg` and save run.json. Returns the run result."""
    seed = cfg.seed if cfg.seed is not None else new_seed()
    cfg.seed = seed
    specs = botlib.resolve_all(cfg.bots, names=cfg.names)
    run_id = run_id or f"{cfg.format}_{time.strftime('%Y%m%d_%H%M%S')}_{seed % 10**6:06d}"
    ctx = RunContext(run_id=run_id, run_dir=Path(runs_dir) / run_id, seed=seed, specs=specs,
                     workers=max(1, workers), use_docker=use_docker, progress=list(progress),
                     pace_ms=pace_ms or 0, cancel=cancel)
    module = _module(cfg.format)
    t0 = time.time()
    base = {"kind": "run", "run_id": run_id, "format": cfg.format, "name": cfg.name,
            "seed": seed, "config": cfg.to_dict(), "bots": [s.to_dict() for s in specs],
            "run_dir": str(ctx.run_dir)}
    ctx.save({**base, "status": "running"})
    try:
        result = module.play(cfg, ctx)
    except Cancelled:
        ctx.save({**base, "status": "cancelled", "duration_s": round(time.time() - t0, 2)})
        raise
    except BaseException as e:
        ctx.save({**base, "status": "error", "error": repr(e),
                  "duration_s": round(time.time() - t0, 2)})
        raise
    result = {**base, "status": "complete", **result, "duration_s": round(time.time() - t0, 2)}
    ctx.save(result)
    return result


def standings(cfg: RunConfig, records: list) -> list:
    return _module(cfg.format).standings(cfg, records)


def two_player_winner(record: dict, tiebreak_seed: int) -> tuple:
    """(winner, loser, how) for a heads-up match record. A tie on chips is
    settled by a seeded coin flip, recorded as how='coin_flip'."""
    a, b = record["results"]
    if a["final_stack"] != b["final_stack"]:
        w, l = (a, b) if a["final_stack"] > b["final_stack"] else (b, a)
        return w["bot"], l["bot"], "chips"
    names = sorted([a["bot"], b["bot"]])
    flip = tiebreak_seed % 2
    return names[flip], names[1 - flip], "coin_flip"
