# Shared paths for the verify-poker-runner helpers (sourced, not run).
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$SKILL_DIR/../../.." && pwd)"
POKER="$REPO/.venv/bin/poker"
PY="$REPO/.venv/bin/python"
evid_dir() { echo "$REPO/data/verify/$1"; }
need_run_id() { [ -n "$1" ] || { echo "usage: $0 <run_id> ..." >&2; exit 2; }; }
