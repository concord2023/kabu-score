# Regime Model v1

## Purpose
Classify each stock's current market state first, then apply an entry rule appropriate to that state. This prevents a healthy uptrend from being mislabeled as a reversal candidate.

## States
- `UPTREND`: price above MA60 and positive 20-day return, with MA20 > MA60 when available.
- `UPTREND_PULLBACK`: same uptrend structure, but short-term weakness (price below MA20 or 5-day return negative).
- `DOWNTREND_REVERSAL_WAIT`: price below MA60 with negative 20-day return while the recent decline is stabilizing; also used for a transition below MA60 with non-negative 20-day return.
- `DOWNTREND_CONTINUED`: below MA60 and negative 20-day return without short-term stabilization.
- `UNKNOWN`: insufficient trend data.

MA20/MA60 slope fields are now calculated from a 10-trading-day comparison and are available for future tightening of the regime classifier. They are not yet hard gates, to avoid overfitting before multi-stock validation.

## Reversal entry rule
Only the two downtrend states use the fixed reversal core:
- vs60 <= -5%
- ret1 > 0%
- ret5 >= -5%
- ret10 >= -10%

All four are required for `BUY_CANDIDATE`; partial matches are `WATCH`.

## Uptrend branches
The uptrend and pullback branches remain `WATCH` only. A dedicated pullback/continuation entry rule will be added only after separate historical validation. The reversal rule is not reused for these states.

## Sanity checks
The design target is:
- RAKUS (3923): `UPTREND_PULLBACK`, not reversal-wait.
- MUFG (8306): `UPTREND`, not reversal-wait.
- Obayashi (1802): `DOWNTREND_REVERSAL_WAIT`, not healthy uptrend.
- Kioxia (285A): downtrend/reversal branch.

## Current validation
The Kioxia historical test produced 11 fixed-rule signals:
- 5-day forward return: +4.92% average; 81.8% positive.
- 10-day: +6.84%; 81.8% positive.
- 20-day: +11.63%; 100% positive.

The sample is small and the late-period results were weaker at 10 days, so this is a hypothesis, not proof of predictive performance.
