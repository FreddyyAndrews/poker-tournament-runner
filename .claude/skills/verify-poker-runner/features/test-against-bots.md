# Test a bot against bots

A participant plays their bot against reference bots to see how it does. The match is logged, scored per bot (place, chips, bb/100, think time, timeouts, errors) and verifiable. `poker bench` gives a fixed-seed score to compare versions.

## Sub-features

- `match.cli`: `poker match` with fixed, cash or freezeout play, blinds and seed. It runs on the server (live in the UI, cancellable there, Ctrl+C cancels) and prints a results table or JSON; `--local` runs in-process.
- `match.ui`: start the same match from the **New** page and land on its live view.
- `match.verify`: `poker verify` replays the log and checks it.
- `match.bench`: `poker bench` gives per-opponent bb/100 with 95% CIs and history per code version.

## How to get to it (user POV)

- Terminal: `poker match <bot> shark mathematician --cash --hands 200 [--seed 7] [--pace 600]`.
- Browser: **New**, then Format Table, add the bot and opponents, choose how hands are played and the pace, then **Start and watch**.
- Terminal: `poker bench <bot>`; `poker verify <run dir or id>`.

## Driving it with Playwright and the poker CLI

Preconditions: a launched run, and `verify_bot` created (see create-bot).

- CLI match: `.venv/bin/poker match verify_bot shark mathematician --cash --hands 200 --seed 7 --id vs_bots --server http://127.0.0.1:$(cat data/verify/$RUN/port) --json > data/verify/$RUN/match_vs_bots.json`. Exit 0; `results` has 3 rows with `hands_played` 200 and deltas summing to 0. Side effect: `curl -s http://127.0.0.1:<port>/api/jobs/vs_bots` shows `"status": "complete"` (a server job).
- Verify: `.venv/bin/poker verify data/verify/$RUN/runs/vs_bots` prints `OK:` and exits 0.
- UI launch: `node web/scripts/screenshot.mjs "http://127.0.0.1:<port>/#/new" data/verify/$RUN/shots/launch.png 1500 'click=[data-testid=add-verify_bot]' 'click=[data-testid=add-shark]' 'click=[data-testid=pace-250]' 'click=[data-testid=launch-btn]' wait=3000` ends on `#/match/match_...` with a `Live` status pill. That match appears under `data/verify/$RUN/runs/`.
- Bench: `.venv/bin/poker bench verify_bot --hands 300 --workers 4 --json > data/verify/$RUN/bench_before.json`. It has one entry per reference bot (except template) and an `overall_bb_per_100`. Running it twice on unchanged code gives the same number, and the second run's `previous` lists the first with the same hash.

## Gotchas

- `poker match` / `poker run` need a running server: without one they exit 2 with "Start one with `poker serve`, or use --local". `--out` with a server is an error (the server writes to its own runs dir).

- `poker bench` writes history to `data/bench/<bot>.jsonl` (shared, not per run). `cleanup.sh` moves `verify_*` history into the evidence dir.
- Same `--seed` plus the same bots and code means the same match, so you can compare before and after a change on identical cards.
- Matches started from the UI default to the Live pace (600 ms per action). Use `pace-0` or `pace-250` for a quick verification.
- A `--docker` run needs `colima start` and `./sandbox.sh build` first. Skip it unless the change touches the sandbox.
