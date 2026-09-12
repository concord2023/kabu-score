import json, os, statistics, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

API='https://api.irbank.net/v1'
TOKEN=os.environ.get('IRBANK_API_KEY')
if not TOKEN:
    raise SystemExit('IRBANK_API_KEY is not set')

with open('watchlist.json',encoding='utf-8') as f:
    codes=json.load(f)['stocks']

def get(path, params=None):
    url=API+path
    if params:
        url+='?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url, headers={'Authorization':f'Bearer {TOKEN}','User-Agent':'kabu-score/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

def get_prices(code, minimum=80):
    rows=[]
    cursor=None
    pages=0
    while len(rows)<minimum and pages<5:
        params={'limit':100}
        if cursor:
            params['cursor']=cursor
        data=get(f'/securities/{code}/prices',params)
        rows.extend(data.get('prices') or [])
        cursor=data.get('next_cursor')
        pages += 1
        if not cursor or not data.get('prices'):
            break
    # newest first, unique by date
    seen=set(); clean=[]
    for x in rows:
        d=x.get('date')
        if d and d not in seen:
            seen.add(d); clean.append(x)
    clean.sort(key=lambda x:x['date'], reverse=True)
    return clean, pages

def pct(a,b):
    return ((a/b)-1)*100 if a is not None and b not in (None,0) else None

def rsi14(c):
    if len(c)<15: return None
    gains=[]; losses=[]
    for i in range(1,15):
        d=c[i-1]-c[i]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/14; al=sum(losses)/14
    if al==0: return 100.0
    return 100-(100/(1+ag/al))

def calc(prices):
    valid=[x for x in prices if x.get('close') is not None]
    c=[float(x['close']) for x in valid]
    v=[x.get('volume') for x in valid]
    if not c: raise ValueError('close data is empty')
    close=c[0]; prev=c[1] if len(c)>1 else None
    change=pct(close,prev)
    vv=[float(x) for x in v[1:21] if x is not None]
    vr=(float(v[0])/statistics.mean(vv)) if v and v[0] is not None and vv else None
    ma20=statistics.mean(c[1:21]) if len(c)>=21 else None
    ma60=statistics.mean(c[1:61]) if len(c)>=61 else None
    r5=pct(close,c[5]) if len(c)>5 else None
    r10=pct(close,c[10]) if len(c)>10 else None
    r20=pct(close,c[20]) if len(c)>20 else None
    rsi=rsi14(c)
    d20=pct(close,ma20); d60=pct(close,ma60)
    daily=18 if change is not None and change<=-7 else 15 if change is not None and change<=-5 else 11 if change is not None and change<=-3 else 7 if change is not None and change<=-2 else 3 if change is not None and change<=-1 else 0
    vol=12 if vr is not None and vr>=1.8 else 10 if vr is not None and vr>=1.5 else 8 if vr is not None and vr>=1.3 else 5 if vr is not None and vr>=1.15 else 0
    weak=15 if d20 is not None and d20<=-12 else 12 if d20 is not None and d20<=-8 else 9 if d20 is not None and d20<=-5 else 5 if d20 is not None and d20<=-3 else 0
    rp=15 if rsi is not None and rsi<=25 else 12 if rsi is not None and rsi<=30 else 8 if rsi is not None and rsi<=35 else 4 if rsi is not None and rsi<=40 else 0
    tp=15 if d60 is not None and d60<=-10 else 10 if d60 is not None and d60<=-5 else 6 if d60 is not None and d60<=0 else 0
    total=min(daily+vol+weak+rp+tp,75)
    return {
      'date':valid[0].get('date'),'price':close,'change':round(change,2) if change is not None else None,
      'volume':v[0] if v else None,'volume_ratio':round(vr,2) if vr is not None else None,
      'ma20':round(ma20,2) if ma20 is not None else None,'ma60':round(ma60,2) if ma60 is not None else None,
      'vs20':round(d20,2) if d20 is not None else None,'vs60':round(d60,2) if d60 is not None else None,
      'ret5':round(r5,2) if r5 is not None else None,'ret10':round(r10,2) if r10 is not None else None,'ret20':round(r20,2) if r20 is not None else None,
      'rsi14':round(rsi,1) if rsi is not None else None,
      'score':total,'score_max':75,'data_points':len(valid),
      'diagnostic':{'rows_received':len(prices),'usable_close_rows':len(valid),'need_15_for_rsi':len(valid)>=15,'need_21_for_ma20':len(valid)>=21,'need_61_for_ma60':len(valid)>=61},
      'score_breakdown':{'daily_drop':daily,'volume':vol,'vs20':weak,'rsi14':rp,'vs60':tp}
    }

out={'updated_at':datetime.now(timezone(timedelta(hours=9))).isoformat(),'source':'IRBANK API','stocks':{},'diagnostics':[]}
for code in codes:
    try:
        info=get(f'/securities/{code}')
        prices,pages=get_prices(code,80)
        s=calc(prices)
        s.update({'code':code,'name':info.get('name',code),'market':info.get('market'),'industry':info.get('industry'),'attribution':prices and get(f'/securities/{code}/prices',{'limit':1}).get('attribution',{}) or {}})
        out['stocks'][code]=s
        out['diagnostics'].append({'code':code,'status':'ok','rows':len(prices),'pages':pages,'rsi14':s['rsi14'],'ret5':s['ret5'],'ret10':s['ret10'],'vs60':s['vs60']})
    except Exception as e:
        out['stocks'][code]={'code':code,'name':code,'error':str(e)}
        out['diagnostics'].append({'code':code,'status':'error','error':str(e)})

if not any('score' in s for s in out['stocks'].values()):
    raise SystemExit('No stock could be calculated. Diagnostics: '+json.dumps(out['diagnostics'],ensure_ascii=False))
with open('data/stocks.json','w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps(out['diagnostics'],ensure_ascii=False,indent=2))
