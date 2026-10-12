# Play heads-up against a bot

A person (or an LLM) takes a seat against a bot. In the browser, an action bar appears on your turn with fold/check/call, raise sizing (slider, Min, half pot, Pot), all-in and a countdown. Opponents' cards stay hidden until showdown. The same seat can be played from the terminal or by an LLM over MCP. The table waits for the seat up to its time limit, then checks or folds.

## Sub-features

- `play.browser`: New page, add **You** and a bot, **Start and play**, act from the action bar.
- `play.timeout`: no action before the countdown ends means check (or fold), logged as `error: timeout`.
- `play.fair_view`: as the player, opponents' hole cards are not in the events your browser gets.
- `play.terminal`: `poker play <bot>` in a terminal.
- `play.mcp`: an LLM plays via `/mcp` tools (`start_match`, `wait_for_turn`, `act`).

## How to get to it (user POV)

- Browser: **New**, then Table, then Add **You (play from this browser)** and a bot, then **Start and play**. The URL becomes `#/match/<key>?seat=<token>&as=You`.
- Terminal: `poker play verify_bot --hands 10`, typing `f`, `x`, `c`, `r 600` or `a`.
- LLM: `claude mcp add --transport http poker http://127.0.0.1:<port>/mcp`, then ask it to play `verify_bot`.

## Driving it with Playwright and the poker CLI

Preconditions: a launched run with `verify_bot` created. The New page must list `verify_bot`.

- Browser play: `node $S/play_heads_up.mjs $RUN verify_bot 5` prints `{"ok":true,"key":"match_...","decisions":N,"ended":true}` with N ≥ 1. `shots/play_1_form.png` is the filled form; `play_2_your_turn.png` plus `play_2_action_bar.aria.txt` show "Your turn" with your two cards and buttons; `play_3_after.png` shows "The match is over".
- Side effect: `.venv/bin/python -c "import json;print(sum(1 for l in open('data/verify/$RUN/runs/<key>/events.jsonl') if '\"bot\": \"You\"' in l and '\"type\": \"decision\"' in l))"` is at least N, and `.venv/bin/poker verify data/verify/$RUN/runs/<key>` exits 0.
- Fair view: `curl -s "http://127.0.0.1:<port>/api/matches/<key>/events?as=You"` has `hand_start` events whose `hole` contains only `You`.
- Terminal: `printf 'c\nx\nx\nx\nf\n%.0s' 1 2 3 | .venv/bin/poker play verify_bot --hands 2 --seed 3 --id term1 --out data/verify/$RUN/runs` prints "Your cards:" and "Legal actions:" prompts and a results table, then `poker verify data/verify/$RUN/runs/term1` exits 0.
- MCP: with `.venv/bin/python`, `Client("http://127.0.0.1:<port>/mcp")` lists tools including `start_match`, `wait_for_turn` and `act`. `start_match {"opponents":["verify_bot"],"hands":3}` returns a token; loop `wait_for_turn` and `act` until `status` is `match_over` with 2 results. The match log has decisions by `claude` whose `logs` equal the `reasoning` passed.

## Gotchas

- While playing live there is no scrubber or transport (`[data-testid=play-bar]` instead). `[data-testid=review]` shows them with a `review-banner`; `back-to-table` hides them. A turn that starts while reviewing returns to the table. Keys on your turn: F, C (check/call), R (focus `raise-amount`), Enter (raise), A (all-in).

- Human and LLM seats are only for single tables played once: the **You** and **An LLM** buttons are disabled for other formats, and the API answers 400.
- Leaving the page doesn't stop the match. Your seat times out each decision; use **Stop** (or `POST /api/jobs/<key>/cancel`).
- The action bar comes from the seat API, not the cursor. It appears on your turn even while you are scrubbing history.
- Pace applies to the bots only; a human decision takes as long as the human does (up to "Your time", default 30 s).
