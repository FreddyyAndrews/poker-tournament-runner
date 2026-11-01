# Playbook: RL bot (learns by self-play)

The bot learns offline: training scripts play the game against themselves and produce a strategy, which the bot ships in `data/` and looks up at play time. A bot can't write files during a match, so all learning happens in training, never in play.

## Shape

- **Training code** lives in `my_bots/<name>/tools/`. It may import the engine (`engine/game.py`) and numpy. PyTorch only if the user allowed it, in a separate training environment; the bot itself always runs on numpy.
- **Training output** (checkpoints, logs) goes to `$MAIN/data/train/<name>/`, which git ignores. Only the exported strategy goes into `my_bots/<name>/data/`, at most 20 MB a file.
- **The bot** loads the strategy at import time and plays it within 200 ms p95. It maps the real situation to the abstraction (hand bucket, betting history) and real bet sizes to the nearest abstract action.
- **Start with Monte Carlo CFR** (external sampling) on a heads-up abstraction: the 169 preflop hand classes, postflop buckets by hand strength and potential (8-16 per street, computed with eval7), and a few bet sizes (half pot, pot, all-in). This is the family of methods behind Slumbot. Grow the abstraction once the small one plays well.

## A run

**Starting training** is a run's experiment. Launch it in the background, sized by the cores the user allowed (6 unless the notes say otherwise), with a time limit:

```bash
mkdir -p $MAIN/data/train/$NAME
nohup $PY $B/tools/train.py --workers 6 --hours 6 --out $MAIN/data/train/$NAME > $MAIN/data/train/$NAME/train.log 2>&1 &
echo $!                                          # the process id
N phase $B training --reason "pid <pid>, until <time>, <what this run changes>"
N record $B --result training --experiment "<what this training run tests>" --summary "<config>"
```

Commit the notes and the training code, and stop.

**While training** (the notes say `training`): check the process (`kill -0 <pid>`) and the tail of its log. If it is still running, record nothing, and stop. Don't start other work that needs the CPU.

**When training is done:** export the strategy to `data/`, check the time budget, bench it (below), then keep or revert as usual. Set the phase back with `N phase $B iterating`.

## Experiments

Abstraction size (buckets, bet sizes), training length, the sampling scheme, how real bets map onto abstract ones, and an exploitation layer on top of the trained strategy that leans against each opponent's observed habits.

## Benches

Same as the heuristic playbook: the gauntlet at 2,000 hands a seat for every trained version, Slumbot after every 3 kept versions (luck-adjusted, and the fairest test of a strategy meant to be hard to exploit), and the multi-way checks after every 10.
