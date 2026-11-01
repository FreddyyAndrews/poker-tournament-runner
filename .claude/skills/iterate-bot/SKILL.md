---
name: iterate-bot
description: Run ONE improvement iteration on an autonomous bot track (heuristic, RL, Jev prompt or Jev tools) in its own worktree - pick one hypothesis from the bot's notes, change one thing, bench it, keep or revert it, record it and commit. Use when a scheduled task or the user says to iterate on, or continue, a bot track such as `iterate on heur_bot`.
---

# Iterate on a bot track: one experiment per run

Each run of this skill is one short, self-contained experiment on one bot. You start with no memory of earlier runs: everything you need is in the bot's notes, and everything you learn goes back into them. Keep the run small: one hypothesis, one change, one verdict, then stop. Aim to finish within about 40 minutes.

Read `.claude/skills/build-poker-bot/SKILL.md` once for the bot contract, the tools (`$POKER`, `$PY`, `$S`), the ground rules and the outside-bot rules. Everything there applies here. On top of it:

- Work only in the bot's worktree, on its branch, in `my_bots/<name>/`.
- The bot's lab notes live in `my_bots/<name>/notes/` and are left out of its code hash, so writing them doesn't create a bot version. Use `notes.py` for them (below), never hand-edit `status.json`.
- Keep your context small. Read summaries, not logs: `notes.py show`, `--json` output, `analyze_match.py`. Never print a whole event log or bench file.
- Never edit `champions/`, the tools, the engine, or other bots. Never read `.env`. Never use `--no-verify`. Never play GTO Wizard.

## 0. Preflight

```bash
NAME=<the bot>                                   # from the task
MAIN=<the main checkout, from the task>          # e.g. /Users/.../poker-tournament-runner
cd "$MAIN/../poker-bot-$NAME"
export POKER=$MAIN/.venv/bin/poker PY=$MAIN/.venv/bin/python POKER_SERVER=http://127.0.0.1:8765
S=$MAIN/.claude/skills/build-poker-bot/scripts
N() { "$PY" "$MAIN/.claude/skills/iterate-bot/scripts/notes.py" "$@"; }   # a function: zsh doesn't split variables
B=my_bots/$NAME
OUT=$MAIN/data/loop/runs; mkdir -p "$OUT"        # bench output (git ignores data/)
```

1. **Pace.** If `$MAIN/data/loop/pace.json` exists and says `"run": false`, record a skip and stop:
   `N record $B --result skipped --experiment "paced" --summary "<its reason>"`, then commit the notes (step 6) and end.
2. **Clean tree.** `git status --porcelain` must be empty. If a previous run left changes, it stopped mid-experiment: note what was left (`git status --short`), put the bot folder back to its last commit with `git restore -- $B && git clean -fdq -- $B`, record `--result error --experiment "recovered" --summary "<what was left>"`, and continue.
3. **Services.** `curl -sf $POKER_SERVER/api/health` and `$POKER proxy --status`. Start either one if it is down, as the build skill says. Never restart a running one.
4. **Sync.** `git fetch -q origin && git merge --no-edit origin/main`. On a conflict: `git merge --abort`, `N phase $B blocked --reason "merge conflict with main"`, commit the notes, stop.
5. **Phase.** If the notes say `paused`, stop without recording. If `training`, follow the RL playbook's "while training" step instead of starting a new experiment.

## 1. Orient

```bash
N show $B
```

Then read the playbook for the bot's track, which sets what an experiment looks like, which benches to run, and what they may cost:

| Track | Playbook |
|---|---|
| `heuristic` | `playbooks/heuristic.md` |
| `rl` | `playbooks/rl.md` |
| `jev_prompt` | `playbooks/jev_prompt.md` |
| `jev_tools` | `playbooks/jev_tools.md` |

If the notes have no `best` yet, this run establishes the baseline: run the playbook's every-run bench on the current code and record it with `--result kept --experiment "baseline"`. That is the whole run.

If `best` was measured at a different hand count than the playbook's every-run bench (or `show` says the count wasn't recorded), re-measure it now, before changing anything: bench the current code at the playbook's count, record it with `--result kept --experiment "rebaseline"`, and commit the notes on their own (`git add $B/notes && git commit -q -m "$NAME: rebaseline"`). Then carry on with this run's experiment, compared against that.

## 2. Pick one hypothesis

Take the first plan item (`N pop $B`). If it is a diagnosis rather than a change ("find where it loses"), do the diagnosis and turn what you find into a testable hypothesis. Or, if the plan is empty or the last runs suggest something better, write your own from evidence: `$PY $S/analyze_match.py <match folder> $NAME` on the gauntlet match the bot lost most. A bench's JSON gives its `runs_dir` and, per opponent, its `matches`: the folder is `<runs_dir>/<match>`. Write the hypothesis down before you test it, as one sentence with a prediction ("folding river bluff-catchers to 2/3-pot bets gains against fable, costs a little against sonnet").

## 3. Change one thing

Make the smallest change that tests the hypothesis. Validate it: `$PY $MAIN/sandbox/validator.py $B/bot.py` must PASS, except for the Jev tracks, which need the network and fail only on their HTTP import. Check the time budget with one short match (`--hands 50`, zero timeouts).

## 4. Measure

Run the benches the playbook says, with the bot's code as it is now:

```bash
$POKER bench $B --gauntlet --hands 2000 --workers 3 --json > $OUT/$NAME-gauntlet.json
```

The hand count is the playbook's (the Jev tracks use far fewer); it is the JSON's `hands_per_leg`, which `--hands` in `notes.py record` means too (each champion row's `hands` is twice that, both seats). Run benches in the foreground: a gauntlet takes seconds to a minute, a Slumbot bench up to half an hour. Use `--workers 3`: other tracks bench on the same machine. Read only the overall score, its CI and each champion's row, for example with `$PY -c` and `json`.

## 5. Keep or revert

Compare against `best` in the notes, on the same benchmark:

Compare only results with the same hand count. The gauntlet's deals are fixed and its CIs can be very tight (±2 bb/100), so a tiny gain can just be a fit to those deals:

- **Keep** when the overall gauntlet score beats `best` by at least 3 bb/100 and by at least half of the new CI, and no champion row got worse by more than 3 bb/100 or that row's CI, whichever is larger.
- **Close call** (better, but by less than that): re-run the gauntlet once at twice the hands and decide on that.
- **Otherwise revert** the bot's code and data, but not its notes (the plan already lost the item you took):
  `git restore -- "$B" ":(exclude)$B/notes" && git clean -fdq -- "$B" ":(exclude)$B/notes"`

## 6. Record and commit

```bash
N record $B --result kept|reverted --experiment "<the hypothesis>" --summary "<what happened, 2-4 sentences>" \
   --hash <the code hash bench printed> --gauntlet <bb> <ci> --hands <hands a seat> [--strategy "<updated paragraph, when kept>"] \
   --next "<the most promising next idea>"
N push $B "<any new idea worth trying>"          # at most two
git add $B && git commit -q -m "$NAME: <experiment> (<kept|reverted>)"
```

The git hooks run on the commit. If one fixes a file, `git add` it and commit again. A reverted experiment still commits its notes, so the next run knows it was tried.

## 7. Checkpoints and milestones

- **Outside check.** The playbook says how often to run the Slumbot bench on the best version (never for the Jev tracks unless their budget allows). Record it with `N record $B --result kept --experiment "slumbot checkpoint" --slumbot <bb> <ci> --hash <best hash> --summary "..."`.
- **Beating the gauntlet.** When the best version beats every champion (each champion row positive with its CI clear of zero), push the branch and open or update the bot's PR as the build skill describes (`pr_report.py`, the PR body). One PR per bot, updated as it improves. Champions are the user's call: say in the PR that this version could become one, and don't add it yourself.
- **Stuck.** After five reverted runs in a row, step back: re-read the journal, write three new kinds of hypothesis into the plan, and record that you did.

## 8. Finish

End with a one-line summary: kept or reverted, the score against `best`, and what the next run should try. Don't start a second experiment in the same run.
