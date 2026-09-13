# 株スコア Signal Model v1 — validation decision

## Fixed core
1. `vs60 <= -5` — price is at least 5% below the 60-day moving average.
2. `ret1 > 0` — today's close is higher than the previous close.
3. `ret5 >= -5` — the 5-day decline has begun to stabilize.
4. `ret10 >= -10` — the 10-day decline has begun to stabilize.

These are fixed common thresholds. Do not optimize them separately per stock.

## Overlays
- Very large down day: caution.
- Large volume + down day: caution.
- Extremely low RSI: caution, not an automatic buy.
- Weakness vs the market: caution.
- Improving credit supply/demand: positive explanation only.
- Deteriorating credit supply/demand: caution.
- Weak market regime: suppress BUY to WATCH.

Overlays never create a BUY signal by themselves.

## States
- `BUY_CANDIDATE`: all 4 core conditions and no suppressing warning.
- `WATCH`: 2–3 core conditions, or all 4 with a warning/weak market regime.
- `AVOID`: 0–1 core conditions.
- `DATA_INSUFFICIENT`: reserved for missing required inputs in the production wrapper.

## 285A validation
Using the supplied 285A daily dataset:
- All 11 core signals: fwd5 +4.92%, fwd10 +6.84%, fwd20 +11.63%.
- First half: 8 signals; fwd5 +6.34%, fwd10 +10.56%, fwd20 +12.62%.
- Second half: 3 signals; fwd5 +1.15%, fwd10 -3.09%, fwd20 +8.99%.

Interpretation: promising but NOT statistically validated. The late-period sample is too small.

## Production principle
Do not force a daily BUY. Zero BUY candidates is a valid result.
The ranking should prioritize closeness to the fixed core conditions and quality of confirmations, not a single 0–100 score.
