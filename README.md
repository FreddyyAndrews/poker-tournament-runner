# Poker Tournament Runner

A toolkit for running No-Limit Texas Hold'em bot competitions.

- **Participants** write a bot against a one-function template, then test it locally, watch it play and dig into its decisions.
- **Organizers** load every submission and run heads-up matches and tournaments in many formats, with replayable, verifiable results and a spectator-friendly UI.

Forked from [fullhouse-engine](https://github.com/uzlez/fullhouse-engine) (MIT), which provides the poker engine, Docker sandbox, validator and reference bots.

> **Status: early development.** The engine, sandbox, validator, reference bots, `poker` command (matches, tournaments, series, bench, verify, export) work today. Roadmap items marked ✅ are done; the rest are being built incrementally.

---

## How it works

You write one file, `bot.py`, with one function:

```python
def decide(game_state: dict) -> dict:
    # your entire strategy goes here
    return {"action": "call"}
```

The engine calls `decide()` once per action. You get the full game state: your cards, community cards, pot size, stack sizes, betting history and position. You return one action.

---

## Getting started

Requires **Python 3.10**. `eval7` needs `longintrepr.h`, which was removed in 3.11+.

```bash
git clone git@github.com:FreddyyAndrews/poker-tournament-runner.git
cd poker-tournament-runner

python3.10 -m venv .venv && source .venv/bin/activate

# eval7 needs Cython<3 at build time and won't pick it up via build isolation.
# This also installs the `poker` command.
make install

make test           # all tests
```

### Playing matches

```bash
poker bots                                        # list bots in my_bots/ and bots/
poker match shark aggressor --hands 500           # heads-up, 500 hands at 50/100
poker match shark aggressor mathematician template --freezeout --blinds turbo -v
poker match my_bots/mybot shark --stack 20000 --blinds 100/200/25 --seed 7
poker match my_bots/mybot shark --cash --hands 1000   # every hand starts from the starting stack
poker verify data/runs/<match-id>                 # replay the log and check it
```

- **Bots** are names from `my_bots/` or `bots/`, or paths to a `bot.py`, a bot folder or a `bot.zip`. 2–9 per table.
- **Fixed mode** (default) plays `--hands` hands with stacks carrying over. **`--cash`** plays `--hands` hands with every hand starting from the starting stack (nobody busts; best for measuring bb/100). **`--freezeout`** plays until one bot has every chip, with blinds rising by hand count (`--max-hands` caps it).
- **`--blinds`** takes flat blinds `SB/BB[/ANTE]` or a structure: `turbo` (10 hands per level), `standard` (20) or `slow` (40). Structures scale with `--stack`.
- **Seats are fixed** for the whole match, and the button moves to the next occupied seat each hand.
- **Results** show each bot's place, final stack, chip delta, bb/100, hands played, think time, timeouts and errors. Add `--json` for machine-readable output.
- **Every match is logged** to `data/runs/<match-id>/`:
  - `events.jsonl` records every card, post, decision, award and elimination. Each decision includes what the bot saw, its reply, how it was applied, its think time, logs and `debug`.
  - `summary.json` holds the results.
- **`poker verify`** replays the log through the engine with the recorded seed and decisions, and checks that the cards, chips, awards and places all match. Any edit to the log is caught.
- **Seeds:** every match has one (generated if you don't pass `--seed`). The same seed and bots replay the same match: same cards, and each bot's `random`/`numpy.random` is seeded from it too.

### Tournaments and series

```bash
poker run configs/bracket.yaml                    # any format from a YAML config (see configs/)
poker run --format round_robin --bots shark aggressor mathematician ref_bot_2 --workers 4
poker run configs/mtt.yaml --blinds turbo --seed 3
poker run configs/bracket.yaml --runs 100 --workers 8   # a series: 100 brackets, aggregated
poker results <run-id>                            # show standings again
poker runs                                        # list recent matches, runs and series
poker verify <run-id>                             # check every match log and the standings
poker export <run-id> --bot shark                 # zip of results for one participant
```

| Format | What it plays | Defaults |
|---|---|---|
| `table` | One table of 2–9 bots. Heads-up is a table of 2; `duplicate: true` plays heads-up twice with the same cards and seats swapped | fixed, 100 hands, 50/100 |
| `bracket` | Single-elimination heads-up knockout. Seeding `random` or `given`; byes go to the top seeds. Losers in a round share a place (2nd, 3rd, 5th, ...) | freezeout, `standard` blinds |
| `round_robin` | Every pair plays heads-up. Points: win 1, draw 0.5; then chips won. Prints a head-to-head table | duplicate cash, 200 hands |
| `mtt` | Multi-table freezeout: random seating at tables of `table_size`, tables balanced and broken as players bust, down to a final table | freezeout, `standard` blinds, 6 per table |

- **Settings:** `bots`, `names`, `mode` (`fixed`/`cash`/`freezeout`), `hands`, `starting_stack`, `blinds`, `time_limit_ms`, `seed`, `table_size`, `duplicate`, `seeding`, plus `runs`, `workers` and `docker`. Command-line flags override the config file.
- **Results** live in `data/runs/<run-id>/`: `run.json` holds the standings and every match, and `matches/<match-id>/` holds each match's log.
- **Series** (`--runs N`) play the same format N times with seeds derived from one series seed, and report each bot's wins, win rate with a 95% confidence interval, top-3 rate, average place, bb/100 and finish distribution.
- **`poker verify`** on a run or series checks every match log, then recomputes the standings (and series totals) from the logs and compares them with what was saved.
- **`poker export --bot NAME`** keeps everything public plus that bot's own private data (its hole cards, what it saw, its logs and `debug`). Other bots' hole cards appear only when they were shown down, and their decision details are removed.

### Is my bot improving?

```bash
poker bench my_bots/mybot                 # vs every reference bot, 500 hands per seat
poker bench my_bots/mybot --opponents shark aggressor --hands 2000 --workers 4
```

`poker bench` plays a duplicate cash match against each opponent with **fixed seeds**, so every version of your bot faces exactly the same cards. It reports bb/100 per opponent with a 95% confidence interval, plus the average, and appends the result to `data/bench/<bot>.jsonl` with the bot's code hash, showing how the score changed since the last version. If the confidence intervals overlap, the change may just be noise: add hands.

### Web UI and live viewing

```bash
make web                                  # build the UI once (needs Node 18+)
poker serve --open                        # http://127.0.0.1:8765  (API docs at /docs)
poker match shark aggressor --pace 600    # then watch it from the Recent list
```

**New** starts anything from a form: pick a format (table, bracket, round robin, multi-table), add players, choose how each match is played (freezeout, fixed hands or cash), the stack and blinds (presets with a level preview, or flat), and whether to watch it live (Live, Slow, Quick or Instant pace) or play it many times as a series. A summary shows exactly what will run and roughly how long a paced run takes. A single table opens straight into the live view; a tournament opens its run page, where **Now playing** links to the match being played.

**Recent** lists every match, tournament and series. Open a match to watch it:

- **Table:** cards dealt, bets, the pot, the acting bot outlined, showdowns with hand names and winners.
- **Realtime with history:** a running match shows **LIVE** and follows each action as it happens. Scrub, step or replay earlier hands at any time, then press **Go live** to jump back to now.
- **Controls:** play/pause, step, previous/next hand, a timeline with hand markers, speeds from 0.5x to 32x. Keys: Space, Left/Right, Shift+Left/Right, L.
- **Hand log** of the current hand, a **decision inspector** (what the bot saw, what it returned, how it was applied, think time, its logs and `debug`), a **stack chart** and live **standings**.
- **Hole cards:** spectator view shows everyone's cards; "Hide hole cards" shows them only at showdown.

For UI development run `poker serve` and `cd web && npm run dev` (Vite on port 5173, proxying `/api`). `make web-test` runs the Playwright end-to-end tests, and `node web/scripts/screenshot.mjs <url> <out.png>` takes a screenshot of any page.

`poker serve` runs the API and the UI on this machine only. Matches, tournaments and series started through it run as background jobs through the same code as the CLI, so their results land in `data/runs/` and work with `verify`, `results` and `export`.

- **Realtime:** a match started from the UI plays at a watchable pace (`pace_ms`, default 600 ms per action; deals and showdowns pause longer). A bot's think time counts toward the pause, so pacing never cuts into a bot's time limit. Series always run at full speed.
- **Live stream:** `GET /api/matches/<key>/stream?from=N` streams a match's events (server-sent events) from event N as they're written. Join late and you catch up first; replays of finished matches use the same stream.
- **Jobs:** `POST /api/matches`, `POST /api/runs` (any format, `runs` > 1 for a series), `GET /api/jobs`, `POST /api/jobs/<id>/cancel`.
- **Results:** `GET /api/runs`, `/api/runs/<id>`, `/api/matches/<key>`, `/api/matches/<key>/events`, `/api/verify/<id>`, `/api/export/<id>?bot=NAME`.

### Docker sandbox

To run bots in the Docker sandbox (`--docker` on `match`, `run` and `bench`; the competition setup), build the image first, and rebuild it whenever `sandbox/runner.py` changes:

```bash
./sandbox.sh build
```

Validate a bot before submitting it:

```bash
make validate BOT=bots/mybot/bot.py
```

---

## Writing your bot

Copy the template and edit `decide()`:

```bash
cp -r bots/template bots/mybot
# edit bots/mybot/bot.py
```

**Game state your bot receives:**

| Key | Type | Description |
|-----|------|-------------|
| `your_cards` | `list[str]` | Your two hole cards, e.g. `["As", "Kh"]` |
| `community_cards` | `list[str]` | Board cards, e.g. `["7d", "Tc", "2s"]` |
| `street` | `str` | `preflop` / `flop` / `turn` / `river` |
| `pot` | `int` | Total chips in the pot |
| `your_stack` | `int` | Your remaining chips |
| `amount_owed` | `int` | Chips needed to call (0 = free check) |
| `can_check` | `bool` | True when there is no bet to call |
| `current_bet` | `int` | Highest bet this street |
| `min_raise_to` | `int` | Minimum legal raise total |
| `players` | `list` | Public info on all seats |
| `your_bot_id` | `str` | Your bot's name. Stable across hands, unlike seat numbers |
| `action_log` | `list` | Every action taken this hand, each `{"seat", "bot_id", "action", "amount"}` (including `ante`, `small_blind`, `big_blind` posts) |
| `match_action_log` | `list` | The last 200 actions across all hands in this match, each `{"hand_num", "seat", "bot_id", "action", "amount"}` |
| `hand_num` | `int` | Hand number within the match |
| `blinds` | `dict` | Current blinds, e.g. `{"sb": 50, "bb": 100, "ante": 0}` (these rise in tournaments) |
| `button_seat` / `sb_seat` / `bb_seat` | `int` | Seats of the button and blinds; compare with `seat_to_act` for your position |
| `time_limit_ms` | `int` | Your deadline for this decision |

> **Seats vs. names:** seat numbers are positions at the table *for this hand*. When a player busts, the table is reseated and seat numbers shift. To track an opponent across hands, use `bot_id` (in `players`, `action_log` and `match_action_log`), not `seat`.

**Valid return values:**

```python
{"action": "fold"}
{"action": "check"}                       # only when can_check is True
{"action": "call"}
{"action": "raise", "amount": 1200}       # amount = total bet, not raise-by
{"action": "all_in"}
```

Invalid or missing actions count as a fold. Raises below the minimum are snapped up automatically.

**Debugging your bot:**
- Anything you `print()` inside `decide()` (stdout or stderr) is captured and saved with that decision as its logs, up to 8 KB per decision. Prints at import time are saved with the bot's load logs.
- You can add an optional `"debug"` value to the action you return, and it is recorded alongside the decision. It must be JSON-serialisable (up to 16 KB). For example:
  ```python
  return {"action": "raise", "amount": 600, "debug": {"equity": 0.71, "reason": "value bet"}}
  ```
- Think time is measured for every decision.
- Before hand 1 your bot gets one `{"type": "warmup"}` call with a long deadline for any lazy loading. Return anything (or ignore it); errors here are harmless.

**Randomness:** use the `random` module or `numpy.random`. They are seeded per match, so a seeded match is reproducible. Your own unseeded generators (`random.Random()`, `os.urandom`, time-based seeds) make your bot's matches impossible to replay.

**Rules and limits:**
- 2 seconds to return an action, or your bot folds
- 768 MB RAM and 0.5 CPU core per bot (in the Docker sandbox)
- No network access during gameplay
- No file writes. You may read from `data/`, but only at module import time
- Crashes and exceptions fold that hand; your bot stays in the game

**Available libraries:** `eval7`, `numpy`, `scipy`, `treys`, `scikit-learn` and the Python 3.10 standard library.

### Not allowed

The sandbox blocks most of these at the OS level. They are listed so there's no ambiguity. Breaking them can get a bot removed from a competition.

- **No external calls of any kind:** no HTTP, LLM APIs, webhooks, DNS or `socket`. The container runs with `--network none`.
- **No peeking:** don't read other bots' code or hole cards, scrape `/proc`, or inspect the runner's memory.
- **No file writes:** the filesystem, including `data/`, is read-only at runtime. `os.system`, `subprocess` and `open(..., "w")` are blocked.
- **No timeout dodging:** don't use background threads or async work to keep computing past the per-action deadline.
- **No collusion:** bots must play independently. No soft-play, chip-dumping or shared state between submissions.
- **No resource abuse:** no crypto-mining, and no deliberately exhausting memory or CPU.
- **No reflection escapes:** `__import__('socket')`, `getattr(__builtins__, 'open')`, `eval()`, `exec()` and `compile()` are flagged by the validator. So are obfuscated versions like `__import__('so'+'cket')`.

**TL;DR:** treat your container as the entire universe. If it isn't in the standard library or the list above, and you can't load it from your own `data/` at import time, it doesn't exist.

### Submission formats

| Format | Use when |
|---|---|
| `bot.py` (single file) | Simple bot, no large lookup tables |
| `bot.zip` containing `bot.py` + optional `data/` | You ship a CFR blueprint, neural-net weights, an equity table, etc. |

`data/` constraints:
- At most 200 MB in total
- No `.py` files inside (put code in `bot.py`)
- Read-only at runtime, found via `os.environ["BOT_DATA_DIR"]`
- Loaded **at import time only**; each action must still respond within the time limit

```python
# bot.py
import os, numpy as np
DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
BLUEPRINT = np.load(os.path.join(DATA_DIR, "blueprint.npz"))   # loaded once at import

def decide(state):
    # use BLUEPRINT to make decisions; no file I/O here
    ...
```

### Reference bots

| Bot | Strategy |
|-----|----------|
| `bots/template/bot.py` | Pocket pairs + basic pot odds |
| `bots/aggressor/bot.py` | Raises constantly regardless of hand |
| `bots/mathematician/bot.py` | Calls only when getting 3:1 pot odds |
| `bots/shark/bot.py` | Tight preflop, position-aware, value bets |
| `bots/ref_bot_2/bot.py` | Pot-odds caller, heuristic baseline |

---

## Roadmap

### Goals

1. **Tournament day:** given 10–40 bots, run a wide range of tournament styles with clear metrics, replayable and checkable results, and a visually satisfying interface for participants and spectators.
2. **Local testing:** participants seat their bots at tables, watch them play, and download structured output covering hands, decisions and the bot's own logs.
3. **Live viewing:** watch with playback controls, and seat **humans or LLMs (via MCP)**, both through the same seat interface.
4. **Formats:** heads-up matches and a variety of tournaments.
5. **Repeated runs:** rerun a format many times and aggregate the results, e.g. "across 100 heads-up single-elimination brackets, this bot won 37 times".

Everything must be drivable by an agent, through the web UI (e.g. Claude in Chrome), the CLI and MCP.

### Engine changes

- ✅ **Blinds and stacks:** configurable small blind, big blind and antes in the engine. Starting stacks and rising blind levels across a match come with the table/format work.
- ✅ **All-in blind fix:** a player who is all-in after posting a blind is never asked to act.
- ✅ **Richer `game_state`:** blinds, button/SB/BB seats, hand number, time limit, and `bot_id` everywhere. Tournament context (level, players left) is still to come. New fields are added only, so existing bots keep working.
- ✅ **Runner protocol v2:**
  - `print()` output inside `decide()` is captured per decision as the bot's logs, instead of corrupting the protocol.
  - Bots may return an optional `debug` dict.
  - Think time is measured for every decision.
- ✅ **Host hardening:** host-side timeouts, recovery when a bot process stalls or dies, stderr capture, and parallel warmup.
- ✅ **Reproducible bots:** each bot's RNG is seeded from the match seed.
- **Seat interface:** bots, humans and MCP clients share one seat interface, with decision times configurable per seat type (e.g. bot 2 s, human 30 s, LLM 120 s).

### Formats

| Format | Description |
|---|---|
| ✅ Heads-up | Fixed hands, cash or freezeout. Optional *duplicate* mode replays each deal with seats swapped to cut luck. (Best-of-K: later) |
| ✅ Ring | 2–9 players, fixed hands or cash, scored in bb/100 |
| ✅ Sit & Go | Single-table freezeout with rising blinds |
| ✅ MTT | Multi-table freezeout with table balancing and a final table |
| ✅ Bracket | Single-elimination heads-up, seeded or random, with byes |
| ✅ Round-robin | Every pair plays heads-up; head-to-head matrix |
| Swiss | Score-grouped tables, no rematches where possible (after v1) |
| ✅ Series | Any format above, run N times with derived seeds |
| Stages | Seed one event from another's results, e.g. Swiss → top-8 bracket (after v1) |

Blind levels advance by **hand count**, not wall-clock time, so runs are deterministic.

### Metrics

- **Per match:**
  - ✅ place, chip delta, bb/100, hands played
  - ✅ timeouts, errors, think time
  - VPIP, PFR, 3-bet, aggression factor, WTSD, W$SD (after v1)
- **Per series:** ✅ win rate with Wilson confidence intervals, finish distribution, top-3 rate, average place; ✅ head-to-head matrix (round-robin); ratings (after v1).
- ✅ **`poker bench <bot>`:** a fixed-seed duplicate gauntlet against the reference bots with 95% CIs, tracked per bot version, so you can see whether a bot is actually improving.

### Reproducibility

- **Seeds:** derived from run → match → hand and recorded alongside a full event log.
- **`poker verify`:** replays every logged decision through the engine and confirms the decks, pots, awards and placings.
- **Exports:** run bundles, plus per-bot exports so each participant only receives their own bot's internal logs.

### Bot Lab

- **Spot tester:** build a situation (cards, board, stacks, action so far), ask the bot what it does, and see its action distribution, logs and think time.
- **Scenario suites:** `poker lab test` runs scenarios with expected actions, like unit tests.
- **Play your bot:** take a human seat against it, in the browser or the terminal.

### Interfaces

- **`poker` CLI:** every command supports `--json`.
  ```
  poker serve [--docker]
  poker bots list | new | validate | import <classroom-dir>
  poker match shark aggressor --hands 500 --sb 50 --bb 100 --stack 20000
  poker run configs/bracket_hu.yaml --runs 100
  poker results | export | verify <run>
  poker bench <bot>
  poker lab run|test <bot>
  poker play <bot>
  ```
- **API:** FastAPI with a WebSocket live event stream.
- **Web UI:**
  - format launcher
  - live/replay table with playback and a decision inspector
  - run dashboards (bracket tree, MTT tables, Swiss rounds, series charts)
  - broadcast mode for spectators
  - bots page with benchmark history
  - Bot Lab
- **MCP server:** LLMs can join seats, wait for their turn and act, using the same seat interface as humans.

### Planned layout

```
engine/ sandbox/ bots/   poker engine, sandbox, reference bots (from upstream, patched)
arena/                   tournament runner: seats, tables, formats, stats, CLI, API, MCP
web/                     React + TypeScript frontend
my_bots/                 participants' bots (auto-discovered)
configs/                 example run configs for every format
docs/                    bot guide, organizer guide (GitHub Classroom), MCP, CLI
```

### Milestones

1. ✅ **Engine + core:** engine patches, runner protocol v2, seats, tables, event logs, `poker match`, `poker verify`
2. ✅ **Formats + metrics:** table, bracket, round-robin, MTT, series, stats, `poker run/results/runs/export/bench` (Swiss and stages after v1)
3. **API + web UI:** ✅ server (jobs, pacing, live event streams); ✅ live/replay table with decision inspector; ✅ launcher; dashboards next
4. **Remote seats:** human seats, MCP server, decision timers
5. **Tournament UX:** run dashboards, broadcast mode, bots page
6. **Lab + docs:** Bot Lab, GitHub Classroom import, Docker validation, docs, end-to-end tests

Upstream leftovers (`demo.py`, `db/schema.sql`, `CONTRIBUTING.md`) will be replaced or removed as these milestones land.

---

## Repo structure (current)

```
arena/          Match runner: seats, tables, blind structures, formats, series, bench, verify, export, `poker` CLI
arena/server/   HTTP API, background jobs, live event streams (`poker serve`)
configs/        Example run configs for every format
web/            React + TypeScript UI (built into web/dist, served by `poker serve`)
engine/         Game engine: NLHE rules, hand evaluation, chip tracking
sandbox/        Bot runner, Docker sandbox, validator, legacy match runner
bots/           Reference bots and starter template
tests/          Engine, runner and arena tests
data/runs/      Match logs (gitignored)
demo.py         Old upstream demo (to be replaced by the web UI)
```

---

## License

MIT. See [LICENSE](LICENSE). Based on [fullhouse-engine](https://github.com/uzlez/fullhouse-engine) by the Fullhouse Hackathon team.
