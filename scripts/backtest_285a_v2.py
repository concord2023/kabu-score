import math, os, requests, pandas as pd
from datetime import datetime

API='https://api.irbank.net/v1'
TOKEN=os.environ.get('IRBANK_API_KEY')
CODE='285A'

FEATURES=['ret1','ret5','ret10','ret20','vs20','vs60','rsi14','volume_ratio']

def get_prices(code):
    headers={'Authorization':f'Bearer {TOKEN}'}
    rows=[]; cursor=None
    for _ in range(20):
        params={'security_code':code,'limit':500}
        if cursor: params['cursor']=cursor
        r=requests.get(f'{API}/securities/{code}/prices',headers=headers,params=params,timeout=30)
        r.raise_for_status(); j=r.json()
        rows.extend(j.get('prices',j.get('data',[])))
        cursor=j.get('next_cursor')
        if not cursor: break
    out=[]
    for x in rows:
        d=x.get('date') or x.get('as_of')
        c=x.get('close')
        v=x.get('volume')
        if d and c is not None: out.append({'date':d,'close':float(c),'volume':float(v or 0)})
    df=pd.DataFrame(out).drop_duplicates('date').sort_values('date').reset_index(drop=True)
    return df

def pct(a,b):
    return (a/b-1)*100 if b else None

def rsi(vals,n=14):
    if len(vals)<n+1: return None
    gains=[]; losses=[]
    for i in range(1,n+1):
        d=vals[-i]-vals[-i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/n; al=sum(losses)/n
    if al==0: return 100.0
    return 100-100/(1+ag/al)

def features(df,i):
    c=df.close.tolist(); v=df.volume.tolist(); p=c[i]
    ma20=sum(c[i-19:i+1])/20 if i>=19 else None
    ma60=sum(c[i-59:i+1])/60 if i>=59 else None
    avgv20=sum(v[i-19:i+1])/20 if i>=19 else None
    if i<60: return None
    return {
      'ret1':pct(c[i],c[i-1]), 'ret5':pct(c[i],c[i-5]), 'ret10':pct(c[i],c[i-10]), 'ret20':pct(c[i],c[i-20]),
      'vs20':pct(p,ma20), 'vs60':pct(p,ma60), 'rsi14':rsi(c[:i+1],14),
      'volume_ratio':(v[i]/avgv20 if avgv20 else None)
    }

def clip(x,a,b): return max(a,min(b,x))

def sweet(x, lo, hi, left_span, right_span):
    if x<lo: return clip(100-(lo-x)/left_span*100,0,100)
    if x>hi: return clip(100-(x-hi)/right_span*100,0,100)
    return 100.0

def score_rebound(f):
    # Deliberately separates "oversold" from "too broken". This is a prototype,
    # not a fitted production model.
    s_vs20=sweet(f['vs20'],-15,-3,15,20)
    s_vs60=clip(((-f['vs60'])+0)/30*100,0,100)
    s_ret1=sweet(f['ret1'],-7,-2,7,8)
    s_rsi=sweet(f['rsi14'],25,40,25,25)
    s_vol=clip((f['volume_ratio']-0.8)/1.2*100,0,100)
    s_ret5=sweet(f['ret5'],-12,-2,12,15)
    return round(0.28*s_vs20+0.22*s_vs60+0.18*s_ret1+0.15*s_rsi+0.10*s_ret5+0.07*s_vol,1)

def score_risk(f):
    # Higher = greater short-term downside risk.
    oversold_break=clip((-f['vs20']-15)/20*100,0,100)
    deep60=clip((-f['vs60']-10)/25*100,0,100)
    crashday=clip((-f['ret1']-7)/8*100,0,100)
    lowrsi=clip((30-f['rsi14'])/15*100,0,100)
    vol=clip((f['volume_ratio']-1.2)/1.5*100,0,100)
    return round(0.35*oversold_break+0.25*deep60+0.20*crashday+0.12*lowrsi+0.08*vol,1)

def bucket(x):
    if x<20:return '0-19'
    if x<40:return '20-39'
    if x<60:return '40-59'
    if x<80:return '60-79'
    return '80-100'

def main():
    if not TOKEN: raise SystemExit('IRBANK_API_KEY missing')
    df=get_prices(CODE)
    rows=[]
    for i in range(60,len(df)-20):
        f=features(df,i)
        if not f or any(v is None for v in f.values()): continue
        rr=score_rebound(f); risk=score_risk(f)
        row={'date':df.loc[i,'date'],'close':df.loc[i,'close'],**f,'rebound_score':rr,'risk_score':risk}
        for n in (5,10,20): row[f'fwd{n}']=pct(df.loc[i+n,'close'],df.loc[i,'close'])
        rows.append(row)
    out=pd.DataFrame(rows)
    os.makedirs('backtest_285a_v2_output',exist_ok=True)
    out.to_csv('backtest_285a_v2_output/285A_v2_daily.csv',index=False)
    summary=[]
    for b in ['0-19','20-39','40-59','60-79','80-100']:
        x=out[out.rebound_score.map(bucket)==b]
        if len(x)==0: continue
        summary.append({'rebound_range':b,'n':len(x),
          'avg_fwd5':round(x.fwd5.mean(),2),'win_fwd5':round((x.fwd5>0).mean()*100,1),
          'avg_fwd10':round(x.fwd10.mean(),2),'win_fwd10':round((x.fwd10>0).mean()*100,1),
          'avg_fwd20':round(x.fwd20.mean(),2),'win_fwd20':round((x.fwd20>0).mean()*100,1)})
    pd.DataFrame(summary).to_csv('backtest_285a_v2_output/285A_v2_rebound_buckets.csv',index=False)
    # Risk buckets
    rs=[]
    for b in ['0-19','20-39','40-59','60-79','80-100']:
        x=out[out.risk_score.map(bucket)==b]
        if len(x)==0: continue
        rs.append({'risk_range':b,'n':len(x),
          'avg_fwd5':round(x.fwd5.mean(),2),'loss5':round((x.fwd5<0).mean()*100,1),
          'avg_fwd10':round(x.fwd10.mean(),2),'loss10':round((x.fwd10<0).mean()*100,1),
          'avg_fwd20':round(x.fwd20.mean(),2),'loss20':round((x.fwd20<0).mean()*100,1)})
    pd.DataFrame(rs).to_csv('backtest_285a_v2_output/285A_v2_risk_buckets.csv',index=False)
    with open('backtest_285a_v2_output/README.txt','w',encoding='utf-8') as fh:
        fh.write('285A v2 prototype: separate rebound_score and risk_score. Price-only, no supply/breadth yet. Scores are deliberately heuristic and must be validated before production.\n')
    print(out.tail(1).to_string(index=False))
    print(pd.DataFrame(summary).to_string(index=False))
    print(pd.DataFrame(rs).to_string(index=False))

if __name__=='__main__': main()
