# Playbook: heuristic bot

A pure-Python bot that improves by changing its own code: rules, ranges, thresholds and opponent models. No training, no outside calls.

## Experiments

One of these per run, chosen from evidence (the matches it loses most, its journal):

- **Preflop:** opening and defending ranges by position and stack depth, 3-bet and 4-bet ranges, limp handling.
- **Sizing:** bet and raise sizes by street, board texture and stack-to-pot ratio.
- **Postflop thresholds:** equity needed to bet, call or raise against the pot odds, by street and by how many opponents are left.
- **Bluffing:** frequencies and spots (missed draws, scare cards), and how often to give up.
- **Opponent modelling:** per-opponent stats kept in module globals keyed by `bot_id` (VPIP, PFR, fold to a bet, aggression), and how they shift the thresholds. The gauntlet is heads-up, so exploit what each champion actually does.
- **Speed:** keep p95 think time under 200 ms. Equity sampling is the usual cost.

## Benches

| When | Command | Cost |
|---|---|---|
| Every run | `$POKER bench $B --gauntlet --hands 2000 --workers 3` | under a minute of CPU |
| After every 3 kept changes | `$POKER bench $B --opponents slumbot --hands 1000` on the best version | 2,000 Slumbot hands, 10-30 minutes |
| After every 10 kept changes | a 6-handed table and a turbo bracket (build skill, step 4), zero timeouts | a few minutes |

A change that gains on the gauntlet but loses clearly against Slumbot (CIs not overlapping) is probably fitting the three champions rather than playing better poker: record it, revert it, and say so in the journal.
