# Decision Model V2

## Core
- Weekly = direction
- Daily = timing
- Market = context / relative strength, never a hard veto

## States
- UPTREND
- UPTREND_PULLBACK
- DOWNTREND_REVERSAL_WAIT
- DOWNTREND_CONTINUED
- RANGE_TRANSITION
- UNKNOWN

## Provisional BUY branch
Only `DOWNTREND_REVERSAL_WAIT` can emit `BUY_CANDIDATE` until the other branches are validated.

Required:
1. close is at least 5% below 60-day MA
2. 1-day return > 0%
3. 5-day return >= -5%
4. 10-day return >= -10%

If exactly one is missing, `one_condition_away=true`.

## Ranking
1. BUY_CANDIDATE
2. WATCH with one condition away
3. WATCH: UPTREND_PULLBACK
4. WATCH: UPTREND
5. WATCH: RANGE_TRANSITION
6. AVOID: DOWNTREND_CONTINUED
7. INSUFFICIENT

Relative strength and supply/demand are ranking/context factors, not hard gates.
