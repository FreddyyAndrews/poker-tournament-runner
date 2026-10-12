# sonnet_bot

Equity-driven NLHE bot. Monte Carlo equity (eval7, ~1500 samples, 0.6 s cap) against random hands,
shrunk when facing bets (more for big bets and aggressive action this hand), compared with pot odds
and the multiway fair share 1/(n+1).

- Preflop: raise when equity edge > 0.10, call/re-raise by edge and price, fold the rest. Equity cached per hand class.
- Postflop: bet 40-75% pot by edge when checked to, occasional light heads-up bluff, call when equity beats pot odds, raise very strong hands.
- No cross-hand opponent model yet.

## Development log
- v1: initial version. bench: +24.6 bb/100 ± 22.5 (shark +5.3, aggressor +81.9, ref_bot_2 +9.4, mathematician +1.8).

## Known weaknesses
- Equity vs random overrates marginal hands against tight bettors; no position awareness; no opponent modelling.
