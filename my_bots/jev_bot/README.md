# jev_bot

A naive bot that asks [Jev](https://docs.typesafe.ai/introduction) (TypeSafe's System One decision model)
for every action. It runs locally only: it needs the network, so it can never play in the Docker sandbox
and the validator rejects it. Its purpose is to measure how strong Jev's judgment is on its own, with
no context management, no equity maths and no opponent modelling.

## How it works

One `POST https://api.typesafe.ai/v1/systemone` per decision:

- **state**: the raw `game_state` dict, unchanged (`match_action_log` included; set `JEV_INCLUDE_MATCH_LOG=0` to drop it).
- **"system prompt"**: a short strategy guide (`SYSTEM_PROMPT` in `bot.py`) sent as the structured `instructions`
  of a Choice question. Jev reads it literally, so it is a list of plain rules.
- **`action`** Choice: the legal actions as options, with concrete chip amounts in the descriptions
  (`fold` is only offered when there is a bet to face, `raise` only when a raise above the minimum is possible).
- **`raise_size`** Choice, asked speculatively in the same request: min raise, half pot, pot, two pot and all-in,
  with the totals computed in code because Jev is not a calculator. Used only when `action` is `raise`.
- Any API error or timeout falls back to check/fold and records the reason in `debug`.

Each decision's `debug` holds the model id, latency, confidence, the full probability distribution over
actions and sizes, and token usage, so the UI's decision inspector shows exactly what Jev thought.

Config: `JEV_API_KEY` (or `TYPESAFE_API_KEY`) from the environment or a `.env` up the tree, `JEV_MODEL`
(default `jev-latest`), `JEV_SAFETY_MS` (time kept free under the deadline, default 400).

## v1 results (jev-1.13.0, local, 50/100 cash)

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
- `match_action_log` is ~80% of the tokens and Jev's docs say irrelevant detail is a distractor. Dropping it
  (`JEV_INCLUDE_MATCH_LOG=0`) is the first experiment for the next version.

## Next ideas

- Run `poker bench my_bots/jev_bot` (needs the local, non-Docker runner) for CIs per opponent.
- Compare with/without `match_action_log` and with a plain-English hand summary instead of raw JSON.
- Add a Score question for hand strength and let code pick the size from it.
