#!/usr/bin/env bash
# Start an autonomous bot track: a worktree and branch for a new bot, its
# code (fresh from the template, or forked from an existing bot), and its
# lab notes, committed on the branch.
#
#   start_track.sh <name> <track> [--from <bot>] [--strategy "..."]
#
#   name    the new bot: ^[a-z][a-z0-9_]{2,30}$, unused
#   track   heuristic | rl | jev_prompt | jev_tools
#   --from  copy this bot (a name in my_bots/, bots/ or champions/ of the main
#           checkout) instead of the template; its name inside bot.py becomes <name>
#
# Then schedule it: run-loop's SKILL.md has the task prompt.
set -euo pipefail
NAME="${1:?usage: start_track.sh <name> <track> [--from <bot>] [--strategy \"...\"]}"
TRACK="${2:?usage: start_track.sh <name> <track> [--from <bot>] [--strategy \"...\"]}"
shift 2
FROM="" STRATEGY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --strategy) STRATEGY="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
case "$TRACK" in heuristic|rl|jev_prompt|jev_tools) ;; *) echo "track must be heuristic, rl, jev_prompt or jev_tools" >&2; exit 2 ;; esac

MAIN="$(git worktree list --porcelain | awk '/^worktree /{print substr($0,10); exit}')"
PY="$MAIN/.venv/bin/python"
NOTES="$PY $MAIN/.claude/skills/iterate-bot/scripts/notes.py"
SRC=""
if [ -n "$FROM" ]; then
  for d in my_bots bots champions; do
    [ -f "$MAIN/$d/$FROM/bot.py" ] && { SRC="$MAIN/$d/$FROM"; break; }
  done
  [ -n "$SRC" ] || { echo "no bot named $FROM in my_bots/, bots/ or champions/" >&2; exit 1; }
fi

"$MAIN/.claude/skills/build-poker-bot/scripts/setup.sh" "$NAME"
WT="$(dirname "$MAIN")/poker-bot-$NAME"
BOT="$WT/my_bots/$NAME"

if [ -n "$SRC" ]; then
  SRC_HASH="$("$PY" -c "import sys; sys.path.insert(0, '$MAIN'); from arena.bots import bot_hash; print(bot_hash('$SRC'))")"
  rm -rf "$BOT"
  rsync -a --exclude __pycache__ --exclude '*.pyc' --exclude '/notes/' "$SRC/" "$BOT/"
  SRC_NAME="$(sed -n 's/^BOT_NAME = "\([^"]*\)".*/\1/p' "$BOT/bot.py" | head -1)"
  if [ -n "$SRC_NAME" ]; then
    sed -i '' "s/^BOT_NAME = \"$SRC_NAME\"/BOT_NAME = \"$NAME\"/" "$BOT/bot.py"
  fi
  if [ -f "$BOT/README.md" ]; then
    { echo "# $NAME"; echo; echo "Forked from \`$FROM\` (code hash \`$SRC_HASH\`) to start the $TRACK track."; \
      tail -n +2 "$BOT/README.md"; } > "$BOT/README.tmp" && mv "$BOT/README.tmp" "$BOT/README.md"
  else
    printf '# %s\n\nForked from `%s` (code hash `%s`) to start the %s track.\n\n## Strategy\n\n(See notes/status.json; updated as the bot improves.)\n' \
      "$NAME" "$FROM" "$SRC_HASH" "$TRACK" > "$BOT/README.md"
  fi
fi

if [ -z "$STRATEGY" ]; then
  case "$TRACK" in
    heuristic)  STRATEGY="Hand-written rules: equity against pot odds, position-aware ranges, sizing by street. Improves by changing its code." ;;
    rl)         STRATEGY="A strategy learned offline by self-play (Monte Carlo CFR on a heads-up abstraction), shipped in data/ and looked up at play time." ;;
    jev_prompt) STRATEGY="Asks Jev for every decision with a compact hand summary. Improves by refining the prompt, summary and options." ;;
    jev_tools)  STRATEGY="Asks Jev for every decision, with facts its code computes (pot odds, equity, opponent stats). Improves by refining that harness." ;;
  esac
fi

IDEAS=()
case "$TRACK" in
  heuristic)  IDEAS=("find the champion it loses most to and the street where it loses" "add per-opponent fold-to-bet tracking and bluff more against folders" "tune river calling thresholds against large bets") ;;
  rl)         IDEAS=("write tools/train.py: external-sampling MCCFR on a small heads-up abstraction" "export the strategy to data/ and play it within 200 ms" "grow the postflop buckets once the small abstraction beats the reference bots") ;;
  jev_prompt) IDEAS=("build the spot suite (tools/spots.py, notes/spots.jsonl)" "add pot odds and position to the summary" "test the option order bias: put fold last") ;;
  jev_tools)  IDEAS=("build the spot suite (tools/spots.py, notes/spots.jsonl)" "compute equity with eval7 and pass it as a fact" "add a yes/no question: is this opponent likely bluffing") ;;
esac
ARGS=()
for i in "${IDEAS[@]}"; do ARGS+=(--idea "$i"); done
$NOTES init "$BOT" --track "$TRACK" --strategy "$STRATEGY" ${FROM:+--from "$FROM"} "${ARGS[@]}"

cd "$WT"
git add "my_bots/$NAME"
git commit -q -m "$NAME: start the $TRACK track${FROM:+ (from $FROM)}"
echo
echo "Track $NAME ($TRACK) is ready in $WT on branch bot/$NAME."
echo "Its first iteration records the baseline. Schedule it with the prompt in run-loop's SKILL.md."
