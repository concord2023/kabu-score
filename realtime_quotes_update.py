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


def parse_chart(payload):
    out={}
    for result in ((payload.get('chart') or {}).get('result') or []):
        meta=result.get('meta') or {}
        symbol=meta.get('symbol')
        if not symbol:
            continue
        price=meta.get('regularMarketPrice')
        if price is None:
            closes=((result.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
            vals=[v for v in closes if v is not None]
            price=vals[-1] if vals else None
        try: price=float(price)
        except (TypeError,ValueError): continue
        prev=meta.get('previousClose',meta.get('chartPreviousClose'))
        try: prev=float(prev) if prev is not None else None
        except (TypeError,ValueError): prev=None
        change=price-prev if prev is not None else None
        rmt=meta.get('regularMarketTime')
        out[symbol.replace('.T','')]={'symbol':symbol,'price':round(price,4),'previous_close':round(prev,4) if prev is not None else None,'change':round(change,4) if change is not None else None,'change_pct':round(change/prev*100,4) if change is not None and prev else None,'market_time':datetime.fromtimestamp(rmt,timezone.utc).astimezone(JST).isoformat() if rmt else datetime.now(JST).isoformat(),'quote_type':'yahoo_chart'}
    return out

def fetch_chart(codes):
    errors=[]; out={}; used_host=None
    for code in codes:
        got=False
        for host in ('https://query1.finance.yahoo.com','https://query2.finance.yahoo.com'):
            url=host+'/v8/finance/chart/'+urllib.parse.quote(code+'.T')+'?'+urllib.parse.urlencode({'range':'1d','interval':'1m','includePrePost':'false','events':'div,splits'})
            try:
                result=parse_chart(fetch_json(url))
                if result:
                    out.update(result); used_host=used_host or host; got=True; break
                errors.append(code+' '+host+': empty')
            except Exception as e: errors.append(code+' '+host+': '+str(e))
        if not got: errors.append(code+': no usable chart quote')
    return out,used_host,errors

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

def load_daily_ranking():
    try:
        return json.loads(Path('data/decision_ranking.json').read_text(encoding='utf-8'))
    except Exception:
        return None


def latest_daily_day_from_ranking(d):
    days=[]
    for row in (d or {}).get('ranking') or []:
        dd=(row.get('details') or {}).get('date') or row.get('date')
        if dd:
            try: days.append(datetime.fromisoformat(str(dd)[:10]).date())
            except Exception: pass
    return max(days) if days else None


def daily_fallback_quotes(codes, ranking):
    out={}
    for row in (ranking or {}).get('ranking') or []:
        code=str(row.get('code') or '').strip().upper()
        if code not in codes: continue
        d=row.get('details') or {}
        price=d.get('price',row.get('price'))
        if price is None: continue
        change=d.get('change',row.get('change'))
        out[code]={'symbol':code+'.T','price':price,'previous_close':round(float(price)-float(change),4) if change is not None else None,
                   'change':change,'change_pct':(round(float(change)/float(price-float(change))*100,4) if change is not None and float(price)-float(change) else None),'market_time':d.get('date') or row.get('date') or (ranking or {}).get('updated_at'),
                   'quote_type':'daily_ranking_fallback'}
    return out


def main():
    codes=codes_from_watchlist()
    if not codes: raise SystemExit('watchlist is empty')
    quotes,host,errors=fetch_batch(codes); source='Yahoo Finance spark API'; fallback=False
    missing=[c for c in codes if c not in quotes]
    if missing:
        chart_quotes,chart_host,chart_errors=fetch_chart(missing)
        quotes.update(chart_quotes); errors.extend(chart_errors)
        if chart_quotes:
            source='Yahoo Finance chart API' if not host else 'Yahoo Finance spark + chart API'
            host=host or chart_host
    ranking=load_daily_ranking()
    latest_day=latest_daily_day_from_ranking(ranking)
    # A quote is valid only when its own market date is the same trading day as
    # the latest Daily stock update. Older quotes are rejected per ticker.
    valid_quotes={}
    rejected=[]
    for code in codes:
        q=quotes.get(code)
        if not q:
            continue
        qday=None
        mt=q.get('market_time')
        if mt:
            try:
                dt=datetime.fromisoformat(str(mt).replace('Z','+00:00'))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=JST)
                qday=dt.astimezone(JST).date()
            except Exception: pass
        if latest_day and qday and qday < latest_day:
            rejected.append({'code':code,'quote_day':str(qday),'latest_daily_day':str(latest_day)})
            continue
        valid_quotes[code]=q

    # Never let a partial/old Yahoo response erase newer daily values. Fill only
    # missing/rejected names from the exact ranking currently displayed by the UI.
    fallback_quotes=daily_fallback_quotes(codes, ranking)
    for code in codes:
        if code not in valid_quotes and code in fallback_quotes:
            valid_quotes[code]=fallback_quotes[code]
            fallback=True

    now=datetime.now(JST)
    quote_days=[]
    for q in valid_quotes.values():
        mt=q.get('market_time')
        if mt:
            try:
                dt=datetime.fromisoformat(str(mt).replace('Z','+00:00'))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=JST)
                quote_days.append(dt.astimezone(JST).date())
            except Exception: pass
    market_date=str(max(quote_days)) if quote_days else (str(latest_day) if latest_day else None)
    if latest_day and market_date and market_date < str(latest_day):
        # Safety net: do not publish a snapshot that globally regresses.
        valid_quotes=fallback_quotes
        market_date=str(latest_day)
        fallback=True
        source='現在表示中のDaily stock update（古いリアルタイム値を除外）'
    elif fallback and not source.startswith('現在表示中'):
        source += ' + 現在表示中のDaily stock update（不足/古い値を補完）'

    payload={'updated_at':now.isoformat(),'market_date':market_date,'source':source,'source_host':host,
             'count':len(valid_quotes),'requested':len(codes),'quotes':valid_quotes,'fallback':fallback,
             'rejected_old_quotes':rejected,'errors':errors,
             'note':'Yahoo取得値は銘柄ごとに市場日を検証。Daily stock updateより古い値は公開せず、同日の日次値で補完します。'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'count':len(valid_quotes),'requested':len(codes),'source':source,'fallback':fallback,'rejected_old_quotes':len(rejected),'errors':errors},ensure_ascii=False))

if __name__=='__main__': main()
