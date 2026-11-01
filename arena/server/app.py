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
import sys
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import arena
from arena import bots as botlib
from arena import formats, structures
from arena.events import DEFAULT_RUNS_DIR, EVENTS_FILE, SUMMARY_FILE, has_events, is_compressed, \
    iter_lines
from arena.pacing import DEFAULT_PACE_MS
from arena.server.jobs import JobManager, ServerStopping
from arena.server.mcp_server import build_mcp
from arena.server import status as status_rule
from arena.server.progress import run_progress, series_progress
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
    time_limits: Optional[dict] = None     # per seat kind, e.g. {"human": 30000, "mcp": 120000}
    workers: Optional[int] = None          # against gto_wizard: hands played at once
    id: Optional[str] = None


class ActRequest(BaseModel):
    action: str
    amount: Optional[int] = None
    decision_id: Optional[int] = None      # guard against acting on a decision you didn't see
    note: Optional[str] = None             # optional reasoning, saved as the decision's logs


class ClaimRequest(BaseModel):
    match: str                             # match key
    seat: str                              # seat name, e.g. "llm2"


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
    workers: Union[int, str] = 1           # matches (or series runs) at once; 0 or "max": as many as possible
    docker: bool = False
    pace_ms: Optional[int] = None          # default: DEFAULT_PACE_MS for one run, 0 for series
    id: Optional[str] = None


def create_app(runs_dir: Path = DEFAULT_RUNS_DIR, port: int = 8765) -> FastAPI:
    runs_dir = Path(runs_dir)
    jobs = JobManager(runs_dir)
    mcp = build_mcp(jobs, runs_dir, base_url=f"http://127.0.0.1:{port}")
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
                                      json_response=True)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        # Let running jobs finish their files (match_end, summary, run.json) and
        # stop their bots before the process exits.
        running = sum(1 for j in jobs.jobs.values() if j.active)
        if running:
            print(f"Stopping {running} running job(s)...", file=sys.stderr, flush=True)
        left = await asyncio.to_thread(jobs.shutdown)
        if left:
            print(f"Gave up waiting for: {', '.join(left)}", file=sys.stderr, flush=True)

    app = FastAPI(title="Poker Tournament Runner", version=arena.__version__, lifespan=lifespan)
    app.state.jobs = jobs
    app.state.mcp = mcp
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
        if not has_events(d) and not jobs.is_active(key.split("/")[0]):
            raise HTTPException(404, f"no match {key!r}")
        return d

    def match_status(key: str, d: Path) -> str:
        return status_rule.match_status(d, active=jobs.is_active(key.split("/")[0]))

    def read_json(p: Path):
        return json.loads(p.read_text())

    def run_kind(d: Path):
        for name, kind in ((SERIES_FILE, "series"), (formats.RUN_FILE, "run"), (SUMMARY_FILE, "match")):
            if (d / name).is_file():
                return kind, d / name
        if has_events(d):
            return "match", None
        return None, None

    def bad_request(e: Exception):
        raise HTTPException(400, str(e))

    # -- meta ------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": arena.__version__, "runs_dir": str(runs_dir),
                "cpus": os.cpu_count()}

    @app.get("/api/bots")
    def list_bots():
        out = []
        for name, path, folder in botlib.available():
            bot_path = str(Path(path).parent) if path.endswith("bot.py") else path
            out.append({"name": name, "folder": folder, "path": bot_path,
                        "hash": botlib.bot_hash(bot_path)})
        return out

    @app.get("/api/external")
    def external_bots():
        """The bots played through their own APIs (GTO Wizard, Slumbot): whether
        each can be played here (e.g. an API key is set), and their game."""
        from arena import external
        out = []
        for o in external.opponents().values():
            try:
                o.check_configured()
                configured, why = True, None
            except external.ExternalError as e:
                configured, why = False, str(e)
            out.append({"name": o.name, "title": o.title, "configured": configured, "why": why,
                        "score_kind": o.score_kind, "default_workers": o.default_workers,
                        "max_workers": o.max_workers, "starting_stack": external.STARTING_STACK,
                        "sb": external.SB, "bb": external.BB})
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
        except ServerStopping as e:
            raise HTTPException(503, str(e))
        except ValueError as e:
            bad_request(e)
        return {"job": job.to_dict(), "match_key": job.id, "seats": job.seats}

    @app.post("/api/runs", status_code=201)
    def start_run(req: RunRequest):
        p = req.model_dump()
        if p["pace_ms"] is None:
            p["pace_ms"] = DEFAULT_PACE_MS if p["runs"] <= 1 else 0
        try:
            job = jobs.start_run(p)
        except ServerStopping as e:
            raise HTTPException(503, str(e))
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
                if kind == "match" and not data.get("seats"):
                    first = _read_lines(d, 0, limit=1)
                    if first:
                        data = {"seats": first[0].get("seats", [])}
                status = (match_status(d.name, d) if kind == "match"
                          else status_rule.run_status(d, active=jobs.is_active(d.name)))
                rows.append({
                    "id": d.name, "kind": kind, "status": status,
                    "format": data.get("format") or ("table" if kind == "match" else None),
                    "name": data.get("name"),
                    "bots": [b.get("name") for b in data.get("bots") or data.get("seats") or []] or
                            [r["bot"] for r in (data.get("results") or data.get("aggregate") or [])],
                    "runs": data.get("runs"), "modified": d.stat().st_mtime,
                    "winner": _winner(kind, data),
                })
        return rows

    @app.get("/api/runs/{run_id:path}")
    def get_run(run_id: str):
        d = resolve(run_id)
        kind, f = run_kind(d)
        job = jobs.get(run_id)
        if kind is None and not job:
            raise HTTPException(404, f"no run {run_id!r}")
        data = read_json(f) if f else {}
        active = jobs.is_active(run_id.split("/")[0])
        status = (match_status(run_id, d) if kind == "match"
                  else status_rule.run_status(d, active=active))
        out = {"id": run_id, "kind": kind or (job.kind if job else None), "status": status,
               "data": data, "job": job.to_dict() if job else None}
        try:
            if kind == "series":
                out["progress"] = series_progress(d, data)
            elif data.get("config"):
                out["progress"] = run_progress(d, data, run_id, active=active)
        except Exception as e:          # progress is a convenience; never break the page
            out["progress_error"] = repr(e)
        if kind == "run" or (job and job.kind == "run"):
            mdir = d / "matches"
            out["matches"] = sorted(
                ({"key": f"{run_id}/matches/{m.name}", "match_id": m.name,
                  "status": match_status(f"{run_id}/matches/{m.name}", m)}
                 for m in mdir.iterdir() if has_events(m)),
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

    # -- remote seats (humans, MCP) -------------------------------------------------

    def seat_or_404(token: str):
        seat = jobs.hub.get(token)
        if seat is None:
            raise HTTPException(404, "no such seat")
        return seat

    def seat_view(seat) -> dict:
        return {"name": seat.name, "kind": seat.kind, "match_key": seat.match_key,
                "time_limit_ms": seat.time_limit_ms, "finished": seat.finished,
                "pending": seat.pending()}

    @app.get("/api/seats")
    def list_seats(kind: Optional[str] = None, match: Optional[str] = None):
        """Open remote seats (this server is local-only). `match` narrows it to one
        match, e.g. so the UI can offer to rejoin a seat you left."""
        return [{**seat_view(s), "pending": s.pending() is not None,
                 **({"token": s.token} if s.kind == "human" else {})}
                for s in jobs.hub.open_seats(kind) if match is None or s.match_key == match]

    @app.post("/api/seats/claim")
    def claim_seat(req: ClaimRequest):
        """Take an open LLM seat (one agent per seat). Returns its token."""
        try:
            seat = jobs.hub.claim(req.match, req.seat)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {**seat_view(seat), "token": seat.token}

    @app.get("/api/seats/{token}")
    def get_seat(token: str):
        seat = seat_or_404(token)
        seat.claim()                    # using a token claims the seat
        return seat_view(seat)

    @app.post("/api/seats/{token}/act")
    def seat_act(token: str, req: ActRequest):
        seat = seat_or_404(token)
        seat.claim()
        if not seat.submit(req.action, req.amount, note=req.note, decision_id=req.decision_id):
            raise HTTPException(409, "it is not this seat's turn (or that decision has passed)")
        return {"ok": True}

    @app.get("/api/matches/{key:path}/turn")
    def match_turn(key: str):
        """Remote seats this match is waiting on (no private state)."""
        out = []
        for s in jobs.hub.for_match(key):
            p = s.pending()
            if p:
                out.append({"bot": s.name, "kind": s.kind, "time_left_ms": p["time_left_ms"],
                            "time_limit_ms": p["time_limit_ms"]})
        return out

    # -- matches ---------------------------------------------------------------

    @app.get("/api/matches/{key:path}/events")
    def match_events(key: str, start: int = Query(0, alias="from"), as_: Optional[str] = Query(None, alias="as")):
        d = match_dir(key)
        events = [player_view(e, as_) for e in _read_lines(d, start)]
        return {"key": key, "from": start, "next": start + len(events), "events": events,
                "status": match_status(key, d)}

    @app.get("/api/matches/{key:path}/stream")
    async def match_stream(key: str, request: Request, start: int = Query(0, alias="from"),
                           as_: Optional[str] = Query(None, alias="as")):
        d = match_dir(key)

        async def gen():
            n, pos, buf = 0, 0, ""
            idle = 0.0
            path = d / EVENTS_FILE
            while True:
                if await request.is_disconnected():
                    return
                if jobs.stopping and idle >= 1.0:
                    # don't hold the server's shutdown open; the log is final by now
                    yield "event: end\ndata: {\"reason\": \"server_stopping\"}\n\n"
                    return
                chunk = ""
                try:
                    with open(path, encoding="utf-8") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                except FileNotFoundError:
                    if is_compressed(d):
                        # finished and compressed (perhaps since the last read):
                        # the rest comes from the compressed log, after the n
                        # lines already seen; drop any partial line read before
                        rest = list(iter_lines(d, n))
                        chunk, buf = "".join(line + "\n" for line in rest), ""
                buf += chunk
                lines = buf.split("\n")
                buf = lines.pop()                       # keep any partial line
                ended = False
                for line in lines:
                    if not line.strip():
                        continue
                    if n >= start:
                        out = json.dumps(player_view(json.loads(line), as_)) if as_ else line
                        yield f"id: {n}\nevent: poker\ndata: {out}\n\n"
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
        first = _read_lines(d, 0, limit=1)
        return {"key": key, "status": match_status(key, d), "summary": summary,
                "match_start": first[0] if first else None,
                "events": _count_lines(d)}

    # -- MCP (LLM seats) ---------------------------------------------------------
    app.router.routes.extend(mcp_app.routes)        # POST/GET /mcp

    # -- web UI ------------------------------------------------------------------

    if WEB_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/") or path == "api":
                raise HTTPException(404, "not found")
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


_PRIVATE_DECISION_FIELDS = ("state", "reply", "logs", "logs_truncated", "debug", "debug_error",
                             "error_detail")


def player_view(event: dict, player: Optional[str]) -> dict:
    """An event as one player may see it: other players' hole cards are hidden
    (showdown events still reveal them) and their decision details removed."""
    if not player:
        return event
    t = event.get("type")
    if t == "hand_start":
        return {**event, "hole": {n: c for n, c in event["hole"].items() if n == player}}
    if t == "decision" and event.get("bot") != player:
        return {k: v for k, v in event.items() if k not in _PRIVATE_DECISION_FIELDS}
    return event


def _winner(kind, data):
    if kind == "series":
        agg = data.get("aggregate") or []
        return agg[0]["bot"] if agg else None
    rows = data.get("standings") or data.get("results") or []
    return rows[0]["bot"] if rows and data.get("status", "complete") == "complete" \
        and data.get("stopped") not in ("cancelled",) else None


def _read_lines(match_dir: Path, start: int = 0, limit: int = None) -> list:
    """Events of a match's log (plain or compressed) from line `start`."""
    out = []
    if not has_events(match_dir):
        return out
    try:
        for line in iter_lines(match_dir, start):
            out.append(json.loads(line))
            if limit and len(out) >= limit:
                break
    except FileNotFoundError:
        pass
    return out


def _count_lines(match_dir: Path) -> int:
    if not has_events(match_dir):
        return 0
    try:
        return sum(1 for _ in iter_lines(match_dir))
    except FileNotFoundError:
        return 0
