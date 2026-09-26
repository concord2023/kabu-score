"""Fetch the latest Yahoo Finance quotes server-side for GitHub Pages.

The browser cannot reliably call Yahoo Finance because of CORS. GitHub Actions
can fetch the data server-side and publish a small JSON snapshot. The UI button
then reads this static file instantly, so it never waits on a third-party CORS
proxy and never changes the saved kabu-score data.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path('data/realtime_quotes.json')
UA = 'Mozilla/5.0 (compatible; kabu-score realtime updater)'
JST = timezone(timedelta(hours=9))


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json,text/plain,*/*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def codes_from_watchlist():
    p = Path('watchlist.json')
    data = json.loads(p.read_text(encoding='utf-8'))
    out=[]
    for x in data.get('stocks', []):
        c = str(x.get('code') if isinstance(x, dict) else x).strip().upper()
        if c and c not in out:
            out.append(c)
    return out


def parse_spark(payload):
    out={}
    for item in ((payload.get('spark') or {}).get('result') or []):
        response = (item.get('response') or [{}])[0]
        meta = response.get('meta') or {}
        symbol = meta.get('symbol') or item.get('symbol')
        if not symbol:
            continue
        price = meta.get('regularMarketPrice')
        prev = meta.get('previousClose', meta.get('chartPreviousClose'))
        try: price=float(price)
        except (TypeError,ValueError): continue
        try: prev=float(prev)
        except (TypeError,ValueError): prev=None
        change = price-prev if prev is not None else None
        out[symbol] = {
            'symbol': symbol,
            'price': round(price, 4),
            'previous_close': round(prev, 4) if prev is not None else None,
            'change': round(change, 4) if change is not None else None,
            'change_pct': round(change/prev*100, 4) if change is not None and prev else None,
            'market_time': datetime.fromtimestamp(meta['regularMarketTime'], timezone.utc).astimezone(JST).isoformat() if meta.get('regularMarketTime') else None,
        }
    return out


def fetch_batch(codes):
    symbols=','.join(c+'.T' for c in codes)
    errors=[]
    for host in ('https://query1.finance.yahoo.com','https://query2.finance.yahoo.com'):
        url=host+'/v7/finance/spark?'+urllib.parse.urlencode({
            'symbols': symbols, 'range':'1d', 'interval':'1m',
            'indicators':'close', 'includeTimestamps':'false',
            'includePrePost':'false', 'corsDomain':'finance.yahoo.com',
        })
        try:
            result=parse_spark(fetch_json(url))
            if result:
                return result, host
            errors.append(host+': empty')
        except Exception as e:
            errors.append(host+': '+str(e))
    raise RuntimeError(' / '.join(errors))


def main():
    codes=codes_from_watchlist()
    if not codes:
        raise SystemExit('watchlist is empty')
    quotes, host=fetch_batch(codes)
    payload={
        'updated_at': datetime.now(JST).isoformat(),
        'source':'Yahoo!ファイナンス / Yahoo Finance spark API',
        'source_host':host,
        'count':len(quotes),
        'requested':len(codes),
        'quotes':quotes,
        'note':'GitHub Actionsで定期取得した表示専用スナップショット。kabu-score保存値は変更しません。',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'updated_at':payload['updated_at'],'count':len(quotes),'requested':len(codes),'source_host':host}, ensure_ascii=False))

if __name__=='__main__':
    main()
