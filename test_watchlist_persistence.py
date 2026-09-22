"""Regression test for app-added watchlist persistence.

An Issue add can already have placed a code in data/custom_watchlist.json.
The effective watchlist must still be materialized into watchlist.json before
rank_decisions.py runs, otherwise the Issue can close successfully while the
public app never sees the new stock.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = (ROOT / 'update_data.py').read_text(encoding='utf-8')
marker = 'def now_jst():'
assert marker in source
bootstrap = source[:source.index(marker)] + '\nprint(json.dumps(watch_data, ensure_ascii=False))\n'

with tempfile.TemporaryDirectory() as td:
    work = Path(td)
    (work / 'data').mkdir()
    (work / 'regime_model_v2.py').write_text((ROOT / 'regime_model_v2.py').read_text(encoding='utf-8'), encoding='utf-8')
    (work / 'watchlist.json').write_text(json.dumps({'stocks': [{'code': '5803', 'name': 'フジクラ'}]}, ensure_ascii=False), encoding='utf-8')
    (work / 'data' / 'custom_watchlist.json').write_text(json.dumps({'stocks': [{'code': '9989', 'name': 'サンドラッグ'}]}, ensure_ascii=False), encoding='utf-8')
    (work / 'bootstrap.py').write_text(bootstrap, encoding='utf-8')
    env = os.environ.copy()
    env['IRBANK_API_KEY'] = 'TEST'
    result = subprocess.run(['python', 'bootstrap.py'], cwd=work, env=env, text=True, capture_output=True, check=True)
    persisted = json.loads((work / 'watchlist.json').read_text(encoding='utf-8'))['stocks']
    codes = {str(x['code']) for x in persisted}
    assert '9989' in codes, persisted

print('WATCHLIST PERSISTENCE REGRESSION OK: custom 9989 materialized into watchlist.json')
