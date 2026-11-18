"""Matches in the arena: the core table (pokercore/table.py) plus what the
runner adds around it: event logs on disk, live pacing and cancellation, run
directories, and seats for humans and LLMs.

The core names are re-exported here, so arena code can keep importing them
from arena.table.
"""

import time
from pathlib import Path

import arena
from arena.events import DEFAULT_RUNS_DIR, EventLog
from arena.pacing import Pacer
from pokercore.seats import BotSeat, Seat
from pokercore.seeds import derive_seed, new_seed
from pokercore.table import (  # noqa: F401  (re-exported)
    MATCH_LOG_MAX_ENTRIES, MAX_STEPS_PER_HAND, MODES, RUNTIME_STATS, Cancelled, MatchConfig,
    Table, bot_result, engine_hash, rank, warm_up,
)
from pokercore.table import play_match as _core_play_match


def play_match(seats, config: MatchConfig, match_id: str, log, pacer=None) -> dict:
    """The core play_match, with this runner's version recorded at match_start.
    Used by run_match and by verify (which replays with recorded decisions)."""
    return _core_play_match(seats, config, match_id, log, pacer=pacer,
                            start_info={"arena_version": arena.__version__})


def make_bot_seats(bot_specs, config, use_docker=None, seed=None):
    """A seat per entry: BotSpecs become sandboxed BotSeats; ready-made Seat
    objects (e.g. remote human/MCP seats) are used as they are."""
    seed = config.seed if seed is None else seed
    seats = []
    for spec in bot_specs:
        if isinstance(spec, Seat):
            seats.append(spec)
        elif getattr(spec, "kind", None) in ("human", "mcp"):
            raise ValueError(f"{spec.name}: {spec.kind} seats need a running server (poker serve)")
        else:
            seats.append(BotSeat(spec, seed=derive_seed(seed, "bot", spec.name),
                                 time_limit_ms=config.time_limit_ms, use_docker=use_docker))
    return seats


def diagnostics(seats) -> dict:
    return {s.name: {k: v for k, v in s.summary().items() if k in ("errors_list", "stderr_tail")}
            for s in seats}


def run_match(bot_specs, config: MatchConfig, match_id: str = None,
              runs_dir: Path = DEFAULT_RUNS_DIR, use_docker: bool = None,
              listeners=(), pace_ms: int = 0, cancel=None) -> dict:
    """Play a match between 2-9 bots and write its event log + summary.
    Returns the summary dict (also saved as summary.json in the run dir)."""
    if not 2 <= len(bot_specs) <= 9:
        raise ValueError(f"A table needs 2-9 players, got {len(bot_specs)}")
    if config.seed is None:
        config.seed = new_seed()
    seed = config.seed
    match_id = match_id or f"match_{time.strftime('%Y%m%d_%H%M%S')}_{seed % 10**6:06d}"
    run_dir = Path(runs_dir) / match_id

    seats = make_bot_seats(bot_specs, config, use_docker)
    t0 = time.time()
    with EventLog(run_dir) as log:
        log.listeners.extend(listeners)
        outcome, interrupted = None, "error"
        try:
            outcome = play_match(seats, config, match_id, log,
                                 pacer=Pacer(pace_ms, cancel) if (pace_ms or cancel) else None)
        except Cancelled:
            interrupted = "cancelled"
            raise
        finally:
            summary = {
                "kind": "match", "match_id": match_id, "seed": seed, "config": config.to_dict(),
                "seats": [s.info() for s in seats],
                "results": (outcome or getattr(log, "final", {})).get("results"),
                "hands": (outcome or getattr(log, "final", {})).get("hands"),
                "stopped": outcome["stopped"] if outcome else interrupted,
                "duration_s": round(time.time() - t0, 2), "run_dir": str(run_dir),
                "bot_diagnostics": diagnostics(seats),
            }
            log.write_summary(summary)
    return summary
