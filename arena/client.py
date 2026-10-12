"""A small client for a running `poker serve` (standard library only).

The CLI uses it to run matches and tournaments *on the server* (so they show
up live in the web UI, can be cancelled there, and can have human/MCP seats)
and to follow them: the live event stream for a match, job progress for runs.

Server URL: --server, else $POKER_SERVER, else http://127.0.0.1:8765.
"""

import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote

DEFAULT_SERVER = os.environ.get("POKER_SERVER", "http://127.0.0.1:8765")


class ServerUnavailable(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _key(key: str) -> str:
    return "/".join(quote(p, safe="") for p in key.split("/"))


class Server:
    def __init__(self, url: str = None, timeout: float = 30):
        self.url = (url or DEFAULT_SERVER).rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"content-type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, list):        # pydantic validation errors
                detail = "; ".join(str(d.get("msg", d)) for d in detail)
            raise ApiError(e.code, detail or f"HTTP {e.code}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise ServerUnavailable(f"No poker server at {self.url} ({getattr(e, 'reason', e)})") from None

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, body):
        return self._request("POST", path, body)

    # -- endpoints -----------------------------------------------------------

    def health(self):
        return self.get("/api/health")

    def start_match(self, body):
        return self.post("/api/matches", body)

    def start_run(self, body):
        return self.post("/api/runs", body)

    def job(self, job_id):
        return self.get(f"/api/jobs/{quote(job_id, safe='')}")

    def cancel(self, job_id):
        return self.post(f"/api/jobs/{quote(job_id, safe='')}/cancel", {})

    def match(self, key):
        return self.get(f"/api/matches/{_key(key)}")

    def run(self, run_id):
        return self.get(f"/api/runs/{_key(run_id)}")

    def watch_url(self, key=None, run_id=None):
        return f"{self.url}/#/match/{key}" if key else f"{self.url}/#/run/{run_id}"

    def stream(self, key, start=0):
        """Yield a match's events from `start`, live, until it ends.
        The generator returns the end reason (match_end, incomplete, ...)."""
        req = urllib.request.Request(f"{self.url}/api/matches/{_key(key)}/stream?from={start}")
        try:
            resp = urllib.request.urlopen(req, timeout=None)
        except urllib.error.URLError as e:
            raise ServerUnavailable(f"Lost the poker server at {self.url} ({e.reason})") from None
        kind = None
        with resp:
            for raw in resp:
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    kind = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:])
                    if kind == "end":
                        return data.get("reason")
                    yield data
        return "disconnected"
