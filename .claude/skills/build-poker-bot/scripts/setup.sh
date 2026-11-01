#!/usr/bin/env bash
# Create a git worktree + branch for a new bot and scaffold it.
#   setup.sh <bot_name>          bot_name: lowercase letters, digits, _ (3-31 chars)
# Creates <main>/../poker-bot-<bot_name> on branch bot/<bot_name> from origin/main,
# with my_bots/<bot_name>/{bot.py,README.md}. Prints the environment to use.
set -euo pipefail
NAME="${1:?usage: setup.sh <bot_name>}"
[[ "$NAME" =~ ^[a-z][a-z0-9_]{2,30}$ ]] || { echo "bot name must match ^[a-z][a-z0-9_]{2,30}$" >&2; exit 2; }

MAIN="$(git worktree list --porcelain | awk '/^worktree /{print substr($0,10); exit}')"
[ -x "$MAIN/.venv/bin/poker" ] || { echo "no $MAIN/.venv/bin/poker: run 'make install' in $MAIN" >&2; exit 1; }
POKER="$MAIN/.venv/bin/poker"; PY="$MAIN/.venv/bin/python"

# The name must be new everywhere: main, reference bots, other worktrees, remote branches.
if "$POKER" bots --json | "$PY" -c "import json,sys; sys.exit(0 if any(b['name']=='$NAME' for b in json.load(sys.stdin)) else 1)"; then
  echo "a bot named $NAME already exists (see: poker bots); pick another name" >&2; exit 1
fi
git -C "$MAIN" fetch -q origin
if git -C "$MAIN" show-ref -q --verify "refs/remotes/origin/bot/$NAME" || git -C "$MAIN" show-ref -q --verify "refs/heads/bot/$NAME"; then
  echo "branch bot/$NAME already exists; pick another name" >&2; exit 1
fi

WT="$(dirname "$MAIN")/poker-bot-$NAME"
git -C "$MAIN" worktree add -q -b "bot/$NAME" "$WT" origin/main
mkdir -p "$WT/my_bots/$NAME"
sed "s/^BOT_NAME = .*/BOT_NAME = \"$NAME\"/" "$WT/bots/template/bot.py" > "$WT/my_bots/$NAME/bot.py"
cat > "$WT/my_bots/$NAME/README.md" <<MD
# $NAME

## Strategy

(One paragraph: the idea behind the bot.)

- **Preflop:**
- **Postflop:**
- **Bet sizing:**
- **Opponent modelling:**
- **Time budget:** (how it stays well inside the 2 s limit)

## Development log

| Version | Change | Bench bb/100 (95% CI) |
|---|---|---|
| v1 | template baseline | |
MD

echo "Worktree: $WT  (branch bot/$NAME)"
echo
echo "Use this environment in every shell:"
echo "  cd $WT"
echo "  export POKER=$POKER PY=$PY POKER_SERVER=\${POKER_SERVER:-http://127.0.0.1:8765}"
echo "  S=$MAIN/.claude/skills/build-poker-bot/scripts"
echo
if curl -sf "${POKER_SERVER:-http://127.0.0.1:8765}/api/health" >/dev/null; then
  echo "Central server: up at ${POKER_SERVER:-http://127.0.0.1:8765}"
else
  echo "Central server: NOT running. Start it from the main worktree (see the skill), then continue."
fi
if curl -sf "${POKER_PROXY_URL:-http://127.0.0.1:8766}/health" >/dev/null; then
  echo "Outside-bot proxy: up at ${POKER_PROXY_URL:-http://127.0.0.1:8766}"
  "$POKER" proxy --status | sed -n '2,$p'
else
  echo "Outside-bot proxy: NOT running. Start it from the main worktree (see the skill) before playing Slumbot."
fi
"$PY" "$WT/sandbox/validator.py" "$WT/my_bots/$NAME/bot.py" --json 2>/dev/null \
  | "$PY" -c "import json,sys; d=json.load(sys.stdin); print('Validator:', 'PASS' if d['passed'] else 'FAIL', d['errors'])"
