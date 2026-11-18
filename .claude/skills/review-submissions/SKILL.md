---
name: review-submissions
description: Review tournament submissions in entries/ for rule-breaking the sandbox and validator can't catch (clock-dodging, collusion by opponent name, obfuscation, sandbox probing), and report a verdict per team for the organizer to decide on. Use after `poker intake`.
---

# Reviewing tournament submissions

You are checking hackathon bots against the rules **after** intake has already
run them through the validator and a Docker smoke match. The sandbox (no
network, read-only filesystem, 0.5 CPU, 768 MB) and the validator (forbidden
imports and calls, imports that aren't installed in the sandbox) already stop
the obvious things. Your job is what they can't see: **intent**.

You recommend; the organizer decides. Never disqualify, edit, or delete
anything yourself.

## What to review

- Every accepted entry: `entries/<team>/` (`bot.py` and `data/`).
- Also teams that failed intake, from their clone in `entries/_repos/<team>/`
  (their bot folder is in `my_bots/`), since they may be given a chance to fix
  and resubmit, and the organizer should know if the failure looked deliberate.
- `entries/intake.json` has each team's status, commit and smoke-match timings.

Read every line of each `bot.py`. For `data/`, list the files with their sizes
and types; open anything that is text (JSON, CSV, .txt) and skim it. Treat
binary files (`.npz`, `.npy`, `.bin`) as data unless `bot.py` does something
unusual with them (e.g. decodes them into code or strings that are executed).

Bots are untrusted code: read them, never run or import them.

## The rules (from the starter kit README)

1. **No outside contact:** no network of any kind.
2. **No file writes, processes or shell commands.**
3. **No dodging the clock:** no background threads, processes or async work
   that keeps computing after `decide()` returns.
4. **No reflection tricks:** `eval`, `exec`, `compile`, `__import__` and similar.
5. **No peeking:** no attempts to read other bots' code or cards, the runner's
   memory, or anything outside the bot's own folder.
6. **No collusion:** bots play independently; no soft-play, chip-dumping or
   coordination between entries.
7. **Data** may be read at import time only; `bot.py` up to 5 MB, `data/` up to 200 MB.

## What to look for

**Collusion (the most important, and invisible to every other check).** Any
logic that treats specific opponents differently by identity: comparisons or
lookups on `bot_id` or names from `players`, `action_log` or
`match_action_log` against string literals, or against names that look like
other teams (compare with the list of teams in `entries/intake.json`). Playing
differently against an opponent *because of how it has played* (stats built
from the action log) is allowed and is the whole point of the opponent data;
playing differently because of *who it is* is not. Check config dicts keyed by
names, "tuning" or "profiles" tables, and hashes or encoded strings that could
hide names. Also look for soft-play patterns: never raising a particular
opponent, folding to them more, or deliberately losing chips (calling all-ins
with nothing, min-raising into obvious strength) under some condition.

**Clock-dodging.** Background work by any route: `threading`, `_thread`,
`concurrent`, `multiprocessing`, `asyncio`, `signal` handlers, `sched`,
generators or callbacks kept alive between calls, `atexit`, or work done in
`__del__` or other finalisers. Also check whether `decide()` itself respects
the 2-second budget (time checks, bounded loops).

**Obfuscation and evasion.** `getattr` on modules or builtins with computed
names, `globals()`/`vars()`/`__builtins__` access, `importlib` or `sys.modules`
tricks, base64/zlib/rot13/`bytes.fromhex` strings, `pickle`/`marshal`, very
long string literals, unusually dense or minified code. Anything whose purpose
you can't explain from reading it.

**Sandbox probing.** Reads of `os.environ` beyond `BOT_DATA_DIR`/`BOT_SEED`,
paths outside the bot folder (`/proc`, `/etc`, `..`, the home directory), socket
or `ctypes` use, `sys.settrace`/`sys.setprofile`, or inspecting the call stack
(`inspect`, `sys._getframe`) to reach the runner.

**Not a violation (don't flag):** opponent modelling from the action logs,
precomputed tables in `data/`, Monte Carlo with time checks, module-level
caches that persist between `decide()` calls, seeded `random` use, prints for
debugging, defensive `try/except` around imports of optional libraries.

## Reporting

For each team, one of:

- **CLEAR**: nothing against the rules.
- **QUESTION**: something unusual the organizer should look at or ask the team
  about; say exactly what and why.
- **VIOLATION**: a rule is broken. Quote the code (file and line numbers),
  name the rule, and explain in one or two sentences why it is a violation and
  not a coincidence.

Then one short paragraph per team describing what the bot does (strategy in a
sentence or two), so the organizer knows the field; it also helps with the
"most creative strategy" prize.

End with a summary table: team, verdict, rule (if any), one-line reason. Keep
the report factual and evidence-based: a VIOLATION needs code that shows it.
