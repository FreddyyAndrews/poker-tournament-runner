#!/usr/bin/env bash
# List opponents: reference bots (bots/) and other agents' bots in sibling
# worktrees (worktree:<branch>). All of them can be passed to poker by name.
#   opponents.sh [--names]     --names: just the other agents' bot names, space separated
set -euo pipefail
MAIN="$(git worktree list --porcelain | awk '/^worktree /{print substr($0,10); exit}')"
ME="$(basename "$(git rev-parse --show-toplevel)")"
"$MAIN/.venv/bin/poker" bots --json | "$MAIN/.venv/bin/python" -c "
import json, sys
bots = json.load(sys.stdin)
mine = [b for b in bots if '/$ME/' in b['path'] and b['folder'] != 'bots']
others = [b for b in bots if b['folder'].startswith('worktree:') and b not in mine]
if '${1:-}' == '--names':
    print(' '.join(b['name'] for b in others)); sys.exit()
print('Reference bots:  ', ' '.join(b['name'] for b in bots if b['folder'] == 'bots'))
print('Other agents:    ', ' '.join(f\"{b['name']} ({b['folder'][9:]})\" for b in others) or '(none yet)')
print('Yours:           ', ' '.join(b['name'] for b in mine) or '(none)')
"
