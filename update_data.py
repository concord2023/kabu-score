import json, os, statistics, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

API='https://api.irbank.net/v1'
TOKEN=os.environ.get('IRBANK_API_KEY')
if not TOKEN:
    raise SystemExit('IRBANK_API_KEY is not set')

with open('watchlist.json',encoding='utf-8') as f:
    codes=json.load(f).get('stocks',[])


def get(path, params=None):
    url=API+path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req=urllib.request.Request(url, headers={'Authorization':f'Bearer {TOKEN}','User-Agent':'kabu-score/6.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_all_prices(code, minimum=80):
    rows=[]; cursor=None
    for _ in range(10):
        params={'limit':500}
        if cursor: params['cursor']=cursor
        data=get(f'/securities/{code}/prices', params)
        page=data.get('prices') or []
        rows.extend(page)
        cursor=data.get('next_cursor')
        if len(rows)>=minimum or not cursor or not page:
            attribution=data.get('attribution') or {}
            break
    else:
        attribution={}
    seen=set(); clean=[]
    for x in rows:
        d=x.get('date')
        if not d or d in seen: continue
        close=x.get('close')
        adj=x.get('adj_close')
        if close is None and adj is None: continue
        seen.add(d)
        clean.append(x)
    clean.sort(key=lambda x:x['date'], reverse=True)
    return clean, attribution


def pct(a,b):
    return ((a/b)-1)*100 if a is not None and b not in (None,0) else None


def rsi14(vals):
    if len(vals)<15: return None
    gains=[]; losses=[]
    for i in range(1,15):
        d=vals[i-1]-vals[i]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/14; al=sum(losses)/14
    if al==0: return 100.0
    return 100-(100/(1+ag/al))


def calc(rows):
    # newest first; use adjusted close for technical calculations
    vals=[]
    for x in rows:
        raw=x.get('adj_close') if x.get('adj_close') is not None else x.get('close')
        if raw is None: continue
        vals.append(float(raw))
    if not vals: raise ValueError('No usable close/adj_close data')
    close=float(rows[0].get('close') if rows[0].get('close') is not None else vals[0])
    prev=vals[1] if len(vals)>1 else None
    vr=None
    vols=[x.get('volume') for x in rows]
    base=[float(v) for v in vols[1:21] if v is not None]
    if vols and vols[0] is not None and base:
        vr=float(vols[0])/statistics.mean(base)
    ma20=statistics.mean(vals[1:21]) if len(vals)>=21 else None
    ma60=statistics.mean(vals[1:61]) if len(vals)>=61 else None
    r5=pct(vals[0],vals[5]) if len(vals)>5 else None
    r10=pct(vals[0],vals[10]) if len(vals)>10 else None
    r20=pct(vals[0],vals[20]) if len(vals)>20 else None
    rsi=rsi14(vals)
    d20=pct(vals[0],ma20); d60=pct(vals[0],ma60)
    change=pct(close, float(rows[1].get('close'))) if len(rows)>1 and rows[1].get('close') is not None else None
    daily=18 if change is not None and change<=-7 else 15 if change is not None and change<=-5 else 11 if change is not None and change<=-3 else 7 if change is not None and change<=-2 else 3 if change is not None and change<=-1 else 0
    vol=12 if vr is not None and vr>=1.8 else 10 if vr is not None and vr>=1.5 else 8 if vr is not None and vr>=1.3 else 5 if vr is not None and vr>=1.15 else 0
    weak=15 if d20 is not None and d20<=-12 else 12 if d20 is not None and d20<=-8 else 9 if d20 is not None and d20<=-5 else 5 if d20 is not None and d20<=-3 else 0
    rp=15 if rsi is not None and rsi<=25 else 12 if rsi is not None and rsi<=30 else 8 if rsi is not None and rsi<=35 else 4 if rsi is not None and rsi<=40 else 0
    tp=15 if d60 is not None and d60<=-10 else 10 if d60 is not None and d60<=-5 else 6 if d60 is not None and d60<=0 else 0
    return {
      'date':rows[0].get('date'),'price':close,'change':round(change,2) if change is not None else None,
      'volume':vols[0] if vols else None,'volume_ratio':round(vr,2) if vr is not None else None,
      'ma20':round(ma20,2) if ma20 is not None else None,'ma60':round(ma60,2) if ma60 is not None else None,
      'vs20':round(d20,2) if d20 is not None else None,'vs60':round(d60,2) if d60 is not None else None,
      'ret5':round(r5,2) if r5 is not None else None,'ret10':round(r10,2) if r10 is not None else None,'ret20':round(r20,2) if r20 is not None else None,
      'rsi14':round(rsi,1) if rsi is not None else None,
      'score':min(daily+vol+weak+rp+tp,75),'score_max':75,
      'data_points':len(vals),'rows_received':len(rows),
      'diagnostic':{'usable_points':len(vals),'rsi_ready':len(vals)>=15,'ma20_ready':len(vals)>=21,'ma60_ready':len(vals)>=61},
      'score_breakdown':{'daily_drop':daily,'volume':vol,'vs20':weak,'rsi14':rp,'vs60':tp},
      'breadth':{'6d':None,'10d':None,'15d':None,'25d':None},
      'nikkei_change':None,'relative_strength':None
    }

out={'updated_at':datetime.now(timezone(timedelta(hours=9))).isoformat(),'source':'IRBANK API','stocks':{},'diagnostics':[]}
for code in codes:
    try:
        info=get(f'/securities/{code}')
        rows, attribution=get_all_prices(code)
        s=calc(rows)
        s.update({'code':code,'name':info.get('name',code),'market':info.get('market'),'industry':info.get('industry'),'attribution':attribution})
        out['stocks'][code]=s
        out['diagnostics'].append({'code':code,'status':'ok','rows_received':len(rows),'data_points':s['data_points'],'rsi14':s['rsi14'],'ret5':s['ret5'],'ret10':s['ret10'],'vs60':s['vs60']})
    except Exception as e:
        out['stocks'][code]={'code':code,'name':code,'error':str(e)}
        out['diagnostics'].append({'code':code,'status':'error','error':str(e)})

if not any(isinstance(s,dict) and s.get('price') is not None for s in out['stocks'].values()):
    raise SystemExit('No stock data calculated: '+json.dumps(out['diagnostics'],ensure_ascii=False))
with open('data/stocks.json','w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps(out['diagnostics'],ensure_ascii=False,indent=2))
