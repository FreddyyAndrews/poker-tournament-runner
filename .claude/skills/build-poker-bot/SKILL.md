---
name: build-poker-bot
description: Build, test and refine a new No-Limit Hold'em bot for the Poker Tournament Runner in its own git worktree, play it against the reference bots and other agents' bots on the shared central server, and finish with a GitHub PR containing the bot's strategy and metrics. Use when asked to create, write, improve or enter a poker bot.
---

# Build a poker bot

You write one bot, `my_bots/<name>/bot.py`, in **your own git worktree** on branch `bot/<name>`. You test it on the **central server** run from the main worktree, where other agents' bots are playable too. When it's good you open a **PR** with the strategy and the numbers that back it.

Ground rules:
- Only change files in `my_bots/<name>/`. The engine, the tools and other bots are off limits; if a tool is broken, tell the user and don't patch it in your branch.
- Never edit another agent's worktree. Their bots are opponents: play them by name, and don't read their code.
- Keep every claim in the PR reproducible: give the seed, the command and the code hash for each.

## 1. Set up the worktree

From the main worktree:

```bash
.claude/skills/build-poker-bot/scripts/setup.sh <name>     # name: ^[a-z][a-z0-9_]{2,30}$, unique
```

What it does:
- Fetches `origin`, then creates `../poker-bot-<name>` on a new branch `bot/<name>` from `origin/main`.
- Scaffolds `my_bots/<name>/bot.py` from `bots/template/bot.py` (with `BOT_NAME` set) and a `README.md` strategy stub.
- Runs the validator.
- Refuses a name that already exists as a bot or a `bot/<name>` branch.

Then, in every shell you use, run the lines it prints:

```bash
cd ../poker-bot-<name>
export POKER=<main>/.venv/bin/poker PY=<main>/.venv/bin/python POKER_SERVER=http://127.0.0.1:8765
S=<main>/.claude/skills/build-poker-bot/scripts
```

Worktrees have no `.venv` of their own, so you use the main worktree's (and the helper scripts from the main worktree, which may be newer than `origin/main`). That means `$POKER` runs the main checkout's tools and writes bench history to the main checkout's `data/` (shared on purpose), while your bot code comes from your worktree by path (`my_bots/<name>`).

**Central server.** `setup.sh` reports whether it's up (`curl -sf $POKER_SERVER/api/health`). If it isn't running, start it from the main worktree, leave it running, and tell the user you started it:

```bash
(cd <main> && nohup .venv/bin/poker serve --port 8765 > data/server.log 2>&1 &)
```

Never stop or restart it: other agents and the user are using it. `poker match` and `poker run` execute on it, so your matches show live at `http://127.0.0.1:8765` (each command prints a `#/match/<key>` or `#/run/<id>` link) and land in `<main>/data/runs/`.

## 2. The bot contract

Read `bots/template/bot.py` (the full `game_state` schema) and look at `bots/shark`, `bots/mathematician` and `bots/aggressor` for working examples.

- `decide(game_state) -> {"action": "fold"|"check"|"call"|"raise"|"all_in", "amount": raise_to}`. `amount` is the total bet this street to raise **to**, at least `min_raise_to`. Invalid actions fold.
- Optional `"debug": {...}` (a small JSON dict such as equity or reasoning) is stored with each decision and shown in the UI's Decision tab. `print()` output is captured as that decision's logs. Use both: they're how you learn from matches.
- `game_state` includes the blinds, positions, the hand number, every player's stack, bet and state, and `action_log` (this hand's actions by bot name), which you can use for opponent modelling.
- The bot is loaded once per match and keeps module state across hands, so you can track opponents in globals keyed by `bot_id`.
- Limits:
  - **2 s per decision** (aim for < 200 ms p95). A timeout or crash folds.
  - stdlib plus `eval7`, `numpy`, `scipy`, `treys` and `scikit-learn`.
  - No network, no file writes and no subprocesses.
  - Optional read-only `data/` next to `bot.py`, loaded at import time only.
- Validate after every edit: `$PY sandbox/validator.py my_bots/<name>/bot.py` (must print PASS).

## 3. Test

All commands run in your worktree. Use fixed `--seed`s so comparisons are fair.

```bash
# quick table vs the reference bots (watch it: open the printed link)
$POKER match my_bots/<name> shark mathematician aggressor --cash --hands 300 --seed 11 --id <name>_t1
# heads-up vs one bot
$POKER match my_bots/<name> shark --cash --hands 500 --seed 12 --id <name>_hu_shark
# the score to improve: duplicate heads-up vs every reference bot, 95% CIs, history kept per code hash
$POKER bench my_bots/<name> --workers 4
# what went wrong in a match: actions by street, think time, errors, biggest losing hands with your logs/debug
$PY $S/analyze_match.py <name>_t1 <name>
```

- `--id` must be unique on the server, so add a suffix per run (`_t2`, `_v3_s11`, ...).
- `poker match ... --json` prints the result as JSON. Add `--pace 300` to slow a match down to watch it live.

**Play other agents.** Their worktree bots are listed and playable by name:

```bash
$S/opponents.sh                       # reference bots, other agents' bots (worktree:<branch>), yours
$POKER match my_bots/<name> $($S/opponents.sh --names) --cash --hands 300 --seed 21 --id <name>_vs_agents_1
$POKER run --format round_robin --bots my_bots/<name> $($S/opponents.sh --names) shark mathematician \
     --mode cash --hands 300 --seed 31 --id <name>_rr_1
$POKER run --format bracket --bots my_bots/<name> $($S/opponents.sh --names) shark aggressor mathematician \
     --blinds turbo --runs 30 --workers 4 --seed 41 --id <name>_br_series_1
```

Other agents' bots change while you work (each result records every bot's code hash), so treat those results as a scrimmage, not a fixed benchmark. The bench (reference bots, fixed seeds) is the stable score.

**Play it yourself (optional).** On `http://127.0.0.1:8765/#/new`, add You and your bot, then press Start and play. Or ask the user to try it.

## 4. Refine loop

Repeat until improvements stop or the user's time budget runs out:

1. **Find a leak.** Use `analyze_match.py` on your latest matches. Look at the biggest losing hands (your `debug`/logs show why you made each decision), actions by street (folding too much? calling down too light?), think time, and errors.
2. **Change one thing** in `bot.py`, with a hypothesis ("stop calling river bets with one pair vs aggressor").
3. **Validate**, then re-run `$POKER bench my_bots/<name> --workers 4`. It uses the same seeds every time, so differences come from your code.
4. **Keep the change** only if `overall_bb_per_100` improved, and check each opponent's row too (an improvement inside the CI is noise; don't trade a big loss against one bot for a small gain against another). Otherwise revert it. Bigger `--hands` shrinks the CI when results are close.
5. **Record it** in `my_bots/<name>/README.md` (Development log: version, change, bench) and commit in your worktree: `git add my_bots/<name> && git commit -m "<name>: <change>"`.

Before finishing, check for failure modes the bench doesn't show:
- A freezeout run with rising blinds (`--blinds turbo`).
- A 6-handed table.
- A deep stack (`--stack 50000`).
- Zero timeouts and errors everywhere.

## 5. Open the PR

1. **README.** Finish `my_bots/<name>/README.md`: a strategy section covering preflop, postflop, sizing, opponent modelling and the time budget; the development log; and known weaknesses.
2. **Final numbers.** Run the final bench, plus one round robin against the other agents and the reference bots, on fixed seeds.
3. **Metrics.** Generate the metrics section:
   ```bash
   $PY $S/pr_report.py <name> --run <name>_rr_final [--run <name>_br_series_1] [--match <name>_t9] > /tmp/<name>_metrics.md
   ```
4. **Body.** Write the PR body to a file, using the structure below.
5. **Push and open the PR.** When the user hasn't asked to wait, push and open it:
   ```bash
   git push -u origin bot/<name>
   gh pr create --base main --head bot/<name> --title "Bot: <name>" --body-file /tmp/<name>_pr.md
   ```
6. **Report.** Give the user the PR URL.

PR body structure:

```markdown
## <name>: <one-line pitch>

### Strategy
<a paragraph on the idea, then bullets: preflop, postflop, bet sizing, opponent modelling, time budget>

<paste the pr_report.py output: bench per opponent with CIs, version history, round robin / bracket vs other agents, match health>

### What changed along the way
<the dev log: each change, why, and its bench effect, including reverted ideas>

### Reproduce
<the exact $POKER commands and seeds behind every number above>

### Limitations
<known leaks, opponents it loses to, untested situations>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

The PR diff must contain only `my_bots/<name>/`. Check with `git diff --stat origin/main...HEAD` before pushing.

## 6. Clean up (after the PR is merged or closed)

```bash
cd <main> && git worktree remove ../poker-bot-<name> && git branch -d bot/<name>
```

`git worktree remove` refuses while your worktree has uncommitted files: commit or delete them yourself (never `--force` someone else's worktree). Use `git branch -D` only if the PR was closed unmerged. Leave the server running and the match logs in `data/runs/` alone.

## Helpers

| Script | Use |
|---|---|
| `setup.sh <name>` | worktree + branch + scaffold + validator; prints the env to export |
| `opponents.sh [--names]` | reference bots and other agents' bots, all playable by name |
| `$PY analyze_match.py <match_key or dir> <bot>` | leaks from one match: actions by street, think time, errors, biggest losing hands with logs/debug |
| `$PY pr_report.py <bot> [--run ID ...] [--match KEY ...]` | Markdown metrics for the PR: bench per opponent and version history, run standings, match health |
