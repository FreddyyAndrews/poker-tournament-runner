"""pokercore is everything a bot experiences, and it is published as the
hackathon starter kit (see kit/ and scripts/publish_kit.py). It must stand on
its own: nothing in it may import the rest of the runner."""

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "pokercore"
RUNNER_ONLY = {"arena", "sandbox", "engine", "web", "tests", "scripts"}


def _imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def test_core_imports_nothing_from_the_runner():
    bad = [f"{p.relative_to(REPO)}:{line} imports {mod}"
           for p in sorted(CORE.rglob("*.py"))
           for line, mod in _imports(p)
           if mod.split(".")[0] in RUNNER_ONLY]
    assert not bad, "pokercore must not depend on the runner:\n" + "\n".join(bad)


def test_core_loads_without_the_runner():
    """Importing every core module pulls in no runner module."""
    code = ("import sys, pokercore, pokercore.engine, pokercore.structures, pokercore.seeds, "
            "pokercore.seats, pokercore.table, pokercore.botproc, pokercore.validator, "
            "pokercore.sandbox.guard\n"
            f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {sorted(RUNNER_ONLY)!r})\n"
            "print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"runner modules loaded by pokercore: {out.stdout.strip()}"
