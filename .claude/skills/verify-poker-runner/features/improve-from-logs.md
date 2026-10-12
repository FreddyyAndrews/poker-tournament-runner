# Improve a bot from its logs

After a match, the bot's author finds out why it lost chips and changes the code. Every decision in the log keeps what the bot saw, what it returned, how it was applied, its think time, everything it printed and its `debug` value. The decision inspector shows one decision. The analysis report, the per-bot export and the bench tie it together across a match and across code versions.

## Sub-features

- `improve.inspector`: read a single decision in the UI (see watch-match `watch.inspector`).
- `improve.analysis`: the report of actions by street, biggest losing hands with the bot's logs/debug, showdowns and calls lost.
- `improve.export`: `poker export <run> --bot <name>`, the participant's zip with only their own private data.
- `improve.compare`: same seed and bench before and after a code change shows whether it helped.

## How to get to it (user POV)

- UI: open the match, click the bot's action in the Hand log, then read the Decision tab. The run page's **Download** gives the zip.
- Terminal: `poker export ... --bot`, `poker bench <bot>` (history shows the change versus the last version), and `poker match ... --seed <same>`.
- Verification helper: `.venv/bin/python $S/analyze.py $RUN <match_key> <bot>`.

## Driving it with Playwright and the poker CLI

Preconditions: a finished match with the bot (e.g. `vs_bots`) and `bench_before.json` from test-against-bots.

- Analyse: `.venv/bin/python $S/analyze.py $RUN vs_bots verify_bot` prints and saves `analysis_verify_bot_vs_bots.md`. It has "Result:", decisions per street, and "Biggest losing hands" with `debug={...}` and `logs:` lines from the bot. For the seed bot, the per-street lines show the real leak: flop, turn and river are 100% `check`, because its `strength` only scores the hole cards. The losing hands show preflop calls marked `why: cheap enough`.
- Export: `.venv/bin/poker export data/verify/$RUN/runs/vs_bots --bot verify_bot -o data/verify/$RUN/export_verify_bot.zip` writes a zip. In its `events.jsonl`, `verify_bot` decisions keep `state` and `logs`, and other bots' decisions have neither.
- Change and compare: edit `my_bots/verify_bot/bot.py` to fix what the report shows. For the seed bot, score made hands postflop with `eval7` (`eval7.handtype(eval7.evaluate(cards))`), bet strong hands, fold weak hands facing bets, and fold weak hands to preflop raises. Then:
  - `$R/sandbox/validator.py` passes;
  - re-run the same match command with `--id vs_bots_v2`;
  - re-run `poker bench verify_bot --hands 300 --workers 4 --json > data/verify/$RUN/bench_after.json`.
  The after bench's `hash` differs from before and `previous` contains the before entry. Report both `overall_bb_per_100` values; `analyze.py` on `vs_bots_v2` should show postflop `raise` counts above zero. An improvement is a claim backed by these numbers, not by the edit.
- Reference result (proof run, Oct 2026): with the v2 fix described above, the same-seed match went from +8.4 to +77.2 bb/100, and the bench from +59.8 ± 41.5 to +304.3 ± 74.4 (intervals don't overlap).

## Gotchas

- Compare like with like: same `--seed`, opponents and hands. Bench seeds are fixed already.
- Bench CIs can be wide (aggressive opponents have high variance). If the before and after intervals overlap, say so instead of claiming an improvement.
- `analyze.py` reads `data/verify/$RUN/runs/<key>`. For matches run elsewhere, copy or point `--out` there.
- Logs are capped at 8 KB per decision (`logs_truncated: true`) and debug at 16 KB.
