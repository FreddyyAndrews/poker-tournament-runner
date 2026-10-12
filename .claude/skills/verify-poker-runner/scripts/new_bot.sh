#!/usr/bin/env bash
# Create a scaffold bot in my_bots/ (so the New page lists it) and validate it.
#   new_bot.sh <run_id> <name> [source_bot.py]     name must start with verify_
# Default source: the skill's leaky seed bot. Recorded for cleanup.
set -euo pipefail
source "$(dirname "$0")/_common.sh"
need_run_id "${1:-}"
RUN_ID="$1"; NAME="${2:?bot name}"; SRC="${3:-$SKILL_DIR/assets/seed_bot.py}"
case "$NAME" in verify_*) ;; *) echo "bot name must start with verify_ (so cleanup can't touch real bots)" >&2; exit 2;; esac
EVID="$(evid_dir "$RUN_ID")"; mkdir -p "$EVID"
DEST="$REPO/my_bots/$NAME"
[ -e "$DEST" ] && { echo "$DEST already exists" >&2; exit 1; }
mkdir -p "$DEST"; cp "$SRC" "$DEST/bot.py"
echo "bot $NAME" >> "$EVID/scaffold.txt"
"$PY" "$REPO/sandbox/validator.py" "$DEST/bot.py" --json > "$EVID/validate_$NAME.json" 2> "$EVID/validate_$NAME.stderr" || true
echo "$DEST/bot.py"
"$PY" -c "import json,sys; d=json.load(open('$EVID/validate_$NAME.json')); print('validator:', 'PASS' if d['passed'] else 'FAIL', d['errors']); sys.exit(0 if d['passed'] else 1)"
