"""Tests for compressed match logs (arena/events.py): a log is plain while its
match plays and gzipped when it ends, and every reader takes either form."""
import gzip
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arena import events
from arena.events import EVENTS_FILE, EVENTS_GZ, EventLog


def write_log(d: Path, n_hands=3, end=True) -> list:
    """A small finished (or unfinished) match log, kept plain."""
    os.environ["POKER_COMPRESS_LOGS"] = "0"
    try:
        with EventLog(d) as log:
            out = [log.emit("match_start", match_id=d.name, seed=1, config={}, seats=[])]
            for h in range(n_hands):
                out.append(log.emit("hand_start", hand=h))
                out.append(log.emit("decision", hand=h, bot="a", state={"pot": 150 + h}))
                out.append(log.emit("hand_end", hand=h, deltas={"a": 10, "b": -10}))
            if end:
                out.append(log.emit("match_end", hands=n_hands, stopped="hand_limit"))
    finally:
        del os.environ["POKER_COMPRESS_LOGS"]
    return out


def test_a_finished_match_leaves_only_a_compressed_log(tmp_path):
    d = tmp_path / "m"
    with EventLog(d) as log:
        sent = [log.emit("match_start", seed=1), log.emit("hand_start", hand=0),
                log.emit("match_end", hands=1)]
        assert (d / EVENTS_FILE).is_file()           # plain while it plays
    assert not (d / EVENTS_FILE).exists() and (d / EVENTS_GZ).is_file()
    assert events.read_events(d) == sent
    assert events.has_events(d) and events.is_compressed(d)
    assert events.last_event(d)["type"] == "match_end"
    assert not list(d.glob("*.tmp"))


def test_compression_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("POKER_COMPRESS_LOGS", "0")
    with EventLog(tmp_path / "m") as log:
        log.emit("match_start")
    assert (tmp_path / "m" / EVENTS_FILE).is_file() and not (tmp_path / "m" / EVENTS_GZ).exists()


def test_compress_keeps_every_byte_and_the_modification_times(tmp_path):
    d = tmp_path / "m"
    sent = write_log(d)
    raw = (d / EVENTS_FILE).read_bytes()
    old = time.time() - 5 * 86400
    os.utime(d / EVENTS_FILE, (old, old))
    os.utime(d, (old, old))
    assert events.compress_events(d)
    assert gzip.open(d / EVENTS_GZ, "rb").read() == raw
    assert events.read_events(d) == sent
    assert abs((d / EVENTS_GZ).stat().st_mtime - old) < 1    # the UI sorts by these
    assert abs(d.stat().st_mtime - old) < 1
    assert events.compress_events(d) is False                # nothing left to do


def test_a_bad_compressed_copy_keeps_the_plain_log(tmp_path, monkeypatch):
    d = tmp_path / "m"
    write_log(d)
    monkeypatch.setattr(events, "_sha256", lambda fh: "not the same")
    with pytest.raises(OSError, match="doesn't match"):
        events.compress_events(d)
    assert (d / EVENTS_FILE).is_file() and not (d / EVENTS_GZ).exists()
    assert not list(d.glob("*.tmp"))


def test_readers_skip_a_partial_last_line_of_a_live_log(tmp_path):
    d = tmp_path / "m"
    sent = write_log(d, end=False)
    with open(d / EVENTS_FILE, "a") as fh:
        fh.write('{"type": "hand_st')                       # still being written
    assert events.read_events(d) == sent
    assert list(events.iter_lines(d, 2)) == [json.dumps(e) for e in sent[2:]]


def test_open_events_finds_a_log_compressed_after_the_check(tmp_path, monkeypatch):
    d = tmp_path / "m"
    sent = write_log(d)
    real_open, done = open, []

    def open_after_compressing(path, *a, **kw):
        # the plain log goes (compressed) between choosing it and opening it
        if Path(path).name == EVENTS_FILE and not done:
            done.append(1)
            events.compress_events(Path(path).parent)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", open_after_compressing)
    assert events.read_events(d) == sent


def test_finished_tells_live_logs_from_finished_ones(tmp_path):
    with_summary, with_end, live, stale = (tmp_path / n for n in ("s", "e", "l", "x"))
    write_log(with_summary, end=False)
    (with_summary / "summary.json").write_text("{}")
    write_log(with_end)
    write_log(live, end=False)
    write_log(stale, end=False)
    old = time.time() - events.STALE_S - 60
    os.utime(stale / EVENTS_FILE, (old, old))
    assert events.finished(with_summary) and events.finished(with_end) and events.finished(stale)
    assert not events.finished(live)


def test_compress_all_skips_live_logs_and_dry_run_changes_nothing(tmp_path):
    root = tmp_path / "runs"
    write_log(root / "done")
    write_log(root / "run1" / "matches" / "m1")
    write_log(root / "playing", end=False)
    dry = events.compress_all([root], dry_run=True)
    assert (dry["compressed"], dry["skipped_live"]) == (2, 1)
    assert not list(root.rglob(EVENTS_GZ))
    out = events.compress_all([root, tmp_path / "missing"])
    assert (out["compressed"], out["skipped_live"], out["failed"]) == (2, 1, 0)
    assert 0 < out["after"] < out["before"]
    assert (root / "playing" / EVENTS_FILE).is_file()
    assert sorted(p.parent.name for p in root.rglob(EVENTS_GZ)) == ["done", "m1"]


def test_find_run_accepts_compressed_logs(tmp_path):
    d = tmp_path / "m"
    write_log(d)
    events.compress_events(d)
    assert events.find_run(str(d)) == d
    assert events.find_run(str(d / EVENTS_GZ)) == d
    assert events.find_run("m", runs_dir=tmp_path) == d


def test_cli_compress(tmp_path, capsys):
    from arena import cli
    write_log(tmp_path / "a")
    write_log(tmp_path / "b", end=False)
    cli.main(["compress", str(tmp_path), "--dry-run"])
    assert "Would compress 1 match logs" in capsys.readouterr().out
    cli.main(["compress", str(tmp_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["compressed"] == 1 and out["skipped_live"] == 1
    assert (tmp_path / "a" / EVENTS_GZ).is_file()


# -- the server reads compressed logs -------------------------------------------

@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from arena.server import create_app
    with TestClient(create_app(tmp_path / "runs")) as c:
        yield c


def sse(client, key, on_first=None):
    """(events, end reason) from a match's live stream."""
    got, reason, kind = [], None, None
    with client.stream("GET", f"/api/matches/{key}/stream") as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                kind = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if kind == "end":
                    reason = data["reason"]
                    break
                got.append(data)
                if on_first and len(got) == 1:
                    on_first()
    return got, reason


def test_server_serves_a_compressed_match(client, tmp_path):
    d = tmp_path / "runs" / "m"
    sent = write_log(d)
    events.compress_events(d)
    assert client.get("/api/matches/m").json()["events"] == len(sent)
    assert client.get("/api/matches/m/events").json()["events"] == sent
    assert client.get("/api/matches/m/events", params={"from": 4}).json()["events"] == sent[4:]
    assert sse(client, "m") == (sent, "match_end")
    assert any(r["id"] == "m" for r in client.get("/api/runs").json())


def test_a_live_stream_carries_on_when_the_log_is_compressed(client, tmp_path, monkeypatch):
    """The viewer has read part of the plain log (and half a line) when the
    match ends and its log is compressed: it gets the rest, nothing twice."""
    import asyncio
    from arena.server import app as app_module
    d = tmp_path / "runs" / "m"
    sent = write_log(d)
    full = (d / EVENTS_FILE).read_text()
    lines = full.splitlines(keepends=True)
    (d / EVENTS_FILE).write_text("".join(lines[:5]) + lines[5][:10])
    swapped = []

    class Asyncio:
        """The stream's asyncio, ending the match during its first wait
        between two reads of the log."""
        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, s):
            if not swapped:
                swapped.append(1)
                (d / EVENTS_FILE).write_text(full)
                events.compress_events(d)
            await asyncio.sleep(s)

    monkeypatch.setattr(app_module, "asyncio", Asyncio())
    got, reason = sse(client, "m")
    assert swapped and not (d / EVENTS_FILE).exists()
    assert got == sent and reason == "match_end"


def test_export_holds_the_plain_log_filtered_for_one_bot(tmp_path):
    import zipfile
    from arena.export import export
    d = tmp_path / "m"
    with EventLog(d) as log:
        log.emit("match_start", seats=[{"name": "a", "seat": 0}, {"name": "b", "seat": 1}])
        log.emit("hand_start", hand=0, hole={"a": ["Ah", "Kd"], "b": ["2c", "3c"]})
        log.emit("match_end", hands=1)
    assert events.is_compressed(d)
    with zipfile.ZipFile(export(d, tmp_path / "all.zip")["path"]) as z:
        assert "m/events.jsonl" in z.namelist() and "m/events.jsonl.gz" not in z.namelist()
        assert [json.loads(l)["type"] for l in z.read("m/events.jsonl").decode().splitlines()] == \
            ["match_start", "hand_start", "match_end"]
    with zipfile.ZipFile(export(d, tmp_path / "a.zip", bot="a")["path"]) as z:
        text = z.read("m/events.jsonl").decode()
    assert "Ah" in text and "2c" not in text         # b's private cards stay out
