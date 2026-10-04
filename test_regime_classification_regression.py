from datetime import date, timedelta
from regime_model_v2 import classify_regime


def rows_from_closes(closes):
    today = date(2026, 10, 2)
    rows = []
    for i, close in enumerate(closes):
        d = today - timedelta(days=i)
        rows.append({'date': d.isoformat(), 'close': close, 'adj_close': close,
                     'high': close + 1, 'low': close - 1, 'volume': 1000})
    return rows


def test_established_uptrend_is_not_demoted_by_stale_breakout_level():
    # Strong established daily/weekly uptrend. The breakout object deliberately
    # contains a nearby historical level to reproduce the failure mode fixed on
    # 2026-10-04. It must remain UPTREND, not RANGE_TRANSITION.
    rows = rows_from_closes([200 - i * 0.5 for i in range(420)])
    regime = classify_regime(rows, breakout_signal={
        'status': 'なし', 'is_breakout': False,
        'breakout_level': 198, 'breakout_trigger': 199,
        'current_price': 200,
    })
    assert regime['weekly_direction'] == 'UP'
    assert regime['regime'] == 'UPTREND', regime


def test_current_range_setup_is_range_transition():
    # A compact current range close to its current ceiling with an incomplete
    # daily trend structure must be treated as breakout waiting.
    closes = []
    for i in range(420):
        if i < 20:
            c = 100 - i * 0.03
        elif i < 100:
            c = 99.4 + (i - 20) * 0.05
        else:
            c = 110 + (i - 100) * 0.2
        closes.append(c)
    rows = rows_from_closes(closes)
    regime = classify_regime(rows, breakout_signal={
        'status': 'なし', 'is_breakout': False,
        'breakout_level': 101, 'breakout_trigger': 101.5,
        'current_price': 100,
    })
    assert regime['regime'] == 'RANGE_TRANSITION', regime


if __name__ == '__main__':
    test_established_uptrend_is_not_demoted_by_stale_breakout_level()
    test_current_range_setup_is_range_transition()
    print('REGIME CLASSIFICATION REGRESSION OK')
