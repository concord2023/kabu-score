# Entry branches v1

## Purpose
The model now separates market-state classification from entry logic.
This document defines the next two entry branches and keeps them provisional until multi-stock historical validation is available.

### 1. Uptrend continuation
Candidate ingredients:
- state = UPTREND
- today's return > 0
- price above MA20
- 5-day return > 0

### 2. Uptrend pullback recovery
Candidate ingredients:
- state = UPTREND_PULLBACK
- today's return > 0
- 5-day return >= -5%
- price no more than 3% below MA20

A stricter diagnostic variant additionally requires:
- 5-day return >= -3%
- price no more than 2% below MA20
- 10-day return >= -5%

## Important status
These are **stress-test rules, not validated buy rules**. They are intentionally not allowed to generate production BUY signals yet.

The only currently validated branch is the downtrend/reversal branch described in `VALIDATION_V1.md`, and even that has a small sample and is not proven predictive.

## 285A stress test
On the existing 342-day Kioxia dataset:
- Pullback recovery: 21 observations; forward 5/10/20-day averages +3.12% / +6.96% / +19.92%.
- Strict pullback: 9 observations; forward 5/10/20-day averages +7.41% / +11.80% / +24.93%.
- Continuation: 110 observations; forward 5/10/20-day averages +10.06% / +21.85% / +42.82%.

These figures are **not evidence that the rules work generally**. They are only a stress test on one stock and are vulnerable to selection/regime effects.

## Next implementation gate
Production BUY for these two branches requires historical validation across multiple watchlist stocks, with:
- fixed rules chosen before evaluation,
- forward 5/10/20-day returns,
- win rate,
- drawdown/loss tail,
- sample count,
- comparison against a simple benchmark,
- and a check that the result is not driven by one stock or one market period.
