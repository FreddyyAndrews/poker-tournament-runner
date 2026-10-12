#!/usr/bin/env bash
# Tear down what this run created; keep the evidence.   cleanup.sh <run_id>
# - stops only the server whose pid this run recorded (and only if it is a poker serve)
# - removes scaffold bots (my_bots/verify_*) listed in scaffold.txt; their final code is
#   copied into the evidence dir first, and bench history for them is moved there
# Evidence stays in data/verify/<run_id>/.
set -uo pipefail
source "$(dirname "$0")/_common.sh"
need_run_id "${1:-}"
EVID="$(evid_dir "$1")"
if [ -f "$EVID/server.pid" ]; then
  PID=$(cat "$EVID/server.pid")
  if ps -p "$PID" -o command= 2>/dev/null | grep -q "poker serve"; then
    kill "$PID" && echo "stopped server pid $PID"
    for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.25; done
  fi
  rm -f "$EVID/server.pid"
fi
if [ -f "$EVID/scaffold.txt" ]; then
  while read -r kind name; do
    [ "$kind" = "bot" ] || continue
    case "$name" in verify_*) ;; *) echo "refusing to remove non-verify bot $name"; continue;; esac
    if [ -d "$REPO/my_bots/$name" ]; then
      mkdir -p "$EVID/bots/$name"; cp "$REPO/my_bots/$name/bot.py" "$EVID/bots/$name/bot.py"
      rm -rf "$REPO/my_bots/$name" && echo "removed my_bots/$name (copy kept in evidence)"
    fi
    if ls "$REPO/data/bench/$name".jsonl >/dev/null 2>&1 || ls -d "$REPO/data/bench/runs/${name}_"* >/dev/null 2>&1; then
      mkdir -p "$EVID/bench"
      mv "$REPO/data/bench/$name.jsonl" "$EVID/bench/" 2>/dev/null
      mv "$REPO/data/bench/runs/${name}_"* "$EVID/bench/" 2>/dev/null
      echo "moved bench history for $name into evidence"
    fi
  done < "$EVID/scaffold.txt"
  mv "$EVID/scaffold.txt" "$EVID/scaffold.done.txt"
fi
echo "evidence kept in $EVID"
