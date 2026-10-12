# Watch and replay a match

Any match (live or finished) opens in a table view: cards, bets, pot, board, the acting seat, showdowns with hand names and winners. Playback controls scrub the history, a running match follows LIVE, and the side panel shows standings, the current hand's log, a decision inspector (what the bot saw, its reply, how it was applied, its logs and debug) and a stack chart.

## Sub-features

- `watch.table`: seats, cards, board, pot, button, acting outline, showdown result.
- `watch.controls`: play/pause, step, previous/next hand, speeds, scrubber, keyboard.
- `watch.live`: LIVE follows new events; stepping back shows GO LIVE; L or clicking it returns.
- `watch.inspector`: the Decision tab for a bot's decision, including its printed logs and `debug`.
- `watch.chart`: the Stacks tab chart with a legend; clicking it jumps to a hand.

## How to get to it (user POV)

- **Recent** (`#/`), then click a match row, or open `#/match/<key>` directly.
- From a run page (`#/run/<id>`): a tie, grid cell or table's **Watch**, or **Now playing**.
- Right after launching from **New**.

## Driving it with Playwright and the poker CLI

Preconditions: a finished match whose key you know (e.g. `vs_bots` from test-against-bots) that includes `verify_bot`.

- Replay and inspect: `node $S/watch.mjs $RUN vs_bots verify_bot` prints `{"ok":true,...,"page_errors":0}`. `shots/watch_1_table.png` shows the table; `watch_2_bot_acting.png` has `verify_bot` outlined with its action under its seat; `watch_3_inspector.png` plus `.aria.txt` show "What it saw", "What it did" and its logs (`hand N preflop cards [...] strength ...`) and debug (`strength`, `price`, `why`); `watch_4_stacks.png` shows the chart.
- Live: start a paced match (`curl -s -X POST http://127.0.0.1:<port>/api/matches -H 'content-type: application/json' -d '{"bots":["verify_bot","shark"],"hands":20,"pace_ms":300,"id":"live1"}'`), then open `#/match/live1`. `[data-testid=status]` reads `Live`, `[data-testid=live]` reads `LIVE`, and `[data-testid=position]`'s event count keeps rising. Press ArrowLeft and `live` reads `GO LIVE`; click it and it reads `LIVE` again.

## Gotchas

- The hand log only shows events up to the cursor (no spoilers), so step forward to reveal later actions.
- A finished match opens at hand 1, paused; a running one opens LIVE.
- Hole cards are visible to spectators by default ("Hide hole cards" toggles); as a seated human (`?as=`) others' cards are absent from the stream itself.
- Multi-table runs: watch a table via its `#/match/<run>/matches/mtt/tables/t<N>` key, not the main `mtt` log.
