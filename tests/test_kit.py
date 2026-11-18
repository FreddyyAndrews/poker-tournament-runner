"""The hackathon starter kit is generated from pokercore/ and kit/ by
scripts/publish_kit.py. These tests keep it honest: the kit must carry exactly
the tournament's core, contain nothing else from the runner, install the
sandbox's library versions, and play a match on its own."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import pokercore

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("publish_kit", REPO / "scripts" / "publish_kit.py")
publish_kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish_kit)


def test_the_published_kit_carries_this_core():
    """Fails whenever pokercore/ changes without the kit being republished."""
    published = REPO / "kit" / "published.json"
    assert published.exists(), "the kit has never been published: python3 scripts/publish_kit.py DEST"
    recorded = json.loads(published.read_text())["core_hash"]
    assert recorded == pokercore.core_hash(), (
        f"pokercore/ has changed since the kit was published (published {recorded}, "
        f"now {pokercore.core_hash()}). Republish with python3 scripts/publish_kit.py DEST "
        "and tell entrants to pull, so nobody tests against code the tournament doesn't run.")


def _pins(text):
    return dict(re.findall(r"([A-Za-z][\w.-]*)==([\w.]+)", text))


def test_the_kit_installs_the_sandbox_library_versions():
    docker = _pins((REPO / "pokercore" / "sandbox" / "Dockerfile").read_text())
    kit = _pins((REPO / "kit" / "requirements.txt").read_text())
    kit.update(_pins((REPO / "kit" / "Makefile").read_text()))      # eval7, installed separately
    assert kit == docker, f"kit installs {kit}, the sandbox has {docker}"


@pytest.fixture
def kit(tmp_path):
    dest = tmp_path / "kit"
    publish_kit.publish(dest, record=False)
    return dest


def test_the_kit_is_the_core_plus_its_own_files_and_nothing_else(kit):
    files = {p.relative_to(kit).as_posix() for p in kit.rglob("*") if p.is_file()}
    core = {f"pokercore/{f}" for f in pokercore.core_files()}
    own = {p.relative_to(REPO / "kit").as_posix() for p in (REPO / "kit").rglob("*")
           if p.is_file() and "__pycache__" not in p.parts and p.name != "published.json"}
    assert files == core | own | {"CORE_VERSION", ".kit-files"}
    assert publish_kit.check(kit) == 0
    text = "\n".join((kit / f).read_text(errors="ignore") for f in files).lower()
    for private in ("jev", "slumbot", "gto wizard", "gto_wizard", "arena", "bench", "champions?", "proxy"):
        assert not re.search(rf"\b{private}\b", text), f"the kit mentions {private!r}"


def test_check_catches_an_edited_core_and_republishing_cleans_up(kit):
    (kit / "pokercore" / "engine.py").write_text("# edited\n")
    (kit / "pokercore" / "extra.py").write_text("")
    (kit / "bots" / "stale").mkdir()
    (kit / "bots" / "stale" / "bot.py").write_text("")
    (kit / ".kit-files").write_text((kit / ".kit-files").read_text() + "bots/stale/bot.py\n")
    (kit / "notes.txt").write_text("mine")
    assert publish_kit.check(kit) == 1
    publish_kit.publish(kit, record=False)
    assert publish_kit.check(kit) == 0
    assert not (kit / "pokercore" / "extra.py").exists()
    assert not (kit / "bots" / "stale" / "bot.py").exists()
    assert (kit / "notes.txt").read_text() == "mine"            # not the kit's to remove


def test_the_kit_plays_a_match_on_its_own(kit):
    out = subprocess.run([sys.executable, "play.py", "bots/template", "bots/caller", "--seed", "1",
                          "--blinds", "turbo", "--log", "match.jsonl"],
                         cwd=kit, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"core {pokercore.core_hash()}" in out.stdout
    assert "one player left" in out.stdout
    events = [json.loads(line) for line in (kit / "match.jsonl").read_text().splitlines()]
    decision = next(e for e in events if e["type"] == "decision")
    for key in ("blinds", "hand_num", "your_bot_id", "button_seat", "action_log"):
        assert key in decision["state"], key
    validate = subprocess.run([sys.executable, "pokercore/validator.py", "bots/template"],
                              cwd=kit, capture_output=True, text=True, timeout=120)
    assert validate.returncode == 0, validate.stdout + validate.stderr
