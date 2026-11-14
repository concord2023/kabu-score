"""Regression test for app-added watchlist persistence (Phase B4 API).

An Issue add can already have placed a code in data/custom_watchlist.json.
Calling update_data.load_watchlist() must materialize the effective
watchlist into watchlist.json before rank_decisions.py runs, otherwise the
Issue can close successfully while the public app never sees the new stock.

Before Phase B4 this behavior ran at import time and the test sliced the
module source at 'def now_jst():'; importing update_data is now side-effect
free, so the test uses the real API in a temporary working directory.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULES = ['update_data.py', 'regime_model_v2.py', 'forecast_parsers.py',
           'indicators.py', 'calc.py', 'market_data.py']

RUNNER = """
import json
import update_data as ud

before = json.loads(open('watchlist.json', encoding='utf-8').read())['stocks']
assert {str(x['code']) for x in before} == {'5803'}, before
ud.load_watchlist()
codes = {str(x['code']) for x in ud.watch}
assert '9989' in codes, codes
print('LOADED', sorted(codes))
"""

with tempfile.TemporaryDirectory() as td:
    work = Path(td)
    (work / 'data').mkdir()
    for name in MODULES:
        (work / name).write_text((ROOT / name).read_text(encoding='utf-8'), encoding='utf-8')
    (work / 'watchlist.json').write_text(json.dumps({'stocks': [{'code': '5803', 'name': 'フジクラ'}]}, ensure_ascii=False), encoding='utf-8')
    (work / 'data' / 'custom_watchlist.json').write_text(json.dumps({'stocks': [{'code': '9989', 'name': 'サンドラッグ'}]}, ensure_ascii=False), encoding='utf-8')
    (work / 'runner.py').write_text(RUNNER, encoding='utf-8')
    env = os.environ.copy()
    env['IRBANK_API_KEY'] = 'TEST'
    result = subprocess.run([sys.executable, 'runner.py'], cwd=work, env=env, text=True, capture_output=True, check=True)
    assert 'LOADED' in result.stdout, result.stdout
    persisted = json.loads((work / 'watchlist.json').read_text(encoding='utf-8'))['stocks']
    codes = {str(x['code']) for x in persisted}
    assert '9989' in codes, persisted

print('WATCHLIST PERSISTENCE REGRESSION OK: custom 9989 materialized into watchlist.json')
