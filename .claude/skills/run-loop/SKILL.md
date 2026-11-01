---
name: run-loop
description: Operate the autonomous bot-building loop - start a bot track and schedule its runs, read or refresh the loop's status page (STATUS.md on the loop-status branch), and pause, stop or promote tracks. Use when asked to start or launch a bot track, check on the loop, or refresh its status.
---

# Run the bot loop

Everything about the loop lives in the GitHub repo:

| What | Where |
|---|---|
| **Status page** | `STATUS.md` on the `loop-status` branch: https://github.com/FreddyyAndrews/poker-tournament-runner/blob/loop-status/STATUS.md |
| **Each bot** | its branch `bot/<name>`: the code in `my_bots/<name>/`, and its lab notes in `my_bots/<name>/notes/` (`journal.md`, `plan.md`, `status.json`) |
| **A bot that beat the gauntlet** | its pull request |

On this machine, each bot track is a worktree `../poker-bot-<name>`, and one scheduled task per track runs the `iterate-bot` skill every few hours. There is no separate supervisor: every run starts by checking the loop (services, budgets, every track, the Claude usage pace), updates the status page, and only then does its experiment, if the pace allows.

`MAIN` below is the main checkout (`/Users/frederickandrews/git/poker/poker-tournament-runner`), `PY=$MAIN/.venv/bin/python`.

## Start a track

```bash
$MAIN/.claude/skills/run-loop/scripts/start_track.sh <name> <track> [--from <bot>] [--strategy "..."]
```

`<track>` is `heuristic`, `rl`, `jev_prompt` or `jev_tools`. `--from` forks an existing bot (from `my_bots/`, `bots/` or `champions/`) instead of the template. The script creates the worktree and branch, renames the bot, starts its notes with a few first ideas for its track, and commits. Its first run records the baseline and pushes the branch.

Then schedule its runs. A scheduled task is standing automation: create one only when the user has said to, for that track. Stagger the tracks so their benches don't overlap:

| Track | Suggested schedule |
|---|---|
| heuristic | `7 */2 * * *` (every 2 hours) |
| rl | `22 */2 * * *` |
| jev_prompt | `37 */4 * * *` (every 4 hours: Jev costs money) |
| jev_tools | `52 */4 * * *` |

The task's prompt (fill in `<name>`):

> Run one iteration of the `iterate-bot` skill on the bot track `<name>` (read /Users/frederickandrews/git/poker/poker-tournament-runner/.claude/skills/iterate-bot/SKILL.md first and follow it exactly, for one run only). The main checkout (MAIN) is /Users/frederickandrews/git/poker/poker-tournament-runner and the bot's worktree is /Users/frederickandrews/git/poker/poker-bot-<name>. Never read the repo's .env file, never play GTO Wizard, and don't open a PR unless the skill's milestone step says so. End with a one-line summary.

## The status page

`scripts/loop_status.py` builds it. Each run calls it at the start (with fresh Claude usage, to decide whether to run) and at the end (to report the run):

```bash
$PY $MAIN/.claude/skills/run-loop/scripts/loop_status.py --usage $MAIN/data/loop/usage.json --publish --by <bot>
```

It checks the central server and the outside-bot proxy, every track's notes, Slumbot hands, Jev spend and disk use, and writes:

- `data/loop/pace.json`: whether runs may go ahead now (below);
- `STATUS.md`: alerts, standings, what each bot is doing with links to its notes, recent runs, budgets and champions. With `--publish` it is committed to the `loop-status` branch (which holds only that file, in the worktree `../poker-loop-status`) and pushed. A lock keeps two runs from publishing at once;
- `ALERT:` lines for anything that needs the user, also listed at the top of the page: a service down, a track blocked, stalled or erroring, a budget near its limit, the disk filling up.

To refresh the page by hand, run it without `--usage` (no pacing) or with a fresh `usage.json` from the app's `get_usage` tool.

## Pacing

The weekly Claude limit is spread over the week, keeping `--reserve` (default 20%) for the user's own work. When usage is ahead of that line, or the 5-hour window is 90% used, `pace.json` says `"run": false` and the run stops before its experiment, recording nothing; the status page says why. A run that hits a hard usage limit waits for it to reset (`autoContinueAtUsageLimit` in the user's settings).

## Pause, stop, promote

- **Pause one track:** `$PY $MAIN/.claude/skills/iterate-bot/scripts/notes.py phase $MAIN/../poker-bot-<name>/my_bots/<name> paused --reason "..."`, or disable its scheduled task. `iterating` resumes it.
- **Pause everything:** disable the scheduled tasks.
- **Remove a track** only when the user says so: disable and delete its task, then `git worktree remove ../poker-bot-<name>`, and delete the branch only if the user wants its history gone.
- **Champions** are the user's call. When a bot's PR says it beat the gauntlet and the user agrees: `poker champions add <bot> --note "..."`, from the main checkout after the PR is merged.

## Knobs

| Knob | Where |
|---|---|
| Claude reserve for the user | `loop_status.py --reserve` (default 0.2) |
| How stale a track may get before an alert | `loop_status.py --stale-hours` (default 6) |
| Slumbot caps, Jev budget and price | the proxy's settings in `.env` (README, "The outside-bot proxy"); the user edits them |
| How often a track runs | its scheduled task |
