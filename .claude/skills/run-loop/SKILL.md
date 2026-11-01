---
name: run-loop
description: Operate the autonomous bot-building loop - start a bot track, schedule its iterations, run the commissioner (health, budgets, pacing, the Poker Bot Loop dashboard, alerts), and pause or stop tracks. Use when asked to start or launch a bot track, run or schedule the commissioner, refresh the loop dashboard, or check on the loop.
---

# Run the bot loop

The loop has four parts:

| Part | What it is |
|---|---|
| **Tracks** | One bot each, in its own worktree `../poker-bot-<name>` on branch `bot/<name>`, with lab notes in `my_bots/<name>/notes/` |
| **Iterations** | A scheduled task per track runs the `iterate-bot` skill: one experiment per run, in a fresh session |
| **Commissioner** | A scheduled task every few hours: checks services, budgets and tracks, sets the pace, refreshes the dashboard, alerts the user |
| **Dashboard** | "Poker Bot Loop", https://claude.ai/artifact/8AB4Am6B4oVwg5cJUZgZdH |

`MAIN` below is the main checkout (`/Users/frederickandrews/git/poker/poker-tournament-runner`), `PY=$MAIN/.venv/bin/python`.

## Start a track

```bash
$MAIN/.claude/skills/run-loop/scripts/start_track.sh <name> <track> [--from <bot>] [--strategy "..."]
```

`<track>` is `heuristic`, `rl`, `jev_prompt` or `jev_tools`. `--from` forks an existing bot (from `my_bots/`, `bots/` or `champions/`) instead of the template. The script creates the worktree and branch, renames the bot, starts its notes with a few first ideas for its track, and commits. Its first iteration records the baseline.

Then schedule its iterations. Scheduled tasks are standing automation: create one only when the user has said to, for that track. Stagger the tracks so their benches don't overlap:

| Track | Suggested schedule |
|---|---|
| heuristic | `7 */2 * * *` (every 2 hours) |
| rl | `22 */2 * * *` |
| jev_prompt | `37 */4 * * *` (every 4 hours: Jev costs money) |
| jev_tools | `52 */4 * * *` |

The task's prompt (fill in `<name>`):

> Run one iteration of the `iterate-bot` skill on the bot track `<name>`. The main checkout is /Users/frederickandrews/git/poker/poker-tournament-runner and the bot's worktree is /Users/frederickandrews/git/poker/poker-bot-<name>. Follow the skill exactly, for one run only. Never read the repo's .env file, never play GTO Wizard, and don't push or open a PR unless the skill's milestone step says so. End with a one-line summary.

## The commissioner

Schedule it every 3 hours (`45 */3 * * *`) with this prompt:

> Run the `run-loop` skill's commissioner steps for the poker bot loop at /Users/frederickandrews/git/poker/poker-tournament-runner. Never read the repo's .env file.

Its steps, each run:

1. **Usage.** Call the app's `get_usage` tool and save its full JSON result to `$MAIN/data/loop/usage.json` (with the Write tool). If the tool isn't available, skip this; pacing then stays off.
2. **Checks.** `cd $MAIN && $PY .claude/skills/run-loop/scripts/commission.py --usage data/loop/usage.json`. It prints a short report and `ALERT:` lines, writes `data/loop/pace.json` (which every iteration reads before it starts) and the dashboard's row files in `data/loop/dash/`.
3. **Dashboard.** For each of `status`, `tracks`, `budgets`, `alerts` and `champions`:
   - read `datasets/<id>` with ArtifactData (`get`) on the dashboard URL, for its current file `url` and `version`;
   - upload `data/loop/dash/<id>.json` as an asset of the dashboard (Artifact `publish` with `url`, `file_path`, `asset: true`; one file per call);
   - `update` `datasets/<id>` (with `if_version`) to the new `source.url`, `source.name` and `updated: {at: <now, ISO>, by: "poker loop commissioner"}`;
   - delete the old asset (Artifact `delete` with `url` and `path`: the old blob id).

   If those tools aren't available, say so in the summary and skip this step.
4. **Alerts.** If the report has `ALERT:` lines, send one push notification (PushNotification, under 200 characters) naming the most important one and how many there are. Don't notify when there are none.
5. **Finish** with a one-line summary: tracks, best score, pace, alerts.

The commissioner never changes a bot, a track's code or a setting. It may start the central server or the outside-bot proxy if one is down, as the build skill says, and never restarts a running one.

## Pacing

`commission.py` spreads the weekly Claude limit over the week and keeps `--reserve` (default 20%) for the user's own work. When usage is ahead of that line, or the 5-hour window is 90% used, it writes `"run": false` to `data/loop/pace.json`, and each iteration records a skip and stops. Iterations that hit a hard usage limit wait for it to reset (`autoContinueAtUsageLimit` in the user's settings).

## Pause, stop, promote

- **Pause one track:** `$PY $MAIN/.claude/skills/iterate-bot/scripts/notes.py phase $MAIN/../poker-bot-<name>/my_bots/<name> paused --reason "..."`, or disable its scheduled task. `iterating` resumes it.
- **Pause everything:** disable the scheduled tasks.
- **Remove a track** only when the user says so: disable and delete its task, then `git worktree remove ../poker-bot-<name>` and `git branch -D bot/<name>` (or keep the branch if its PR matters).
- **Champions** are the user's call. When a track's PR says its bot beat the gauntlet and the user agrees: `poker champions add <bot> --note "..."`, from the main checkout after the PR is merged.

## Budgets and knobs

| Knob | Where |
|---|---|
| Claude reserve for the user | `commission.py --reserve` (default 0.2) |
| How stale a track may get before an alert | `commission.py --stale-hours` (default 6) |
| Slumbot caps, Jev budget and price | the proxy's settings in `.env` (README, "The outside-bot proxy"); the user edits them |
| Iteration cadence | each track's scheduled task |
