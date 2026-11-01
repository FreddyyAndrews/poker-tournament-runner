# jev_bot

A naive bot that asks [Jev](https://docs.typesafe.ai/introduction) (TypeSafe's System One decision model)
for every action. It runs locally only: it needs the network, so it can never play in the Docker sandbox
and the validator rejects it. Its purpose is to measure how strong Jev's judgment is on its own, with
no context management, no equity maths and no opponent modelling.

## How it works

One `POST https://api.typesafe.ai/v1/systemone` per decision:

- **state**: a compact summary of the current hand built in code (`_summary` in `bot.py`): cards, board, position,
  pot, call price and pot odds, stacks, the opponents still in the hand, and this hand's action split by street
  (`"flop": "opus_bot bet 300, jev_bot call 300"`). About 1.1k input tokens per decision. The raw `game_state`
  used to be sent instead, at ~9.7k tokens, ~70% of it `match_action_log`.
- **"system prompt"**: a short strategy guide (`SYSTEM_PROMPT` in `bot.py`) sent as the structured `instructions`
  of the action Choice question, once per request. Jev reads it literally, so it is a list of plain rules.
- **`action`** Choice: the legal actions as options, with concrete chip amounts in the descriptions
  (`fold` is only offered when there is a bet to face, `raise` only when a raise above the minimum is possible).
- **`raise_size`** Choice, asked speculatively in the same request: min raise, half pot, pot, two pot and all-in,
  with the totals computed in code because Jev is not a calculator. Used only when `action` is `raise`.
- Any API error or timeout falls back to check/fold and records the reason in `debug`.

Each decision's `debug` holds the model id, latency, confidence, the full probability distribution over
actions and sizes, and token usage, so the UI's decision inspector shows exactly what Jev thought.

Jev is reached through the outside-bot proxy (`poker proxy`, `POST /jev/v1/systemone`): the proxy holds
`JEV_API_KEY`, counts each reply's tokens against the Jev budget, and refuses with HTTP 402 once the day's
allowance or this bot's share is spent (the bot then falls back to check/fold and records `http_402` in
`debug`). The bot sends no key, only `X-Poker-Bot: jev_bot`. Config: `POKER_PROXY_URL` (default
`http://127.0.0.1:8766`), `JEV_MODEL` (default `jev-latest`), `JEV_SAFETY_MS` (time kept free under the
deadline, default 400).

## v1 results with the raw game state (jev-1.13.0, local, 50/100 cash)

These predate the compact summary, so token counts and costs below are for the old ~9.7k-token requests.

```bash
poker match my_bots/jev_bot shark --cash --hands 200 --local --seed 1 --id jev_v1_hu_shark
poker match my_bots/jev_bot shark mathematician aggressor --cash --hands 150 --local --seed 2 --id jev_v1_4way
```

| Match | bb/100 | Actions (fold/check/call/raise/all-in) | Avg latency | Max | Input tokens/decision |
|---|---|---|---|---|---|
| HU vs shark, 200 hands | +10.1 | 37 / 93 / 151 / 24 / 0 | 166 ms | 287 ms | ~9.4k |
| 4-way vs shark, mathematician, aggressor, 150 hands | +176.0 (all three lost) | 91 / 92 / 176 / 28 / 3 | 160 ms | 389 ms | ~9.9k |

No timeouts, no errors, no fallbacks. Mean confidence 0.56. Raise sizes chosen: mostly `min` and `pot`.
Cost at $0.042/Mtok: about $0.12 per 200 heads-up hands, almost all of it the `match_action_log`.

Single seeds, so the heads-up number is well inside the noise; the 4-way result is large but also one seed.

## Observations

- Very call-heavy (about half of all decisions), rarely raises, never bluffs by design of the prompt.
- Preflop judgment looks sane in spot checks (raises AK, folds 72o with 0.99 confidence).
- Jev leans toward the first option in a Choice, so the option order (fold, check, call, raise, all_in) is a bias
  worth testing by reordering.

## Next ideas

- Per-opponent stats computed in code over the whole match (VPIP, PFR, fold to bets) as a few short lines in the
  summary: opponent modelling for a few dozen tokens, instead of the raw `match_action_log`.
- Run `poker bench my_bots/jev_bot` (needs the local, non-Docker runner) for CIs per opponent.
- Add a Score question for hand strength and let code pick the size from it.
