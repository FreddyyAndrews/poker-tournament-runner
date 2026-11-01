# fable_bot

An equity-driven, exploitative No-Limit Hold'em bot. Every decision is an
expected-value comparison between fold / check / call and a menu of bet or
raise sizes, where the EV of each size comes from a per-opponent model learned
from the match action log: how often that opponent folds to a bet of a given
price, how often they re-raise, and how tight their range is behind a raise of a
given size. Opponents that fold to big bets get bluffed with the cheapest size
that works; opponents that never fold get value-bet thin and never bluffed.

## Strategy

- **Preflop:** no fixed chart. Heads-up equity vs a random hand comes from a
  precomputed 169-hand table; against a raiser the range is the top X% of hands,
  where X is that opponent's observed frequency of raising to that size or
  bigger (so shoves are read as tighter than min-raises). Open sizes 2/2.5/3/4bb
  and all-in are scored by EV: P(all fold) x pot plus P(called) x showdown EV
  against the continuing range, minus a risk margin. Against opponents who fold
  blinds a lot this becomes "min-raise any two"; against stations it becomes
  "raise the top of the range, limp or fold the rest".
- **Postflop:** Monte Carlo equity (eval7, 600 samples heads-up, seeded per
  decision) against each opponent's range. An opponent who bet this street is
  filtered to the top (1 - aggression frequency at that size) of their range by
  made-hand strength on the board. The continuing range when I bet is the top
  (1 - fold probability at that price). Fold probabilities are learned per
  opponent in 8 price buckets, separately for preflop/postflop and for whether
  the opponent had already bet that street (a 3-bettor doesn't fold like a
  blind defender). Bluffs appear automatically when the fold model says the pot
  is worth more than the bet risked.
- **Bet sizing:** menu of 15/25/35/47/60/80/130% pot (chosen so that common
  thresholds like a third, a half and two thirds of the pot land in different
  price buckets), 2.2x/2.8x/3.6x when raising, plus all-in. The size with the
  best EV wins; it only replaces check/call if it beats it by 2% of the pot.
  Bets beyond 62% of my stack become all-in.
- **Opponent modelling:** `match_action_log` is replayed hand by hand (street
  boundaries reconstructed from the betting) to learn, per bot id: VPIP, PFR,
  fold-to-bet by price bucket, raise-vs-bet rate, shove rate, and bet/raise
  frequency by size. A "selectivity" factor (how often the player folds at all)
  decides how much their sizing says about their hand: the aggressor never
  folds, so its random big bets are not read as strength. The model keeps
  module state across hands and starts from mild priors.
- **Time budget:** 2-3 Monte Carlo runs per decision, each 300-600 samples
  (about 2-4 ms each). Mean think time 7-9 ms, p95 under 15 ms, max observed
  22 ms versus the 2000 ms limit. Any exception falls back to check / cheap
  call / fold, so it has never timed out or errored.

## Development log

Bench = `poker bench my_bots/fable_bot --workers 4` (duplicate heads-up, 500
hands x 2 seats vs shark, mathematician, ref_bot_2, aggressor; 100bb; 95% CI).

| Version | Code hash | Change | Bench bb/100 (95% CI) |
|---|---|---|---|
| v1 | fcc08ae90244b2c1 | EV engine with size-aware fold model, board-aware continuing ranges | +304.2 ± 63.6 (shark -39 ± 73) |
| v2 | d6eff3ff1fa53ed8 | Fold model split by "opponent already bet this street" (was 4-bet bluffing the shark's premium 3-bets) | +394.0 ± 68.2 (shark +69) |
| v3 | 059b226318228630 | Model opponent re-raises (raising range, size, my response); fix all-in opponent counted as a fold | +406.3 ± 67.4 (opus_bot HU: -1363 to +53) |
| v4 | e2926a4eca9f70b5 | Opponent range depends on raise size (shoves tighter than min-raises), small risk margin on calls | +307.1 ± 64.0 (aggressor fell to +952: its random big bets read as strength) |
| v5 | 6d9540a4693b2f65 | Selectivity: scale range inference by the opponent's fold rate | +419.2 ± 66.7 (aggressor +1402) |
| v6 | 5eb4ecbea68bc372 | Selectivity from overall fold rate; risk margin on oversized calls | +407.6 ± 67.3 (100bb opus +446/+127; 500bb opus still -257/-646) |
| v7 | 01052b5ad3661123 | Only never-folding opponents count as non-selective (threshold 0.12) | +408.8 ± 67.6 (500bb opus +186/+799) |

Reverted/rejected ideas: a flat aggression penalty on medium-strength bets (v1-v2)
was replaced by the explicit re-raise model in v3; a 0.3 fold-rate selectivity
threshold (v5-v6) let opus_bot's 500bb shoves look random.

## Known weaknesses

- Chip-EV only: in a freezeout it will take a 60/40 for its whole stack early,
  which is fine for bb/100 but busted it on hand 16 of one turbo table.
- No showdown information is used (hole cards are not in the log), so range
  inference rests on frequencies and sizes alone; a bot that shoves a
  polarised range (nuts or air) is read as "top X%".
- Multiway pots use a simpler model (preflop-ranked ranges, all-or-nothing fold
  assumption), so 6-max play is less sharp than heads-up.
- The equity estimate is noisy (about 2% standard error); very close
  decisions are effectively coin flips.
- Other agents' bots keep changing, so results against them are scrimmages,
  not benchmarks.
