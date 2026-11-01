"""The outside-bot proxy: one local service that every call to Slumbot and
GTO Wizard goes through (`poker proxy`, on 127.0.0.1:8766 by default).

Why: many bots (and the agents building them) play the outside bots at once.
Slumbot is a free service run by one person and GTO Wizard is a paid key with
a monthly quota, so the limits have to hold across every process, not per
process. The proxy is also the only process that reads the repo's .env, so
the API key and Slumbot account never reach the processes that play matches.

What it does, per outside bot:
  - Turns it on or off from .env, re-read on every request, so a change
    applies at once: ALLOW_SLUMBOT_BENCHMARKING (default on) and
    ALLOW_GTO_WIZARD_BENCHMARKING (default off). These two are read from
    .env only, never from the environment, so starting another proxy with a
    different environment can't turn GTO Wizard on.
  - Queues requests first come, first served, with a cap on requests in
    flight across everyone. Over the cap, a request waits its turn: clients
    just run slower. (Slumbot: SLUMBOT_PROXY_MAX_IN_FLIGHT, default 3, the
    load one client already put on it. GTO Wizard:
    GTO_WIZARD_PROXY_MAX_IN_FLIGHT, default 20, the API's own limit.)
  - Caps the hands started per day across everyone (SLUMBOT_PROXY_DAILY_HANDS,
    default 20,000; GTO_WIZARD_PROXY_DAILY_HANDS, default 5,000), counted in
    data/external/proxy_usage.json so a restart doesn't reset them.
  - Adds the credentials: GTO_WIZARD_API_KEY, and SLUMBOT_USERNAME and
    SLUMBOT_PASSWORD when set (otherwise Slumbot sessions are anonymous).
  - Forwards only the calls a match makes (Slumbot: login, new_hand, act;
    GTO Wizard: POST /hands and POST /hands/<id>/act).

Its own refusals are HTTP 403 with a JSON body ({"error_msg": ...} for
Slumbot, {"detail": ...} for GTO Wizard) saying why. GET /status reports the
switches, caps, queue and today's counts, and never a secret.

Standard library only, so it starts fast and doesn't import the engine.
"""

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRETS_FILE = REPO_ROOT / ".env"
USAGE_FILE = REPO_ROOT / "data" / "external" / "proxy_usage.json"
HOST, DEFAULT_PORT = "127.0.0.1", 8766
DEFAULT_URL = f"http://{HOST}:{DEFAULT_PORT}"
UPSTREAM_TIMEOUT_S = 30
MAX_BODY = 64 * 1024
KEEP_DAYS = 60
# Hands one bot may play against GTO Wizard per day, enforced by the client
# from its usage log (the proxy doesn't know which bot is playing).
GTOW_DAILY_PER_BOT_SETTING = "GTO_WIZARD_DAILY_HANDS_PER_BOT"
GTOW_DAILY_PER_BOT_DEFAULT = 5000
TRUE = {"1", "true", "yes", "on"}


def _version():
    try:
        import arena
        return arena.__version__
    except Exception:
        return "unknown"


class Settings:
    """Settings from .env, falling back to the environment, read afresh on
    every call so edits to .env apply without a restart."""

    def __init__(self, path: Path = SECRETS_FILE, environ=None):
        self.path = Path(path)
        self.environ = os.environ if environ is None else environ

    def _file(self) -> dict:
        out = {}
        try:
            text = self.path.read_text()
        except OSError:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            out[key] = value.strip().strip("'\"")
        return out

    def get(self, name: str, file_only: bool = False):
        value = self._file().get(name)
        if not value and not file_only:
            value = self.environ.get(name)
        return value or None

    def flag(self, name: str, default: bool) -> bool:
        value = self.get(name, file_only=True)
        return default if value is None else value.strip().lower() in TRUE

    def number(self, name: str, default: int) -> int:
        value = self.get(name)
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default


class Gate:
    """First come, first served, with at most `limit` holders at once."""

    def __init__(self):
        self.cond = threading.Condition()
        self.queue = deque()
        self.in_flight = 0

    def acquire(self, limit: int):
        ticket = object()
        with self.cond:
            self.queue.append(ticket)
            while self.queue[0] is not ticket or self.in_flight >= max(1, limit):
                self.cond.wait()
            self.queue.popleft()
            self.in_flight += 1
            self.cond.notify_all()

    def release(self):
        with self.cond:
            self.in_flight -= 1
            self.cond.notify_all()

    @property
    def waiting(self) -> int:
        return len(self.queue)


class Usage:
    """Hands started per day per outside bot, kept in a JSON file."""

    def __init__(self, path: Path = USAGE_FILE):
        self.path = Path(path)
        self.lock = threading.Lock()
        try:
            self.days = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.days = {}

    def today(self, name: str) -> int:
        with self.lock:
            return self.days.get(time.strftime("%Y-%m-%d"), {}).get(name, 0)

    def add(self, name: str, n: int = 1):
        with self.lock:
            day = self.days.setdefault(time.strftime("%Y-%m-%d"), {})
            day[name] = day.get(name, 0) + n
            for old in sorted(self.days)[:-KEEP_DAYS]:
                del self.days[old]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.days, indent=1, sort_keys=True))
            tmp.replace(self.path)


class Upstream:
    """One outside bot behind the proxy. Subclasses name its settings and
    which calls it forwards."""
    name = title = None
    allow_setting = None
    allow_default = False
    in_flight_setting = None
    in_flight_default = 1
    daily_setting = None
    daily_default = None
    url_setting = None
    url_default = None
    error_key = "error_msg"

    def __init__(self, settings: Settings, usage: Usage):
        self.settings, self.usage = settings, usage
        self.gate = Gate()
        self.requests = self.errors = self.refused = 0
        self.lock = threading.Lock()

    # settings, read on every request
    def url(self):
        return (self.settings.get(self.url_setting) or self.url_default).rstrip("/")

    def allowed(self):
        return self.settings.flag(self.allow_setting, self.allow_default)

    def max_in_flight(self):
        return self.settings.number(self.in_flight_setting, self.in_flight_default)

    def daily_hands(self):
        return self.settings.number(self.daily_setting, self.daily_default)

    def why_not(self, starts_hand: bool):
        """Why a request is refused, or None."""
        if not self.allowed():
            return (f"{self.title} benchmarking is turned off: {self.allow_setting} is not true "
                    "in the repo's .env (only the owner can change it).")
        missing = self.missing_config()
        if missing:
            return missing
        if starts_hand and self.usage.today(self.name) >= self.daily_hands():
            return (f"{self.title}: the daily cap of {self.daily_hands():,} hands across every bot "
                    f"is reached ({self.daily_setting}). It resets at midnight.")
        return None

    def missing_config(self):
        return None

    def route(self, method: str, path: str):
        """(upstream path, starts_hand) for a call we forward, or None."""
        raise NotImplementedError

    def prepare(self, upstream_path: str, body: bytes):
        """(body, headers, local response or None) for the upstream call."""
        return body, {}, None

    def status(self) -> dict:
        why = self.why_not(starts_hand=True)
        return {"title": self.title, "allowed": why is None, "why": why,
                "switch": self.allow_setting, "switched_on": self.allowed(),
                "upstream": self.url(), "max_in_flight": self.max_in_flight(),
                "in_flight": self.gate.in_flight, "waiting": self.gate.waiting,
                "daily_hands": self.daily_hands(), "hands_today": self.usage.today(self.name),
                "requests": self.requests, "errors": self.errors, "refused": self.refused}

    def handle(self, method: str, path: str, body: bytes):
        """(status, JSON-able or bytes) for one client request."""
        route = self.route(method, path)
        if route is None:
            return 404, {self.error_key: f"the proxy doesn't forward {method} {path}"}
        upstream_path, starts_hand = route
        why = self.why_not(starts_hand)
        if why:
            with self.lock:
                self.refused += 1
            log(f"refused {self.name} {path}: {why}")
            return 403, {self.error_key: why}
        body, headers, local = self.prepare(upstream_path, body)
        if local is not None:
            return 200, local
        self.gate.acquire(self.max_in_flight())
        try:
            status, data = forward(self.url() + upstream_path, method, body, headers)
        finally:
            self.gate.release()
        with self.lock:
            self.requests += 1
            if status >= 400:
                self.errors += 1
        if starts_hand and status < 400:
            self.usage.add(self.name)
        return status, data


class SlumbotUpstream(Upstream):
    name, title = "slumbot", "Slumbot"
    allow_setting, allow_default = "ALLOW_SLUMBOT_BENCHMARKING", True
    in_flight_setting, in_flight_default = "SLUMBOT_PROXY_MAX_IN_FLIGHT", 3
    daily_setting, daily_default = "SLUMBOT_PROXY_DAILY_HANDS", 20_000
    url_setting, url_default = "SLUMBOT_UPSTREAM_URL", "https://slumbot.com/slumbot/api"
    error_key = "error_msg"
    CALLS = {"login": False, "new_hand": True, "act": False}

    def route(self, method, path):
        m = re.fullmatch(r"/slumbot/api/(\w+)", path)
        if method == "POST" and m and m.group(1) in self.CALLS:
            return "/" + m.group(1), self.CALLS[m.group(1)]
        return None

    def prepare(self, upstream_path, body):
        if upstream_path == "/login":
            user = self.settings.get("SLUMBOT_USERNAME")
            password = self.settings.get("SLUMBOT_PASSWORD")
            if not (user and password):
                return body, {}, {"token": None}        # anonymous sessions
            body = json.dumps({"username": user, "password": password}).encode()
        return body, {}, None

    def status(self):
        user = self.settings.get("SLUMBOT_USERNAME")
        return {**super().status(), "account": bool(user and self.settings.get("SLUMBOT_PASSWORD"))}


class GTOWizardUpstream(Upstream):
    name, title = "gto_wizard", "GTO Wizard"
    allow_setting, allow_default = "ALLOW_GTO_WIZARD_BENCHMARKING", False
    in_flight_setting, in_flight_default = "GTO_WIZARD_PROXY_MAX_IN_FLIGHT", 20
    daily_setting, daily_default = "GTO_WIZARD_PROXY_DAILY_HANDS", 5_000
    url_setting, url_default = "GTO_WIZARD_UPSTREAM_URL", "https://researcher.gtowizard.com"
    error_key = "detail"

    def route(self, method, path):
        if method != "POST":
            return None
        if path == "/gtow/hands":
            return "/hands", True
        m = re.fullmatch(r"/gtow/hands/(\d+)/act", path)
        if m:
            return f"/hands/{m.group(1)}/act", False
        return None

    def missing_config(self):
        if not self.settings.get("GTO_WIZARD_API_KEY"):
            return "GTO Wizard: GTO_WIZARD_API_KEY is not set in the repo's .env."
        return None

    def prepare(self, upstream_path, body):
        return body, {"X-API-Key": self.settings.get("GTO_WIZARD_API_KEY")}, None

    def status(self):
        return {**super().status(), "key_configured": self.missing_config() is None,
                "daily_hands_per_bot": self.settings.number(GTOW_DAILY_PER_BOT_SETTING,
                                                            GTOW_DAILY_PER_BOT_DEFAULT)}


def forward(url: str, method: str, body: bytes, headers: dict):
    """One upstream call. Returns (status, bytes); network failures are 504s."""
    headers = {"Content-Type": "application/json",
               "User-Agent": f"poker-tournament-runner/{_version()}", **headers}
    req = urllib.request.Request(url, data=body if method == "POST" else None, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 504, {"error_msg": f"upstream unreachable: {e}", "detail": f"upstream unreachable: {e}"}


def log(msg: str):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + msg, file=sys.stderr, flush=True)


class Proxy:
    """The upstreams and the HTTP server in front of them."""

    def __init__(self, settings: Settings = None, usage: Usage = None):
        self.settings = settings or Settings()
        self.usage = usage or Usage()
        self.upstreams = {u.name: u for u in (SlumbotUpstream(self.settings, self.usage),
                                               GTOWizardUpstream(self.settings, self.usage))}
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.httpd = None

    def status(self) -> dict:
        return {"ok": True, "version": _version(), "started": self.started,
                "upstreams": {n: u.status() for n, u in self.upstreams.items()}}

    def upstream_for(self, path: str):
        if path.startswith("/slumbot/"):
            return self.upstreams["slumbot"]
        if path.startswith("/gtow/"):
            return self.upstreams["gto_wizard"]
        return None

    def make_server(self, host: str = HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "poker-proxy"

            def log_message(self, fmt, *args):      # quiet: refusals and errors are logged
                pass

            def _send(self, status, data):
                raw = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/health":
                    return self._send(200, {"ok": True})
                if self.path == "/status":
                    return self._send(200, proxy.status())
                self._send(404, {"error_msg": f"no GET {self.path}"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send(413, {"error_msg": "request too large"})
                body = self.rfile.read(length) if length else b"{}"
                upstream = proxy.upstream_for(self.path)
                if upstream is None:
                    return self._send(404, {"error_msg": f"no POST {self.path}"})
                try:
                    status, data = upstream.handle("POST", self.path, body)
                except Exception as e:              # never take the proxy down
                    log(f"error {self.path}: {e!r}")
                    status, data = 502, {upstream.error_key: f"proxy error: {e}"}
                try:
                    self._send(status, data)
                except (BrokenPipeError, ConnectionResetError):
                    log(f"client went away before the reply to {self.path}")

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.httpd = Server((host, port), Handler)
        return self.httpd


def fetch_status(url: str = None, timeout: float = 3) -> dict:
    """A running proxy's /status (raises OSError/ValueError if it isn't up)."""
    url = (url or os.environ.get("POKER_PROXY_URL") or DEFAULT_URL).rstrip("/")
    with urllib.request.urlopen(url + "/status", timeout=timeout) as r:
        return json.loads(r.read())


def describe(status: dict) -> str:
    lines = []
    for u in status["upstreams"].values():
        state = "on" if u["allowed"] else f"OFF ({u['why']})"
        lines.append(f"  {u['title']}: {state}; {u['in_flight']}/{u['max_in_flight']} in flight, "
                     f"{u['waiting']} waiting; {u['hands_today']:,}/{u['daily_hands']:,} hands today")
    return "\n".join(lines)


def serve(host: str = HOST, port: int = DEFAULT_PORT):
    proxy = Proxy()
    try:
        httpd = proxy.make_server(host, port)
    except OSError as e:
        raise SystemExit(f"error: can't listen on {host}:{port} ({e}). Is a proxy already running? "
                         f"Check with `poker proxy --status`.")
    log(f"Outside-bot proxy on http://{host}:{port} (Ctrl+C to stop)\n" + describe(proxy.status()))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
