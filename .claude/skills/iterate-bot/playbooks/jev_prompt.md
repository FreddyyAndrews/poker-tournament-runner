# Playbook: Jev prompt bot

Every decision is one request to Jev (TypeSafe's System One model) with a compact description of the hand. The bot improves by refining what it sends: the instructions, the hand summary, and the options it offers. It can't run in the sandbox, because it needs the network to reach the outside-bot proxy.

## Budget comes first

Jev costs money, and the proxy enforces a daily allowance with a share per bot (see `$POKER proxy --status`). Before anything that calls Jev:

```bash
$POKER proxy --status --json | $PY -c "import json,sys; j=json.load(sys.stdin)['upstreams']['jev']; \
print(j['allowance_today_usd'], j['bot_daily_share'], j['bots_today_usd'].get('$NAME', 0))"
```

Your share left today is `allowance x share - spent`. Estimate a run's cost before starting it:

| What | Jev decisions | About |
|---|---|---|
| 1,000 heads-up hands | 1,500 | $0.074 |
| Gauntlet at 250 hands a seat (1,500 hands) | 2,250 | $0.11 |
| Spot suite, 200 decisions | 200 | $0.01 |

These assume about 1,230 tokens a decision at $0.04 per million. Use your own measured tokens once you have them (each decision's `debug.usage`). If the share left can't cover the run, record `--result skipped --experiment "jev budget"` and stop. A match that hits the limit midway keeps playing with check/fold fallbacks (`http_402` in `debug`): throw away any result with fallbacks.

## The eval ladder

1. **Spot suite (cheap screen).** Build it once, in the first run after the baseline: `tools/spots.py` samples about 200 decision states from recorded matches (all streets, facing bets and not) into `notes/spots.jsonl`. For a candidate change, run the bot's `decide()` on every spot (through the proxy) and compare with the previous version: which decisions changed, the fold/call/raise mix, and agreement with the strongest champion's decision on the same state (run its `decide()` locally, which is free). Only a change that moves decisions the way the hypothesis predicted goes on to a match.
2. **Gauntlet, small:** `$POKER bench $B --gauntlet --hands 250 --workers 3`, at most once a day unless the budget clearly allows more. Its CIs are wider, so the keep rule needs a bigger gain.
3. **No Slumbot,** unless the user raised the budget for it.

## Experiments

The strategy guide (Jev reads it literally: short plain rules work best), what goes into the hand summary (pot odds, position, stack-to-pot ratio, a line or two of per-opponent stats), option wording and order (Jev leans toward the first option), raise-size options, and whether to play Jev's top choice or sample from its probabilities.

Keep every request compact: about 1,500 tokens is the target, and the proxy refuses anything over 16,000 bytes.
