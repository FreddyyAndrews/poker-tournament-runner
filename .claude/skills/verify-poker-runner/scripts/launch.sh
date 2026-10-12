#!/usr/bin/env bash
# Start an isolated poker server for one verification run.
#   launch.sh <run_id> [port]      (default port 8790)
# Writes data/verify/<run_id>/{server.pid,port,server.log,runs/}. Prints the URL when usable.
set -euo pipefail
source "$(dirname "$0")/_common.sh"
need_run_id "${1:-}"
RUN_ID="$1"; PORT="${2:-8790}"; EVID="$(evid_dir "$RUN_ID")"
mkdir -p "$EVID/runs" "$EVID/shots"

[ -x "$POKER" ] || { echo "No $POKER: run 'make install' in $REPO first" >&2; exit 1; }
if [ ! -f "$REPO/web/dist/index.html" ]; then
  echo "Building the web UI (make web)..." >&2
  (cd "$REPO" && make web >/dev/null)
fi
if lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "Port $PORT is already in use; pick another: launch.sh $RUN_ID <port>" >&2; exit 1
fi

nohup "$POKER" serve --port "$PORT" --out "$EVID/runs" > "$EVID/server.log" 2>&1 &
echo $! > "$EVID/server.pid"
echo "$PORT" > "$EVID/port"

for _ in $(seq 1 60); do
  # usable = API answers with our runs dir AND bots are listed AND the UI is served
  if curl -sf "http://127.0.0.1:$PORT/api/health" | grep -q "$EVID/runs" \
     && curl -sf "http://127.0.0.1:$PORT/api/bots" | grep -q '"shark"' \
     && curl -sf "http://127.0.0.1:$PORT/" | grep -q 'id="root"'; then
    echo "http://127.0.0.1:$PORT"
    exit 0
  fi
  sleep 0.5
done
echo "Server did not become ready; see $EVID/server.log" >&2
exit 1
