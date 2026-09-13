import json, os, statistics, urllib.parse, urllib.request, re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

API = 'https://api.irbank.net/v1'
TOKEN = os.environ.get('IRBANK_API_KEY')
if not TOKEN:
    raise SystemExit('IRBANK_API_KEY is not set')

with open('watchlist.json', encoding='utf-8') as f:
    codes = json.load(f).get('stocks', [])

def get(path, params=None):
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {TOKEN}',
        'User-Agent': 'kabu-score/9.0'
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def get_all_prices(code, minimum=80):
    rows = []
    cursor = None
    attribution = {}
    for _ in range(10):
        params = {'limit': 500}
        if cursor:
            params['cursor'] = cursor
        data = get(f'/securities/{code}/prices', params)
        page = data.get('prices') or []
        rows.extend(page)
        attribution = data.get('attribution') or attribution
        cursor = data.get('next_cursor')
        if len(rows) >= minimum or not cursor or not page:
            break

    seen = set()
    clean = []
    for x in rows:
        d = x.get('date')
        if not d or d in seen:
            continue
        if x.get('close') is None and x.get('adj_close') is None:
            continue
        seen.add(d)
        clean.append(x)
    clean.sort(key=lambda x: x['date'], reverse=True)
    return clean, attribution

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None
        self._text = []
    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._table = []
        elif tag == 'tr' and self._table is not None:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []
    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td','th') and self._cell is not None and self._row is not None:
            s = ' '.join(''.join(self._cell).split())
            self._row.append(s)
            self._cell = None
        elif tag == 'tr' and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == 'table' and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

def fetch_breadth_and_nikkei(target_date):
    """Fetch daily Prime advance/decline counts and calculate 6/10/15/25-day breadth."""
    url = 'https://tofuhardboiled.com/updownratio/'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; kabu-score/9.0)'})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode('utf-8', errors='ignore')

    p = TableParser(); p.feed(html)
    daily = []
    for table in p.tables:
        for row in table:
            if len(row) >= 10 and re.fullmatch(r'\d{4}/\d{2}/\d{2}', row[0]):
                try:
                    daily.append((row[0], int(row[3].replace(',','')), int(row[4].replace(',','')), float(row[2].replace(',',''))))
                except Exception:
                    pass
    daily.sort(key=lambda x:x[0], reverse=True)
    target = target_date.replace('-','/')
    idx = next((i for i,x in enumerate(daily) if x[0]==target), None)
    if idx is None:
        raise RuntimeError(f'Breadth source has no row for {target_date}. Run after the source daily update (normally after 20:00 JST).')
    def ratio(n):
        part=daily[idx:idx+n]
        if len(part)<n: return None
        up=sum(x[1] for x in part); down=sum(x[2] for x in part)
        return round(up/down*100,2) if down else None
    breadth={'6d':ratio(6),'10d':ratio(10),'15d':ratio(15),'25d':ratio(25),
             'source':'豆腐ハードボイルド（東証プライムの値上がり・値下がり銘柄数から算出）'}
    nikkei_value=daily[idx][3]
    nikkei_change=None
    if idx+1<len(daily) and daily[idx+1][3]:
        nikkei_change=round((nikkei_value/daily[idx+1][3]-1)*100,2)
    return {'date':target_date,'breadth':breadth,'nikkei_value':nikkei_value,'nikkei_change':nikkei_change,'source_url':url}



def fetch_supply(code, target_date, security_name=None):
    """Fetch current IRBANK supply/credit metrics through the authenticated API.
    We deliberately do not scrape irbank.net HTML because GitHub Actions can be
    blocked by the public website (403). Screening exposes the current supply
    metrics and week-over-week changes through the API.
    """
    name = security_name or code
    fields = [
        'marginBuyBalance', 'marginSellBalance',
        'marginBuyBalanceChangeWow', 'marginSellBalanceChangeWow',
        'marginRatio', 'jsfLoanRatio', 'marginBuyToFloatRatio'
    ]
    result = {}
    errors = []
    for field in fields:
        try:
            params = {
                'name': name,
                'sort_by': field,
                'sort_order': 'desc',
                'as_of': target_date,
                'limit': 100
            }
            data = get('/screening', params)
            matches = data.get('securities') or []
            # Name matching is normally exact enough; prefer exact security name.
            match = next((x for x in matches if x.get('name') == name), None)
            if match is None and matches:
                match = matches[0]
            if match:
                for m in match.get('metrics') or []:
                    result[m.get('field')] = m.get('value')
                    result[m.get('field') + '_as_of'] = m.get('as_of')
        except Exception as e:
            errors.append(f'{field}: {e}')

    if not result:
        raise RuntimeError('IRBANK API supply metrics unavailable: ' + ('; '.join(errors) if errors else 'no matching metrics'))

    buy = result.get('marginBuyBalance')
    sell = result.get('marginSellBalance')
    buy_chg = result.get('marginBuyBalanceChangeWow')
    sell_chg = result.get('marginSellBalanceChangeWow')
    ratio = result.get('marginRatio')
    jsf_ratio = result.get('jsfLoanRatio')
    buy_float = result.get('marginBuyToFloatRatio')

    return {
        'date': result.get('marginBuyBalance_as_of') or result.get('marginRatio_as_of') or target_date,
        'buy_balance': buy,
        'sell_balance': sell,
        'loan_balance': None,
        'sell_plus_loan': None,
        'credit_ratio': ratio,
        'jsf_loan_ratio': jsf_ratio,
        'buy_to_float_ratio': buy_float,
        'buy_change': buy_chg,
        'sell_change': sell_chg,
        'loan_change': None,
        'source_url': 'https://api.irbank.net/v1/screening',
        'source_note': 'IRBANK API スクリーニング（東証信用残・日証金貸借倍率等）',
        'api_partial': bool(errors),
        'api_errors': errors
    }

def supply_points(s):
    """Experimental 15-point supply score; deliberately conservative until backtest.
    Positive when long-side inventory is being reduced or short-side supply rises.
    """
    if not s: return 0,{}
    p=0
    detail={}
    bc=s.get('buy_change'); sc=s.get('sell_change'); lc=s.get('loan_change')
    if bc is not None:
        detail['buy_balance_change']='improving' if bc<0 else 'heavy' if bc>0 else 'flat'
        p += 4 if bc<0 else 0
    if sc is not None:
        detail['sell_balance_change']='supportive' if sc>0 else 'less_short' if sc<0 else 'flat'
        p += 3 if sc>0 else 0
    if lc is not None:
        detail['loan_balance_change']='supportive' if lc>0 else 'less_lending' if lc<0 else 'flat'
        p += 3 if lc>0 else 0
    r=s.get('credit_ratio')
    detail['credit_ratio_level']=r
    if r is not None:
        p += 5 if r<=3 else 3 if r<=6 else 1 if r<=10 else 0
    jr=s.get('jsf_loan_ratio')
    detail['jsf_loan_ratio']=jr
    if jr is not None:
        # Lower JSF loan ratio means less long-side financing relative to stock lending.
        p += 2 if jr<=1 else 1 if jr<=2 else 0
    bf=s.get('buy_to_float_ratio')
    detail['buy_to_float_ratio']=bf
    if bf is not None:
        p += 2 if bf<=5 else 1 if bf<=10 else 0
    return min(p,15),detail

def pct(a,b):
    return ((a/b)-1)*100 if a is not None and b not in (None,0) else None

def rsi14(vals):
    if len(vals) < 15:
        return None
    gains=[]; losses=[]
    for i in range(1,15):
        d=vals[i-1]-vals[i]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/14; al=sum(losses)/14
    if al == 0:
        return 100.0
    return 100-(100/(1+ag/al))

def breadth_points(b):
    if not b:
        return 0, {'6d':0,'10d':0,'15d':0,'25d':0}

    def pts6(x):
        if x <= 60: return 15
        if x <= 70: return 13
        if x <= 80: return 10
        if x <= 90: return 6
        if x <= 100: return 3
        return 0
    def pts10(x):
        if x <= 70: return 10
        if x <= 80: return 8
        if x <= 90: return 6
        if x <= 100: return 3
        return 0
    def pts15(x):
        if x <= 80: return 5
        if x <= 90: return 4
        if x <= 100: return 2
        return 0
    def pts25(x):
        if x <= 100: return 10
        if x <= 110: return 6
        if x <= 115: return 3
        return 0

    p = {
        '6d': pts6(b['6d']),
        '10d': pts10(b['10d']),
        '15d': pts15(b['15d']),
        '25d': pts25(b['25d'])
    }
    # 15-day and 25-day share the longer-horizon bucket; cap breadth total at 35.
    total = min(sum(p.values()), 35)
    return total, p

def relative_points(x):
    if x is None: return 0
    if x <= -7: return 10
    if x <= -5: return 8
    if x <= -3: return 6
    if x <= -2: return 3
    return 0

def calc(rows, breadth_info, supply_info=None):
    vals=[]
    for x in rows:
        raw=x.get('adj_close') if x.get('adj_close') is not None else x.get('close')
        if raw is not None:
            vals.append(float(raw))
    if not vals:
        raise ValueError('No usable close data')

    close=float(rows[0].get('close') if rows[0].get('close') is not None else vals[0])
    prev=vals[1] if len(vals)>1 else None
    change=pct(close, float(rows[1].get('close'))) if len(rows)>1 and rows[1].get('close') is not None else None

    vols=[x.get('volume') for x in rows]
    base=[float(v) for v in vols[1:21] if v is not None]
    vr=(float(vols[0])/statistics.mean(base)) if vols and vols[0] is not None and base else None

    ma20=statistics.mean(vals[1:21]) if len(vals)>=21 else None
    ma60=statistics.mean(vals[1:61]) if len(vals)>=61 else None
    r5=pct(vals[0],vals[5]) if len(vals)>5 else None
    r10=pct(vals[0],vals[10]) if len(vals)>10 else None
    r20=pct(vals[0],vals[20]) if len(vals)>20 else None
    rsi=rsi14(vals)
    d20=pct(vals[0],ma20); d60=pct(vals[0],ma60)

    # Stock-only bucket: 55 points max.
    daily = 15 if change is not None and change<=-7 else 12 if change is not None and change<=-5 else 9 if change is not None and change<=-3 else 6 if change is not None and change<=-2 else 3 if change is not None and change<=-1 else 0
    vol = 10 if vr is not None and vr>=1.8 else 8 if vr is not None and vr>=1.5 else 6 if vr is not None and vr>=1.3 else 3 if vr is not None and vr>=1.15 else 0
    weak = 10 if d20 is not None and d20<=-12 else 8 if d20 is not None and d20<=-8 else 6 if d20 is not None and d20<=-5 else 3 if d20 is not None and d20<=-3 else 0
    rp = 10 if rsi is not None and rsi<=25 else 8 if rsi is not None and rsi<=30 else 5 if rsi is not None and rsi<=35 else 2 if rsi is not None and rsi<=40 else 0
    tp = 10 if d60 is not None and d60<=-10 else 7 if d60 is not None and d60<=-5 else 4 if d60 is not None and d60<=0 else 0

    bp, bdetail = breadth_points((breadth_info or {}).get('breadth'))
    nikkei_change=(breadth_info or {}).get('nikkei_change')
    rel=(change-nikkei_change) if change is not None and nikkei_change is not None else None
    relp=relative_points(rel)

    sp, sdetail = supply_points(supply_info)
    # Experimental 100-point model: supply is visible and scored, but calibration remains provisional.
    total=min(daily+vol+weak+rp+tp+bp+relp+sp,100)

    return {
      'date':rows[0].get('date'),'price':close,
      'change':round(change,2) if change is not None else None,
      'volume':vols[0] if vols else None,
      'volume_ratio':round(vr,2) if vr is not None else None,
      'ma20':round(ma20,2) if ma20 is not None else None,
      'ma60':round(ma60,2) if ma60 is not None else None,
      'vs20':round(d20,2) if d20 is not None else None,
      'vs60':round(d60,2) if d60 is not None else None,
      'ret5':round(r5,2) if r5 is not None else None,
      'ret10':round(r10,2) if r10 is not None else None,
      'ret20':round(r20,2) if r20 is not None else None,
      'rsi14':round(rsi,1) if rsi is not None else None,
      'score':total,'score_max':100,
      'data_points':len(vals),'rows_received':len(rows),
      'diagnostic':{'usable_points':len(vals),'rsi_ready':len(vals)>=15,'ma20_ready':len(vals)>=21,'ma60_ready':len(vals)>=61},
      'breadth':(breadth_info or {}).get('breadth') or {'6d':None,'10d':None,'15d':None,'25d':None},
      'breadth_points':bp,'breadth_breakdown':bdetail,
      'nikkei_change':nikkei_change,
      'nikkei_value':(breadth_info or {}).get('nikkei_value'),
      'relative_strength':round(rel,2) if rel is not None else None,
      'supply':supply_info or {'date':None},
      'supply_points':sp,
      'supply_breakdown':sdetail,
      'relative_points':relp,
      'score_breakdown':{
        'daily_drop':daily,'volume':vol,'vs20':weak,'rsi14':rp,'vs60':tp,
        'breadth':bp,'relative_strength':relp,'supply':sp
      },
      'breadth_source':(breadth_info or {}).get('source_url'),
      'breadth_error':(breadth_info or {}).get('error')
    }

# Determine the most recent stock date from the first successful symbol.
out={'updated_at':datetime.now(timezone(timedelta(hours=9))).isoformat(),
     'source':'IRBANK API + 豆腐ハードボイルド（騰落銘柄数から算出） + IRBANK需給（週次）',
     'stocks':{},'diagnostics':[]}

for code in codes:
    try:
        info=get(f'/securities/{code}')
        rows, attribution=get_all_prices(code)
        if not rows:
            raise ValueError('No price rows returned')
        # Fetch market breadth once for the stock's latest date.
        breadth_info=fetch_breadth_and_nikkei(rows[0]['date'])
        supply_info=fetch_supply(code,rows[0]['date'], info.get('name', code))
        s=calc(rows,breadth_info,supply_info)
        s.update({'code':code,'name':info.get('name',code),'market':info.get('market'),
                  'industry':info.get('industry'),'attribution':attribution})
        out['stocks'][code]=s
        out['diagnostics'].append({
          'code':code,'status':'ok','rows_received':len(rows),
          'data_points':s['data_points'],'rsi14':s['rsi14'],
          'breadth':s['breadth'],'nikkei_change':s['nikkei_change'],
          'relative_strength':s['relative_strength'],'supply':s.get('supply'),'score':s['score']
        })
    except Exception as e:
        out['stocks'][code]={'code':code,'name':code,'error':str(e)}
        out['diagnostics'].append({'code':code,'status':'error','error':str(e)})

if not any(isinstance(s,dict) and s.get('price') is not None for s in out['stocks'].values()):
    raise SystemExit('No stock data calculated: '+json.dumps(out['diagnostics'],ensure_ascii=False))

with open('data/stocks.json','w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)

print(json.dumps(out['diagnostics'],ensure_ascii=False,indent=2))
