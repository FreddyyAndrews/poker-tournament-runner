#!/usr/bin/env python3
"""Publish the hackathon starter kit, or check a copy of it against this runner.

  python3 scripts/publish_kit.py ../poker-hackathon-kit           # write the kit there
  python3 scripts/publish_kit.py --check ../poker-hackathon-kit   # same core as this runner?

The kit is generated, never edited by hand. It is exactly two things:

  pokercore/   copied unchanged from this repo: the engine, table, blinds,
               sandbox and validator, i.e. everything a bot experiences
  kit/         the kit's own files (README, play.py, bot template, ...),
               copied to the root of the kit

Nothing else in this repo can reach the kit: there is no exclude list to keep
up to date, only these two folders, and within them only files git tracks or
would track (ignored files never go). In the destination, pokercore/ is
replaced wholesale and kit files that no longer exist are removed; anything
else there (.git, your own notes) is left alone.

Publishing records the core's fingerprint in kit/published.json. The test
suite fails while pokercore/ differs from what was last published, so the kit
can't silently drift from the tournament (tests/test_kit.py).
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pokercore import core_hash  # noqa: E402

KIT_SRC = REPO / "kit"
PUBLISHED = KIT_SRC / "published.json"     # stays in the runner; never copied
MANIFEST = ".kit-files"                     # in the kit: the files the last publish wrote
VERSION_FILE = "CORE_VERSION"


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def source_files(folder: str) -> list:
    """Files under `folder` that git tracks or would track (never ignored ones)."""
    out = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", folder)
    return sorted(p for p in out.split("\0") if p and (REPO / p).is_file())


def runner_version() -> str:
    commit = _git("rev-parse", "--short", "HEAD").strip()
    dirty = _git("status", "--porcelain", "--", "pokercore", "kit").strip()
    return commit + ("+uncommitted" if dirty else "")


def plan() -> dict:
    """{destination path: source path} for the whole kit."""
    files = {}
    for src in source_files("pokercore"):
        files[src] = src
    for src in source_files("kit"):
        rel = Path(src).relative_to("kit").as_posix()
        if src == PUBLISHED.relative_to(REPO).as_posix():
            continue
        if rel.split("/")[0] == "pokercore" or rel in (VERSION_FILE, MANIFEST):
            raise SystemExit(f"kit/{rel} would overwrite a generated file")
        files[rel] = src
    return files


def publish(dest: Path, record: bool = True):
    """Write the kit into dest. record: note the published core in kit/published.json."""
    dest = dest.resolve()
    if dest == REPO or REPO in dest.parents:
        raise SystemExit("the kit must be published outside this repo")
    dest.mkdir(parents=True, exist_ok=True)
    files = plan()
    old = set((dest / MANIFEST).read_text().split("\n")) - {""} if (dest / MANIFEST).exists() else set()

    shutil.rmtree(dest / "pokercore", ignore_errors=True)
    for rel in sorted(old - set(files) - {VERSION_FILE, MANIFEST}):
        if not rel.startswith("pokercore/"):
            (dest / rel).unlink(missing_ok=True)
    for rel, src in files.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / src, dest / rel)

    h = core_hash()
    version = runner_version()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    (dest / VERSION_FILE).write_text(
        f"core {h}\nrunner {version}\npublished {stamp}\n\n"
        "pokercore/ is generated from the tournament runner and must not be edited.\n"
        "`python3 play.py ...` prints the core's fingerprint; it should match the first line.\n")
    (dest / MANIFEST).write_text("\n".join(sorted(set(files) | {VERSION_FILE})) + "\n")
    if core_hash(dest / "pokercore") != h:
        raise SystemExit("the published pokercore/ doesn't match this repo's; nothing recorded")
    if record:      # only the fingerprint, so republishing an unchanged core changes nothing here
        PUBLISHED.write_text(json.dumps({"core_hash": h}, indent=2) + "\n")
    print(f"Published the kit to {dest}: core {h}, {len(files)} files (runner {version}).")
    print("Review it, then commit and push the kit repo.")


def check(dest: Path) -> int:
    dest = dest.resolve()
    if not (dest / "pokercore").is_dir():
        print(f"{dest} has no pokercore/: not a kit")
        return 1
    theirs, ours = core_hash(dest / "pokercore"), core_hash()
    if theirs == ours:
        print(f"OK: {dest} carries the same core as this runner ({ours}).")
        return 0
    print(f"MISMATCH: the kit's core is {theirs}, this runner's is {ours}.")
    print("Republish (python3 scripts/publish_kit.py DEST) and have entrants pull the update.")
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dest", type=Path, help="the kit's folder (a checkout of the kit repo)")
    p.add_argument("--check", action="store_true", help="only check that the kit's core matches this runner's")
    args = p.parse_args(argv)
    if args.check:
        return check(args.dest)
    publish(args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
