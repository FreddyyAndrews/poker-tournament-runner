# Poker Bot Hackathon: Starter Kit

<!-- TODO: hackathon name, dates, how and when to submit, prizes, contact. -->

Everything you need to build a No-Limit Texas Hold'em bot for the hackathon: the
game engine, the table and blind logic, the bot sandbox, a validator, a bot
template and a small script for playing matches on your own machine.

The code under `pokercore/` is the same code that runs the tournament, byte for
byte, so a bot that works here works on tournament day. Each copy of the kit
records which version of that code it carries in `CORE_VERSION`.

## The tournament

All matches are **heads-up** (one bot against one bot).

**Qualification.** Every bot plays every other bot. Each pairing is a
*duplicate* pair of freezeout matches: the two bots play until one has every
chip, then play again with the same cards and the seats swapped, which cancels
out much of the luck of the deal. Winning both matches wins the pairing, one
each is a draw. The round robin is repeated as many times as the qualification
window allows, and the four bots with the best records go through to the final.

**Final.** <!-- TODO: format of the final. --> To be announced.

**Every match** starts at 10,000 chips each, with standard blinds that rise every
20 hands:

| Level | Hands | Blinds | Ante |
|---|---|---|---|
| 1 | 1-20 | 25/50 | - |
| 2 | 21-40 | 50/100 | - |
| 3 | 41-60 | 75/150 | - |
| 4 | 61-80 | 100/200 | - |
| 5 | 81-100 | 150/300 | - |
| 6 | 101-120 | 200/400 | 40 |
| 7 | 121-140 | 300/600 | 60 |
| 8 | 141-160 | 400/800 | 80 |
| 9 | 161-180 | 500/1,000 | 100 |
| 10 | 181-200 | 700/1,400 | 140 |
| 11 | 201-220 | 1,000/2,000 | 200 |
| ... | | blinds keep rising | |

Your bot doesn't need to remember this table: every decision includes the
current `blinds`.

## Getting started

You need Python 3.10 (one of the libraries, eval7, doesn't build on 3.11+).

```bash
make install                                   # eval7, numpy, scipy, treys, scikit-learn
cp -r bots/template my_bots/mybot              # your bot lives in my_bots/mybot/bot.py
python3 play.py my_bots/mybot bots/caller      # play a match against a calling station
python3 play.py my_bots/mybot bots/caller --show    # ...and watch every hand
make validate BOT=my_bots/mybot                # the checks your bot must pass
```

`play.py` plays the tournament format by default (heads-up freezeout, standard
blinds). Useful options:

- `--seed 7` deals the same cards every time, so you can compare two versions of your bot fairly.
- `--blinds turbo`, `--blinds 50/100` (flat), `--mode fixed --hands 500`, `--stack 20000`.
- `--log match.jsonl` writes every event, including exactly what your bot saw at each decision.
- `--docker` runs the bots inside the tournament's sandbox (see below).

The kit is intentionally small: it plays a match and tells you who won. Testing
harnesses, statistics and opponents to benchmark against are part of the
challenge, and are yours to build.

### Testing in the sandbox

In the tournament every bot runs in its own Docker container with no network,
a read-only filesystem, **768 MB of memory and half a CPU core**. Before you
submit, run your bot there at least once, since a bot that's fast on your
laptop can be slow on half a core:

```bash
make sandbox                                   # build the image (once, and after updating the kit)
python3 play.py my_bots/mybot bots/caller --docker
```

## What your bot sees

Your bot is a `bot.py` with a `decide(game_state)` function, called each time
it's your turn. It returns an action:

```python
{"action": "fold"}
{"action": "check"}                       # only when can_check is True
{"action": "call"}
{"action": "raise", "amount": 1200}       # amount = your TOTAL bet this street, not the raise-by
{"action": "all_in"}
```

Invalid or missing actions count as a fold. A raise below the minimum is
raised to the minimum.

`game_state` contains:

| Key | Type | Description |
|-----|------|-------------|
| `your_cards` | `list[str]` | Your two hole cards, e.g. `["As", "Kh"]` |
| `community_cards` | `list[str]` | The board, e.g. `["7d", "Tc", "2s"]` (empty preflop) |
| `street` | `str` | `preflop`, `flop`, `turn` or `river` |
| `pot` | `int` | Total chips in the pot |
| `your_stack` | `int` | Your remaining chips |
| `your_bet_this_street` | `int` | Chips you've put in on this street |
| `amount_owed` | `int` | Chips needed to call (0 means you can check) |
| `can_check` | `bool` | True when there is nothing to call |
| `current_bet` | `int` | The highest bet on this street |
| `min_raise_to` | `int` | The smallest legal raise, as a total |
| `your_bot_id` | `str` | Your bot's name |
| `seat_to_act` | `int` | Your seat this hand |
| `button_seat`, `sb_seat`, `bb_seat` | `int` | Who has the button and the blinds this hand |
| `hand_num` | `int` | Hand number in the match, from 0 |
| `hand_id` | `str` | A unique id for the hand |
| `blinds` | `dict` | This hand's blinds, e.g. `{"sb": 50, "bb": 100, "ante": 0}` |
| `players` | `list` | Public info for each seat: `seat`, `bot_id`, `stack`, `state` (`active`, `folded`, `all_in`, `busted`), `is_folded`, `is_all_in`, `bet_this_street`, `hole_cards` (always `None` for opponents) |
| `action_log` | `list` | Every action so far this hand, each `{"seat", "bot_id", "action", "amount"}`, including the `small_blind`, `big_blind` and `ante` posts |
| `match_action_log` | `list` | The last 200 entries across the whole match (this hand included): the same entries as `action_log`, plus `hand_num` |
| `time_limit_ms` | `int` | Your deadline for this decision |

**Amounts in the logs:** for `raise` and `all_in`, `amount` is the bettor's total
on that street. For `call` it is the chips added, for posts the chips posted, and
for `check` and `fold` it is 0. Antes are dead money and don't count toward
anyone's bet on the street.

**Tracking your opponent:** `match_action_log` and the `bot_id` on every entry
let you build a picture of how your opponent plays across hands: how often it
folds, raises, calls down. Use `bot_id` rather than `seat` to tell players
apart.

**Before hand 1** your bot gets one call with `{"type": "warmup"}` and a long
deadline, so slow imports and loading happen before the clock matters. Return
anything; errors here are harmless.

**Debugging:** anything you `print()` inside `decide()` is saved with that
decision (up to 8 KB), and you can return an extra JSON-serialisable `"debug"`
value with your action, e.g. `{"action": "call", "debug": {"equity": 0.41}}`.
Both appear in `play.py --log`.

**Randomness:** use `random` or `numpy.random`. They're seeded from the match
seed, so a seeded match replays exactly. Generators you seed yourself from the
clock or `os.urandom` make your matches impossible to replay.

## Rules and limits

- **2 seconds per decision.** Run out of time and your bot folds. Crashes also
  fold that hand; your bot stays in the match.
- **768 MB of memory and 0.5 CPU core** in the sandbox.
- **Libraries:** the Python 3.10 standard library, `eval7`, `numpy`, `scipy`,
  `treys` and `scikit-learn`. Nothing else is installed.
- **Data files:** you can ship a `data/` folder next to `bot.py` (lookup tables,
  model weights) and read it **at import time only**, via
  `os.environ.get("BOT_DATA_DIR")` or the folder next to `bot.py`. Limits:
  `bot.py` up to 5 MB, `data/` up to 200 MB, 250 MB in total, and no `.py`
  files inside `data/`.

### Not allowed

The sandbox blocks most of this anyway; it's listed so nothing is ambiguous.
Breaking these rules gets a bot removed from the tournament.

- **No outside contact:** no network of any kind (HTTP, LLM APIs, sockets, DNS).
- **No file writes, processes or shell commands:** no `open(..., "w")`,
  `subprocess` or `os.system`.
- **No dodging the clock:** no background threads or async work to keep thinking
  after `decide()` returns.
- **No reflection tricks:** `eval`, `exec`, `compile`, `__import__` and similar
  are rejected by the validator.
- **No peeking:** don't try to read other bots' code, cards or the runner's memory.
- **No collusion:** bots play independently. No soft-play, chip-dumping or
  coordination between entries.

`make validate BOT=...` runs the same checks the organisers run on every
submission.

## What's in the kit

```
bots/template/      a starting point for your bot
bots/caller/        a calling station to play against
my_bots/            your bots
play.py             play a match on your machine
pokercore/          the engine, table, blinds, sandbox and validator (the tournament's own code)
CORE_VERSION        which version of pokercore this kit carries
```

**Don't edit `pokercore/`.** It's generated from the tournament's code and
replaced whenever the kit is updated; changes there won't exist on tournament
day. If you find a bug in it, tell the organisers.

## Credits

The engine, sandbox and validator come from
[Full House Engine](https://github.com/uzlez/fullhouse-engine) (MIT licence).
This kit builds on it with rising blinds and antes, richer game state (`blinds`,
positions, `hand_num`, `bot_id` on every logged action, blind posts in the
match log), seeded bots for reproducible matches, and a hardened sandbox. See
`LICENSE`.
