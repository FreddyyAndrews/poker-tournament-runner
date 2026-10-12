"""Background jobs: matches, tournament runs and series started from the API.

Each job runs in its own thread through the same code as the CLI, writing to
data/runs/<id>/ as usual, so everything started here can be verified, shown
and exported with the CLI too. A job's id is the id of what it produces (the
match id, run id or series id), chosen before it starts, so a client can open
the live view immediately.
"""

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from arena import bots as botlib
from arena import formats
from arena.pacing import Cancelled
from arena.remote import RemoteSeat, SeatHub
from arena.series import run_series
from arena.table import MatchConfig, run_match


@dataclass
class Job:
    id: str
    kind: str                       # match | run | series
    params: dict
    status: str = "queued"          # queued | running | complete | cancelled | error
    error: str = None
    created: float = field(default_factory=time.time)
    started: float = None
    finished: float = None
    live: list = field(default_factory=list)       # match keys being played right now
    seats: list = field(default_factory=list)      # remote seats: name, kind, token
    matches_done: int = 0
    runs_done: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "status": self.status, "error": self.error,
                "params": self.params, "created": self.created, "started": self.started,
                "finished": self.finished, "live": list(self.live),
                "matches_done": self.matches_done, "runs_done": self.runs_done,
                "seats": [{"name": x["name"], "kind": x["kind"]} for x in self.seats]}

    @property
    def active(self):
        return self.status in ("queued", "running")


def _stamp(prefix, seed):
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"


class JobManager:
    def __init__(self, runs_dir: Path, hub: SeatHub = None):
        self.runs_dir = Path(runs_dir)
        self.hub = hub or SeatHub()
        self.jobs = {}
        self._lock = threading.Lock()

    # -- queries -------------------------------------------------------------

    def get(self, job_id):
        return self.jobs.get(job_id)

    def list(self):
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def active_keys(self) -> set:
        """Match keys (paths under runs_dir) currently being played."""
        keys = set()
        for j in self.jobs.values():
            if j.active:
                keys.update(j.live)
        return keys

    def is_active(self, run_id: str) -> bool:
        j = self.jobs.get(run_id)
        return bool(j and j.active)

    # -- starting jobs -------------------------------------------------------

    def start_match(self, p: dict) -> Job:
        """p: bots, names, mode, hands, starting_stack, blinds, seed, time_limit_ms,
        time_limits ({"human": ms, "mcp": ms}), docker, pace_ms, id.
        Bots may include "human:Name" / "mcp:Name" seats. Validates before
        starting (raises ValueError). Returns the job; job.seats lists the
        remote seats (name, kind, token)."""
        specs = botlib.resolve_all(p["bots"], names=p.get("names"), allow_remote=True)
        if not 2 <= len(specs) <= 9:
            raise ValueError(f"A table needs 2-9 players, got {len(specs)}")
        mode = p.get("mode") or "fixed"
        from arena import structures
        blinds = p.get("blinds") or ("standard" if mode == "freezeout" else "50/100")
        stack = p.get("starting_stack") or 10_000
        hands = p.get("hands") or (2000 if mode == "freezeout" else 100)
        config = MatchConfig(mode=mode, hands=hands, starting_stack=stack,
                             structure=structures.parse(blinds, stack), seed=p.get("seed"),
                             time_limit_ms=p.get("time_limit_ms"))
        match_id = p.get("id") or _stamp("match", config.seed)
        self._check_new(match_id)
        job = Job(id=match_id, kind="match", params=p, live=[match_id])
        limits = p.get("time_limits") or {}
        for i, spec in enumerate(specs):
            if isinstance(spec, botlib.RemoteSpec):
                seat = RemoteSeat(spec.name, spec.kind, time_limit_ms=limits.get(spec.kind), cancel=job.cancel)
                self.hub.add(seat, match_id)
                specs[i] = seat
                job.seats.append({"name": seat.name, "kind": seat.kind, "token": seat.token,
                                  "time_limit_ms": seat.time_limit_ms})

        def work():
            run_match(specs, config, match_id=match_id, runs_dir=self.runs_dir,
                      use_docker=True if p.get("docker") else None,
                      pace_ms=p.get("pace_ms") or 0, cancel=job.cancel)
            job.matches_done = 1

        return self._launch(job, work)

    def start_run(self, p: dict) -> Job:
        """p: RunConfig fields plus runs, workers, docker, pace_ms, id."""
        fields = {k: v for k, v in p.items()
                  if k in formats.RunConfig.__dataclass_fields__ and v is not None}
        cfg = formats.RunConfig(**fields)
        botlib.resolve_all(cfg.bots, names=cfg.names)          # fail fast on unknown bots
        runs = p.get("runs") or 1
        workers = p.get("workers") or 1
        use_docker = True if p.get("docker") else None
        if runs > 1:
            run_id = p.get("id") or _stamp(f"series_{cfg.format}", cfg.seed)
            self._check_new(run_id)
            job = Job(id=run_id, kind="series", params=p)

            def progress(e):
                if e.get("event") == "run_done":
                    job.runs_done = e["runs_done"]

            def work():
                run_series(cfg, runs, series_id=run_id, runs_dir=self.runs_dir, workers=workers,
                           use_docker=use_docker, progress=[progress], cancel=job.cancel)
        else:
            run_id = p.get("id") or _stamp(cfg.format, cfg.seed)
            self._check_new(run_id)
            job = Job(id=run_id, kind="run", params=p)

            def progress(e):
                key = f"{run_id}/matches/{e['match_id']}"
                with self._lock:
                    if e["event"] == "match_start" and key not in job.live:
                        job.live.append(key)
                    elif e["event"] == "match_done":
                        if key in job.live:
                            job.live.remove(key)
                        job.matches_done += 1

            def work():
                formats.run(cfg, run_id=run_id, runs_dir=self.runs_dir, workers=workers,
                            use_docker=use_docker, progress=[progress],
                            pace_ms=p.get("pace_ms") or 0, cancel=job.cancel)
        return self._launch(job, work)

    def cancel(self, job_id) -> Job:
        job = self.jobs.get(job_id)
        if job and job.active:
            job.cancel.set()
        return job

    def shutdown(self):
        for j in self.jobs.values():
            j.cancel.set()

    # -- internals -----------------------------------------------------------

    def _check_new(self, run_id):
        if "/" in run_id or run_id.startswith("."):
            raise ValueError(f"invalid id {run_id!r}")
        if (self.runs_dir / run_id).exists() or run_id in self.jobs:
            raise ValueError(f"{run_id!r} already exists")

    def _launch(self, job: Job, work) -> Job:
        with self._lock:
            self.jobs[job.id] = job

        def target():
            job.status, job.started = "running", time.time()
            try:
                work()
                job.status = "complete"
            except Cancelled:
                job.status = "cancelled"
            except Exception as e:
                job.status = "error"
                job.error = repr(e)
                traceback.print_exc()
            finally:
                job.finished = time.time()
                if job.kind != "match":
                    job.live.clear()

        threading.Thread(target=target, name=f"job-{job.id}", daemon=True).start()
        return job
