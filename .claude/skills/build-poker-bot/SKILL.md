---
name: build-poker-bot
description: Set up a No-Limit Hold'em bot for the Poker Tournament Runner in its own git worktree, and find the tools to build, test and measure it (matches, benches, the champion gauntlet, Slumbot, match analysis) and the rules for using them. Use when asked to create, write, improve or enter a poker bot.
---

# Build a poker bot

You build one bot, `my_bots/<name>/`, in your own git worktree on branch `bot/<name>`. How you build it, what you try and when you stop are up to you and the user: this skill sets up the worktree, describes the bot contract, points you to the tools, and lists the rules that keep you from getting in the way of the user or other agents.

## Set up

From the main checkout:

```bash
.claude/skills/build-poker-bot/scripts/setup.sh <name>     # name: ^[a-z][a-z0-9_]{2,30}$, unused
```

It fetches `origin`, creates `../poker-bot-<name>` on a new branch `bot/<name>` from `origin/main`, scaffolds `my_bots/<name>/bot.py` from the template with a README stub, runs the validator, and reports whether the central server and the outside-bot proxy are up. Then use what it prints in every shell:

```bash
cd ../poker-bot-<name>
export POKER=<main>/.venv/bin/poker PY=<main>/.venv/bin/python POKER_SERVER=http://127.0.0.1:8765
S=<main>/.claude/skills/build-poker-bot/scripts
```

Worktrees have no `.venv`, so `$POKER` runs the main checkout's tools (and writes match logs and bench history to its `data/`), while your bot's code comes from your worktree by path.

If the **central server** (`poker serve`, port 8765) or the **outside-bot proxy** (`poker proxy`, port 8766) isn't running, start it from the main checkout, leave it running, and tell the user:

```bash
(cd <main> && nohup .venv/bin/poker serve --port 8765 > data/server.log 2>&1 &)
(cd <main> && nohup .venv/bin/poker proxy > data/proxy.log 2>&1 &)
```

## The bot contract

Read `bots/template/bot.py` for the full `game_state` schema, and `bots/shark`, `bots/mathematician` and `bots/aggressor` for working examples.

- `decide(game_state) -> {"action": "fold"|"check"|"call"|"raise"|"all_in", "amount": raise_to}`. `amount` is the total to raise to this street, at least `min_raise_to`. Invalid actions fold.
- Optional `"debug": {...}` in the reply (a small JSON dict) and anything you `print()` are saved with each decision and shown in the UI's Decision tab and by `analyze_match.py`.
- The bot is loaded once per match and keeps module state across hands, so it can track opponents in globals keyed by `bot_id`.
- **2 seconds per decision** (a timeout or crash folds). Libraries: the standard library, `eval7`, `numpy`, `scipy`, `treys` and `scikit-learn`.
- No network, no file writes, no subprocesses. Locally `sandbox/guard.py` enforces this too: a bot can open only files in its own folder, gets no secrets from the environment, and can connect only to this machine.
- An optional read-only `data/` next to `bot.py` (tables, trained weights, up to 20 MB a file) loads at import time. It is part of the bot: commit it. Train anything offline, with scripts in your own folder.
- **A bot that asks Jev** (TypeSafe's decision model) calls the proxy at `http://127.0.0.1:8766/jev/v1/systemone` with an `X-Poker-Bot: <name>` header and no key; see `my_bots/jev_bot`. Jev costs money: the proxy enforces a daily budget and a share per bot, and refuses (HTTP 402) once it's spent. Check `$POKER proxy --status` before a long run, keep requests compact, and throw away results from matches where it fell back to check/fold.
- Validate after edits: `$PY sandbox/validator.py my_bots/<name>/bot.py` must PASS (a bot that needs the network fails only on its HTTP import).

## Tools

All run from your worktree.

| Tool | What it's for |
|---|---|
| `$POKER match my_bots/<name> shark aggressor --cash --hands 300 --seed 11 --id <name>_t1` | One table, played on the central server, so it shows live at `http://127.0.0.1:8765` and lands in the main checkout's `data/runs/`. `--json` for the result, `--pace 300` to watch it. `--id` must be unique. |
| `$POKER run --format round_robin\|bracket\|... --bots ...` | Tournaments and series, including against other agents' bots |
| `$POKER bench my_bots/<name> --workers 4` | Duplicate heads-up against every reference bot on fixed seeds, with 95% CIs and a history per code hash. The same deals every time, so differences come from your code. |
| `$POKER bench my_bots/<name> --gauntlet --workers 4` | The same against every champion (`$POKER champions`): frozen copies of the strongest bots, for once the reference bots are beaten easily |
| `$POKER bench my_bots/<name> --opponents slumbot --hands 1000` | Slumbot, a strong outside bot, through the proxy: no seeds, scored by its luck-adjusted bb/100. Its CI is wide (about ±140 at 200 hands), so compare versions over 2,000+ hands. |
| `$PY $S/analyze_match.py <match id or folder> <name>` | What went wrong in a match: actions by street, think time, errors, the biggest losing hands with your logs and `debug` |
| `$S/opponents.sh [--names]` | Every bot you can play by name, including other agents' worktree bots |
| `$POKER proxy --status` | Slumbot hands and Jev spend today, and whether each is on |
| `$PY $S/pr_report.py <name> [--run ID ...] [--match KEY ...]` | A Markdown metrics section for a PR, from your bench history and runs |
| `http://127.0.0.1:8765` | The web UI: watch matches, inspect decisions, play your bot yourself |

Other agents' bots change while you work (every result records each bot's code hash), so treat matches against them as scrimmages; the benches are the stable scores. Multi-way tables, rising blinds (`--blinds turbo`) and deep stacks (`--stack 50000`) catch failures a heads-up bench can't.

## Rules

- **Your folder only.** Change only `my_bots/<name>/` in your worktree. The engine, the tools, `bots/`, `champions/` and other bots are off limits; if a tool is broken, tell the user. Don't edit another agent's worktree or read its code; play its bot by name.
- **Never read, print or copy `.env`.** Never call slumbot.com, GTO Wizard or Jev yourself: only through the proxy.
- **Never play GTO Wizard:** its quota is the user's.
- **Slumbot is a free service run by one person.** Run one Slumbot bench at a time, not on every edit. If the proxy refuses (switched off, or the daily cap across everyone reached), stop playing it; don't retry in a loop or work around it.
- **Never stop or restart** the central server or the proxy: the user and other agents use them.
- **Commits run git hooks** (gitleaks, basic lint, whitespace). If one fixes a file, `git add` it and commit again. Never use `--no-verify`.
- **Champions are the user's call.** Don't add or remove any.

## A pull request, when the user wants one

Write up the bot in `my_bots/<name>/README.md` (the strategy, what you tried and what it did, its known weaknesses), make every number in it reproducible (the command, seed and code hash), and use `pr_report.py` for the metrics. The diff must contain only `my_bots/<name>/` (`git diff --stat origin/main...HEAD`). Then:

```bash
git push -u origin bot/<name>
gh pr create --base main --head bot/<name> --title "Bot: <name>" --body-file <your PR body>
```

After it's merged or closed: `cd <main> && git worktree remove ../poker-bot-<name>`, and delete the branch with `git branch -d bot/<name>` (`-D` only if it was closed unmerged).
