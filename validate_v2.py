from pathlib import Path
import ast,json
ROOT=Path(__file__).parent
required=['update_data.py','regime_model_v2.py','rank_decisions.py','run_daily.py','backtest_v2.py','watchlist.json','dashboard.html','manifest.json','sw.js','.github/workflows/daily-update.yml']
missing=[x for x in required if not (ROOT/x).exists()]
if missing: raise SystemExit('Missing: '+', '.join(missing))
for p in ROOT.glob('*.py'): ast.parse(p.read_text(encoding='utf-8'))
watch=json.loads((ROOT/'watchlist.json').read_text(encoding='utf-8'))['stocks']
if not watch: raise SystemExit('Watchlist is empty')
codes=[str(x.get('code','')).strip() for x in watch]
if len(codes)!=len(set(codes)): raise SystemExit('Duplicate stock codes in watchlist')
text=(ROOT/'dashboard.html').read_text(encoding='utf-8')
for token in ['data/decision_ranking.json','d.ranking','x.regime','x.signal']:
    if token not in text: raise SystemExit('Dashboard wiring missing: '+token)
print('V2 validation OK')
