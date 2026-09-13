import os, re, math, requests, pandas as pd
from datetime import datetime, timedelta

API='https://api.irbank.net/v1'
TOKEN=os.environ.get('IRBANK_API_KEY')
CODE='285A'
NAME='キオクシアホールディングス'
BREADTH_URL='https://tofuhardboiled.com/updownratio/'

HEADERS={'Authorization':f'Bearer {TOKEN}'} if TOKEN else {}

def get_prices(code):
    rows=[]; cursor=None
    for _ in range(20):
        p={'security_code':code,'limit':500}
        if cursor: p['cursor']=cursor
        r=requests.get(f'{API}/securities/{code}/prices',headers=HEADERS,params=p,timeout=30)
        r.raise_for_status(); j=r.json(); rows.extend(j.get('prices',j.get('data',[])))
        cursor=j.get('next_cursor')
        if not cursor: break
    out=[]
    for x in rows:
        d=x.get('date') or x.get('as_of'); c=x.get('close'); v=x.get('volume')
        if d and c is not None: out.append({'date':pd.to_datetime(d).date(),'close':float(c),'volume':float(v or 0)})
    return pd.DataFrame(out).drop_duplicates('date').sort_values('date').reset_index(drop=True)

def pct(a,b): return (a/b-1)*100 if b not in (None,0) else None

def rsi14(vals):
    if len(vals)<15: return None
    gains=[]; losses=[]
    for k in range(len(vals)-14,len(vals)):
        d=vals[k]-vals[k-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/14; al=sum(losses)/14
    if al==0: return 100.0
    return 100-100/(1+ag/al)

def percentile(hist, x, higher_is_more_extreme=True):
    h=pd.Series(hist).dropna()
    if len(h)<40 or x is None: return None
    return float((h < x).mean()*100 + 0.5*(h == x).mean()*100)

def features(df,i):
    c=df.close.tolist(); v=df.volume.tolist(); p=c[i]
    if i<60: return None
    ma20=sum(c[i-19:i+1])/20; ma60=sum(c[i-59:i+1])/60; av20=sum(v[i-19:i+1])/20
    f={'ret1':pct(c[i],c[i-1]),'ret5':pct(c[i],c[i-5]),'ret10':pct(c[i],c[i-10]),'ret20':pct(c[i],c[i-20]),
       'vs20':pct(p,ma20),'vs60':pct(p,ma60),'rsi14':rsi14(c[:i+1]),'volume_ratio':v[i]/av20 if av20 else None}
    # Stock-specific trailing rarity: only information available before/at decision date.
    start=max(60,i-252); prev=range(start,i)
    for key in ['vs20','vs60','ret1','ret5','ret20','rsi14']:
        vals=[]
        for j in prev:
            if j<60: continue
            cc=c[j]; mm20=sum(c[j-19:j+1])/20; mm60=sum(c[j-59:j+1])/60
            if key=='vs20': z=pct(cc,mm20)
            elif key=='vs60': z=pct(cc,mm60)
            elif key=='ret1': z=pct(c[j],c[j-1])
            elif key=='ret5': z=pct(c[j],c[j-5])
            elif key=='ret20': z=pct(c[j],c[j-20])
            else: z=rsi14(c[:j+1])
            vals.append(z)
        f[key+'_pct']=percentile(vals,f[key])
    return f

def fetch_breadth():
    r=requests.get(BREADTH_URL,timeout=30); r.raise_for_status()
    tables=pd.read_html(r.text)
    for t in tables:
        cols=[str(x) for x in t.columns]
        if '日付' in cols and '値上' in cols and '値下' in cols:
            df=t.copy(); break
    else: raise RuntimeError('breadth table not found')
    df['date']=pd.to_datetime(df['日付']).dt.date
    df['up']=pd.to_numeric(df['値上'],errors='coerce'); df['down']=pd.to_numeric(df['値下'],errors='coerce')
    df=df.dropna(subset=['date','up','down']).sort_values('date').reset_index(drop=True)
    # The site's ratio is up-volume / down-volume; we recreate it from up/down counts.
    out=[]
    for i,row in df.iterrows():
        rec={'date':row['date']}
        for n in [6,10,15,25]:
            a=df.loc[max(0,i-n+1):i,'up'].sum(); b=df.loc[max(0,i-n+1):i,'down'].sum()
            rec[f'breadth{n}']=a/b*100 if b else None
        out.append(rec)
    return pd.DataFrame(out)

def fetch_supply_snapshot(target_date):
    fields=['marginBuyBalance','marginSellBalance','marginBuyBalanceChangeWow','marginSellBalanceChangeWow','marginRatio','jsfLoanRatio','marginBuyToFloatRatio']
    result={}; actual_dates=[]; errors=[]
    for field in fields:
        params={'name':NAME,'sort_by':field,'sort_order':'desc','as_of':target_date.isoformat(),'limit':100}
        try:
            r=requests.get(f'{API}/screening',headers=HEADERS,params=params,timeout=30); r.raise_for_status(); j=r.json()
            ms=j.get('securities') or []
            m=next((x for x in ms if x.get('name')==NAME), ms[0] if ms else None)
            if m:
                for x in m.get('metrics') or []:
                    result[x.get('field')]=x.get('value')
                    if x.get('as_of'): actual_dates.append(pd.to_datetime(x['as_of']).date())
        except Exception as e: errors.append(f'{field}: {e}')
    if not result: return None
    actual=max(actual_dates) if actual_dates else target_date
    return {'supply_asof':actual,'buy_balance':result.get('marginBuyBalance'),'sell_balance':result.get('marginSellBalance'),
            'buy_change':result.get('marginBuyBalanceChangeWow'),'sell_change':result.get('marginSellBalanceChangeWow'),
            'credit_ratio':result.get('marginRatio'),'jsf_loan_ratio':result.get('jsfLoanRatio'),
            'buy_to_float_ratio':result.get('marginBuyToFloatRatio'),'api_errors':' | '.join(errors)}

def load_supply_for_dates(dates):
    # Credit balances are weekly. For a market-close decision, use the latest snapshot
    # that should already have been public: previous Friday, never the same Friday.
    cache={}; rows=[]
    for d in sorted(set(dates)):
        prev=d-timedelta(days=1)
        while prev.weekday()>=5: prev-=timedelta(days=1)
        # Keep one snapshot per prior-week Friday.
        while prev.weekday()!=4: prev-=timedelta(days=1)
        key=prev
        if key not in cache: cache[key]=fetch_supply_snapshot(key)
        s=cache[key]
        rows.append({'date':d,**(s or {})})
    return pd.DataFrame(rows)

def breadth_score(b):
    p=0
    for x,w in [(b.get('breadth6'),18),(b.get('breadth10'),10),(b.get('breadth15'),4),(b.get('breadth25'),3)]:
        if x is None: continue
        # 0 at 120+, max at 60 or below. Smooth rather than hard cliffs.
        p += max(0,min(1,(120-x)/60))*w
    return round(p,1)

def supply_score(s):
    if s is None: return 0
    p=0
    # Direction of weekly balance changes is more important than the absolute ratio.
    bc=s.get('buy_change'); sc=s.get('sell_change')
    if bc is not None: p += 5 if bc<0 else -3 if bc>0 else 0
    if sc is not None: p += 4 if sc>0 else -2 if sc<0 else 0
    cr=s.get('credit_ratio')
    if cr is not None: p += 3 if cr<=10 else 1 if cr<=20 else -2 if cr>=30 else 0
    bf=s.get('buy_to_float_ratio')
    if bf is not None: p += 2 if bf<=5 else 1 if bf<=10 else 0
    return round(max(0,min(12,p)),1)

def score_row(f,b,s):
    # Rebound candidate: oversold relative to THIS stock, but penalize structural damage.
    rp=0
    if f['vs20_pct'] is not None: rp += max(0,min(25,(50-f['vs20_pct'])/50*25))
    if f['vs60_pct'] is not None: rp += max(0,min(20,(50-f['vs60_pct'])/50*20))
    if f['rsi14_pct'] is not None: rp += max(0,min(15,(50-f['rsi14_pct'])/50*15))
    if f['ret1_pct'] is not None: rp += max(0,min(12,(50-f['ret1_pct'])/50*12))
    if f['ret5_pct'] is not None: rp += max(0,min(8,(50-f['ret5_pct'])/50*8))
    rp += breadth_score(b)*0.5
    rp += supply_score(s)*0.8
    # Risk: extreme stock-specific weakness + crash + worsening supply.
    risk=0
    for key,w in [('vs20_pct',22),('vs60_pct',18),('ret1_pct',15),('rsi14_pct',10)]:
        z=f.get(key)
        if z is not None: risk += max(0,min(w,(20-z)/20*w))
    if s:
        if s.get('buy_change') and s['buy_change']>0: risk+=7
        if s.get('sell_change') and s['sell_change']<0: risk+=5
    return round(max(0,min(100,rp)),1), round(max(0,min(100,risk)),1)

def main():
    if not TOKEN: raise SystemExit('IRBANK_API_KEY missing')
    prices=get_prices(CODE); breadth=fetch_breadth()
    # Limit to the overlap where breadth history is available.
    merged=prices.merge(breadth,on='date',how='inner')
    if len(merged)<80: raise SystemExit('Not enough breadth overlap')
    merged=merged.reset_index(drop=True)
    dates=list(merged.date)
    supply=load_supply_for_dates(dates)
    merged=merged.merge(supply,on='date',how='left')
    rows=[]
    for i in range(60,len(merged)-20):
        f=features(merged,i)
        if not f or any(f.get(k) is None for k in ['vs20_pct','vs60_pct','ret1_pct','ret5_pct','rsi14_pct']): continue
        b={k:merged.loc[i,k] for k in ['breadth6','breadth10','breadth15','breadth25']}
        s={k:merged.loc[i,k] for k in ['buy_balance','sell_balance','buy_change','sell_change','credit_ratio','jsf_loan_ratio','buy_to_float_ratio','supply_asof']}
        rr,risk=score_row(f,b,s)
        row={'date':merged.loc[i,'date'],'close':merged.loc[i,'close'],**f,**b,**s,'breadth_score':breadth_score(b),'supply_score':supply_score(s),'rebound_score':rr,'risk_score':risk}
        for n in (5,10,20): row[f'fwd{n}']=pct(merged.loc[i+n,'close'],merged.loc[i,'close'])
        rows.append(row)
    out=pd.DataFrame(rows)
    os.makedirs('backtest_285a_v3_output',exist_ok=True)
    out.to_csv('backtest_285a_v3_output/285A_v3_daily.csv',index=False)
    with open('backtest_285a_v3_output/README.txt','w',encoding='utf-8') as fh:
        fh.write('285A v3 prototype. Adds market breadth, stock-specific trailing percentiles, and point-in-time weekly credit/supply snapshots. Supply uses the latest prior-Friday snapshot to avoid same-day lookahead. This is a research prototype, not production validation.\n')
        fh.write('IRBANK /screening supports historical as_of dates; TSE credit data is only available from 2025-06-01 onward.\n')
    print(out.tail(1).to_string(index=False))

if __name__=='__main__': main()
