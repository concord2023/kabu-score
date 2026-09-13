"""Rank the latest data/stocks.json using Decision Model V2."""
import json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))

ORDER={'BUY_CANDIDATE':0,'WATCH':1,'AVOID':2,'INSUFFICIENT':3}
STATE={'DOWNTREND_REVERSAL_WAIT':0,'UPTREND_PULLBACK':1,'UPTREND':2,'RANGE_TRANSITION':3,'DOWNTREND_CONTINUED':4,'UNKNOWN':5}

def rank(s):
    sig=s.get('signal','INSUFFICIENT')
    one=1 if s.get('one_condition_away') else 0
    rs=s.get('relative_strength')
    rs=rs if rs is not None else -999
    return (ORDER.get(sig,9), -one, STATE.get(s.get('signal_state'),9), -rs)

p=ROOT/'data'/'stocks.json'
if not p.exists():
    raise SystemExit('data/stocks.json not found; run update_data.py first')
data=json.loads(p.read_text(encoding='utf-8'))
items=[s for s in data.get('stocks',{}).values() if isinstance(s,dict)]
items.sort(key=rank)
report={
 'updated_at':data.get('updated_at'),
 'market_context':{'nikkei_change': next((s.get('nikkei_change') for s in items if s.get('nikkei_change') is not None),None)},
 'stocks':[{k:s.get(k) for k in ['code','name','price','change','weekly_regime','signal_state','signal','signal_reason','one_condition_away','missing_conditions','relative_strength','supply_points']} for s in items]
}
print(json.dumps(report,ensure_ascii=False,indent=2))
(ROOT/'data').mkdir(exist_ok=True)
(ROOT/'data'/'decision_ranking.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
