---
name: verify-poker-runner
description: Verify the Poker Tournament Runner end to end through its real surfaces (web UI at `poker serve`, the `poker` CLI, MCP). Use to prove the bot-development loop works - create a bot, test it against other bots, watch the match in the frontend, play it heads-up as a human, and use the match logs to improve it - or to check any UI/CLI change in a real running instance with screenshots and logs as evidence.
---

# Verify the Poker Tournament Runner

The app: a Python package (`arena/`, `engine/`, `sandbox/`) with a `poker` CLI, and `poker serve`, which serves an HTTP API, a React UI (built into `web/dist`) and an MCP endpoint at `/mcp`. Results land in a runs directory as event logs per match: `events.jsonl` while the match plays, gzipped to `events.jsonl.gz` when it ends (read them with `arena.events.read_events`). Bots are folders in `my_bots/` or `bots/` with a `bot.py` exposing `decide(state)`.

Everything below runs from the repo root, `R=$(git rev-parse --show-toplevel)`, with helpers in `$R/.claude/skills/verify-poker-runner/scripts` (call it `$S`). Pick a run id per verification run: `RUN=$(date +%Y%m%d_%H%M%S)`.

## Launch

Prerequisites (once): `make install` (Python 3.10 venv at `.venv`), `make web` (Node; builds `web/dist`), `cd web && npx playwright install chromium`.

```bash
$S/launch.sh $RUN 8790          # prints http://127.0.0.1:8790 when usable
```

- Starts `poker serve` on a **spare port** with a **fresh runs dir** `data/verify/$RUN/runs`, so it never shares state with the user's own server (usually on 8765). Builds the UI first if `web/dist` is missing. Refuses a port that is already in use.
- "Ready" means: `/api/health` reports this run's runs dir, `/api/bots` lists `shark`, and `/` serves the UI. No migrations or seed data are needed; the reference bots in `bots/` are the seed opponents.
- Never drive an instance this run did not start (e.g. the user's 8765): matches you start there end up in their Recent list and logs.

## Doctor

```bash
$S/doctor.sh $RUN
```

Read-only. Checks: our pid is alive and owns the port, the API answers with this run's runs dir, the UI is served, bots are listed, `/mcp` answers an MCP `initialize`. Run it first whenever anything looks off; it ends `OK` or `NOT OK`.

## Drive

The main journey, with the real handles. The feature files in `features/` have more detail and the other entry points.

1. **Create a bot**: `$S/new_bot.sh $RUN verify_bot`. This copies `assets/seed_bot.py` (a deliberately leaky "calling station" that prints its reasoning and returns `debug`) to `my_bots/verify_bot/bot.py` and runs the validator (`validator: PASS`). Names must start with `verify_`; pass a third argument to start from another file. Edit `my_bots/verify_bot/bot.py` to change strategy.
2. **Test it against bots**:
   - CLI: `.venv/bin/poker match verify_bot shark mathematician --cash --hands 200 --seed 7 --id vs_bots --server http://127.0.0.1:$(cat data/verify/$RUN/port) --json`, then `.venv/bin/poker verify data/verify/$RUN/runs/vs_bots` (exit 0, `OK`). `poker match` and `poker run` run on the server, so the match is written to this run's runs dir and shows live in its UI. `--out` only works with `--local` (in-process, never live).
   - Score: `.venv/bin/poker bench verify_bot --hands 300 --workers 4 --json > data/verify/$RUN/bench_before.json`.
3. **Watch it in the frontend**: `node $S/watch.mjs $RUN vs_bots verify_bot`. This opens `#/match/vs_bots`, steps with the keyboard until the hand log's newest decision row is `verify_bot`, then opens the Decision tab and the Stacks tab. Screenshots and an ARIA snapshot of the inspector go to `shots/watch_*`.
4. **Play it heads-up**: `node $S/play_heads_up.mjs $RUN verify_bot 5`. On the New page it picks Table, adds You (`add-human`) and the bot, cash, 5 hands, Quick pace, then Start and play. It acts from the action bar (raise first, then check/call) until the bar says the match is over. It writes `play.json` with the match key and decisions, plus `shots/play_*`.
5. **Improve it from the logs**:
   - `.venv/bin/python $S/analyze.py $RUN vs_bots verify_bot` writes `analysis_verify_bot_vs_bots.md`. It covers actions by street, timeouts and errors, the biggest losing hands with the bot's own `logs`/`debug` per decision, and losses after calling.
   - Change `my_bots/verify_bot/bot.py` to fix what it shows (for the seed bot: stop calling down with weak hands).
   - Re-run the step 2 match with a new `--id`, and `poker bench ... > bench_after.json`. Compare `overall_bb_per_100`: same seeds, so the change is the code. Re-run `analyze.py` on the new match.
   - Also available: `poker export data/verify/$RUN/runs/vs_bots --bot verify_bot -o data/verify/$RUN/export_verify_bot.zip`, the participant download.

Stable handles in the UI: `data-testid`s such as `format-table`, `add-<bot>`, `add-human`, `add-mcp`, `mode-cash`, `hands`, `pace-0|250|600|1200`, `launch-btn`, `poker-table`, `seat-<name>`, `board`, `pot`, `position`, `play`, `step`, `step-back`, `live`, `scrubber`, `tab-hand|tab-decision|tab-chart`, `log-row` (with `data-type`), `inspector`, `action-bar`, `act-fold|act-check|act-call|act-raise|act-allin`, `raise-amount`, `status`, `bracket`, `tie`, `rr-grid`, `mtt-tables`, `watch-table`, `series-chart`, `verify`, `verify-result`. Routes: `#/new`, `#/`, `#/match/<key>[?seat=<token>&as=<name>]`, `#/run/<id>`. Keyboard on a match: Space, Left/Right, Shift+Left/Right, L.

## Evidence

All of it lives in `data/verify/$RUN/` (gitignored), and none of it is deleted by cleanup:

- `runs/<match>/events.jsonl.gz` + `summary.json`: the full logs of every match the run played, verifiable later with `poker verify`.
- `shots/*.png` + `shots/*.aria.txt`: the user action and the resulting state (form filled, then table, then inspector; your-turn bar, then after).
- `watch.json`, `play.json`: what the drivers saw (position, inspector text, actions taken, match key, page errors).
- `validate_<bot>.json`, `bench_*.json`, `analysis_*.md`, `server.log`, and after cleanup `bots/<name>/bot.py` (the final code) and `bench/` (bench history).

Proof standards:
- Drive the real user path (UI clicks, CLI commands, MCP tools), never internal setters.
- Capture the action and the resulting state, not just a final screen.
- Check side effects alongside what's visible: the match log exists, `poker verify` passes, the human's decisions appear in `events.jsonl` with `"bot": "You"`, and bench history was written.
- `page_errors` in `watch.json` / `play.json` must be empty.

## Cleanup

```bash
$S/cleanup.sh $RUN
```

- **Server:** stops only the pid this run recorded, and only if that pid is a `poker serve`. It never kills by name.
- **Scaffold bots:** removes the `my_bots/verify_*` bots listed in `scaffold.txt`, after copying their final `bot.py` into the evidence dir.
- **Bench history:** moves `data/bench/verify_*` into the evidence dir.
- **Evidence:** stays. Also run cleanup after a failed attempt so no server or scaffold bot is left behind.

## Helpers

| Script | Use |
|---|---|
| `launch.sh <run> [port]` | start an isolated server; prints its URL |
| `doctor.sh <run>` | read-only health check |
| `new_bot.sh <run> <verify_name> [src.py]` | scaffold bot in `my_bots/` + validation |
| `node watch.mjs <run> <match_key> <bot>` | UI: step to the bot's first decision, inspector + chart screenshots |
| `node play_heads_up.mjs <run> <bot> [hands]` | UI: play as a human through the New page and action bar |
| `.venv/bin/python analyze.py <run> <match_key> <bot>` | improvement report from a match log |
| `cleanup.sh <run>` | stop our server, remove scaffold bots, keep evidence |

The `.mjs` helpers import Playwright from `web/node_modules`; they need `npm install` in `web/` and the Chromium binary. For quick one-off screenshots of any page there's also `node web/scripts/screenshot.mjs <url> <out.png> [waitMs] [click=<selector> key=<Key> fill=<selector>|<value> wait=<ms> ...]`.
