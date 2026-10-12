"""HTTP API for the web UI (and anything else on this machine).

Matches are addressed by *key*: their directory under data/runs, e.g.
"match_20261012_101500_ab12" or "bracket_x/matches/r1_m2".

Live viewing: GET /api/matches/{key}/stream?from=N is a server-sent event
stream that follows the match's events.jsonl as it is written, starting at
event N. A viewer can join at any time, catch up on everything so far, then
receive each new event as it happens; the same endpoint replays finished
matches. This works for matches started from the CLI too.
"""

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import arena
from arena import bots as botlib
from arena import formats, structures
from arena.events import DEFAULT_RUNS_DIR, EVENTS_FILE, SUMMARY_FILE
from arena.pacing import DEFAULT_PACE_MS
from arena.server.jobs import JobManager
from arena.series import SERIES_FILE

WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
POLL_S = 0.1
HEARTBEAT_S = 15


class MatchRequest(BaseModel):
    bots: List[str]
    names: Optional[List[Optional[str]]] = None
    mode: str = "fixed"
    hands: Optional[int] = None
    starting_stack: int = 10_000
    blinds: Optional[str] = None
    seed: Optional[int] = None
    time_limit_ms: Optional[int] = None
    docker: bool = False
    pace_ms: Optional[int] = None          # default: DEFAULT_PACE_MS (watchable)
    id: Optional[str] = None


class RunRequest(BaseModel):
    format: str
    bots: List[str]
    names: Optional[List[Optional[str]]] = None
    name: Optional[str] = None
    mode: Optional[str] = None
    hands: Optional[int] = None
    starting_stack: int = 10_000
    blinds: Optional[str] = None
    time_limit_ms: Optional[int] = None
    seed: Optional[int] = None
    table_size: Optional[int] = None
    duplicate: Optional[bool] = None
    seeding: Optional[str] = None
    runs: int = 1
    workers: int = 1
    docker: bool = False
    pace_ms: Optional[int] = None          # default: DEFAULT_PACE_MS for one run, 0 for series
    id: Optional[str] = None


def create_app(runs_dir: Path = DEFAULT_RUNS_DIR) -> FastAPI:
    runs_dir = Path(runs_dir)
    jobs = JobManager(runs_dir)

    @asynccontextmanager
    async def lifespan(app):
        yield
        jobs.shutdown()

    app = FastAPI(title="Poker Tournament Runner", version=arena.__version__, lifespan=lifespan)
    app.state.jobs = jobs
    app.state.runs_dir = runs_dir

    # -- helpers ---------------------------------------------------------------

    def resolve(key: str) -> Path:
        """A path under runs_dir, refusing anything that escapes it."""
        p = (runs_dir / key).resolve()
        if runs_dir.resolve() not in p.parents and p != runs_dir.resolve():
            raise HTTPException(404, "not found")
        return p

    def match_dir(key: str) -> Path:
        d = resolve(key)
        if not (d / EVENTS_FILE).is_file() and key not in jobs.active_keys():
            raise HTTPException(404, f"no match {key!r}")
        return d

    def match_status(key: str, d: Path) -> str:
        if (d / SUMMARY_FILE).is_file():
            return "complete"
        if key in jobs.active_keys():
            return "running"
        return "incomplete"

    def read_json(p: Path):
        return json.loads(p.read_text())

    def run_kind(d: Path):
        for name, kind in ((SERIES_FILE, "series"), (formats.RUN_FILE, "run"), (SUMMARY_FILE, "match")):
            if (d / name).is_file():
                return kind, d / name
        if (d / EVENTS_FILE).is_file():
            return "match", None
        return None, None

    def bad_request(e: Exception):
        raise HTTPException(400, str(e))

    # -- meta ------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": arena.__version__, "runs_dir": str(runs_dir)}

    @app.get("/api/bots")
    def list_bots():
        out = []
        for name, path, folder in botlib.available():
            bot_path = str(Path(path).parent) if path.endswith("bot.py") else path
            out.append({"name": name, "folder": folder, "path": bot_path,
                        "hash": botlib.bot_hash(bot_path)})
        return out

    @app.get("/api/structures")
    def list_structures(stack: int = 10_000):
        return {"presets": {name: structures.preset(name, stack).to_dict()
                            for name in structures.PRESETS},
                "formats": {f: formats._module(f).DEFAULTS for f in formats.FORMATS},
                "default_pace_ms": DEFAULT_PACE_MS}

    # -- starting things -------------------------------------------------------

    @app.post("/api/matches", status_code=201)
    def start_match(req: MatchRequest):
        p = req.model_dump()
        if p["pace_ms"] is None:
            p["pace_ms"] = DEFAULT_PACE_MS
        try:
            job = jobs.start_match(p)
        except ValueError as e:
            bad_request(e)
        return {"job": job.to_dict(), "match_key": job.id}

    @app.post("/api/runs", status_code=201)
    def start_run(req: RunRequest):
        p = req.model_dump()
        if p["pace_ms"] is None:
            p["pace_ms"] = DEFAULT_PACE_MS if p["runs"] <= 1 else 0
        try:
            job = jobs.start_run(p)
        except (ValueError, TypeError) as e:
            bad_request(e)
        return {"job": job.to_dict(), "run_id": job.id, "kind": job.kind}

    # -- jobs ------------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs():
        return [j.to_dict() for j in jobs.list()]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = jobs.cancel(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        return job.to_dict()

    # -- runs (matches, tournament runs, series) --------------------------------

    @app.get("/api/runs")
    def list_runs(limit: int = 50):
        rows = []
        if runs_dir.is_dir():
            dirs = sorted((d for d in runs_dir.iterdir() if d.is_dir()),
                          key=lambda d: d.stat().st_mtime, reverse=True)
            for d in dirs[:limit]:
                kind, f = run_kind(d)
                if kind is None:
                    continue
                data = read_json(f) if f else {}
                job = jobs.get(d.name)
                status = (job.status if job else None) or data.get("status") or \
                    ("complete" if f else "incomplete")
                rows.append({
                    "id": d.name, "kind": kind, "status": status,
                    "format": data.get("format") or ("table" if kind == "match" else None),
                    "name": data.get("name"),
                    "bots": [b.get("name") for b in data.get("bots", [])] or
                            [r["bot"] for r in (data.get("results") or data.get("aggregate") or [])],
                    "runs": data.get("runs"), "modified": d.stat().st_mtime,
                    "winner": _winner(kind, data),
                })
        return rows

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        d = resolve(run_id)
        kind, f = run_kind(d)
        job = jobs.get(run_id)
        if kind is None and not job:
            raise HTTPException(404, f"no run {run_id!r}")
        data = read_json(f) if f else {}
        out = {"id": run_id, "kind": kind or (job.kind if job else None), "data": data,
               "job": job.to_dict() if job else None}
        if kind == "run" or (job and job.kind == "run"):
            mdir = d / "matches"
            out["matches"] = sorted(
                ({"key": f"{run_id}/matches/{m.name}", "match_id": m.name,
                  "status": match_status(f"{run_id}/matches/{m.name}", m)}
                 for m in mdir.iterdir() if (m / EVENTS_FILE).is_file()),
                key=lambda m: m["match_id"]) if mdir.is_dir() else []
        return out

    @app.get("/api/verify/{key:path}")
    def verify(key: str):
        from arena.verify import verify_run
        d = resolve(key)
        if not d.exists():
            raise HTTPException(404, "not found")
        if jobs.is_active(key.split("/")[0]):
            raise HTTPException(409, "still running")
        return verify_run(str(d))

    @app.get("/api/export/{key:path}")
    def export(key: str, bot: Optional[str] = None):
        from arena.export import export as do_export
        d = resolve(key)
        if not d.is_dir():
            raise HTTPException(404, "not found")
        tmp = Path(tempfile.mkdtemp()) / f"{d.name}{'_' + bot if bot else ''}.zip"
        do_export(d, tmp, bot=bot)
        return FileResponse(tmp, filename=tmp.name, media_type="application/zip")

    # -- matches ---------------------------------------------------------------

    @app.get("/api/matches/{key:path}/events")
    def match_events(key: str, start: int = Query(0, alias="from")):
        d = match_dir(key)
        events = _read_lines(d / EVENTS_FILE, start)
        return {"key": key, "from": start, "next": start + len(events), "events": events,
                "status": match_status(key, d)}

    @app.get("/api/matches/{key:path}/stream")
    async def match_stream(key: str, request: Request, start: int = Query(0, alias="from")):
        d = match_dir(key)

        async def gen():
            n, pos, buf = 0, 0, ""
            idle = 0.0
            path = d / EVENTS_FILE
            while True:
                if await request.is_disconnected():
                    return
                chunk = ""
                if path.is_file():
                    with open(path, encoding="utf-8") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                buf += chunk
                lines = buf.split("\n")
                buf = lines.pop()                       # keep any partial line
                ended = False
                for line in lines:
                    if not line.strip():
                        continue
                    if n >= start:
                        yield f"id: {n}\nevent: poker\ndata: {line}\n\n"
                    n += 1
                    if json.loads(line).get("type") == "match_end":
                        ended = True
                if ended:
                    yield "event: end\ndata: {\"reason\": \"match_end\"}\n\n"
                    return
                if lines:
                    idle = 0.0
                else:
                    idle += POLL_S
                    if match_status(key, d) != "running" and not chunk:
                        yield "event: end\ndata: {\"reason\": \"%s\"}\n\n" % match_status(key, d)
                        return
                    if idle >= HEARTBEAT_S:
                        idle = 0.0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(POLL_S)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/matches/{key:path}")
    def get_match(key: str):
        d = match_dir(key)
        summary = read_json(d / SUMMARY_FILE) if (d / SUMMARY_FILE).is_file() else None
        first = _read_lines(d / EVENTS_FILE, 0, limit=1)
        return {"key": key, "status": match_status(key, d), "summary": summary,
                "match_start": first[0] if first else None,
                "events": _count_lines(d / EVENTS_FILE)}

    # -- web UI ------------------------------------------------------------------

    if WEB_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (WEB_DIST / path).resolve()
            if path and WEB_DIST.resolve() in f.parents and f.is_file():
                return FileResponse(f)
            return FileResponse(WEB_DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"ok": True, "message": "API is running; the web UI is not built "
                                 "yet (cd web && npm install && npm run build). API docs: /docs"})

    return app


def _winner(kind, data):
    if kind == "series":
        agg = data.get("aggregate") or []
        return agg[0]["bot"] if agg else None
    rows = data.get("standings") or data.get("results") or []
    return rows[0]["bot"] if rows and data.get("status", "complete") == "complete" else None


def _read_lines(path: Path, start: int = 0, limit: int = None) -> list:
    out = []
    if not path.is_file():
        return out
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < start or not line.endswith("\n"):
                continue
            out.append(json.loads(line))
            if limit and len(out) >= limit:
                break
    return out


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.endswith("\n"))
