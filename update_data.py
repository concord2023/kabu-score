import json, os, statistics, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

API='https://api.irbank.net/v1'
TOKEN=os.environ.get('IRBANK_API_KEY')
if not TOKEN: raise SystemExit('IRBANK_API_KEY is not set')

with open('watchlist.json',encoding='utf-8') as f: codes=json.load(f)['stocks']

def get(path, params=None):
    url=API+path
    if params: url+='?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Authorization':f'Bearer {TOKEN}','User-Agent':'kabu-score/4.0'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            body=r.read().decode('utf-8')
            return json.loads(body)
    except Exception as e:
        raise RuntimeError(f'{path}: {e}')

def pct(a,b): return ((a/b)-1)*100 if a is not None and b not in (None,0) else None

def rsi14(c):
    if len(c)<15:return None
    gains=[];losses=[]
    for i in range(1,15):
        d=c[i-1]-c[i];gains.append(max(d,0));losses.append(max(-d,0))
    ag=sum(gains)/14; al=sum(losses)/14
    return 100.0 if al==0 else 100-(100/(1+ag/al))

def breadth_points(b):
    if not b:return {'6d':0,'10d':0,'15d':0,'25d':0}
    def v(k):
        try:return float(b[k])
        except:return None
    x=v('6d');p6=5 if x is not None and x<=60 else 4 if x is not None and x<=70 else 3 if x is not None and x<=80 else 2 if x is not None and x<=90 else 1 if x is not None and x<=100 else 0
    x=v('10d');p10=4 if x is not None and x<=70 else 3 if x is not None and x<=80 else 2 if x is not None and x<=90 else 1 if x is not None and x<=100 else 0
    x=v('15d');p15=3 if x is not None and x<=80 else 2 if x is not None and x<=90 else 1 if x is not None and x<=100 else 0
    x=v('25d');p25=4 if x is not None and x>=130 else 3 if x is not None and x>=125 else 2 if x is not None and x>=120 else 1 if x is not None and x>=115 else 0
    return {'6d':p6,'10d':p10,'15d':p15,'25d':p25}

def score_stock(prices,market):
    valid=[x for x in prices if x.get('close') is not None]
    if not valid:return None,'no usable close prices'
    c=[float(x['close']) for x in valid]; v=[x.get('volume') for x in valid]
    close=c[0];prev=c[1] if len(c)>1 else None; change=pct(close,prev)
    vr=None
    vv=[float(x) for x in v[1:21] if x is not None]
    if v[0] is not None and vv:vr=float(v[0])/statistics.mean(vv)
    ma20=statistics.mean(c[1:21]) if len(c)>=21 else None
    ma60=statistics.mean(c[1:61]) if len(c)>=61 else None
    r5=pct(close,c[5]) if len(c)>5 else None;r10=pct(close,c[10]) if len(c)>10 else None;r20=pct(close,c[20]) if len(c)>20 else None
    rsi=rsi14(c);d20=pct(close,ma20);d60=pct(close,ma60)
    daily=18 if change is not None and change<=-7 else 15 if change is not None and change<=-5 else 11 if change is not None and change<=-3 else 7 if change is not None and change<=-2 else 3 if change is not None and change<=-1 else 0
    vol=12 if vr is not None and vr>=1.8 else 10 if vr is not None and vr>=1.5 else 8 if vr is not None and vr>=1.3 else 5 if vr is not None and vr>=1.15 else 0
    weak=15 if d20 is not None and d20<=-12 else 12 if d20 is not None and d20<=-8 else 9 if d20 is not None and d20<=-5 else 5 if d20 is not None and d20<=-3 else 0
    rp=15 if rsi is not None and rsi<=25 else 12 if rsi is not None and rsi<=30 else 8 if rsi is not None and rsi<=35 else 4 if rsi is not None and rsi<=40 else 0
    tp=15 if d60 is not None and d60<=-10 else 10 if d60 is not None and d60<=-5 else 6 if d60 is not None and d60<=0 else 0
    bp=breadth_points(market.get('breadth'));breadth=sum(bp.values())
    nk=market.get('nikkei_change');rel=(change-nk) if change is not None and nk is not None else None
    relp=10 if rel is not None and rel<=-7 else 8 if rel is not None and rel<=-5 else 6 if rel is not None and rel<=-3 else 3 if rel is not None and rel<=-2 else 0
    total=min(daily+vol+weak+rp+tp+breadth+relp,100)
    return {'date':valid[0].get('date'),'price':close,'change':round(change,2) if change is not None else None,'volume':v[0],'volume_ratio':round(vr,2) if vr is not None else None,'ma20':round(ma20,2) if ma20 is not None else None,'ma60':round(ma60,2) if ma60 is not None else None,'vs20':round(d20,2) if d20 is not None else None,'vs60':round(d60,2) if d60 is not None else None,'ret5':round(r5,2) if r5 is not None else None,'ret10':round(r10,2) if r10 is not None else None,'ret20':round(r20,2) if r20 is not None else None,'rsi14':round(rsi,1) if rsi is not None else None,'score':total,'score_max':100,'data_points':len(valid),'score_breakdown':{'daily_drop':daily,'volume':vol,'vs20':weak,'rsi14':rp,'vs60':tp,'breadth_6d':bp['6d'],'breadth_10d':bp['10d'],'breadth_15d':bp['15d'],'breadth_25d':bp['25d'],'nikkei_relative':relp},'breadth':market.get('breadth'),'nikkei_change':nk,'relative_strength':round(rel,2) if rel is not None else None,'breadth_source':market.get('source_url')},None

# Keep market context optional. Stock calculation must never silently disappear because market data failed.
market={'breadth':None,'nikkei_change':None,'source_url':None,'date':None,'status':'pending'}
out={'updated_at':datetime.now(timezone(timedelta(hours=9))).isoformat(),'source':'IRBANK API','market_context':market,'stocks':{},'diagnostics':[]}
for code in codes:
    try:
        info=get(f'/securities/{code}')
        prices=get(f'/securities/{code}/prices',{'limit':100})
        result,err=score_stock(prices.get('prices',[]),market)
        if result:
            result.update({'code':code,'name':info.get('name',code),'market':info.get('market'),'industry':info.get('industry'),'attribution':prices.get('attribution',{})})
            out['stocks'][code]=result
            out['diagnostics'].append({'code':code,'status':'ok','price_rows':len(prices.get('prices',[])),'usable_rows':result['data_points']})
        else:
            out['stocks'][code]={'code':code,'name':info.get('name',code),'error':err,'price_rows':len(prices.get('prices',[]))}
            out['diagnostics'].append({'code':code,'status':'error','error':err,'price_rows':len(prices.get('prices',[]))})
    except Exception as e:
        out['stocks'][code]={'code':code,'name':code,'error':str(e)}
        out['diagnostics'].append({'code':code,'status':'error','error':str(e)})

if not out['stocks'] or not any('score' in s for s in out['stocks'].values()):
    raise SystemExit('No stock could be calculated. Diagnostics: '+json.dumps(out['diagnostics'],ensure_ascii=False))
with open('data/stocks.json','w',encoding='utf-8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps(out['diagnostics'],ensure_ascii=False,indent=2))
