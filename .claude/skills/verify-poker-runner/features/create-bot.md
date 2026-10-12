# Create a bot

A participant writes a bot as a folder with a `bot.py` exposing `decide(state)`. Bots in `my_bots/` (and the reference bots in `bots/`) are found by name everywhere: the CLI, the New page's player list and the API. The validator checks a bot before it plays.

## Sub-features

- `bot.scaffold`: create a bot folder from the template or the seed bot.
- `bot.validate`: the validator passes a well-formed bot and reports errors for a broken one.
- `bot.discover`: the new bot appears in `poker bots`, `GET /api/bots` and the New page with a code hash.
- `bot.debug`: `print()` output and a returned `debug` value are captured per decision.

## How to get to it (user POV)

- Copy `bots/template/` to `my_bots/<name>/` and edit `bot.py` (README "Writing your bot").
- `make validate BOT=my_bots/<name>/bot.py`, or `python sandbox/validator.py <path> --json`.
- `poker bots` lists it; on the web UI, **New** shows it under Players with a code hash.

## Driving it with Playwright and the poker CLI

Preconditions: a launched run (`$RUN`), with no existing `my_bots/verify_bot`.

- Scaffold and validate: `$S/new_bot.sh $RUN verify_bot`. It prints `validator: PASS []` and the path; `data/verify/$RUN/validate_verify_bot.json` has `"passed": true`.
- CLI discovery: `.venv/bin/poker bots` includes the line `verify_bot  my_bots/`.
- UI discovery: `node web/scripts/screenshot.mjs http://127.0.0.1:<port>/#/new data/verify/$RUN/shots/bot_listed.png 1500` shows a `verify_bot` row with an Add button (`data-testid=add-verify_bot`), and `curl -s http://127.0.0.1:<port>/api/bots` includes `"verify_bot"`.
- Broken bot: `$S/new_bot.sh $RUN verify_broken <file with a syntax error>` prints `validator: FAIL` with the error.

## Gotchas

- Names must start with `verify_`: `cleanup.sh` only removes those, so it can never delete a real participant bot.
- The New page reads the bot list when it loads. Reload it after creating a bot.
- The validator imports the bot in-process. Treat untrusted bots with `--docker` runs, not the validator alone.
- A bot whose name equals a reference bot's (e.g. `my_bots/shark`) shadows it by name; `my_bots/` is searched first.
