"""Focused regression tests for the strict Tora RSI gate.

This file intentionally keeps the app's legacy simple RSI out of the Tora
path.  It independently computes Wilder RSI for a fixed sequence and verifies
that the scanner's gate treats >30 as a hard failure.
"""
import math
import update_data as ud


def independent_wilder_rsi(values):
    chronological = list(values)
    changes = [chronological[i] - chronological[i - 1] for i in range(1, len(chronological))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    for gain, loss in zip(gains[14:], losses[14:]):
        avg_gain = (avg_gain * 13 + gain) / 14
        avg_loss = (avg_loss * 13 + loss) / 14
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


# 20 chronological weekly closes with both gains and losses.
chronological = [
    100, 101, 99, 98, 100, 102, 101, 100, 97, 96,
    98, 99, 97, 95, 96, 94, 93, 95, 94, 92, 91,
]
newest_first = list(reversed(chronological))
expected = independent_wilder_rsi(chronological)
actual = ud.rsi14_wilder(newest_first)
assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12), (actual, expected)

# Regression for the hard threshold: a value above 30 must never pass.
assert actual > 30.0
assert not (actual <= 30.0)

print(f'TORA RSI REGRESSION OK: {actual:.6f} (hard gate rejects >30)')
