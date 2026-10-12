# Poker Tournament Runner verification map

This directory is the maintained source for verifying what users do with the Poker Tournament Runner. Read this index first, then use the matching feature file as the recipe. Paths are relative to the repo root; `$S` is `.claude/skills/verify-poker-runner/scripts` and `$RUN` is this run's id (see `../SKILL.md`).

## Baseline preconditions

- `make install` and `make web` have been run; `.venv/bin/poker` exists and `web/dist/index.html` exists.
- An isolated server from `$S/launch.sh $RUN <port>`, with `$S/doctor.sh $RUN` printing `OK`. Its runs dir is `data/verify/$RUN/runs`.
- The reference bots `shark`, `aggressor`, `mathematician`, `ref_bot_2` and `template` are in `bots/`. They are the seed opponents.
- Never drive an instance this run did not start (the user's own `poker serve` is usually on 8765).

## Driving conventions

- UI: Playwright through the helpers in `$S` or `web/scripts/screenshot.mjs`, using `data-testid` handles and routes. Never use coordinates.
- CLI: `poker match` and `poker run` run on a server. Point them at this run's server with `--server http://127.0.0.1:$(cat data/verify/$RUN/port)` (or `export POKER_SERVER=http://127.0.0.1:<port>`), so results land in `data/verify/$RUN/runs`. `poker play` and `--local` runs work in-process; give them `--out data/verify/$RUN/runs`.
- MCP: `mcp.client.Client("http://127.0.0.1:<port>/mcp")` from `.venv/bin/python`, or a real MCP client pointed at the run's port.
- Bots created for a run are named `verify_*` (created with `$S/new_bot.sh`) and removed by `$S/cleanup.sh`.

## Proof and skip reporting

- Capture the user action and the resulting state: a screenshot before and after, plus an ARIA snapshot for UI; the command, its output and the exit code for CLI.
- Back visible results with side effects: the `events.jsonl` exists and `poker verify` exits 0; human/LLM decisions appear in the log under their seat name; bench history is written.
- Record the feature ID and the entry point used with each artifact. A path that couldn't be reached is reported with the attempted command and the unmet precondition, never "verified" through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior, then exactly four H2 sections in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with Playwright and the poker CLI` (starting with `Preconditions:`), and `Gotchas`.

## Features

- [Create a bot](./create-bot.md): template/seed bot in `my_bots/`, validation, discovery in the UI and CLI.
- [Test a bot against bots](./test-against-bots.md): CLI match, launching from the New page, verify, bench.
- [Watch and replay a match](./watch-match.md): live and replay table view, controls, hand log, decision inspector, stacks.
- [Play heads-up against a bot](./play-heads-up.md): human seat in the browser and in the terminal; LLM seat over MCP.
- [Improve a bot from its logs](./improve-from-logs.md): inspector, analysis report, per-bot export, before/after bench.

Not yet mapped (add files as they are verified): tournament formats and dashboards (`#/run/<id>`: bracket, round robin, multi-table, series), Docker sandbox runs (`--docker`).
