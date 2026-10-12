#!/usr/bin/env bash
# Read-only: is this verification instance worth driving?   doctor.sh <run_id>
set -uo pipefail
source "$(dirname "$0")/_common.sh"
need_run_id "${1:-}"
EVID="$(evid_dir "$1")"; ok=1
say() { printf '%-28s %s\n' "$1" "$2"; }
fail() { say "$1" "FAIL: $2"; ok=0; }

[ -f "$EVID/server.pid" ] || { echo "No server for run $1 (launch.sh first)"; exit 1; }
PID=$(cat "$EVID/server.pid"); PORT=$(cat "$EVID/port")
if kill -0 "$PID" 2>/dev/null; then say "process" "pid $PID alive"; else fail "process" "pid $PID not running"; fi
OWNER=$(lsof -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ "$OWNER" = "$PID" ]; then say "port" "$PORT owned by our pid"; else fail "port" "$PORT owned by '${OWNER:-nobody}', not $PID"; fi
H=$(curl -sf "http://127.0.0.1:$PORT/api/health" || true)
if echo "$H" | grep -q "$EVID/runs"; then say "api" "$(echo "$H" | "$PY" -c 'import sys,json; d=json.load(sys.stdin); print("version", d["version"], "runs", d["runs_dir"])')"
else fail "api" "health missing or wrong runs dir: $H"; fi
if curl -sf "http://127.0.0.1:$PORT/" | grep -q 'id="root"'; then say "web ui" "served from web/dist"; else fail "web ui" "not served (make web)"; fi
B=$(curl -sf "http://127.0.0.1:$PORT/api/bots" | "$PY" -c 'import sys,json; print(", ".join(b["name"] for b in json.load(sys.stdin)))' 2>/dev/null)
[ -n "$B" ] && say "bots" "$B" || fail "bots" "none listed"
M=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/mcp" -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"doctor","version":"0"}}}')
[ "$M" = "200" ] && say "mcp" "/mcp answers initialize" || fail "mcp" "HTTP $M"
[ "$ok" = 1 ] && echo "OK" || { echo "NOT OK"; exit 1; }
