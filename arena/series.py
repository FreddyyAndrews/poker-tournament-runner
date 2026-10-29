"""Series: run the same tournament format many times with different seeds and
aggregate the results, e.g. "across 100 heads-up brackets, shark won 37 times
(95% CI 28-47%)".

Run i uses seed derive_seed(series_seed, "run", i), so the whole series is
reproducible from one seed. Runs are played in parallel (`workers`); each run
plays its own matches one at a time.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from arena import formats
from arena.events import DEFAULT_RUNS_DIR
from arena.seeds import derive_seed, new_seed
from arena.stats import mean_ci, wilson

SERIES_FILE = "series.json"


def aggregate(runs: list) -> list:
    """Per-bot results across runs, best first."""
    per_bot = {}
    for r in runs:
        for row in r["standings"]:
            b = per_bot.setdefault(row["bot"], {"places": [], "deltas": [], "bb": []})
            b["places"].append(row["place"])
            b["deltas"].append(row.get("delta", 0))
            if row.get("hands_played"):
                b["bb"].append(row.get("bb_per_100", 0.0))
    n_runs = len(runs)
    out = []
    for bot, b in per_bot.items():
        wins = sum(1 for p in b["places"] if p == 1)
        rate, lo, hi = wilson(wins, n_runs)
        top3 = sum(1 for p in b["places"] if p is not None and p <= 3)
        avg_place, place_ci, _ = mean_ci([p for p in b["places"] if p is not None])
        bb, bb_ci, _ = mean_ci(b["bb"])
        dist = {}
        for p in b["places"]:
            dist[str(p)] = dist.get(str(p), 0) + 1
        out.append({
            "bot": bot, "runs": n_runs, "wins": wins,
            "win_rate": round(rate, 4), "win_rate_ci": [round(lo, 4), round(hi, 4)],
            "top3_rate": round(top3 / n_runs, 4) if n_runs else 0.0,
            "avg_place": round(avg_place, 3), "avg_place_ci": round(place_ci, 3),
            "avg_delta": round(sum(b["deltas"]) / n_runs, 1) if n_runs else 0.0,
            "bb_per_100": round(bb, 2), "bb_per_100_ci": round(bb_ci, 2),
            "place_counts": dict(sorted(dist.items(), key=lambda kv: int(kv[0]) if kv[0] != "None" else 99)),
        })
    out.sort(key=lambda r: (-r["wins"], r["avg_place"], r["bot"]))
    return out


def run_series(cfg: formats.RunConfig, runs: int, series_id: str = None,
               runs_dir: Path = DEFAULT_RUNS_DIR, workers: int = 1, use_docker: bool = None,
               progress=(), cancel=None) -> dict:
    if runs < 1:
        raise ValueError("runs must be >= 1")
    seed = cfg.seed if cfg.seed is not None else new_seed()
    cfg.seed = seed
    series_id = series_id or f"series_{cfg.format}_{time.strftime('%Y%m%d_%H%M%S')}_{seed % 10**6:06d}"
    series_dir = Path(runs_dir) / series_id
    series_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    done = []
    _write(series_dir, {"kind": "series", "series_id": series_id, "format": cfg.format,
                        "name": cfg.name, "seed": seed, "runs": runs, "config": cfg.to_dict(),
                        "status": "running", "run_dir": str(series_dir)})

    def one(i):
        run_cfg = replace(cfg, seed=derive_seed(seed, "run", i))
        result = formats.run(run_cfg, run_id=f"run_{i + 1:03d}", runs_dir=series_dir,
                             workers=1, use_docker=use_docker, cancel=cancel)
        done.append(i)
        for fn in progress:
            fn({"event": "run_done", "run": i + 1, "runs_done": len(done), "runs": runs,
                "winner": result["standings"][0]["bot"]})
        return result

    workers = formats.normalise_workers(workers)
    if workers == formats.MAX_WORKERS:          # a run per CPU: each run plays its matches in turn
        workers = os.cpu_count() or 4
    try:
        if workers > 1 and runs > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, range(runs)))
        else:
            results = [one(i) for i in range(runs)]
    except BaseException as e:
        _write(series_dir, {"kind": "series", "series_id": series_id, "format": cfg.format,
                            "name": cfg.name, "seed": seed, "runs": runs, "config": cfg.to_dict(),
                            "status": "cancelled" if type(e).__name__ == "Cancelled" else "error",
                            "error": repr(e), "runs_done": len(done), "run_dir": str(series_dir)})
        raise

    summary = {
        "kind": "series", "series_id": series_id, "format": cfg.format, "name": cfg.name,
        "seed": seed, "runs": runs, "config": cfg.to_dict(),
        "aggregate": aggregate(results),
        "run_ids": [r["run_id"] for r in results],
        "winners": [r["standings"][0]["bot"] for r in results],
        "duration_s": round(time.time() - t0, 2), "run_dir": str(series_dir),
        "status": "complete",
    }
    _write(series_dir, summary)
    return summary


def _write(series_dir: Path, data: dict):
    tmp = series_dir / (SERIES_FILE + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(series_dir / SERIES_FILE)
