#!/usr/bin/env python3
# 285A historical backtest / score calibration tool
# Uses only information available on or before each decision date.
import json, os, statistics, urllib.parse, urllib.request, csv, math
from datetime import date, timedelta

API='https://api.irbank.net/v1'
TOKEN=os.environ.get('IRBANK_API_KEY')
CODE='285A'

if not TOKEN:
    raise SystemExit('IRBANK_API_KEY is not set')

def get(path, params=None):
    url=API+path
    if params: url += '?' + urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Authorization':f'Bearer {TOKEN}','User-Agent':'kabu-score-backtest/1.0'})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.load(r)

def prices(code):
    rows=[]; cursor=None
    for _ in range(20):
        p={'limit':500}
        if cursor: p['cursor']=cursor
        d=get(f'/securities/{code}/prices',p)
        rows += d.get('prices') or []
        cursor=d.get('next_cursor')
        if not cursor: break
    seen=set(); out=[]
    for x in rows:
        if x.get('date') in seen: continue
        px=x.get('adj_close',x.get('close'))
        if px is None: continue
        seen.add(x['date']); out.append({'date':x['date'],'close':float(px),'volume':float(x.get('volume') or 0)})
    return sorted(out,key=lambda x:x['date'])

def pct(a,b): return (a/b-1)*100 if b else None

def rsi(vals,n=14):
    if len(vals)<=n: return None
    gains=[]; losses=[]
    for i in range(1,len(vals)):
        d=vals[i]-vals[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    if len(gains)<n: return None
    ag=sum(gains[-n:])/n; al=sum(losses[-n:])/n
    if al==0: return 100.0
    return 100-100/(1+ag/al)

def percentile_rank(history,x):
    h=[v for v in history if v is not None]
    if not h or x is None: return None
    return 100*sum(v<=x for v in h)/len(h)

def features(rows,i):
    c=rows[i]['close']; vals=[r['close'] for r in rows[:i+1]]
    vols=[r['volume'] for r in rows[:i+1]]
    f={'date':rows[i]['date'],'close':c}
    for n,k in [(1,'ret1'),(5,'ret5'),(10,'ret10'),(20,'ret20')]:
        f[k]=pct(c,vals[-1-n]) if len(vals)>n else None
    for n,k in [(20,'vs20'),(60,'vs60')]:
        f[k]=pct(c,sum(vals[-n:])/n) if len(vals)>=n else None
    f['rsi14']=rsi(vals)
    f['volume_ratio']=vols[-1]/(sum(vols[-20:])/20) if len(vols)>=20 and sum(vols[-20:]) else None
    return f

def forward(rows,i,n):
    if i+n>=len(rows): return None
    return pct(rows[i+n]['close'],rows[i]['close'])

def quantile_score(x, hist, reverse=False):
    p=percentile_rank(hist,x)
    if p is None: return None
    # For oversold metrics: lower percentile => higher score.
    return 100-p if not reverse else p

def provisional_score(f,hist):
    # Stock-specific, quantile-based; deliberately independent from current app weights.
    parts={}
    parts['ret1']=quantile_score(f['ret1'],hist['ret1'])
    parts['vs20']=quantile_score(f['vs20'],hist['vs20'])
    parts['vs60']=quantile_score(f['vs60'],hist['vs60'])
    parts['rsi14']=quantile_score(f['rsi14'],hist['rsi14'])
    # Volume is supportive only when weakness exists; high volume alone is not a buy signal.
    p=percentile_rank(hist['volume_ratio'],f['volume_ratio'])
    parts['volume']=p
    vals=[v for v in parts.values() if v is not None]
    return round(sum(vals)/len(vals),1) if vals else None, parts

def main():
    rows=prices(CODE)
    if len(rows)<100: raise SystemExit(f'Only {len(rows)} price rows; need at least 100')
    records=[]
    for i in range(60,len(rows)-20):
        f=features(rows,i)
        # Expanding-window history: only data strictly before decision date.
        hist={k:[] for k in ['ret1','vs20','vs60','rsi14','volume_ratio']}
        for j in range(60,i):
            q=features(rows,j)
            for k in hist: hist[k].append(q[k])
        score,parts=provisional_score(f,hist)
        rec={**f,'score':score,**{f's_{k}':v for k,v in parts.items()}}
        for n in (5,10,20): rec[f'fwd{n}']=forward(rows,i,n)
        records.append(rec)
    outdir='backtest_285a_output'; os.makedirs(outdir,exist_ok=True)
    with open(outdir+'/285A_backtest_daily.csv','w',newline='',encoding='utf-8-sig') as fp:
        w=csv.DictWriter(fp,fieldnames=records[0].keys()); w.writeheader(); w.writerows(records)
    # Score bucket summary.
    buckets=[(0,20),(20,40),(40,60),(60,80),(80,101)]
    summary=[]
    for lo,hi in buckets:
        rs=[r for r in records if r['score'] is not None and lo<=r['score']<hi]
        row={'score_range':f'{lo}-{hi-1}','n':len(rs)}
        for n in (5,10,20):
            xs=[r[f'fwd{n}'] for r in rs if r[f'fwd{n}'] is not None]
            row[f'avg_fwd{n}']=round(statistics.mean(xs),2) if xs else None
            row[f'win_fwd{n}']=round(100*sum(x>0 for x in xs)/len(xs),1) if xs else None
            row[f'median_fwd{n}']=round(statistics.median(xs),2) if xs else None
        summary.append(row)
    with open(outdir+'/285A_score_buckets.csv','w',newline='',encoding='utf-8-sig') as fp:
        w=csv.DictWriter(fp,fieldnames=summary[0].keys()); w.writeheader(); w.writerows(summary)
    with open(outdir+'/README.txt','w',encoding='utf-8') as fp:
        fp.write('285A backtest\n')
        fp.write('Decision-date features use only prices through that date; future returns are targets only.\n')
        fp.write('Score is a provisional stock-specific percentile score, not the production score.\n')
        fp.write('Supply/market breadth are intentionally not fabricated here; they will be added only after historical point-in-time retrieval is verified.\n')
    print(json.dumps({'rows':len(rows),'backtest_records':len(records),'outputs':outdir},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
