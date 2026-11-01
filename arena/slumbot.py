"""Slumbot (slumbot.com), through its public API (see arena/external.py for
how matches against it are played and logged).

The API (https://slumbot.com/sample_api.py): POST /slumbot/api/new_hand
starts a hand and POST /slumbot/api/act sends one action ({"token", "incr"}).
Each response has the whole action so far as a string like "b200c/kb400"
("k" "c" "f", "bX" = total bet on the street, "/" = end of a street), our
position (client_pos 1 = small blind), our cards and the board; once the
hand is over, Slumbot's cards, our winnings and the baseline: what Slumbot
won with our cards against itself. winnings - baseline is a luck-adjusted
score (Davidson, Archibald & Bowling, "Baseline: practical control variates
for agent evaluation in zero-sum domains", AAMAS 2013).

No key is needed: the first new_hand returns a session token, sent with
every request after it (it can change; we always keep the latest). A session
plays one hand at a time, so playing several at once uses several sessions.
Slumbot is a free service run by one person, so we keep it gentle: at most
MAX_WORKERS sessions at once per match, retries that back off, and every call
goes through the outside-bot proxy (arena/proxy.py), which caps the requests
in flight and the hands per day across every process and logs in to the
Slumbot account when one is configured.
"""

import json
import os
import queue
import re
import time
import urllib.error
import urllib.request

import arena
from arena import external
from arena.external import ExternalError, View

API_URL = external.PROXY_URL + "/slumbot/api"        # Slumbot's API, through the proxy
NAME = "slumbot"
DEFAULT_WORKERS = 2
MAX_WORKERS = 3


class SlumbotError(ExternalError):
    pass


class Client:
    """Slumbot's calls through the proxy, with retries on server errors and
    timeouts. The proxy logs in when the owner set up a Slumbot account
    (results then count on slumbot.com under it); otherwise sessions are
    anonymous. The timeout is long because the proxy may queue a request
    while other bots play."""

    RETRY_STATUS = (429, 500, 502, 503, 504)

    def __init__(self, url: str = API_URL, retries: int = 5, timeout_s: float = external.PROXY_WAIT_S):
        self.url = url.rstrip("/")
        self.retries = retries
        self.timeout_s = timeout_s

    def _post(self, path, body):
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json",
                   "User-Agent": f"poker-tournament-runner/{arena.__version__}"}
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(f"{self.url}/{path}", data=data, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    resp = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                detail = external.http_error_detail(e.read().decode(errors="replace")[:500])
                if e.code not in self.RETRY_STATUS or attempt == self.retries:
                    raise SlumbotError(f"{path}: HTTP {e.code} {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == self.retries:
                    raise SlumbotError(f"{path}: {e}") from None
            time.sleep(min(2 ** attempt, 30))
        if "error_msg" in resp:
            raise SlumbotError(f"{path}: {resp['error_msg']}")
        return resp

    def login(self):
        """A session token for the owner's account, or None (anonymous). The
        proxy adds the credentials."""
        return self._post("login", {}).get("token")

    def new_hand(self, token=None) -> dict:
        return self._post("new_hand", {"token": token} if token else {})

    def act(self, token, incr: str) -> dict:
        return self._post("act", {"token": token, "incr": incr})


def history(action: str) -> list:
    """Slumbot's action string as history tokens: "b200c/kb400" ->
    ["b200", "c", "_", "k", "b400"]."""
    tokens = re.findall(r"b\d+|[kcf/]", action or "")
    if "".join(tokens) != (action or ""):
        raise SlumbotError(f"can't read Slumbot's action {action!r}")
    return ["_" if t == "/" else t for t in tokens]


class Session:
    """One Slumbot session (a token), playing one hand at a time."""

    def __init__(self, token=None):
        self.token = token
        self.hands = 0


def to_view(resp: dict, session: Session) -> View:
    over = resp.get("winnings") is not None
    if resp.get("token"):
        session.token = resp["token"]
    winnings = resp.get("winnings")
    baseline = resp.get("baseline_winnings")
    return View(handle=session, hand_ref=f"{(session.token or '')[:8]}#{session.hands}",
                we_are_sb=resp["client_pos"] == 1, hole=list(resp["hole_cards"]),
                board=list(resp.get("board") or []), history=history(resp["action"]), over=over,
                winnings=winnings,
                score=(winnings - baseline) if over and baseline is not None else None,
                opp_hole=list(resp.get("bot_hole_cards") or []) if over else [])


class Backend:
    """Slumbot's API as Views. Hands in play at once each get their own
    session; sessions are reused hand after hand."""

    def __init__(self, client=None):
        self.client = client or Client()
        self.idle = queue.SimpleQueue()        # sessions between hands
        self._login_token = None

    def _session(self) -> Session:
        try:
            return self.idle.get_nowait()
        except queue.Empty:
            if self._login_token is None:
                self._login_token = self.client.login() or ""
                return Session(self._login_token or None)
            return Session()

    def new_hand(self) -> View:
        session = self._session()
        session.hands += 1
        view = to_view(self.client.new_hand(session.token), session)
        if view.over:
            self.idle.put(session)
        return view

    def act(self, session: Session, code, amount=None) -> View:
        incr = f"b{int(amount)}" if code == "b" else code
        view = to_view(self.client.act(session.token, incr), session)
        if view.over:
            self.idle.put(session)
        return view


class Slumbot(external.Opponent):
    name = NAME
    title = "Slumbot"
    score_kind = "baseline"
    default_workers = DEFAULT_WORKERS
    max_workers = MAX_WORKERS

    def check_configured(self):
        external.check_proxy(NAME)

    def backend(self):
        return Backend()

    def info(self) -> dict:
        return {**super().info(), "api": API_URL}
