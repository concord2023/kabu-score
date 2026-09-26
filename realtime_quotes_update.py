"""Create a static quote snapshot for the GitHub Pages UI."""
import json, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
OUT=Path('data/realtime_quotes.json'); UA='Mozilla/5.0 (compatible; kabu-score realtime updater)'; JST=timezone(timedelta(hours=9))

def fetch_json(url, timeout=12):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Referer':'https://finance.yahoo.com/'})
    with urllib.request.urlopen(req,timeout=timeout) as r: return json.loads(r.read().decode('utf-8','replace'))

def codes_from_watchlist():
    data=json.loads(Path('watchlist.json').read_text(encoding='utf-8')); out=[]
    for x in data.get('stocks',[]):
        c=str(x.get('code') if isinstance(x,dict) else x).strip().upper()
        if c and c not in out: out.append(c)
    return out

def parse_spark(payload):
    out={}
    for item in ((payload.get('spark') or {}).get('result') or []):
        response=(item.get('response') or [{}])[0]; meta=response.get('meta') or {}; symbol=meta.get('symbol') or item.get('symbol')
        if not symbol: continue
        try: price=float(meta.get('regularMarketPrice'))
        except (TypeError,ValueError): continue
        try: prev=float(meta.get('previousClose',meta.get('chartPreviousClose')))
        except (TypeError,ValueError): prev=None
        change=price-prev if prev is not None else None
        out[symbol.replace('.T','')]={'symbol':symbol,'price':round(price,4),'previous_close':round(prev,4) if prev is not None else None,'change':round(change,4) if change is not None else None,'change_pct':round(change/prev*100,4) if change is not None and prev else None,'market_time':datetime.fromtimestamp(meta['regularMarketTime'],timezone.utc).astimezone(JST).isoformat() if meta.get('regularMarketTime') else None,'quote_type':'yahoo'}
    return out

def fetch_batch(codes):
    symbols=','.join(c+'.T' for c in codes); errors=[]
    for host in ('https://query1.finance.yahoo.com','https://query2.finance.yahoo.com'):
        url=host+'/v7/finance/spark?'+urllib.parse.urlencode({'symbols':symbols,'range':'1d','interval':'1m','indicators':'close','includeTimestamps':'false','includePrePost':'false','corsDomain':'finance.yahoo.com'})
        try:
            result=parse_spark(fetch_json(url))
            if result: return result,host,errors
            errors.append(host+': empty')
        except Exception as e: errors.append(host+': '+str(e))
    return {},None,errors

def fallback_from_stocks(codes):
    try: data=json.loads(Path('data/stocks.json').read_text(encoding='utf-8'))
    except Exception as e: return {},'stocks.json unavailable: '+str(e)
    out={}
    stocks=data.get('stocks') or {}
    for code in codes:
        row=stocks.get(code) or stocks.get(str(code))
        if not isinstance(row,dict) or row.get('price') is None: continue
        price=float(row['price']); change=row.get('change')
        out[code]={'symbol':code+'.T','price':price,'previous_close':round(price-float(change),4) if change is not None else None,'change':change,'change_pct':round(float(change)/(price-float(change))*100,4) if change is not None and price-float(change) else None,'market_time':row.get('date'),'quote_type':'daily_close_fallback'}
    return out,'保存済み日次終値（Yahoo取得失敗/時間外）'

def main():
    codes=codes_from_watchlist()
    if not codes: raise SystemExit('watchlist is empty')
    quotes,host,errors=fetch_batch(codes); source='Yahoo Finance spark API'; fallback=False
    if not quotes:
        quotes,source=fallback_from_stocks(codes); fallback=True
    payload={'updated_at':datetime.now(JST).isoformat(),'source':source,'source_host':host,'count':len(quotes),'requested':len(codes),'quotes':quotes,'fallback':fallback,'errors':errors,'note':'Yahoo取得時は市場データ。取得不能時は保存済み日次終値を表示し、リアルタイム値とは明示的に区別します。'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'count':len(quotes),'requested':len(codes),'source':source,'fallback':fallback,'errors':errors},ensure_ascii=False))
if __name__=='__main__': main()
