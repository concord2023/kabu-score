from datetime import date, timedelta
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('update_data', ROOT / 'update_data.py')
ud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ud)

# 800 trading-like daily rows => roughly 38 calendar months.
rows = []
d = date(2023, 1, 2)
for i in range(800):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    close = 1000 + i * 0.7 + (i % 17) * 2.0
    rows.append({
        'date': d.isoformat(),
        'open': close - 2,
        'high': close + 4,
        'low': close - 5,
        'close': close,
        'adj_close': close,
        'volume': 100000 + (i % 23) * 1000,
    })
    d += timedelta(days=1)

out = ud.build_period_chart_history(rows, 'monthly', 18)
assert len(out) == 18, len(out)
assert sum(1 for x in out if x.get('macd') is not None) == 18
assert sum(1 for x in out if x.get('macd_signal') is not None) == 18
assert sum(1 for x in out if x.get('macd_hist') is not None) == 18
print('MONTHLY CHART MACD REGRESSION OK: 18 displayed months have MACD/Signal/Histogram values from full history')

# calc() golden output (Phase A safety net): the full result dict for a fixed
# synthetic fixture must not change when calc/indicators are refactored.
import hashlib as _hashlib
import json as _json
from datetime import date as _date, timedelta as _timedelta

_grows = []
_gd = _date(2025, 1, 6)
while len(_grows) < 400:
    if _gd.weekday() < 5:
        _j = len(_grows)
        _price = 100 + _j * 0.35 + ((_j % 7) - 3) * 0.4
        _grows.append({'date': _gd.isoformat(), 'open': _price - 1, 'high': _price + 2,
                       'low': _price - 2, 'close': _price, 'adj_close': _price,
                       'volume': 100000 + _j * 100})
    _gd += _timedelta(days=1)
_grows = list(reversed(_grows))
_gout = ud.calc(_grows, breadth_info={'breadth': {'6d': 55.0, '10d': 54.0, '15d': 53.0, '25d': 52.0}}, supply_info=None)
_ghash = _hashlib.sha256(_json.dumps(_gout, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
assert _ghash == 'c447f0d8fe5934d3731e9c86c69b10b8de2543e60d786e2fab9908d54673af16', _ghash
print('CALC GOLDEN OUTPUT OK: sha256 matches pre-refactor fixture')
