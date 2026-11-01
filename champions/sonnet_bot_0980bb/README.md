# sonnet_bot

Equity-driven NLHE bot. Monte Carlo equity (eval7, <=1500 samples, 0.6 s cap) against random hands,
shrunk when facing bets, then compared with pot odds and the multiway fair share 1/(n+1).

## Strategy
- Preflop: raise when equity edge over fair share > 0.06; facing a bet, call when equity beats pot odds by 5 pts (and fair share by 3), shove very strong hands. Equity cached per hand class.
- Postflop: when checked to, bet 40-75% pot by edge, occasional light heads-up bluff on flop/turn; facing a bet, call when shrunk equity beats pot odds, raise > 0.80.
- Sizing: fraction of pot after call; goes all-in when the raise would commit >70% of the stack.
- Opponent modelling: shrink grows with bet size and this-hand aggression, and is scaled by the opponents' raise frequency over `match_action_log` (aggressive opponents have wide ranges, so we shrink less); extra shrink when re-raised after our own bet.
- Time: ~5-10 ms per decision on average, hard cap 0.6 s of simulation.

## Development log
- v1 (7e49122): initial. bench +24.6 +/- 22.5 (500 hands).
- v2 (a20e76f): preflop open threshold 0.10 -> 0.06, preflop calls by pot odds (v1 folded AA to a 4-bet and had 2% VPIP multiway), extra shrink when re-raised. bench 2000h: +5.0 +/- 9.0 (shark +9.6, mathematician +7.0, ref_bot_2 +9.7, aggressor -6.4 +/- 35).
- v3 (53cb754): shrink scaled by opponent raise frequency. bench 2000h: +52.1 +/- 14.8 (aggressor +188 +/- 59; others unchanged within noise).

## Known weaknesses
- Equity is vs random hands: overvalues one pair vs tight bettors; ignores position and board texture of opponent range.
- No cross-hand per-opponent modelling beyond pooled raise frequency.
- Deep-stack 6-max (50k, 6 bots) single match was -44 bb/100 (noisy, one seed).
