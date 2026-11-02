"""Tests for the outside-bot proxy (arena/proxy.py): a real proxy in front of a
fake upstream on localhost, with the real Slumbot and GTO Wizard clients.
No network beyond 127.0.0.1."""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arena import gtow, proxy, slumbot

SECRET_KEY = "gtow-secret-key-123"  # gitleaks:allow (made up, for the fake API)
SECRET_PASSWORD = "slumbot-secret-pw-456"  # gitleaks:allow (made up)


class FakeUpstream:
    """Slumbot's and GTO Wizard's calls, recording what arrives and how many
    requests are in flight at once."""

    def __init__(self, delay_s=0.0):
        self.delay_s = delay_s
        self.requests = []
        self.in_flight = self.max_in_flight = 0
        self.lock = threading.Lock()
        up = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                with up.lock:
                    up.in_flight += 1
                    up.max_in_flight = max(up.max_in_flight, up.in_flight)
                    up.requests.append({"path": self.path, "body": body, "headers": dict(self.headers)})
                time.sleep(up.delay_s)
                with up.lock:
                    up.in_flight -= 1
                if self.path == "/login":
                    resp = {"token": "account-token"}
                elif self.path in ("/new_hand", "/act"):
                    resp = {"token": "t1", "action": "", "client_pos": 1, "hole_cards": ["Ah", "Kd"],
                            "board": []}
                elif self.path == "/v1/systemone":                # Jev
                    resp = {"answers": {"action": {"choice": "call"}},
                            "usage": {"input_tokens": 1000, "output_tokens": 100}}
                else:                                           # GTO Wizard
                    resp = {"hand_id": 7, "path": self.path}
                raw = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        class Server(ThreadingHTTPServer):
            daemon_threads = True

        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def paths(self):
        return [r["path"] for r in self.requests]


@pytest.fixture
def upstream():
    up = FakeUpstream()
    yield up
    up.httpd.shutdown()


@pytest.fixture
def secrets(tmp_path, upstream):
    """The owner's settings file, pointing both upstreams at the fake one."""
    path = tmp_path / "secrets.env"

    def write(**settings):
        base = {"SLUMBOT_UPSTREAM_URL": upstream.url, "GTO_WIZARD_UPSTREAM_URL": upstream.url}
        path.write_text("".join(f"{k}={v}\n" for k, v in {**base, **settings}.items()))
    write()
    write.path = path
    return write


@pytest.fixture
def run_proxy(tmp_path, secrets):
    """Start a proxy on a spare port; returns its URL and the Proxy."""
    started = []

    def start(environ=None):
        p = proxy.Proxy(proxy.Settings(secrets.path, environ=environ or {}),
                        proxy.Usage(tmp_path / "proxy_usage.json"))
        httpd = p.make_server("127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}", p
    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def post(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def slumbot_client(url):
    return slumbot.Client(url=url + "/slumbot/api", retries=0, timeout_s=10)


def gtow_client(url):
    return gtow.Client(url=url + "/gtow", retries=0, timeout_s=10)


def test_slumbot_calls_are_forwarded_and_hands_counted(run_proxy, upstream):
    url, p = run_proxy()
    c = slumbot_client(url)
    assert c.new_hand()["token"] == "t1"
    assert c.act("t1", "c")["hole_cards"] == ["Ah", "Kd"]
    assert upstream.paths() == ["/new_hand", "/act"]
    assert upstream.requests[1]["body"] == {"token": "t1", "incr": "c"}
    s = proxy.fetch_status(url)["upstreams"]["slumbot"]
    assert s["allowed"] and s["hands_today"] == 1 and s["requests"] == 2     # act starts no hand


def test_slumbot_login_is_anonymous_without_an_account(run_proxy, upstream):
    url, _ = run_proxy()
    assert slumbot_client(url).login() is None
    assert upstream.requests == []                  # nothing sent upstream


def test_slumbot_login_adds_the_owners_account(run_proxy, upstream, secrets):
    secrets(SLUMBOT_USERNAME="owner", SLUMBOT_PASSWORD=SECRET_PASSWORD)
    url, _ = run_proxy()
    assert slumbot_client(url).login() == "account-token"
    assert upstream.requests[0]["body"] == {"username": "owner", "password": SECRET_PASSWORD}
    status = proxy.fetch_status(url)
    assert status["upstreams"]["slumbot"]["account"] is True
    assert SECRET_PASSWORD not in json.dumps(status)


def test_switching_slumbot_off_refuses_at_once(run_proxy, upstream, secrets):
    url, _ = run_proxy()
    secrets(ALLOW_SLUMBOT_BENCHMARKING="false")      # no restart needed
    with pytest.raises(slumbot.SlumbotError, match="HTTP 403 Slumbot benchmarking is turned off"):
        slumbot_client(url).new_hand()
    assert upstream.requests == []
    secrets(ALLOW_SLUMBOT_BENCHMARKING="true")
    slumbot_client(url).new_hand()
    assert upstream.paths() == ["/new_hand"]


def test_gto_wizard_is_off_unless_the_owner_turns_it_on(run_proxy, upstream, secrets):
    secrets(GTO_WIZARD_API_KEY=SECRET_KEY)
    # the environment can't turn it on: only the owner's settings file can
    url, _ = run_proxy(environ={"ALLOW_GTO_WIZARD_BENCHMARKING": "true"})
    s = proxy.fetch_status(url)["upstreams"]["gto_wizard"]
    assert not s["allowed"] and "ALLOW_GTO_WIZARD_BENCHMARKING" in s["why"]
    with pytest.raises(gtow.GTOWizardError, match="HTTP 403 GTO Wizard benchmarking is turned off"):
        gtow_client(url).new_hand()
    assert upstream.requests == []


def test_gto_wizard_gets_the_key_and_clients_never_see_it(run_proxy, upstream, secrets):
    secrets(ALLOW_GTO_WIZARD_BENCHMARKING="true", GTO_WIZARD_API_KEY=SECRET_KEY)
    url, _ = run_proxy()
    c = gtow_client(url)
    assert c.new_hand()["path"] == "/hands"
    assert c.act(7, "c")["path"] == "/hands/7/act"
    assert [r["headers"].get("X-Api-Key") or r["headers"].get("X-API-Key")
            for r in upstream.requests] == [SECRET_KEY, SECRET_KEY]
    status = proxy.fetch_status(url)
    assert status["upstreams"]["gto_wizard"]["key_configured"] is True
    assert status["upstreams"]["gto_wizard"]["hands_today"] == 1
    assert SECRET_KEY not in json.dumps(status)


def test_gto_wizard_without_a_key_is_refused(run_proxy, secrets):
    secrets(ALLOW_GTO_WIZARD_BENCHMARKING="true")
    url, _ = run_proxy()
    s = proxy.fetch_status(url)["upstreams"]["gto_wizard"]
    assert not s["allowed"] and "GTO_WIZARD_API_KEY" in s["why"]


def test_daily_cap_is_shared_and_survives_a_restart(run_proxy, upstream, secrets):
    secrets(SLUMBOT_PROXY_DAILY_HANDS="2")
    url, _ = run_proxy()
    c = slumbot_client(url)
    c.new_hand()
    c.new_hand()
    with pytest.raises(slumbot.SlumbotError, match="daily cap of 2 hands"):
        c.new_hand()
    c.act("t1", "k")                                 # hands already started can finish
    url2, _ = run_proxy()                            # a new proxy reads the same counts
    assert proxy.fetch_status(url2)["upstreams"]["slumbot"]["hands_today"] == 2
    assert upstream.paths().count("/new_hand") == 2


def test_requests_queue_under_one_global_limit(tmp_path, secrets):
    up = FakeUpstream(delay_s=0.15)
    try:
        secrets(SLUMBOT_UPSTREAM_URL=up.url, SLUMBOT_PROXY_MAX_IN_FLIGHT="2")
        p = proxy.Proxy(proxy.Settings(secrets.path, environ={}), proxy.Usage(tmp_path / "u.json"))
        httpd = p.make_server("127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        results, seen_waiting = [], []

        def one():
            results.append(slumbot_client(url).new_hand())

        threads = [threading.Thread(target=one) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        seen_waiting.append(proxy.fetch_status(url)["upstreams"]["slumbot"]["waiting"])
        for t in threads:
            t.join(10)
        assert len(results) == 8                     # everyone got through, just slower
        assert up.max_in_flight == 2                 # never more than the limit upstream
        assert seen_waiting[0] >= 4
        httpd.shutdown()
        httpd.server_close()
    finally:
        up.httpd.shutdown()


def test_only_the_calls_a_match_makes_are_forwarded(run_proxy, upstream, secrets):
    secrets(ALLOW_GTO_WIZARD_BENCHMARKING="true", GTO_WIZARD_API_KEY=SECRET_KEY)
    url, _ = run_proxy()
    assert post(url + "/gtow/account")[0] == 404
    assert post(url + "/gtow/hands/7/delete")[0] == 404
    assert post(url + "/slumbot/api/anything")[0] == 404
    assert post(url + "/elsewhere")[0] == 404
    assert upstream.requests == []


def test_an_unreachable_upstream_is_a_504(run_proxy, secrets):
    secrets(SLUMBOT_UPSTREAM_URL="http://127.0.0.1:9")      # nothing listens there
    url, _ = run_proxy()
    status, body = post(url + "/slumbot/api/new_hand")
    assert status == 504 and "unreachable" in body["error_msg"]


def test_settings_file_wins_and_ignores_comments(tmp_path):
    path = tmp_path / "secrets.env"
    path.write_text("# a comment\nexport SLUMBOT_PROXY_DAILY_HANDS=7\nGTO_WIZARD_API_KEY='k'\n")
    s = proxy.Settings(path, environ={"SLUMBOT_PROXY_DAILY_HANDS": "99", "OTHER": "x"})
    assert s.number("SLUMBOT_PROXY_DAILY_HANDS", 1) == 7
    assert s.get("GTO_WIZARD_API_KEY") == "k" and s.get("OTHER") == "x"
    assert s.flag("ALLOW_GTO_WIZARD_BENCHMARKING", False) is False


def test_cli_status(run_proxy, capsys):
    from arena import cli
    url, _ = run_proxy()
    cli.main(["proxy", "--status", "--url", url])
    out = capsys.readouterr().out
    assert "Slumbot: on" in out and "GTO Wizard: OFF" in out
    cli.main(["proxy", "--status", "--url", url, "--json"])
    assert json.loads(capsys.readouterr().out)["upstreams"]["slumbot"]["allowed"] is True


def test_cli_status_without_a_proxy(capsys):
    from arena import cli
    with pytest.raises(SystemExit):
        cli.main(["proxy", "--status", "--url", "http://127.0.0.1:9"])
    assert "no outside-bot proxy" in capsys.readouterr().err


# -- Jev: paced, not capped --------------------------------------------------------

JEV_KEY = "jev-made-up-key-789"  # gitleaks:allow (made up, for the fake API)


def jev(url, bot="jev_test", body=None):
    """One Jev request through the proxy: (status, JSON body)."""
    headers = {"Content-Type": "application/json"}
    if bot:
        headers["X-Poker-Bot"] = bot
    req = urllib.request.Request(url + "/jev/v1/systemone", method="POST", headers=headers,
                                 data=json.dumps(body or {"state": "s", "questions": {}}).encode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def jev_secrets(secrets, upstream):
    def write(**settings):
        secrets(JEV_UPSTREAM_URL=upstream.url + "/v1/systemone", JEV_API_KEY=JEV_KEY, **settings)
    write()
    return write


def test_jev_gets_the_key_and_counts_each_bots_spend(run_proxy, upstream, jev_secrets):
    jev_secrets(JEV_PRICE_PER_MTOK="2")
    url, _ = run_proxy()
    status, body = jev(url, "jev_bot")
    assert status == 200 and body["answers"]["action"]["choice"] == "call"
    assert jev(url, "jev_bot_v2")[0] == 200
    assert upstream.requests[0]["headers"].get("Authorization") == f"Bearer {JEV_KEY}"
    s = proxy.fetch_status(url)["upstreams"]["jev"]
    assert s["allowed"] and s["key_configured"] and s["tokens_today"] == 2200
    assert s["bots_today_usd"] == {"jev_bot": 0.0022, "jev_bot_v2": 0.0022}   # 1,100 tokens at $2/M
    assert s["spent_today_usd"] == 0.0044
    assert JEV_KEY not in json.dumps(proxy.fetch_status(url))


def test_jev_needs_the_bots_name_and_a_compact_request(run_proxy, upstream, jev_secrets):
    jev_secrets(JEV_MAX_REQUEST_BYTES="500")
    url, _ = run_proxy()
    status, body = jev(url, bot=None)
    assert status == 400 and "X-Poker-Bot" in body["detail"]
    status, body = jev(url, body={"state": "x" * 1000})
    assert status == 413 and "compact state" in body["detail"]
    assert upstream.requests == []


def test_pacer_spaces_starts_evenly_in_arrival_order():
    clock = [100.0]
    slept = []

    def sleep(d):
        slept.append(round(d, 6))
        clock[0] += d
    pacer = proxy.Pacer(clock=lambda: clock[0], sleep=sleep)
    for _ in range(4):                                 # 4 arrive at once, at 60 a minute
        clock[0] = 100.0
        pacer.wait(60)
    assert slept == [1.0, 2.0, 3.0]                    # the first goes at once, then one a second
    clock[0] = 200.0
    pacer.wait(60)                                     # after a quiet spell, no wait
    assert slept == [1.0, 2.0, 3.0]
    assert pacer.last_minute() == 1


def test_jev_is_slowed_to_the_rate_not_refused(run_proxy, upstream, jev_secrets):
    jev_secrets(JEV_MAX_REQUESTS_PER_MINUTE="600")    # one every 0.1 s
    url, _ = run_proxy()
    results = []
    t0 = time.monotonic()
    threads = [threading.Thread(target=lambda: results.append(jev(url, "jev_bot")[0])) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 6
    assert time.monotonic() - t0 >= 0.45               # 6 starts, 0.1 s apart
    s = proxy.fetch_status(url)["upstreams"]["jev"]
    assert s["requests_last_minute"] == 6 and s["max_requests_per_minute"] == 600
    assert s["refused"] == 0 and len(upstream.requests) == 6


def test_jev_drops_a_request_whose_bot_hung_up(run_proxy, upstream, jev_secrets):
    jev_secrets(JEV_MAX_REQUESTS_PER_MINUTE="60")     # one a second
    url, p = run_proxy()
    assert jev(url, "jev_bot")[0] == 200               # takes the slot; the next waits ~1 s
    host, port = url.removeprefix("http://").split(":")
    body = b'{"state": "s", "questions": {}}'
    sock = socket.create_connection((host, int(port)))
    sock.sendall(b"POST /jev/v1/systemone HTTP/1.1\r\nHost: x\r\nX-Poker-Bot: jev_bot\r\n"
                 b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode()
                 + b"\r\n\r\n" + body)
    time.sleep(0.2)
    sock.close()                                       # the bot's decision timed out
    deadline = time.monotonic() + 5
    while p.upstreams["jev"].dropped == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert p.upstreams["jev"].dropped == 1
    assert len(upstream.requests) == 1                 # never sent, never paid for


def test_jev_reports_the_months_spend(tmp_path, jev_secrets, secrets):
    now = time.mktime((2026, 11, 21, 12, 0, 0, 0, 0, -1))
    usage = proxy.Usage(tmp_path / "u.json", now=lambda: now)
    usage.days = {"2026-11-03": {"jev_tokens": 7_000_000}, "2026-10-30": {"jev_tokens": 99_000_000},
                  "2026-11-21": {"jev_tokens": 500_000}}
    jev_secrets(JEV_PRICE_PER_MTOK="1")
    j = proxy.Proxy(proxy.Settings(secrets.path, environ={}), usage).upstreams["jev"]
    assert j.money() == {"spent_today_usd": 0.5, "spent_month_usd": 7.5}    # October doesn't count
    assert j.why_not() is None                                               # spend never refuses


def test_jev_can_be_switched_off(run_proxy, upstream, jev_secrets):
    jev_secrets(ALLOW_JEV="false")
    url, _ = run_proxy()
    status, body = jev(url)
    assert status == 403 and "ALLOW_JEV" in body["detail"]
    assert upstream.requests == []


def test_jev_prices_input_and_output_separately(run_proxy, upstream, jev_secrets):
    jev_secrets(JEV_PRICE_PER_MTOK="1", JEV_OUTPUT_PRICE_PER_MTOK="10")
    url, _ = run_proxy()
    assert jev(url, "jev_bot")[0] == 200                     # 1,000 in at $1/M + 100 out at $10/M
    s = proxy.fetch_status(url)["upstreams"]["jev"]
    assert (s["input_tokens_today"], s["output_tokens_today"]) == (1000, 100)
    assert s["spent_today_usd"] == 0.002 and s["bots_today_usd"] == {"jev_bot": 0.002}


def test_jev_default_prices(run_proxy, jev_secrets):
    url, _ = run_proxy()
    s = proxy.fetch_status(url)["upstreams"]["jev"]
    assert s["price_per_mtok"] == 0.04 and s["output_price_per_mtok"] == 0.04
