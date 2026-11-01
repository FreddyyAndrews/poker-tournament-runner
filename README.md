# Poker Tournament Runner

A toolkit for running No-Limit Texas Hold'em bot competitions.

- **Participants** write a bot against a one-function template, then test it locally, watch it play and dig into its decisions.
- **Organizers** load every submission and run heads-up matches and tournaments in many formats, with replayable, verifiable results and a spectator-friendly UI.

Forked from [fullhouse-engine](https://github.com/uzlez/fullhouse-engine) (MIT), which provides the poker engine, Docker sandbox, validator and reference bots.

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
brew install gitleaks   # used by the git hooks
make hooks          # install the git hooks: secrets, lint, whitespace

make test           # all tests
```

### Playing matches

`poker match` and `poker run` run on the poker server, so start one first in another terminal (`poker serve`, see [Web UI](#web-ui-and-live-viewing)). The CLI sends the match to it, follows along and prints the results; Ctrl+C cancels it on the server. To run in-process without a server (tests, CI, offline), add `--local`; those matches never show as live.

```bash
poker serve                                       # in another terminal
poker bots                                        # list bots in my_bots/ and bots/
poker match shark aggressor --hands 500           # heads-up, 500 hands at 50/100
poker match shark aggressor mathematician template --freezeout --blinds turbo -v
poker match my_bots/mybot shark --stack 20000 --blinds 100/200/25 --seed 7
poker match my_bots/mybot shark --cash --hands 1000   # every hand starts from the starting stack
poker verify data/runs/<match-id>                 # replay the log and check it
```

- **Server:** `--server URL` (or `$POKER_SERVER`) picks the server; the default is `http://127.0.0.1:8765`. Results go to that server's runs directory; `--out` only applies with `--local`.
- **Bots** are names from `my_bots/`, `bots/` or another git worktree's `my_bots/` (see [Building bots with an agent](#building-bots-with-an-agent)), or paths to a `bot.py`, a bot folder or a `bot.zip`. 2–9 per table. `mcp:Name` or `human:Name` adds a seat for an LLM or a person; the CLI prints its token.
- **Fixed mode** (default) plays `--hands` hands with stacks carrying over. **`--cash`** plays `--hands` hands with every hand starting from the starting stack (nobody busts; best for measuring bb/100). **`--freezeout`** plays until one bot has every chip, with blinds rising by hand count (`--max-hands` caps it).
- **`--blinds`** takes flat blinds `SB/BB[/ANTE]` or a structure: `turbo` (10 hands per level), `standard` (20) or `slow` (40). Structures scale with `--stack`.
- **Seats are fixed** for the whole match, and the button moves to the next occupied seat each hand.
- **Results** show each bot's place, final stack, chip delta, bb/100, hands played, think time, timeouts and errors. Add `--json` for machine-readable output.
- **Every match is logged** to `data/runs/<match-id>/`:
  - `events.jsonl` records every card, post, decision, award and elimination. Each decision includes what the bot saw, its reply, how it was applied, its think time, logs and `debug`. It is plain text while the match plays (so it can be followed live) and gzipped to `events.jsonl.gz` when it ends, about 20x smaller with nothing lost. Every `poker` command, the web UI and exports read either form; exports hold the plain log. `poker compress` gzips logs written before this (`--dry-run` to preview), and `POKER_COMPRESS_LOGS=0` keeps new logs plain.
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

- **In parallel:** `--workers N` plays up to N matches at once (a series: N runs). `--workers max` plays every match the tournament allows at once: a round robin all together, a bracket a whole round at a time (the next round waits for it), every table of a multi-table, and for a series one run per CPU. With more matches than CPUs, slow bots may hit their time limit. Seeds don't depend on it, so the results are the same at any setting.
- **Settings:** `bots`, `names`, `mode` (`fixed`/`cash`/`freezeout`), `hands`, `starting_stack`, `blinds`, `time_limit_ms`, `seed`, `table_size`, `duplicate`, `seeding`, plus `runs`, `workers` and `docker`. Command-line flags override the config file.
- **Results** live in `data/runs/<run-id>/`: `run.json` holds the standings and every match, and `matches/<match-id>/` holds each match's log.
- **Series** (`--runs N`) play the same format N times with seeds derived from one series seed, and report each bot's wins, win rate with a 95% confidence interval, top-3 rate, average place, bb/100 and finish distribution.
- **`poker verify`** on a run or series checks every match log, then recomputes the standings (and series totals) from the logs and compares them with what was saved.
- **`poker export --bot NAME`** keeps everything public plus that bot's own private data (its hole cards, what it saw, its logs and `debug`). Other bots' hole cards appear only when they were shown down, and their decision details are removed.

### Playing GTO Wizard and Slumbot

```bash
poker proxy                                            # once, and leave it running (see below)
poker match my_bots/mybot slumbot --hands 1000        # heads-up against Slumbot (free, no key)
poker match my_bots/mybot gto_wizard --hands 500      # heads-up against GTO Wizard AI
poker match human:Me gto_wizard --hands 20 --pace 600  # or play one yourself in the browser
```

Two strong outside bots can be played through their own APIs, as `slumbot` or `gto_wizard` in place of the opponent (also on the **New** page and over MCP):

| | [Slumbot](https://slumbot.com) | [GTO Wizard AI](https://gtowizard.com/benchmark) |
|---|---|---|
| Access | Free public API, no key | API key in `.env` as `GTO_WIZARD_API_KEY=...` |
| Switched on by | `ALLOW_SLUMBOT_BENCHMARKING` (default on) | `ALLOW_GTO_WIZARD_BENCHMARKING=true` (default off) |
| Limits | A free service run by one person: at most 3 hands at once per match (default 2) | About 100,000 hands a month per key; up to 20 at once (default 5) |
| Luck-adjusted score | Baseline: your result minus what Slumbot won with your cards against itself | AIVAT |

**The outside-bot proxy.** Every call to either bot goes through `poker proxy`, a small local service on `127.0.0.1:8766`. Start it once and leave it running next to `poker serve`; matches against an outside bot refuse to start without it, and `poker proxy --status` shows how it's doing. It exists so that many bots, and the agents building them, can share the outside bots safely:

- **It is the only process that reads `.env`.** The API key and Slumbot account stay in the proxy, so the processes that play matches never see them.
- **It switches each bot on or off** from `ALLOW_SLUMBOT_BENCHMARKING` and `ALLOW_GTO_WIZARD_BENCHMARKING` in `.env`. It re-reads them on every request, so a change applies at once. These two switches are read from `.env` only, never from the environment.
- **It queues requests** first come, first served, under one cap on requests in flight across every process. Over the cap, matches just run slower.
- **It caps the hands per day** across every bot, and keeps the count in `data/external/proxy_usage.json` so it survives a restart.
- **It forwards only the calls a match makes.** Its own refusals say why.

| Setting in `.env` | Default | What it does |
|---|---|---|
| `ALLOW_SLUMBOT_BENCHMARKING` | `true` | Slumbot on or off |
| `ALLOW_GTO_WIZARD_BENCHMARKING` | `false` | GTO Wizard on or off |
| `SLUMBOT_PROXY_MAX_IN_FLIGHT` | 3 | Slumbot requests in flight at once, across everyone |
| `SLUMBOT_PROXY_DAILY_HANDS` | 20,000 | Slumbot hands per day, across everyone |
| `GTO_WIZARD_PROXY_MAX_IN_FLIGHT` | 20 | GTO Wizard requests in flight at once |
| `GTO_WIZARD_PROXY_DAILY_HANDS` | 5,000 | GTO Wizard hands per day, across everyone |
| `GTO_WIZARD_DAILY_HANDS_PER_BOT` | 5,000 | GTO Wizard hands per day for one bot |
| `ALLOW_JEV` | `true` | Jev, the model the jev bots ask, on or off |
| `JEV_API_KEY` | | Jev's key; the proxy adds it, bots never see it |
| `JEV_MONTHLY_BUDGET_USD` | 10 | Jev spend per month, spread evenly over the days left |
| `JEV_PRICE_PER_MTOK` | 0.04 | Dollars per million Jev input tokens, TypeSafe's starting price |
| `JEV_OUTPUT_PRICE_PER_MTOK` | the input price | Dollars per million Jev output tokens; set it if output costs more |
| `JEV_BOT_DAILY_SHARE` | 0.5 | The most one bot may spend of a day's Jev allowance |
| `JEV_MAX_REQUEST_BYTES` | 16,000 | Larger Jev requests are refused, about 4k tokens |
| `POKER_PROXY_URL` (environment) | `http://127.0.0.1:8766` | Where matches find the proxy |

**Jev through the proxy.** The jev bots call Jev at `http://127.0.0.1:8766/jev/v1/systemone` with only an `X-Poker-Bot: <name>` header. The proxy adds the key, counts each reply's tokens per bot, and refuses with HTTP 402 once today's allowance or the bot's share is spent. Today's allowance is what's left of the month divided by the days left, so the budget can't be spent in a day. `poker proxy --status` shows today's and the month's spend and each bot's share.

- **The game is fixed:** heads-up, 200bb stacks at 50/100, reset every hand (like `--cash`). `--freezeout`, `--blinds`, `--stack` and `--seed` are refused.
- **They deal**, on their own server: you see your cards, the board and the action, and theirs when the hand ends. The match is logged like any other (live view, replay, analysis), but there is no seed, so it can't be replayed or duplicated. `poker verify` checks only the chip accounting.
- **Judge by the luck-adjusted score, not chips.** Each hand's score is in the log (`hand_end.score`, with their cards in `hand_end.opp_hole`). The CLI prints luck-adjusted bb/100 with a 95% CI, and the standings show it as a column.
- **Speed:** about 1 s per hand one at a time. `--workers N` plays N hands at once. It falls back to 1 with `--pace` or a human/LLM seat. With several at once, `match_action_log` holds the hands already finished.
- **Slumbot account (optional):** set `SLUMBOT_USERNAME` and `SLUMBOT_PASSWORD` in `.env` to play under your slumbot.com account; otherwise sessions are anonymous. The proxy logs in for you.
- An LLM can play them too: `start_match(opponents=["slumbot"])` over MCP.

### Is my bot improving?

```bash
poker bench my_bots/mybot                 # vs every reference bot, 500 hands per seat
poker bench my_bots/mybot --opponents shark aggressor --hands 2000 --workers 4
```

`poker bench` plays a duplicate cash match against each opponent with **fixed seeds**, so every version of your bot faces exactly the same cards. It reports bb/100 per opponent with a 95% confidence interval, plus the average, and appends the result to `data/bench/<bot>.jsonl` with the bot's code hash, showing how the score changed since the last version. If the confidence intervals overlap, the change may just be noise: add hands.

**The champion gauntlet.** Once a bot beats the reference bots by hundreds of bb/100, they stop telling versions apart. Champions are frozen copies of the strongest bots, kept in `champions/` and played with `--gauntlet`:

```bash
poker champions                                  # list them
poker bench my_bots/mybot --gauntlet --workers 4  # play every champion
poker champions add my_bots/mybot --note "beat the gauntlet by 120 bb/100"
poker champions remove mybot_1a2b3c
```

A champion is an exact copy of the bot's folder, named `<bot>_<first 6 hex of its hash>`, so it keeps the hash its bench history uses. `champions/index.json` records where each came from. A champion must pass the validator, so bots that need the network, like the jev bots, can't be champions, and a gauntlet bench never spends outside credits. An identical copy of the bot being benched is left out. Each bench history entry records its `suite` (`reference`, `gauntlet` or `custom`), and results only compare with the same opponents and hands.

**Against Slumbot and GTO Wizard:** add them as opponents, e.g. `poker bench my_bots/mybot --opponents shark slumbot gto_wizard`. They deal on their own servers, so there are no fixed seeds: each plays 2 x `--hands` hands, scored by their luck-adjusted bb/100 (with raw chips alongside). Slumbot is free, so use it while iterating. Save GTO Wizard for a final check, because its quota is shared: it is off until you set `ALLOW_GTO_WIZARD_BENCHMARKING=true`, and each bot may play at most 5,000 hands a day against it (`GTO_WIZARD_DAILY_HANDS_PER_BOT` changes this). Every hand played against either is logged in `data/external/usage.jsonl`.

### Building bots with an agent

The repo ships a Claude Code skill, [`.claude/skills/build-poker-bot`](.claude/skills/build-poker-bot/SKILL.md), that takes an agent from an empty folder to a pull request. Ask Claude Code to build a poker bot and it follows the skill:

1. **Own worktree.** `setup.sh <name>` creates a git worktree `../poker-bot-<name>` on branch `bot/<name>` from `origin/main`, then scaffolds `my_bots/<name>/` from the template. Each agent works only in its own bot folder.
2. **Shared server.** Run `poker serve` from the main checkout and leave it running. Every agent's matches run there, so you can watch them all live, and agents can play each other: the server finds bots in other worktrees' `my_bots/` by name. `poker bots` and the **New** page list those bots with their branch.
3. **Refine.** The agent tests against the reference bots and other agents' bots, finds leaks with `analyze_match.py` (the biggest losing hands, with the bot's own logs and `debug`), and keeps a change only if `poker bench` improves beyond the confidence interval.
4. **PR.** `pr_report.py` writes the metrics section: bench results per opponent with CIs, the version history, and tournament standings against the other agents. The agent opens a PR containing only `my_bots/<name>/`, with the strategy, the metrics, the commands to reproduce each number, and known weaknesses.

After the PR is merged, remove the worktree with `git worktree remove ../poker-bot-<name>`.

### Letting agents improve bots on their own

Two more skills run bot development as a loop that goes on while you do other things:

- [`.claude/skills/iterate-bot`](.claude/skills/iterate-bot/SKILL.md) is one experiment per run on one bot: read its lab notes, pick a hypothesis, change one thing, bench it against the champion gauntlet, keep it or revert it, record it and commit. Its playbooks cover four kinds of bot: a hand-written heuristic bot, a bot trained by self-play, and two bots that ask the Jev model for decisions.
- [`.claude/skills/run-loop`](.claude/skills/run-loop/SKILL.md) operates the loop: `start_track.sh` starts a bot track in its own worktree, a scheduled task per track runs its iterations, and a commissioner task checks services and budgets, paces the runs against your Claude usage, refreshes the "Poker Bot Loop" dashboard and alerts you only when something needs attention.

Each bot's notes live in `my_bots/<name>/notes/` (a journal, a plan of ideas, and a status file), which is left out of the bot's code hash, so writing notes never makes a new bot version.

### Web UI and live viewing

```bash
make web                                  # build the UI once (needs Node 18+)
poker serve --open                        # http://127.0.0.1:8765  (API docs at /docs)
poker match shark aggressor --pace 600    # runs on the server; open the watch link it prints
```

**New** starts anything from a form: pick a format (table, bracket, round robin, multi-table), add players (bots in other worktrees show their branch), choose how each match is played (freezeout, fixed hands or cash), the stack and blinds (presets with a level preview, or flat), and whether to watch it live (Live, Slow, Quick or Instant pace) or play it many times as a series. A summary shows exactly what will run and roughly how long a paced run takes. A single table opens straight into the live view; a tournament opens its run page, where **Now playing** links to the match being played.

**Run pages** show each tournament live as it plays, with **Verify** (replays every match log) and **Download** buttons:

- **Bracket:** the tree, round by round, with seeds, byes, chip counts and the champion; click a tie to watch it.
- **Round robin:** a head-to-head grid (result and chips won per pairing), plus standings so far.
- **Multi-table:** every table with its players, stacks, button and blinds, the chip leader and the elimination feed. **Watch** any table to follow it action by action.
- **Series:** win rate per bot with 95% confidence intervals, top-3 rate, average place, finishing places, and every run.

**Recent** lists every match, tournament and series. Open a match to watch it:

- **Table:** cards dealt, the acting bot outlined, showdowns with hand names and winners, and **chips**: each player's stack beside their seat, bets in front of them and the pot in the middle, drawn as columns of coloured chips (25 green, 100 black, 500 purple, 1k yellow, 5k orange, 25k blue, 100k pink, 1M white; a legend in the corner shows the values on the table right now, and hovering a pile gives its breakdown). Chips fly from a stack into a bet, from the bets into the pot, and from the pot to the winner, whose stack grows.
- **Realtime with history:** a running match shows **LIVE** and follows each action as it happens. Scrub, step or replay earlier hands at any time, then press **Go live** to jump back to now.
- **Controls:** play/pause, step, previous/next hand, a timeline with hand markers, speeds from 0.5x to 32x. Keys: Space, Left/Right, Shift+Left/Right, L.
- **Hand log** of the current hand, a **decision inspector** (what the bot saw, what it returned, how it was applied, think time, its logs and `debug`), a **stack chart** and live **standings**.
- **Cash games:** stacks reset every hand, so the UI also tracks each bot's running **net** (the sum of its hand results):
  - each seat shows its net next to its stack;
  - standings are ranked by net;
  - the chart tab becomes **Net**, plotting every bot's running total around zero.
- **Hole cards:** spectator view shows everyone's cards; "Hide hole cards" shows them only at showdown.

For UI development run `poker serve` and `cd web && npm run dev` (Vite on port 5173, proxying `/api`). `make web-test` runs the Playwright end-to-end tests, and `node web/scripts/screenshot.mjs <url> <out.png>` takes a screenshot of any page.

`poker serve` runs the API and the UI on this machine only, and it is the one place matches run: whether started from the UI, the API, an MCP client or the CLI (`poker match`, `poker run`), each runs as a background job on the server. Results land in its runs directory (`data/runs/` by default) and work with `verify`, `results` and `export`.

- **Live means running on the server right now.** Everything else shows how it ended: Complete, Stopped (cancelled), Error, or Didn't finish (no final result, e.g. the server stopped before it could save one). Matches run with `--local` play in the CLI's own process and are never shown as live.
- **Realtime:** a match started from the UI plays at a watchable pace (`pace_ms`, default 600 ms per action; deals and showdowns pause longer); from the CLI, add `--pace 600`. A bot's think time counts toward the pause, so pacing never cuts into a bot's time limit. Series always run at full speed.
- **Stopping the server** (Ctrl+C) cancels running jobs and waits for them to save their results, so nothing is left half-written; they show as Stopped. Ctrl+C on `poker match` / `poker run` cancels that one job.
- **Live stream:** `GET /api/matches/<key>/stream?from=N` streams a match's events (server-sent events) from event N as they're written. Join late and you catch up first; replays of finished matches use the same stream.
- **Jobs:** `POST /api/matches`, `POST /api/runs` (any format, `runs` > 1 for a series), `GET /api/jobs`, `POST /api/jobs/<id>/cancel`.
- **Results:** `GET /api/runs`, `/api/runs/<id>`, `/api/matches/<key>`, `/api/matches/<key>/events`, `/api/verify/<id>`, `/api/export/<id>?bot=NAME`.

### Playing against your bot: humans and LLMs

A seat can be played by **you** or by **an LLM over MCP** instead of a bot. Both use the same seat: when it's their turn the table waits (30 s for humans and 120 s for LLMs by default), and if time runs out they check or fold, logged as a timeout just like a bot.

- **In the browser:** on **New**, add **You** and some bots, then **Start and play**. The table stays live (no playback controls). On your turn a dock shows what you need (to call, pot, pot odds, your stack, a countdown) and three big actions: **Fold**, **Check / Call** and **Raise to** (sized with Min, 1/2 pot, 3/4 pot, Pot and All-in chips, a slider or a typed amount). Keys: **F** fold, **C** check/call, **R** type a raise, **Enter** raise, **A** all-in. After you act it confirms what you did. **Review hands** shows the playback controls to look back; **Back to the table** (or your next turn) returns to the live game. Opponents' cards stay face down until showdown. If you leave a game that is still running and come back (same browser), you get your seat back.
- **In the terminal:** `poker play shark aggressor --hands 20` (add `--freezeout`, `--blinds`, `--stack`, `--seed`). Type `f`, `x`, `c`, `r 600`, `a`.
- **An LLM over MCP:** `poker serve` also serves an MCP endpoint. Connect Claude Code (or any MCP client) with

  ```bash
  claude mcp add --transport http poker http://127.0.0.1:8765/mcp
  ```

  and ask it to play. Tools: `list_bots`, `start_match` (a seat for the LLM against chosen bots), `list_open_seats` (seats nobody has claimed yet, e.g. created on the New page with **An LLM**), `claim_seat` (take one and get its token), `wait_for_turn` (returns a plain-English summary, the legal actions and the full state), `act` (with optional `reasoning`, saved as the decision's logs) and `get_match`. Spectators see who the table is waiting on and how long is left.

  **Several LLMs can play each other:** add **An LLM** more than once, or start a match with more `mcp:` seats, e.g. `start_match(opponents=["mcp:gpt", "shark"])`. Each seat belongs to one agent. A seat is claimed with `claim_seat`, or by its token's first use, and claimed seats are no longer listed. Tokens are never listed, so two agents can't end up playing the same seat.

Human and LLM seats play single tables (not tournaments). Their decisions are logged like a bot's, so these matches replay and verify too. API: `POST /api/matches` with bots like `"human:Ann"` / `"mcp:claude"` returns a token per seat; `GET /api/seats/<token>` shows the pending decision and `POST /api/seats/<token>/act` answers it. `GET /api/seats?kind=mcp` lists unclaimed LLM seats, and `POST /api/seats/claim` with `{"match", "seat"}` claims one and returns its token (409 if it's taken).

### Docker sandbox

**Without Docker,** the default, each bot runs as a local Python process confined by `sandbox/guard.py`:

- **A short environment.** It gets only an allowlist (`PATH`, `HOME`, locale, `BOT_*`, `JEV_*`, `POKER_PROXY_URL`), and never a variable whose name looks like a secret, such as `*KEY*`, `*TOKEN*` or `*PASSWORD*`.
- **Its own folder only.** An audit hook, installed before the bot is imported, refuses opening or listing files outside the bot's folder and the Python installation, writing files, starting processes, and network connections to anything but this machine. The jev bots reach the proxy on `127.0.0.1`.
- **The validator** runs bots confined the same way. It also rejects `dotenv`, any path to a `.env` file, and reading credential-like environment variables.

This stops careless code, such as a bot loading the repo's `.env`, and casual attempts, but not a determined attacker. For untrusted code, use Docker.

To run bots in the Docker sandbox (`--docker` on `match`, `run` and `bench`; the competition setup), build the image first, and rebuild it whenever `sandbox/runner.py` changes:

```bash
./sandbox.sh build
```

Validate a bot before submitting it:

```bash
make validate BOT=my_bots/mybot/bot.py
```

---

## Writing your bot

Copy the template into `my_bots/` and edit `decide()`:

```bash
mkdir -p my_bots && cp -r bots/template my_bots/mybot
# edit my_bots/mybot/bot.py, then check it:
make validate BOT=my_bots/mybot/bot.py
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
| `match_action_log` | `list` | The last 200 entries across all hands in this match, including this hand so far: the same entries as `action_log` (posts included) plus `hand_num` |
| `hand_num` | `int` | Hand number within the match |
| `blinds` | `dict` | Current blinds, e.g. `{"sb": 50, "bb": 100, "ante": 0}` (these rise in tournaments) |
| `button_seat` / `sb_seat` / `bb_seat` | `int` | Seats of the button and blinds; compare with `seat_to_act` for your position |
| `time_limit_ms` | `int` | Your deadline for this decision |

> **Action amounts:** for `raise` and `all_in`, `amount` is your total bet on this street (as in your reply). For `call` it is the chips added, for posts the chips posted, and for `check` and `fold` it is 0. Antes are dead money and don't count toward anyone's bet on the street.

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
- No file writes. You may read from `data/`, but only at module import time. A bot's `data/` folder is part of the bot: commit it with `bot.py`. Files there may be up to 20 MB each.
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

## Repo structure

```
arena/          Match runner: seats, tables, blind structures, formats, series, bench, verify, export, `poker` CLI
arena/server/   HTTP API, background jobs, live event streams, MCP endpoint (`poker serve`)
configs/        Example run configs for every format
web/            React + TypeScript UI (built into web/dist, served by `poker serve`); Playwright tests in web/e2e
engine/         Game engine: NLHE rules, hand evaluation, chip tracking
sandbox/        Bot runner, Docker sandbox, validator, legacy match runner
bots/           Reference bots and starter template
my_bots/        Your bots (found by name, as are other worktrees' my_bots/)
tests/          Engine, runner and arena tests
.claude/skills/ Agent skills: build-poker-bot, verify-poker-runner
data/runs/      Match logs (gitignored)
data/bench/     Bench history per bot (gitignored)
demo.py         Upstream demo script
```

---

## Contributing

Bug reports, fixes and new reference bots are welcome. Open an issue or a pull request on [GitHub](https://github.com/FreddyyAndrews/poker-tournament-runner). Run `make test` before sending a PR, and `make web-test` if you touched the UI. See [CONTRIBUTING.md](CONTRIBUTING.md) for what can and can't change close to an event, and how to request a library for the sandbox.

---

## License

MIT. See [LICENSE](LICENSE).

Forked from [fullhouse-engine](https://github.com/uzlez/fullhouse-engine) by the Fullhouse Hackathon team, also MIT. Their copyright notice is kept in [LICENSE](LICENSE) and must stay in copies of this project.
