# Playbook: Jev bot with a tool harness

Like the Jev prompt bot (read `jev_prompt.md` first: the same budget rules and eval ladder apply), but the bot's code does the work Jev is bad at and hands Jev the results. The bot improves by refining that harness.

## The harness

Jev has no native tool calling: one request is a state plus typed questions, each answered in parallel and in isolation against that state. So the "tools" run in the bot's own code:

- **Computed facts:** pot odds, equity against a range (eval7 sampling, within about 100 ms), position, stack-to-pot ratio, draws and made-hand class, and per-opponent stats tracked across the match in module globals keyed by `bot_id` (how often each one folds to a bet, raises, bluffs at showdown).
- **More questions in the same request:** besides the action Choice, a Score ("how strong is my hand against this line", 1-10) or a yes/no question ("is this opponent likely bluffing") can inform the decision at no extra latency. Each extra question costs output tokens: measure it.
- **A second request** only when the first answer says it's needed (for example, a sizing question only after Jev chose to raise). Each one costs about 150 ms and its tokens. Keep p95 decision time under 1 second.
- **Mixing:** sample the action from Jev's probabilities with the bot's seeded `random`, so matches stay reproducible.

## Budget

Target about 2,000 tokens a decision at most. Measure tokens per decision on the spot suite before any match, and re-estimate the run costs in `jev_prompt.md` with your real numbers.

## Experiments

Which facts to compute and how to phrase them, which extra questions pay off, when a second request is worth it, and how the code combines Jev's answers with the computed facts (for example, refusing a call Jev likes when the pot odds are far off).
