"""Backtest the provisional V2 reversal BUY rule on local daily CSV files.

CSV columns required: date and adj_close/close. This is deliberately a simple
research tool, not a performance guarantee. It uses only information available
on each signal date and measures forward 5/10/20 trading-day returns.
"""
import csv, glob, os
from statistics import mean
from regime_model_v2 import classify

ROOT=os.path.dirname(__file__)

def load(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows=list(csv.DictReader(f))
    out=[]
    for r in rows:
        c=r.get('adj_close') or r.get('close')
        if not r.get('date') or c in (None,''): continue
        rr=dict(r); rr['adj_close']=float(c); out.append(rr)
    out.sort(key=lambda x:x['date'], reverse=True)
    return out

def ret(a,b): return (b/a-1)*100

for path in sorted(glob.glob(os.path.join(ROOT,'*.csv'))):
    rows=load(path)
    if len(rows)<80: continue
    # chronological index i; classifier gets newest-first slice ending at i
    signals=[]
    chron=list(reversed(rows))
    for i in range(60, len(chron)-20):
        hist=list(reversed(chron[:i+1]))
        d=classify(hist)
        if d.get('signal')=='BUY_CANDIDATE':
            px=chron[i]['adj_close']
            signals.append({
                'date':chron[i]['date'],
                'fwd5':ret(px,chron[i+5]['adj_close']),
                'fwd10':ret(px,chron[i+10]['adj_close']),
                'fwd20':ret(px,chron[i+20]['adj_close']),
            })
    print(os.path.basename(path), 'signals=',len(signals))
    if signals:
        for k in ('fwd5','fwd10','fwd20'):
            vals=[x[k] for x in signals]
            win=sum(v>0 for v in vals)/len(vals)*100
            print(f'  {k}: avg={mean(vals):.2f}% win={win:.1f}%')
