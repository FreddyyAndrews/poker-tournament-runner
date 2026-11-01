"""GTO Wizard AI, through its benchmark API (see arena/external.py for how
matches against it are played and logged).

The API: POST /hands starts a hand, POST /hands/<id>/act sends our action,
and every response is the full state with GTO Wizard's replies already
applied: positions, our cards, the board, the action history ("f", "k", "c",
"bX", "_"), our legal actions and raise range, and once the hand is over the
chips won, the AIVAT score and GTO Wizard's cards. It needs an API key
(GTO_WIZARD_API_KEY) and allows about 100,000 hands a month and 20 hands at
once per key.

Every call goes through the outside-bot proxy (arena/proxy.py), which holds
the key, refuses everything unless the owner turned GTO Wizard on
(ALLOW_GTO_WIZARD_BENCHMARKING), and caps the hands in play and per day
across every process. This process never sees the key.
"""

import json
import os
import time
import urllib.error
import urllib.request

import arena
from arena import external, proxy
from arena.external import BB, SB, STARTING_STACK, ExternalError, View

API_URL = external.PROXY_URL + "/gtow"     # GTO Wizard's API, through the proxy
GAME_NAME = "HUNL 200BB"
NAME = "gto_wizard"                 # GTO Wizard's name in our logs
API_NAME = "GTO Wizard"             # ... and in the API's player list
DEFAULT_WORKERS = 5                 # hands in play at once (as GTO Wizard's own client)
MAX_WORKERS = 20                    # GTO Wizard's limit
# Hands one bot may play against GTO Wizard per day. The key's ~100k hands a
# month are shared by every bot (and agent) using it.
DAILY_CAP_SETTING = proxy.GTOW_DAILY_PER_BOT_SETTING
DEFAULT_DAILY_CAP = proxy.GTOW_DAILY_PER_BOT_DEFAULT


class GTOWizardError(ExternalError):
    pass


class Client:
    """The few API calls we need, through the proxy (which adds the key), with
    retries on rate limits and server errors. The timeout is long because
    the proxy may queue a request while other bots play."""

    RETRY_STATUS = (429, 500, 502, 503, 504)

    def __init__(self, url: str = API_URL, retries: int = 5, timeout_s: float = external.PROXY_WAIT_S):
        self.url = url.rstrip("/")
        self.retries = retries
        self.timeout_s = timeout_s

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        # the API rejects urllib's default User-Agent with a 403
        headers = {"Content-Type": "application/json",
                   "User-Agent": f"poker-tournament-runner/{arena.__version__}"}
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                detail = external.http_error_detail(e.read().decode(errors="replace")[:500])
                if e.code not in self.RETRY_STATUS or attempt == self.retries:
                    raise GTOWizardError(f"{method} {path}: HTTP {e.code} {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == self.retries:
                    raise GTOWizardError(f"{method} {path}: {e}") from None
            time.sleep(min(2 ** attempt, 30))

    def new_hand(self) -> dict:
        return self._request("POST", "/hands", {"game_name": GAME_NAME})

    def act(self, hand_id: int, action: str, amount: int = None) -> dict:
        body = {"action": action} if amount is None else {"action": action, "amount": int(amount)}
        return self._request("POST", f"/hands/{hand_id}/act", body)


def _cards(s: str) -> list:
    return [s[i:i + 2] for i in range(0, len(s or ""), 2)]


def check_game(resp: dict):
    game = resp["game"]
    sb, bb = sorted(int(b) for b in game["blinds"])
    if (int(game["starting_stack"]), sb, bb) != (STARTING_STACK, SB, BB) or \
            not game["stack_reset_per_hand"]:
        raise GTOWizardError(f"GTO Wizard's game changed: {game}; arena/external.py assumes "
                             f"{STARTING_STACK} stacks at {SB}/{BB}, reset every hand")


def to_view(resp: dict) -> View:
    gs = resp["game_state"]
    ours = next(p for p in gs["players"] if p["name"] != API_NAME)
    theirs = next(p for p in gs["players"] if p["name"] == API_NAME)
    return View(handle=resp["hand_id"], hand_ref=resp["hand_id"],
                we_are_sb=ours["position"] == "SB", hole=_cards(ours["hole_cards"]),
                board=_cards(gs["board_cards"]), history=list(gs["action_history"]),
                over=gs["is_hand_over"],
                legal=gs["legal_actions"] if gs["player_to_act"] == "user" else None,
                raise_range=gs["raise_range"], winnings=gs["winnings"],
                total_pot=gs["total_pot"], score=gs["aivat_score"],
                opp_hole=_cards(theirs["hole_cards"]) if gs["is_hand_over"] else [])


class Backend:
    """GTO Wizard's API as Views (wraps a Client, or a fake one in tests)."""

    def __init__(self, client=None):
        self.client = client or Client()

    def new_hand(self) -> View:
        resp = self.client.new_hand()
        check_game(resp)
        return to_view(resp)

    def act(self, hand_id, code, amount=None) -> View:
        return to_view(self.client.act(hand_id, code, amount))


class GTOWizard(external.Opponent):
    name = NAME
    title = "GTO Wizard"
    score_kind = "AIVAT"
    default_workers = DEFAULT_WORKERS
    max_workers = MAX_WORKERS

    cap_setting = DAILY_CAP_SETTING

    def check_configured(self):
        external.check_proxy(NAME)

    def daily_cap_per_bot(self):
        """From the environment if set there, else from the proxy (the owner's
        setting in the repo's secrets file), else the default."""
        value = os.environ.get(DAILY_CAP_SETTING)
        if value:
            return int(value)
        try:
            return int(external.proxy_status()["upstreams"][NAME]["daily_hands_per_bot"])
        except ExternalError:
            return DEFAULT_DAILY_CAP

    def backend(self):
        return Backend()

    def info(self) -> dict:
        return {**super().info(), "api": API_URL, "game_name": GAME_NAME}


def run_gtow_match(bot_spec, hands: int, client=None, **kw) -> dict:
    """Play `bot_spec` against GTO Wizard (see external.run_external_match).
    client: a GTO Wizard Client (default: a real one)."""
    return external.run_external_match(GTOWizard(), bot_spec, hands,
                                       backend=Backend(client) if client else None, **kw)
