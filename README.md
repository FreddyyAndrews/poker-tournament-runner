# Poker Tournament Runner

A toolkit for running No-Limit Texas Hold'em bot competitions.

- **Participants** write a bot against a one-function template, then test it locally, watch it play and dig into its decisions.
- **Organizers** load every submission and run heads-up matches and tournaments in many formats, with replayable, verifiable results and a spectator-friendly UI.

Forked from [fullhouse-engine](https://github.com/uzlez/fullhouse-engine) (MIT), which provides the poker engine, Docker sandbox, validator and reference bots.

> **Status: early development.** The engine, sandbox, validator, reference bots and single-match runner work today. Everything under [Roadmap](#roadmap) is planned and being built incrementally.

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

# eval7 needs Cython<3 at build time and won't pick it up via build isolation
make install

make test           # engine unit tests
python3 demo.py     # live demo at http://localhost:5001
```

`demo.py` shows six reference bots playing each other live, with a leaderboard and hand replay.

> **macOS:** the demo uses port 5001 because macOS binds port 5000 to AirPlay Receiver. Override with `DEMO_PORT=8080 python3 demo.py`.

Run a single match from the terminal:

```bash
python3 sandbox/match.py bots/mybot/bot.py bots/shark/bot.py --hands 400 [--seed 7] [--json]
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
| `action_log` | `list` | Every action taken this hand |

**Valid return values:**

```python
{"action": "fold"}
{"action": "check"}                       # only when can_check is True
{"action": "call"}
{"action": "raise", "amount": 1200}       # amount = total bet, not raise-by
{"action": "all_in"}
```

Invalid or missing actions count as a fold. Raises below the minimum are snapped up automatically.

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

- **Blinds and stacks:** configurable small blind, big blind, antes and starting stacks, with rising blind levels in tournament formats.
- **All-in blind fix:** a player who is all-in after posting a blind is never asked to act.
- **Richer `game_state`:** blinds, button/SB/BB seats, hand number, time limit, and tournament context (level, players left). New fields are added only, so existing bots keep working.
- **Runner protocol v2:**
  - `print()` output inside `decide()` is captured per decision as the bot's logs, instead of corrupting the protocol.
  - Bots may return an optional `debug` dict.
  - Think time is measured for every decision.
- **Host hardening:** host-side timeouts, recovery when a bot process stalls, stderr capture, and parallel warmup.
- **Seat interface:** bots, humans and MCP clients share one seat interface, with decision times configurable per seat type (e.g. bot 2 s, human 30 s, LLM 120 s).

### Formats

| Format | Description |
|---|---|
| Heads-up | Fixed hands or freezeout. Optional *duplicate* mode replays each deal with seats swapped to cut luck. Best-of-K. |
| Ring | 2–9 players, fixed hands, scored in bb/100 |
| Sit & Go | Single-table freezeout with rising blinds |
| MTT | Multi-table freezeout with table balancing and a final table |
| Bracket | Single-elimination heads-up, seeded or random, with byes |
| Round-robin | Every pair plays heads-up; head-to-head matrix |
| Swiss | Score-grouped tables, no rematches where possible |
| Series | Any format above, run N times with derived seeds |
| Stages | Seed one event from another's results, e.g. Swiss → top-8 bracket |

Blind levels advance by **hand count**, not wall-clock time, so runs are deterministic.

### Metrics

- **Per match:**
  - place and chip delta
  - bb/100 with confidence intervals
  - VPIP, PFR, 3-bet, aggression factor, WTSD, W$SD
  - timeouts, errors, think time
- **Per series:** win rate with Wilson confidence intervals, finish distribution, top-k rate, head-to-head matrix, ratings.
- **`poker bench <bot>`:** a fixed-seed duplicate gauntlet against the reference bots, tracked per bot version, so you can see whether a bot is actually improving.

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

1. **Engine + core:** engine patches, runner protocol v2, seats, tables, event logs, `poker match`, `poker verify`
2. **Formats + metrics:** all formats, series, stages, stats, `poker run/results/bench`
3. **API + web UI:** launcher, live/replay table, decision inspector, exports
4. **Remote seats:** human seats, MCP server, decision timers
5. **Tournament UX:** run dashboards, broadcast mode, bots page
6. **Lab + docs:** Bot Lab, GitHub Classroom import, Docker validation, docs, end-to-end tests

Upstream leftovers (`demo.py`, `db/schema.sql`, `CONTRIBUTING.md`) will be replaced or removed as these milestones land.

---

## Repo structure (current)

```
engine/         Game engine: NLHE rules, hand evaluation, chip tracking
sandbox/        Bot runner, Docker sandbox, validator, local match runner
bots/           Reference bots and starter template
tests/          Engine unit tests
demo.py         Quick local demo
```

---

## License

MIT. See [LICENSE](LICENSE). Based on [fullhouse-engine](https://github.com/uzlez/fullhouse-engine) by the Fullhouse Hackathon team.
