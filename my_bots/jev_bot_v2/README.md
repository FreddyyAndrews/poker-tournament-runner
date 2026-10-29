# jev_bot_v2

[jev_bot](../jev_bot/README.md) with a mixed strategy. The request to Jev is unchanged; the only difference is
how the answer is used.

v1 always plays Jev's top choice. v2 samples the action from Jev's probability distribution, so if Jev gives
`raise 0.6, call 0.4` the bot raises 60% of the time and calls 40% of the time in that spot. The raise size is
sampled the same way from the `raise_size` distribution. Probabilities for options that were not offered are
ignored, and if the distribution is missing the bot falls back to Jev's own choice.

Each decision's `debug` adds `jev_choice` (what v1 would have played) and `sampled` (what v2 played), plus
`jev_size_choice` on raises, so the two can be compared in the decision inspector.

Config, network requirements and fallbacks are the same as v1.

## First smoke run (jev-1.13.0, local, 50/100 cash)

```bash
poker match my_bots/jev_bot_v2 shark --cash --hands 30 --local --seed 1 --id jev_v2_smoke
```

30 hands, +56.7 bb/100, no timeouts or fallbacks. 19 of 38 decisions differed from Jev's top choice: Jev's
distributions are flat (top option often 0.5 to 0.7), so sampling makes the bot much looser than v1, for example
calling preflop with 2h 3c at `fold 0.68, call 0.30`. Too few hands to say whether that helps.
