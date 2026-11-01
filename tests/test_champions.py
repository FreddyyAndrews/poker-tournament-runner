"""Tests for the champion gauntlet (arena/champions.py, `poker bench --gauntlet`)."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arena import bots as botlib
from arena import champions

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def gauntlet(tmp_path, monkeypatch):
    """A temporary champions/ folder, searched like the real one, and a
    my_bots/ with copies of two reference bots to freeze."""
    champs, mine = tmp_path / "champions", tmp_path / "my_bots"
    for name, src in (("alpha", "shark"), ("beta", "aggressor")):
        shutil.copytree(REPO / "bots" / src, mine / name, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(champions, "CHAMPIONS_DIR", champs)
    monkeypatch.setattr(champions, "INDEX_FILE", champs / "index.json")
    monkeypatch.setattr(botlib, "SEARCH_DIRS", [mine, REPO / "bots", champs])
    return {"champions": champs, "my_bots": mine}


def test_a_champion_is_an_exact_copy_that_resolves_by_name(gauntlet):
    (alpha,) = botlib.resolve_all(["alpha"])
    e = champions.add("alpha", note="first")
    assert e["name"] == f"alpha_{alpha.hash[:6]}" and e["hash"] == alpha.hash and e["note"] == "first"
    (champ,) = botlib.resolve_all([e["name"]])
    assert champ.hash == alpha.hash                      # same code, same bench history
    assert champions.names() == [e["name"]]
    assert json.loads((gauntlet["champions"] / "index.json").read_text())[0]["bot"] == "alpha"


def test_the_same_version_cant_be_added_twice_but_a_new_one_can(gauntlet):
    first = champions.add("alpha")
    with pytest.raises(ValueError, match="already a champion"):
        champions.add("alpha")
    with pytest.raises(ValueError, match="already a champion"):
        champions.add(first["name"])
    bot = gauntlet["my_bots"] / "alpha" / "bot.py"
    bot.write_text(bot.read_text() + "\n# v2\n")
    second = champions.add("alpha")
    assert second["name"] != first["name"] and len(champions.names()) == 2


def test_a_network_bot_cant_be_a_champion(gauntlet):
    d = gauntlet["my_bots"] / "netbot"
    d.mkdir()
    (d / "bot.py").write_text("import httpx\n\ndef decide(state):\n    return {'action': 'fold'}\n")
    with pytest.raises(ValueError, match="fails the validator"):
        champions.add("netbot")
    assert champions.names() == [] and not (gauntlet["champions"] / "netbot").exists()


def test_the_gauntlet_leaves_out_an_identical_copy_of_the_bot(gauntlet):
    a, b = champions.add("alpha"), champions.add("beta")
    (alpha,) = botlib.resolve_all(["alpha"])
    assert champions.opponents_for(alpha.hash) == [b["name"]]
    assert sorted(champions.opponents_for("someotherhash")) == sorted([a["name"], b["name"]])


def test_remove(gauntlet):
    e = champions.add("alpha")
    champions.remove(e["name"])
    assert champions.names() == [] and not (gauntlet["champions"] / e["name"]).exists()
    with pytest.raises(ValueError, match="no champion"):
        champions.remove(e["name"])


def test_bench_against_the_gauntlet(gauntlet, tmp_path):
    from arena.bench import bench
    champions.add("alpha")
    b = champions.add("beta")
    r = bench("alpha", hands=10, gauntlet=True, bench_dir=tmp_path / "bench")
    assert r["suite"] == "gauntlet" and [o["opponent"] for o in r["opponents"]] == [b["name"]]
    again = bench("alpha", hands=10, gauntlet=True, bench_dir=tmp_path / "bench")
    assert len(again["previous"]) == 1                   # compared only with the same suite
    reference = bench("alpha", hands=10, bench_dir=tmp_path / "bench")
    assert reference["suite"] == "reference" and reference["previous"] == []


def test_bench_gauntlet_errors(gauntlet, tmp_path):
    from arena.bench import bench
    with pytest.raises(ValueError, match="no champions yet"):
        bench("alpha", hands=10, gauntlet=True, bench_dir=tmp_path / "bench")
    champions.add("beta")
    with pytest.raises(ValueError, match="leave out --opponents"):
        bench("alpha", opponents=["shark"], hands=10, gauntlet=True, bench_dir=tmp_path / "bench")


def test_notes_are_not_part_of_the_bot(gauntlet):
    (before,) = botlib.resolve_all(["alpha"])
    notes = gauntlet["my_bots"] / "alpha" / "notes"
    notes.mkdir()
    (notes / "journal.md").write_text("tried a thing\n")
    (after,) = botlib.resolve_all(["alpha"])
    assert after.hash == before.hash                      # writing notes isn't a new version
    data = gauntlet["my_bots"] / "alpha" / "data"
    data.mkdir()
    (data / "notes").write_text("a data file that happens to be called notes")
    (changed,) = botlib.resolve_all(["alpha"])
    assert changed.hash != before.hash                     # only the top-level notes/ is left out
    e = champions.add("alpha")
    assert not (gauntlet["champions"] / e["name"] / "notes").exists()
    assert (gauntlet["champions"] / e["name"] / "data" / "notes").is_file()
