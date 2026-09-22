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
